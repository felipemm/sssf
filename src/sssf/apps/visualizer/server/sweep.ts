/**
 * Automatic archival sweep — review triage on a timer.
 *
 * The same policy as `sssf sweep` (Python CLI) and `setArchived`'s one-write
 * discipline: finished sessions older than the interval are marked archived.
 * Runs over every registered project's db (plus the adhoc db when serving
 * one), each on its own short-lived writable connection.
 */
import { Database } from "bun:sqlite";
import { existsSync } from "node:fs";
import type { ProjectRegistry } from "./registry";

export interface SweepResult {
  project: string;
  db: string;
  archived: number;
  error?: string;
}

const SWEEP_SQL = `UPDATE sessions SET archived = 1
  WHERE archived = 0 AND status IN ('success','fail')
    AND ended_at IS NOT NULL
    AND datetime(ended_at) < datetime('now', ?)`;

/** Sweep one db file; returns how many sessions were archived. */
export function sweepDb(dbPath: string, interval = "-30 days"): number {
  if (!existsSync(dbPath)) return 0;
  const db = new Database(dbPath);
  try {
    const res = db.query(SWEEP_SQL).run(interval);
    return Number(res.changes ?? 0);
  } finally {
    db.close();
  }
}

/** Sweep every registered project's db, plus the adhoc db when one is served. */
export function sweepAll(
  registry: ProjectRegistry,
  adhocDbPath: string | null,
  interval = "-30 days",
): SweepResult[] {
  const targets = registry.list().map((p) => ({ project: p.name, db: p.db }));
  if (adhocDbPath) targets.push({ project: "adhoc", db: adhocDbPath });
  return targets.map((t) => {
    try {
      return { project: t.project, db: t.db, archived: sweepDb(t.db, interval) };
    } catch (error) {
      return {
        project: t.project,
        db: t.db,
        archived: 0,
        error: (error as Error).message,
      };
    }
  });
}
