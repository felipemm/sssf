/** Ticketing: enabled check + backlog reads over a project's trace db. */
import { Database } from "bun:sqlite";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface TicketRun {
  adw_id: string
  status: string | null        // session status: running | success | fail | null
  started_at: string | null
  ended_at: string | null
}

export interface Ticket {
  id: string;
  provider: string;
  external_id: string | null;
  title: string;
  description: string;
  status: string;
  prompt_file: string | null;
  adw_id: string | null;
  source_url: string;
  runs: TicketRun[];           // every run of this ticket, oldest first
}

const TICKETS_DDL = `CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
  title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
  prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`;

const TICKET_RUNS_DDL = `CREATE TABLE IF NOT EXISTS ticket_runs (
  ticket_id TEXT NOT NULL, adw_id TEXT NOT NULL, created_at TEXT,
  PRIMARY KEY (ticket_id, adw_id))`;

/** The feature is on when ticketing.yaml exists with an uncommented providers: line. */
export function isEnabled(root: string): boolean {
  const path = resolve(root, "adws", "config", "ticketing.yaml");
  if (!existsSync(path)) return false;
  try {
    return readFileSync(path, "utf8")
      .split("\n")
      .some((line) => /^\s*providers\s*:/.test(line));
  } catch {
    return false;
  }
}

export function readTickets(dbPath: string): Ticket[] {
  const db = new Database(dbPath);
  try {
    db.run(TICKETS_DDL);
    db.run(TICKET_RUNS_DDL);
    const rows = db.query<any, []>(
      "SELECT id, provider, external_id, title, description, status, prompt_file, adw_id, source_url"
      + " FROM tickets ORDER BY created_at DESC, rowid DESC",
    ).all();
    return rows.map((row) => {
      let status = row.status as string;
      // A ticket moved back to the backlog rests there even though its past
      // run failed — backlog is the explicit retry state, so it wins over
      // session derivation. Every other stored status derives from the
      // CURRENT (latest) run's session.
      if (row.status !== "backlog" && row.adw_id) {
        try {
          const s = db.query<{ status: string }, [string]>(
            "SELECT status FROM sessions WHERE adw_id = ?",
          ).get(row.adw_id);
          if (s) status = s.status === "success" ? "done" : s.status === "fail" ? "failed" : "running";
        } catch {
          // sessions table may not exist yet (no runs) — keep the ticket status
        }
      }
      const runs = runHistory(db, row.id, row.adw_id);
      return { ...row, status, runs, source_url: row.source_url ?? "" };
    });
  } finally {
    db.close();
  }
}

function runHistory(db: Database, ticketId: string, currentAdwId: string | null): TicketRun[] {
  // ticket_runs is the authoritative history; pre-feature tickets carry only
  // the adw_id column, so synthesize their single run.
  let rows: { adw_id: string; started_at: string | null; ended_at: string | null }[] = [];
  try {
    rows = db.query<{ adw_id: string; started_at: string | null; ended_at: string | null }, [string]>(
      "SELECT r.adw_id, s.started_at, s.ended_at"
      + " FROM ticket_runs r LEFT JOIN sessions s ON s.adw_id = r.adw_id"
      + " WHERE r.ticket_id = ? ORDER BY r.created_at",
    ).all(ticketId);
  } catch {
    // ticket_runs table missing — fall through to synthesis
  }
  if (rows.length === 0 && currentAdwId) {
    rows = db.query<{ adw_id: string; started_at: string | null; ended_at: string | null }, [string]>(
      "SELECT adw_id, started_at, ended_at FROM sessions WHERE adw_id = ?",
    ).all(currentAdwId);
    if (rows.length === 0) rows = [{ adw_id: currentAdwId, started_at: null, ended_at: null }];
  }
  return rows.map((r) => ({
    adw_id: r.adw_id,
    status: sessionStatus(db, r.adw_id),
    started_at: r.started_at,
    ended_at: r.ended_at,
  }));
}

function sessionStatus(db: Database, adwId: string): string | null {
  try {
    return db.query<{ status: string | null }, [string]>(
      "SELECT status FROM sessions WHERE adw_id = ?",
    ).get(adwId)?.status ?? null;
  } catch {
    return null;
  }
}
