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
import type { ProjectRegistry } from "./registry.ts";
import type {
  ActivityItem,
  CockpitData,
  CockpitProject,
  ControlResult,
  HealSummary,
  RunningSession,
} from "../shared/types.ts";

export interface CockpitDeps {
  registry: ProjectRegistry;
  sssfHome?: string;
  /** "name status" lines for every sssf-* container; tests inject a fake. */
  dockerPs?: () => Promise<string>;
}

interface ContainerInfo {
  adwId: string;
  running: boolean;
}

interface ProjectRow extends CockpitProject {
  /** internal: per-project running detail + activity merged by computeCockpit */
  _running: RunningSession[];
  _activity: ActivityItem[];
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

async function parseContainers(dockerPs: () => Promise<string>): Promise<ContainerInfo[]> {
  let out = "";
  try {
    out = await dockerPs();
  } catch {
    return [];
  }
  return out
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, ...rest] = line.split(/\s+/);
      return { adwId: name.replace(/^sssf-/, ""), running: (rest[0] ?? "") === "Up" };
    });
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
    containers: 0, worktrees: 0, costTodayUsd: 0,
    lastActivity: deps.registry.list().find((p) => p.name === name)?.lastRun ?? null,
    stale: false, _running: [], _activity: [],
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
  const owned = new Set<string>();
  try {
    const hasSessions = hasTable(db, "sessions");
    if (hasSessions) {
      const t = db.query<{
        running: number; today: number; failedToday: number; cost: number; lastActivity: string | null;
      }, []>(
        `SELECT SUM(status='running') running,
                SUM(date(started_at)=date('now')) today,
                SUM(status='fail' AND date(started_at)=date('now')) failedToday,
                COALESCE(SUM(CASE WHEN date(started_at)=date('now') THEN total_cost ELSE 0 END),0) cost,
                MAX((SELECT MAX(started_at) FROM events WHERE events.adw_id = sessions.adw_id)) lastActivity
           FROM sessions`,
      ).get()!;
      empty.sessionsRunning = Number(t.running ?? 0);
      empty.sessionsToday = Number(t.today ?? 0);
      empty.sessionsFailedToday = Number(t.failedToday ?? 0);
      empty.costTodayUsd = Number(t.cost ?? 0);
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
      const tk = db.query<{ backlog: number; inflight: number }, []>(
        `SELECT SUM(status='backlog') backlog,
                SUM(status IN ('starting','running')) inflight FROM tickets`).get()!;
      empty.ticketsBacklog = Number(tk.backlog ?? 0);
      empty.ticketsInFlight = Number(tk.inflight ?? 0);
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
  const containers = await parseContainers(deps.dockerPs ?? realDockerPs);
  const heal = readHeal(home);

  const ownedGlobal = new Set<string>();
  let activity: ActivityItem[] = [];
  const out: CockpitData = {
    generatedAt: new Date().toISOString(),
    kpis: {
      runningSessions: 0, liveContainers: containers.length, orphanContainers: 0,
      sandboxWorktrees: 0, ticketsInFlight: 0, costTodayUsd: 0,
      healRunning: heal.running, healPid: heal.pid,
    },
    projects: [], running: [], heal, activity: [],
  };
  for (const p of projects) {
    const row = projectRow(deps, p.name, p.root, p.db, home, containers, ownedGlobal);
    out.projects.push(row);
    out.kpis.runningSessions += row.sessionsRunning;
    out.kpis.sandboxWorktrees += row.worktrees;
    out.kpis.ticketsInFlight += row.ticketsInFlight;
    out.kpis.costTodayUsd += row.costTodayUsd;
    out.running.push(...row._running);
    activity = activity.concat(row._activity);
  }
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

export async function realDockerPs(): Promise<string> {
  const proc = Bun.spawn(
    ["docker", "ps", "-a", "--filter", "name=sssf-", "--format", "{{.Names}} {{.Status}}"],
    { stdout: "pipe", stderr: "pipe" },
  );
  const out = await new Response(proc.stdout).text();
  await proc.exited;
  return out;
}
