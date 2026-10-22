import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { MIN_SCHEMA_VERSION, SssfDb } from "./db.ts";

function fakeDb() {
  const root = mkdtempSync(join(tmpdir(), "db-"));
  mkdirSync(join(root, "adws", "data"), { recursive: true });
  const path = join(root, "adws", "data", "sssf.db");
  const db = new Database(path);
  db.exec("PRAGMA journal_mode = WAL");
  db.run("PRAGMA user_version = 7");  // a current contract db
  db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT,
          status TEXT, engineer TEXT, started_at TEXT, ended_at TEXT,
          total_tokens INTEGER, total_cost REAL, archived INTEGER DEFAULT 0)`);
  db.run(`CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, seq INTEGER,
          name TEXT, kind TEXT, owner TEXT, description TEXT,
          status TEXT DEFAULT 'fail', attempt INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
          error TEXT, started_at TEXT, ended_at TEXT)`);
  db.run(`CREATE TABLE tickets (id TEXT PRIMARY KEY, status TEXT, adw_id TEXT)`);
  db.run(`INSERT INTO sessions VALUES ('a1','adw_simple_sdlc','req1','success','eng','2026-08-16T10:00:00','2026-08-16T10:30:00',10,1.0,0)`);
  db.run(`INSERT INTO sessions VALUES ('a2','adw_simple_sdlc','req2','running','eng','2026-08-16T11:00:00',NULL,5,0.5,0)`);
  db.run(`INSERT INTO sessions VALUES ('a3','adw_simple_sdlc','req3','fail','eng','2026-08-16T12:00:00','2026-08-16T12:30:00',5,0.5,0)`);
  db.run(`INSERT INTO sessions VALUES ('a4','adw_simple_sdlc','req4','success','eng','2026-08-16T13:00:00','2026-08-16T13:30:00',5,0.5,1)`);
  db.run(`INSERT INTO sessions VALUES ('a5','adw_simple_sdlc','req5','fail','eng','2026-08-16T14:00:00','2026-08-16T14:30:00',5,0.5,1)`);
  db.run(`INSERT INTO tickets VALUES ('internal:t1','done','a1')`);
  db.close();
  return { root, path };
}

describe("SssfDb.sessions", () => {
  test("filters all, done, blocked, and archived runs by session outcome", () => {
    const { root, path } = fakeDb();
    const sdb = new SssfDb(path);
    const ids = (filter: "all" | "done" | "blocked" | "archived") =>
      sdb.sessions(200, filter).map((session) => session.adw_id);

    expect(ids("all")).toEqual(["a3", "a2", "a1"]);
    expect(ids("done")).toEqual(["a1"]);
    expect(ids("blocked")).toEqual(["a3"]);
    expect(ids("archived")).toEqual(["a5", "a4"]);
    expect(sdb.session("a4")?.archived).toBe(1);
    sdb.close();
    rmSync(root, { recursive: true, force: true });
  });

  test("carries the originating ticket id per session", () => {
    const { root, path } = fakeDb();
    const sdb = new SssfDb(path);
    const sessions = sdb.sessions();
    const a1 = sessions.find((s) => s.adw_id === "a1")!;
    const a2 = sessions.find((s) => s.adw_id === "a2")!;
    expect(a1.ticket_id).toBe("internal:t1");
    expect(a2.ticket_id).toBeNull();
    rmSync(root, { recursive: true, force: true });
  });
});

describe("schema-version contract", () => {
  test("reader MIN matches the writer SCHEMA_VERSION (db_schema.py)", () => {
    expect(MIN_SCHEMA_VERSION).toBe(7);
  });

  test("reads migration-added columns on a current db", () => {
    const { root, path } = fakeDb();
    const sdb = new SssfDb(path);
    const a1 = sdb.sessions().find((s) => s.adw_id === "a1")!;
    expect(a1.adw_name).toBe("adw_simple_sdlc");
    expect(a1.archived).toBe(0);
    rmSync(root, { recursive: true, force: true });
  });

  test("degrades a pre-contract db: migration columns read as NULL, never throw", () => {
    const root = mkdtempSync(join(tmpdir(), "db-"));
    mkdirSync(join(root, "adws", "data"), { recursive: true });
    const path = join(root, "adws", "data", "sssf.db");
    const db = new Database(path);
    // legacy shape: no adw_name, no archived, no user_version stamp
    db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, request TEXT, status TEXT,
            engineer TEXT, started_at TEXT, ended_at TEXT, total_tokens INTEGER, total_cost REAL)`);
    db.run(`CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, seq INTEGER,
            name TEXT, kind TEXT, owner TEXT, description TEXT, status TEXT DEFAULT 'fail',
            attempt INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, error TEXT,
            started_at TEXT, ended_at TEXT)`);
    db.run(`CREATE TABLE tickets (id TEXT PRIMARY KEY, status TEXT, adw_id TEXT)`);
    db.run("INSERT INTO sessions VALUES ('old1','req','success','eng','2026-08-16T10:00:00',NULL,1,0.1)");
    db.close();

    const sdb = new SssfDb(path);
    const row = sdb.sessions().find((s) => s.adw_id === "old1")!;
    expect(row.adw_name).toBeNull();
    expect(row.archived).toBeNull();  // degraded, not thrown
    rmSync(root, { recursive: true, force: true });
  });
});
