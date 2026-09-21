/**
 * MR monitor (issue #98) — a 10-minute watcher embedded in the viz service.
 *
 * Every sweep scans each registered project's `ready-to-deploy` tickets that
 * have a registered MR (`ticket_mrs`), polls GitLab for the MR's pipeline and
 * merge state, and alerts through the notify adapter (`sssf notify`) on the
 * transitions an operator actually cares about: pipeline green, pipeline
 * failed, MR merged. Last-seen state lives in `monitor_state` in the same
 * trace db, so a transition alerts exactly once — and a replaced MR (the
 * deploy flow's fix-forward path) resets the state and alerts again.
 *
 * Lives and dies with the viz server: index.ts starts the interval on boot
 * and clears it on shutdown. No separate daemon.
 */
import { Database } from "bun:sqlite";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fetchMrState as defaultFetchMrState } from "./gitlab.ts";
import type { Fetcher, MrPipeline, MrState, MrStateResult } from "./gitlab.ts";
import type { ProjectRegistry } from "./registry.ts";

export const MONITOR_CONFIG_FILE = "adws/config/monitor.json";
export const DEFAULT_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes, per the issue

export interface MonitorConfig {
  gitlabUrl: string;
  tokenEnv: string;
}

export interface SweepResult {
  project: string;
  db: string;
  watched: number;
  notified: number;
  error?: string;
}

/** The monitor's injectable surface — every live system boundary is a dep. */
export interface SweepDeps {
  fetchMrState?: (opts: { gitlabUrl: string; token: string; repo: string; iid: string }) => Promise<MrStateResult | null>;
  token?: (envName: string) => string;
  notify?: (ticketId: string, text: string, mrUrl: string) => Promise<void>;
  now?: () => string;
}

const MONITOR_STATE_DDL = `CREATE TABLE IF NOT EXISTS monitor_state (
  ticket_id TEXT PRIMARY KEY,
  repo TEXT NOT NULL,
  iid TEXT NOT NULL,
  pipeline TEXT NOT NULL,
  state TEXT NOT NULL,
  updated_at TEXT
)`;

const SCAN_SQL = `SELECT t.id, t.title, m.repo, m.iid, m.url
  FROM tickets t JOIN ticket_mrs m ON m.ticket_id = t.id
  WHERE t.status = 'ready-to-deploy'`;

/** Parse adws/config/monitor.json; null when the file is absent. */
export function readMonitorConfig(root: string): MonitorConfig | null {
  const path = join(root, MONITOR_CONFIG_FILE);
  if (!existsSync(path)) return null;
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
  const gitlabUrl = String(data.gitlab_url ?? "https://gitlab.com").replace(/\/+$/, "");
  const tokenEnv = String(data.token_env ?? "GITLAB_TOKEN");
  return { gitlabUrl, tokenEnv };
}

/** Simple KEY=VALUE .env read — mirrors the notify adapter's dotenv load. */
function readDotEnv(root: string): Record<string, string> {
  const path = join(root, ".env");
  if (!existsSync(path)) return {};
  const out: Record<string, string> = {};
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m) out[m[1]!] = m[2]!.trim();
  }
  return out;
}

export function defaultToken(envName: string): string {
  return process.env[envName] ?? "";
}

export function ensureMonitorSchema(db: Database): void {
  db.run(MONITOR_STATE_DDL);
}

/** The default notifier: the notify adapter CLI, exactly as a human would. */
export async function defaultNotify(ticketId: string, text: string, mrUrl: string): Promise<void> {
  const args = ["notify", ticketId, text];
  if (mrUrl) args.push("--mr", mrUrl);
  const proc = Bun.spawn(["sssf", ...args], { stdout: "pipe", stderr: "pipe" });
  const out = await new Response(proc.stdout).text();
  const err = await new Response(proc.stderr).text();
  await proc.exited;
  if (proc.exitCode !== 0) {
    throw new Error((out + err).trim() || `sssf notify exited ${proc.exitCode}`);
  }
}

function decideAlert(prev: { pipeline: string; state: string } | null, mr: MrStateResult):
  "merged" | "success" | "failed" | null {
  // A changed MR (new repo/iid) starts fresh — the previous status belonged
  // to the old MR and must not suppress this one's alerts.
  const current = prev?.pipeline === mr.pipeline && prev.state === mr.state;
  if (current) return null;
  if (mr.state === "merged" && prev?.state !== "merged") return "merged";
  if (mr.pipeline === "success" && prev?.pipeline !== "success") return "success";
  if (mr.pipeline === "failed" && prev?.pipeline !== "failed") return "failed";
  return null;
}

/**
 * One sweep of one project's db. Returns what happened — how many
 * ready-to-deploy-with-MR tickets were watched, how many alerts fired, and a
 * per-project error string when the monitor is not configured for it.
 */
