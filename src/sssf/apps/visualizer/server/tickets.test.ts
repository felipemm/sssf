import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { readTickets } from "./tickets";

function makeDb(path: string): Database {
  const db = new Database(path);
  db.run(`CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
    title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
    prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`);
  db.run(`CREATE TABLE IF NOT EXISTS sessions (
    adw_id TEXT PRIMARY KEY, status TEXT, started_at TEXT, ended_at TEXT)`);
  db.run(`CREATE TABLE IF NOT EXISTS ticket_runs (
    ticket_id TEXT, adw_id TEXT, created_at TEXT,
    PRIMARY KEY(ticket_id, adw_id))`);
  return db;
}

describe("readTickets", () => {
  test("reconciles status from the linked session", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:a", "internal", "", "running ticket", "running", "sess1");
    db.query("INSERT INTO sessions (adw_id, status) VALUES (?,?)").run("sess1", "success");
    db.close();

    const tickets = readTickets(dbPath);
    expect(tickets).toHaveLength(1);
    expect(tickets[0]!.status).toBe("done");          // session success => done
    expect(tickets[0]!.adw_id).toBe("sess1");
  });

  test("backlog tickets stay backlog", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status) VALUES (?,?,?,?,?)")
      .run("internal:b", "internal", "", "unrun", "backlog");
    db.close();
    expect(readTickets(dbPath)[0]!.status).toBe("backlog");
  });

  test("backlog wins over a failed session — the retry state", () => {
    // A ticket moved back to backlog keeps its adw_id (history) but must NOT
    // re-derive 'failed' from the old run.
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:c", "internal", "", "retry me", "backlog", "sess_fail");
    db.query("INSERT INTO sessions (adw_id, status) VALUES (?,?)").run("sess_fail", "fail");
    db.close();
    const t = readTickets(dbPath)[0]!;
    expect(t.status).toBe("backlog");
    expect(t.adw_id).toBe("sess_fail");
  });

  test("runs synthesize from adw_id when no history exists", () => {
    // Pre-feature tickets have adw_id but no ticket_runs rows.
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:d", "internal", "", "old", "starting", "sess_old");
    db.query("INSERT INTO sessions (adw_id, status, started_at, ended_at) VALUES (?,?,?,?)")
      .run("sess_old", "fail", "2026-08-16T00:00:00+00:00", "2026-08-16T00:01:00+00:00");
    db.close();
    const t = readTickets(dbPath)[0]!;
    expect(t.runs).toHaveLength(1);
    expect(t.runs[0]).toMatchObject({ adw_id: "sess_old", status: "fail" });
  });

  test("runs come from ticket_runs across retries", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:e", "internal", "", "retried", "starting", "sess2");
    db.query("INSERT INTO sessions (adw_id, status, started_at, ended_at) VALUES (?,?,?,?)").run("sess1", "fail", "2026-08-15T00:00:00+00:00", "2026-08-15T00:01:00+00:00");
    db.query("INSERT INTO sessions (adw_id, status, started_at, ended_at) VALUES (?,?,?,?)").run("sess2", "success", "2026-08-16T00:00:00+00:00", "2026-08-16T00:01:00+00:00");
    db.query("INSERT INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)").run("internal:e", "sess1", "2026-08-15T00:00:00+00:00");
    db.query("INSERT INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)").run("internal:e", "sess2", "2026-08-16T00:00:00+00:00");
    db.close();
    const t = readTickets(dbPath)[0]!;
    expect(t.runs).toHaveLength(2);
    expect(t.runs.map((r) => r.adw_id)).toEqual(["sess1", "sess2"]);   // chronological
    expect(t.runs[1]!.status).toBe("success");
    expect(t.status).toBe("done");          // latest run drives the ticket status
  });

  test("starting ticket with failed session derives failed (regression)", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:f", "internal", "", "will fail", "starting", "sess_f");
    db.query("INSERT INTO sessions (adw_id, status) VALUES (?,?)").run("sess_f", "fail");
    db.close();
    expect(readTickets(dbPath)[0]!.status).toBe("failed");
  });
});
