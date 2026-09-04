import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { computeStatus } from "./status.ts";
import { ticketingEnabled } from "./ticketing.ts";

function fakeProject() {
  const root = mkdtempSync(join(tmpdir(), "status-"));
  const dbDir = join(root, "adws", "data");
  mkdirSync(dbDir, { recursive: true });
  const path = join(dbDir, "sssf.db");
  const db = new Database(path);
  db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, started_at TEXT,
          ended_at TEXT, total_cost REAL, total_tokens INTEGER, archived INTEGER DEFAULT 0)`);
  // real tracer schemas — the quality/agents sections read more than status
  db.run(`CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, seq INTEGER,
          name TEXT, kind TEXT, owner TEXT, description TEXT,
          status TEXT DEFAULT 'fail', attempt INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
          error TEXT, started_at TEXT, ended_at TEXT)`);
  db.run(`CREATE TABLE events (event_id TEXT PRIMARY KEY, adw_id TEXT, phase_id TEXT,
          parent_id TEXT, type TEXT, name TEXT, payload_json TEXT, tokens INTEGER,
          started_at TEXT, ended_at TEXT)`);
  db.run(`CREATE TABLE tickets (id TEXT PRIMARY KEY, provider TEXT, external_id TEXT,
          title TEXT, description TEXT, status TEXT, prompt_file TEXT, adw_id TEXT,
          source_url TEXT, created_at TEXT, updated_at TEXT)`);
  db.run(`CREATE TABLE agent_sessions (agent TEXT, adw_id TEXT, model TEXT,
          context_tokens INTEGER, last_used_at TEXT)`);
  return { root, db, path };
}

describe("computeStatus trends", () => {
  test("trend buckets include archived sessions (archive is review triage, not erasure)", () => {
    const { root, db, path } = fakeProject();
    const today = new Date().toISOString().slice(0, 10);
    db.run(`INSERT INTO sessions VALUES ('a1','success',?,?,1.0,10,1)`, [`${today}T10:00:00`, `${today}T10:30:00`]);
    db.run(`INSERT INTO sessions VALUES ('a2','fail',?,?,0.5,5,1)`, [`${today}T11:00:00`, `${today}T11:10:00`]);
    db.close();
    const status = computeStatus(path, root, "fixture", 90);
    expect(status.trends.window).toBe(90);
    const runs = status.trends.buckets.reduce((n, b) => n + b.runs, 0);
    expect(runs).toBe(2); // both archived sessions still appear
    expect(status.trends.buckets[0]!.success).toBe(1);
    expect(status.trends.buckets[0]!.fail).toBe(1);
    expect(status.totals.archived).toBe(2);
    rmSync(root, { recursive: true, force: true });
  });
});


describe("ticketingEnabled (shared with tickets.ts)", () => {
  test("v2 project with providers is enabled", () => {
    const root = mkdtempSync(join(tmpdir(), "st-en-"));
    mkdirSync(join(root, "adws", "config"), { recursive: true });
    writeFileSync(join(root, "adws", "config", "ticketing.yaml"),
                  "providers:\n  - internal\n");
    expect(ticketingEnabled(root)).toBe(true);
  });

  test("no config → disabled", () => {
    const root = mkdtempSync(join(tmpdir(), "st-no-"));
    expect(ticketingEnabled(root)).toBe(false);
  });
});


describe("computeStatus avg duration", () => {
  test("average is per-run ACTIVE time (phase sums), not the row span", () => {
    // One successful session whose row spans 12h (two attempts: 09:00 + 21:00)
    // but whose phases sum to 10 minutes of actual work.
    const { root, db, path } = fakeProject();
    const today = new Date().toISOString().slice(0, 10);
    const h = (hh: number, mm: number, ss = 0) => `${today}T${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
    db.run(`INSERT INTO sessions VALUES ('r1','success',?,?,1.0,10,0)`, [h(9, 0), h(21, 5)]);
    // attempt 1: 09:00→09:05 (5 min across two phases); attempt 2: 21:00→21:05
    db.run(`INSERT INTO phases VALUES ('r1_01','r1',1,'request','engineer','felipe','d','success',0,0,NULL,?,?)`, [h(9, 0), h(9, 1)]);
    db.run(`INSERT INTO phases VALUES ('r1_02','r1',2,'build','agent','builder','d','success',0,0,NULL,?,?)`, [h(9, 1), h(9, 5)]);
    db.run(`INSERT INTO phases VALUES ('r1_03','r1',3,'request','engineer','felipe','d','success',0,0,NULL,?,?)`, [h(21, 0), h(21, 1)]);
    db.run(`INSERT INTO phases VALUES ('r1_04','r1',4,'build','agent','builder','d','success',0,0,NULL,?,?)`, [h(21, 1), h(21, 5)]);
    db.close();
    const status = computeStatus(path, root, "fixture", 90);
    expect(status.totals.avg_duration_s!).toBeCloseTo(600, 0); // ~10 minutes of work, not 12h
    rmSync(root, { recursive: true, force: true });
  });
});