export async function sweepMonitor(
  dbPath: string,
  root: string,
  deps: SweepDeps = {},
): Promise<SweepResult> {
  const result: SweepResult = { project: root, db: dbPath, watched: 0, notified: 0 };
  const config = readMonitorConfig(root);
  if (!config) {
    result.error = "monitor not configured (adws/config/monitor.json)";
    return result;
  }
  const fetchMrState = deps.fetchMrState ?? ((opts) => defaultFetchMrState(opts));
  const token = deps.token ?? defaultToken;
  const notify = deps.notify ?? defaultNotify;
  const now = deps.now ?? (() => new Date().toISOString());

  const rawToken = token(config.tokenEnv) || readDotEnv(root)[config.tokenEnv] || "";
  if (!rawToken) {
    result.error = `${config.tokenEnv} is not set`;
    return result;
  }

  if (!existsSync(dbPath)) return result;
  const db = new Database(dbPath);
  try {
    ensureMonitorSchema(db);
    const rows = db.query(SCAN_SQL).all() as Array<{
      id: string; title: string; repo: string; iid: string; url: string;
    }>;
    for (const row of rows) {
      result.watched++;
      const prev = db
        .query("SELECT repo, iid, pipeline, state FROM monitor_state WHERE ticket_id=?")
        .get(row.id) as { repo: string; iid: string; pipeline: string; state: string } | null;
      let mr: MrStateResult | null;
      try {
        mr = await fetchMrState({
          gitlabUrl: config.gitlabUrl,
          token: rawToken,
          repo: row.repo,
          iid: row.iid,
        });
      } catch (error) {
        result.error = `gitlab ${row.repo}!${row.iid}: ${(error as Error).message}`;
        continue;
      }
      if (!mr) continue; // 404 — MR gone; nothing to record
      const effectivePrev = prev && prev.repo === row.repo && prev.iid === row.iid ? prev : null;
      const alert = decideAlert(effectivePrev, mr);
      if (alert) {
        const text =
          alert === "merged"
            ? `MR merged (ticket ${row.id}: ${row.title})`
            : alert === "success"
              ? `pipeline green (ticket ${row.id}: ${row.title})`
              : `pipeline failed (ticket ${row.id}: ${row.title})`;
        try {
          await notify(row.id, text, row.url);
          result.notified++;
        } catch (error) {
          result.error = `notify ${row.id}: ${(error as Error).message}`;
        }
      }
      db.query(
        "INSERT INTO monitor_state (ticket_id, repo, iid, pipeline, state, updated_at)"
        + " VALUES (?,?,?,?,?,?)"
        + " ON CONFLICT(ticket_id) DO UPDATE SET"
        + " repo=excluded.repo, iid=excluded.iid, pipeline=excluded.pipeline,"
        + " state=excluded.state, updated_at=excluded.updated_at",
      ).run(row.id, row.repo, row.iid, mr.pipeline, mr.state, now());
    }
  } finally {
    db.close();
  }
  return result;
}

/** Sweep every registered project's db, plus the adhoc db when one is served. */
export async function sweepMonitorAll(
  registry: ProjectRegistry,
  adhocDbPath: string | null,
  deps: SweepDeps = {},
): Promise<SweepResult[]> {
  const targets = registry.list().map((p) => ({ project: p.name, db: p.db, root: p.root }));
  if (adhocDbPath) targets.push({ project: "adhoc", db: adhocDbPath, root: "." });
  const out: SweepResult[] = [];
  for (const t of targets) {
    const res = await sweepMonitor(t.db, t.root, deps);
    out.push({ ...res, project: t.project });
  }
  return out;
}

export interface MonitorStatus {
  configured: boolean;
  gitlabUrl: string | null;
  tokenEnv: string | null;
  states: Array<{ ticket_id: string; repo: string; iid: string; pipeline: string; state: string; updated_at: string }>;
}

/** Read-only view of a project's monitor: config + last-seen states. */
export function monitorStatus(dbPath: string, root: string): MonitorStatus {
  const config = readMonitorConfig(root);
  const base: MonitorStatus = {
    configured: config !== null,
    gitlabUrl: config?.gitlabUrl ?? null,
    tokenEnv: config?.tokenEnv ?? null,
    states: [],
  };
  if (!existsSync(dbPath)) return base;
  const db = new Database(dbPath, { readonly: true });
  try {
    db.run(MONITOR_STATE_DDL); // ensure the table exists even for a fresh read
    base.states = db
      .query("SELECT ticket_id, repo, iid, pipeline, state, updated_at FROM monitor_state")
      .all() as MonitorStatus["states"];
  } catch {
    /* schema not present yet — no states to show */
  } finally {
    db.close();
  }
  return base;
}
