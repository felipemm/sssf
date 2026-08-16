/**
 * Mission Control cockpit — the cross-project aggregate for the visualizer.
 *
 * One `/api/cockpit` document: every registered project's live state (sessions,
 * tickets, containers, worktrees, cost, last activity) plus global KPIs, the
 * running-now strip, healer status, and a cross-project activity feed. Reads
 * are read-only (openReadonly); a project whose db is broken renders zeros
 * with a stale flag — it never fails the whole cockpit.
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import type { Database } from "bun:sqlite";
import { openReadonly } from "./db.ts";
import { contributions } from "./git.ts";
import type { ContributionDay } from "./git.ts";
import type { ProjectRegistry } from "./registry.ts";
import type {
  ActivityItem,
  CockpitData,
  CockpitProject,
  ContainerLogsResponse,
  ControlResult,
  HealSummary,
  RunningSession,
} from "../shared/types.ts";

const CONTRIB_TTL_MS = 5 * 60 * 1000;   // git history is slow-moving — cache it
const DAY_MS = 86400_000;

let contribCache: { at: number; data: ContributionDay[] } | null = null;

/** Tests only — drop the contributions cache between cases. */
export function _resetContribCache(): void {
  contribCache = null;
}

/**
 * Cross-project contributions heatmap: sum each day's commits across every
 * registered project, over the last 364 days (oldest first) — the same shape
 * `contributions()` yields per project. Cached 5 minutes; git walks are
 * `spawnSync` and would otherwise run on every poll.
 */
export function computeCockpitContributions(registry: ProjectRegistry): ContributionDay[] {
  const now = Date.now();
  if (contribCache && now - contribCache.at < CONTRIB_TTL_MS) return contribCache.data;
  const byDate = new Map<string, number>();
  for (const p of registry.list()) {
    try {
      for (const d of contributions(p.root)) {
        byDate.set(d.date, (byDate.get(d.date) ?? 0) + d.count);
      }
    } catch {
      /* unreadable repo — skip this project */
    }
  }
  const days: ContributionDay[] = [];
  for (let i = 363; i >= 0; i--) {
    const date = new Date(now - i * DAY_MS).toISOString().slice(0, 10);
    days.push({ date, count: byDate.get(date) ?? 0 });
  }
  contribCache = { at: now, data: days };
  return days;
}

export interface CockpitDeps {
  registry: ProjectRegistry;
  sssfHome?: string;
  /** "name status" lines for every sssf-* container; tests inject a fake. */
  dockerPs?: () => Promise<string>;
}

interface ContainerInfo {
  adwId: string;
  running: boolean;
  image: string;
  status: string;
  created: string;
}

interface ProjectRow extends CockpitProject {
  /** internal: per-project running detail + activity + owned adw_ids */
  _running: RunningSession[];
  _activity: ActivityItem[];
  _owned: Set<string>;
}

const logTailLines = (path: string, n = 5): string[] => {
  try {
    const lines = readFileSync(path, "utf8").split("\n").filter((l) => l.trim());
    return lines.slice(-n);
  } catch {
    return [];
  }
};

function alivePid(pid: number | null): pid is number {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    // ESRCH: no such process → dead. EPERM: exists but not ours → alive.
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}

function readHeal(home: string): HealSummary {
  let pid: number | null = null;
  try {
    pid = Number.parseInt(readFileSync(join(home, "heal.pid"), "utf8").trim(), 10);
  } catch {
    pid = null;
  }
  let restarts: Record<string, number> = {};
  try {
    restarts = JSON.parse(readFileSync(join(home, "heal-state.json"), "utf8")).restarts ?? {};
  } catch {
    restarts = {};
  }
  return { running: alivePid(pid), pid, logTail: logTailLines(join(home, "heal.log")), restarts };
}

interface ContainersResult {
  containers: ContainerInfo[];
  dockerOk: boolean;
  dockerError: string;
}

