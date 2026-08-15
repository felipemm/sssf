/** Ticketing: enabled check + backlog reads over a project's trace db. */
import { Database } from "bun:sqlite";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

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
}

const TICKETS_DDL = `CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
  title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
  prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`;

/** The feature is on when ticketing.yaml exists with an uncommented providers: line. */
export function isEnabled(root: string): boolean {
  const path = resolve(root, "adws", "adw_sssf_config", "ticketing.yaml");
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
    const rows = db.query<any, []>(
      "SELECT id, provider, external_id, title, description, status, prompt_file, adw_id, source_url"
      + " FROM tickets ORDER BY created_at DESC, rowid DESC",
    ).all();
    return rows.map((row) => {
      let status = row.status as string;
      if (row.adw_id) {
        try {
          const s = db.query<{ status: string }, [string]>(
            "SELECT status FROM sessions WHERE adw_id = ?",
          ).get(row.adw_id);
          if (s) status = s.status === "success" ? "done" : s.status === "fail" ? "failed" : "running";
        } catch {
          // sessions table may not exist yet (no runs) — keep the ticket status
        }
      }
      return { ...row, status, source_url: row.source_url ?? "" };
    });
  } finally {
    db.close();
  }
}
