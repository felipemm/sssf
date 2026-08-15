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
    adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)`);
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
});