async function parseContainers(dockerPs: () => Promise<string>): Promise<ContainersResult> {
  let out = "";
  try {
    out = await dockerPs();
  } catch (e) {
    // docker itself is down/unreachable — the container list is meaningless
    return { containers: [], dockerOk: false, dockerError: (e as Error).message };
  }
  return {
    containers: out
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [name, image, status, created] = line.split("\t");
        return {
          adwId: (name ?? "").replace(/^sssf-/, ""),
          running: (status ?? "").startsWith("Up"),
          image: image ?? "",
          status: status ?? "",
          created: created ?? "",
        };
      }),
    dockerOk: true,
    dockerError: "",
  };
}

function hasTable(db: Database, table: string): boolean {
  return db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(table) !== null;
}

/** One project's row; any db open/query failure → zeros + stale, never throws. */
function projectRow(
  deps: CockpitDeps,
  name: string,
  root: string,
  dbPath: string,
  home: string,
  containers: ContainerInfo[],
  ownedGlobal: Set<string>,
): ProjectRow {
  const empty: ProjectRow = {
    name, root,
    sessionsRunning: 0, sessionsToday: 0, sessionsFailedToday: 0,
    ticketsBacklog: 0, ticketsInFlight: 0,
    containers: 0, worktrees: 0, costTodayUsd: 0, costTotalUsd: 0,
    lastActivity: deps.registry.list().find((p) => p.name === name)?.lastRun ?? null,
    stale: false, _running: [], _activity: [], _owned: new Set<string>(),
  };
  if (!existsSync(dbPath)) {
    return { ...empty, stale: true };
  }
  let db: Database;
  try {
    db = openReadonly(dbPath);
  } catch {
    return { ...empty, stale: true };
  }
  // adw_ids owned by THIS project (sessions + sandbox worktree dirs) —
  // containers map to projects via this set, never the global one.
  const owned = empty._owned;
  try {
    const hasSessions = hasTable(db, "sessions");
    if (hasSessions) {
      const t = db.query<{
        running: number; today: number; failedToday: number; cost: number; costTotal: number;
        lastActivity: string | null;
      }, []>(
        `SELECT SUM(status='running') running,
                SUM(date(started_at)=date('now')) today,
                SUM(status='fail' AND date(started_at)=date('now')) failedToday,
                COALESCE(SUM(CASE WHEN date(started_at)=date('now') THEN total_cost ELSE 0 END),0) cost,
                COALESCE(SUM(total_cost),0) costTotal,
                MAX((SELECT MAX(started_at) FROM events WHERE events.adw_id = sessions.adw_id)) lastActivity
           FROM sessions`,
      ).get()!;
      empty.sessionsRunning = Number(t.running ?? 0);
      empty.sessionsToday = Number(t.today ?? 0);
      empty.sessionsFailedToday = Number(t.failedToday ?? 0);
      empty.costTodayUsd = Number(t.cost ?? 0);
      empty.costTotalUsd = Number(t.costTotal ?? 0);
      if (t.lastActivity) empty.lastActivity = t.lastActivity;
      // running-now detail + adw_id ownership for container mapping
      const running = db.query<{ adw_id: string; started_at: string }, []>(
        "SELECT adw_id, started_at FROM sessions WHERE status='running'").all();
      for (const r of running) {
        owned.add(r.adw_id);
        ownedGlobal.add(r.adw_id);
        const phase = hasTable(db, "phases")
          ? db.query<{ phase_id: string; status: string }, [string]>(
              "SELECT phase_id, status FROM phases WHERE adw_id=? ORDER BY started_at DESC LIMIT 1",
            ).get(r.adw_id)
          : null;
        const started = Date.parse(r.started_at);
        empty._running.push({
          project: name, adwId: r.adw_id,
          phase: phase?.phase_id ?? null, phaseStatus: phase?.status ?? null,
          ageSec: Number.isNaN(started) ? 0 : Math.max(0, Math.round(Date.now() / 1000 - started / 1000)),
        });
      }
      const ids = db.query<{ adw_id: string }, []>("SELECT adw_id FROM sessions").all();
      for (const i of ids) {
        owned.add(i.adw_id);
        ownedGlobal.add(i.adw_id);
      }
    }
    if (hasTable(db, "tickets")) {
      // A ticket's stage is derived from its SESSION (the session is the
      // first-class citizen; the ticket is provenance). A ticket whose run
      // finished is done/failed even if its row still says 'starting'/'running'
      // (mirrors server/status.ts).
      const rows = db.query<{ status: string; adw_id: string | null }, []>(
        "SELECT status, adw_id FROM tickets").all();
      let backlog = 0;
      let inflight = 0;
      for (const r of rows) {
        let status = r.status;
        if (r.adw_id) {
          const srow = hasTable(db, "sessions")
            ? db.query<{ status: string }, [string]>(
                "SELECT status FROM sessions WHERE adw_id=?").get(r.adw_id)
            : null;
          if (srow) status = srow.status === "success" ? "done" : srow.status === "fail" ? "failed" : "running";
        }
        if (status === "starting") status = "running"; // spawned, run warming up
        if (status === "backlog") backlog++;
        else if (status === "running") inflight++;
      }
      empty.ticketsBacklog = backlog;
      empty.ticketsInFlight = inflight;
    }
    if (hasTable(db, "events")) {
      const evs = db.query<{ adw_id: string; started_at: string; type: string }, []>(
        "SELECT adw_id, started_at, type FROM events ORDER BY started_at DESC LIMIT 30").all();
      empty._activity = evs.map((e) => ({ project: name, adwId: e.adw_id, ts: e.started_at, event: e.type }));
    }
  } catch {
    // a genuinely broken db (corrupt, replaced mid-read): zeros + stale
    db.close();
    return { ...empty, stale: true };
  }
  db.close();

  // worktrees + containers owned by this project
  const sbx = join(home, "sandboxes", name);
  let worktrees = 0;
  if (existsSync(sbx)) {
    try {
      worktrees = readdirSync(sbx, { withFileTypes: true }).filter((d) => d.isDirectory()).length;
      for (const d of readdirSync(sbx)) owned.add(d); // orphan worktrees are owned too
    } catch {
      worktrees = 0;
    }
  }
  empty.worktrees = worktrees;
  empty.containers = containers.filter((c) => owned.has(c.adwId)).length;
  return empty;
}

