import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { computeStatus } from "./status.ts";

function fakeProject() {
  const root = mkdtempSync(join(tmpdir(), "status-"));
  const dbDir = join(root, "adws", "adw_data");
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