export async function computeCockpit(deps: CockpitDeps): Promise<CockpitData> {
  const home = resolve(deps.sssfHome ?? process.env.SSSF_HOME ?? join(homedir(), ".sssf"));
  const projects = deps.registry.list();
  const { containers, dockerOk, dockerError } = await parseContainers(deps.dockerPs ?? realDockerPs);
  const heal = readHeal(home);

  const ownedGlobal = new Set<string>();
  let activity: ActivityItem[] = [];
  const out: CockpitData = {
    generatedAt: new Date().toISOString(),
    kpis: {
      runningSessions: 0, liveContainers: containers.length, orphanContainers: 0,
      sandboxWorktrees: 0, ticketsInFlight: 0, costTodayUsd: 0, costTotalUsd: 0,
      healRunning: heal.running, healPid: heal.pid,
      dockerOk, dockerError,
    },
    projects: [], running: [], containers: [], heal, activity: [],
  };
  const rows: ProjectRow[] = [];
  for (const p of projects) {
    const row = projectRow(deps, p.name, p.root, p.db, home, containers, ownedGlobal);
    rows.push(row);
    out.kpis.runningSessions += row.sessionsRunning;
    out.kpis.sandboxWorktrees += row.worktrees;
    out.kpis.ticketsInFlight += row.ticketsInFlight;
    out.kpis.costTodayUsd += row.costTodayUsd;
    out.kpis.costTotalUsd += row.costTotalUsd;
    out.running.push(...row._running);
    activity = activity.concat(row._activity);
  }
  // container detail with project ownership (orphans get '')
  const adwToProject = new Map<string, string>();
  for (const row of rows) for (const id of row._owned) adwToProject.set(id, row.name);
  out.projects.push(...rows);
  out.containers = containers.map((c) => ({
    name: `sssf-${c.adwId}`,
    adwId: c.adwId,
    image: c.image,
    status: c.status,
    created: c.created,
    running: c.running,
    project: adwToProject.get(c.adwId) ?? "",
  }));
  out.kpis.orphanContainers = containers.filter((c) => !ownedGlobal.has(c.adwId)).length;
  out.activity = activity.sort((x, y) => y.ts.localeCompare(x.ts)).slice(0, 30);
  return out;
}

export interface SpawnResult {
  code: number;
  out: string;
}

export interface ControlParams {
  project?: string;
  root?: string;
  confirm?: boolean;
}

/**
 * Run a cockpit control. Every control shells to the sssf CLI (the only
 * writer) and returns a ControlResult; validations happen before any spawn.
 */
export async function handleControl(
  kind: "refresh" | "add" | "remove",
  params: ControlParams,
  deps: CockpitDeps & { spawnCli?: (args: string[]) => Promise<SpawnResult> },
): Promise<ControlResult> {
  const spawn = deps.spawnCli ?? defaultSpawnCli;
  const fail = (error: string): ControlResult => ({ ok: false, error });

  if (kind === "add") {
    if (!params.root) return fail("root is required");
    const root = resolve(params.root);
    if (!existsSync(root) || !existsSync(join(root, "adws"))) {
      return fail("not a project: no adws/ directory at the given path");
    }
    const r = await spawn(["projects", "add", root]);
    return r.code === 0 ? { ok: true, output: r.out } : fail(r.out || `sssf projects add exited ${r.code}`);
  }

  if (kind === "remove") {
    if (!params.confirm) return fail("removal requires confirm: true");
    if (!params.project) return fail("project is required");
    const r = await spawn(["projects", "remove", params.project]);
    return r.code === 0 ? { ok: true, output: r.out } : fail(r.out || `sssf projects remove exited ${r.code}`);
  }

  // refresh
  if (!params.project) return fail("project is required");
  const entry = deps.registry.list().find((p) => p.name === params.project);
  if (!entry) return fail(`no project ${params.project}`);
  const r = await spawn(["init", "--refresh", "--auto", "--project", entry.root]);
  return r.code === 0 ? { ok: true, output: r.out } : fail(r.out || `sssf init exited ${r.code}`);
}

export async function defaultSpawnCli(args: string[]): Promise<SpawnResult> {
  const proc = Bun.spawn(["sssf", ...args], { stdout: "pipe", stderr: "pipe" });
  const out = await new Response(proc.stdout).text();
  const err = await new Response(proc.stderr).text();
  await proc.exited;
  return { code: proc.exitCode ?? 0, out: (out + err).trim() };
}

const SAFE_CONTAINER = /^sssf-[A-Za-z0-9._-]+$/;

/**
 * Tail a sandbox container's logs (`docker logs --tail N --timestamps`).
 * The name is validated — anything not shaped like an sssf container is
 * rejected before docker is ever invoked.
 */
export async function containerLogs(
  name: string,
  tail: number,
  dockerLogs?: (args: string[]) => Promise<string>,
): Promise<ContainerLogsResponse> {
  if (!SAFE_CONTAINER.test(name)) return { ok: false, lines: [], error: "invalid container name" };
  const n = Math.min(500, Math.max(10, Math.floor(tail) || 100));
  const run = dockerLogs ?? defaultDockerLogs;
  try {
    const out = await run(["logs", "--tail", String(n), "--timestamps", name]);
    return { ok: true, lines: out.split("\n").filter(Boolean) };
  } catch (e) {
    return { ok: false, lines: [], error: (e as Error).message };
  }
}

export async function defaultDockerLogs(args: string[]): Promise<string> {
  const proc = Bun.spawn(["docker", ...args], { stdout: "pipe", stderr: "pipe" });
  const out = await new Response(proc.stdout).text();
  const err = await new Response(proc.stderr).text();
  await proc.exited;
  if (proc.exitCode !== 0) throw new Error(err.trim() || `docker exited ${proc.exitCode}`);
  return out;
}

export async function realDockerPs(): Promise<string> {
  const proc = Bun.spawn(
    ["docker", "ps", "-a", "--filter", "name=sssf-", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.CreatedAt}}"],
    { stdout: "pipe", stderr: "pipe" },
  );
  const out = await new Response(proc.stdout).text();
  const err = await new Response(proc.stderr).text();
  await proc.exited;
  if (proc.exitCode !== 0) throw new Error(err.trim() || `docker exited ${proc.exitCode}`);
  return out;
}
