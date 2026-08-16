import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdirSync, mkdtempSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { computeStatus } from "./status";

function fixtureDb(path: string): string[] {
  const db = new Database(path);
  db.run(`CREATE TABLE sessions (
    adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT, status TEXT,
    engineer TEXT, started_at TEXT, ended_at TEXT,
    total_tokens INTEGER, total_cost REAL, archived INTEGER)`);
  db.run(`CREATE TABLE phases (
    phase_id TEXT PRIMARY KEY, adw_id TEXT, seq INTEGER, name TEXT,
    kind TEXT, owner TEXT, description TEXT, status TEXT,
    attempt INTEGER, retries INTEGER, error TEXT, started_at TEXT, ended_at TEXT)`);
  db.run(`CREATE TABLE agent_sessions (
    adw_id TEXT, agent TEXT, coding_agent TEXT, model TEXT, color TEXT,
    session_id TEXT, context_tokens INTEGER, context_window INTEGER,
    created_at TEXT, last_used_at TEXT, PRIMARY KEY (adw_id, agent))`);
  db.run(`CREATE TABLE gate_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, adw_id TEXT, phase_id TEXT,
    attempt INTEGER, gate TEXT, passed INTEGER, violations_json TEXT,
    checks_json TEXT, created_at TEXT)`);
  db.run(`CREATE TABLE tickets (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
    title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
    prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`);

  // Dates are RELATIVE to now so the tests stay green regardless of when they run.
  const now = Date.now();
  const iso = (daysAgo: number) => new Date(now - daysAgo * 86400_000).toISOString();
  const day = (daysAgo: number) => new Date(now - daysAgo * 86400_000).toISOString().slice(0, 10);
  const end = (start: string, seconds: number) => new Date(Date.parse(start) + seconds * 1000).toISOString();
  const s1 = iso(6), s2 = iso(4), s3 = iso(2), s4 = iso(0), s5 = iso(5);

  const s = db.prepare(`INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)`);
  // s1, s2, s5 success · s3 fail · s4 running (no ended_at)
  s.run("s1", "adw_simple_sdlc", "r1", "success", "eng", s1, end(s1, 300), 100000, 0.50, 0);
  s.run("s2", "adw_simple_sdlc", "r2", "success", "eng", s2, end(s2, 120), 50000, 0.20, 0);
  s.run("s3", "adw_simple_sdlc", "r3", "fail", "eng", s3, end(s3, 60), 10000, 0.05, 0);
  s.run("s4", "adw_simple_sdlc", "r4", "running", "eng", s4, null, 0, 0, 0);
  s.run("s5", "adw_simple_sdlc", "r5", "success", "eng", s5, end(s5, 60), 0, 0, 1); // Archived session

  const p = db.prepare(`INSERT INTO phases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  p.run("s1_01", "s1", 1, "request", "engineer", "eng", "", "success", 0, 0, null, s1, end(s1, 1));
  p.run("s1_02", "s1", 2, "commit_build", "code", "git", "", "fail", 0, 1, "boom", s1, end(s1, 1));
  p.run("s1_03", "s1", 3, "commit_build", "code", "git", "", "success", 1, 0, null, s1, end(s1, 1));
  p.run("s2_01", "s2", 1, "review_1", "agent", "reviewer", "", "fail", 0, 2, "nope", s2, end(s2, 30));
  p.run("s3_01", "s3", 1, "plan", "agent", "planner", "", "success", 0, 0, null, s3, end(s3, 30));

  const g = db.prepare(`INSERT INTO gate_results (adw_id, phase_id, attempt, gate, passed, checks_json, created_at) VALUES (?,?,?,?,?,?,?)`);
  g.run("s2", "s2_02", 0, "quality", 1, '[{"item":"a","ok":true},{"item":"b","ok":true}]', s2);
  g.run("s3", "s3_02", 0, "quality", 0, '[{"item":"a","ok":true},{"item":"b","ok":false}]', s3);

  const a = db.prepare(`INSERT INTO agent_sessions (adw_id, agent, model, context_tokens, created_at, last_used_at) VALUES (?,?,?,?,?,?)`);
  a.run("s1", "planner", "litellm/deepseek-v4-flash-official", 60000, s1, s1);
  a.run("s1", "builder", "litellm/gpt-5.5", 40000, s1, s1);
  a.run("s2", "reviewer", "litellm/gemini-2.5-flash", 30000, s2, s2);
  a.run("s3", "documenter", "litellm/gemini-2.5-flash", 20000, s3, s3);

  db.run(`INSERT INTO tickets (id, provider, external_id, title, status) VALUES ('internal:b','internal','','backlog ticket','backlog')`);
  db.run(`INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES ('internal:r','internal','','running ticket','running','s4')`);
  db.close();
  return [day(6), day(4), day(2), day(0)];   // ascending day strings for the trend assertions
}

function setup(): { dbPath: string; root: string; days: string[] } {
  const dir = mkdtempSync(join(tmpdir(), "sssf-status-"));
  const dbPath = join(dir, "sssf.db");
  const days = fixtureDb(dbPath);
  return { dbPath, root: dir, days };
}

describe("computeStatus", () => {
  test("totals, quality, agents, tickets, trends over a known dataset", () => {
    const { dbPath, root, days } = setup();
    const status = computeStatus(dbPath, root, "fixture", 30);

    // totals: all-time, includes the running session
    expect(status.totals.runs).toBe(5);
    expect(status.totals.active).toBe(1);
    expect(status.totals.success).toBe(3);
    expect(status.totals.failed).toBe(1);
    expect(status.totals.archived).toBe(1);
    expect(status.totals.success_rate).toBeCloseTo(3 / 4, 5);   // 3 of 4 finished
    // julianday() arithmetic on current-era dates carries ~1e-5 s of double noise,
    // so assert within 0.5 ms rather than expecting exactly 210.
    expect(status.totals.avg_duration_s).toBeCloseTo(160, 3);   // (300+120+60)/3, successful only
    expect(status.totals.total_cost).toBeCloseTo(0.75, 5);
    expect(status.totals.avg_cost_per_run).toBeCloseTo(0.15, 5); // 0.75/5
    expect(status.totals.total_tokens).toBe(160000);
    expect(status.totals.avg_tokens_per_run).toBe(32000);
    // last_run is the full ISO timestamp of the most recent start (s4, running today);
    // Task 5 displays its date part — assert that part is today.
    expect(status.project.last_run?.slice(0, 10)).toBe(new Date(Date.now()).toISOString().slice(0, 10)); // today

    // quality
    expect(status.quality.gate_pass_rate).toBeCloseTo(0.75, 5);  // 3 of 4 checks ok
    expect(status.quality.hotspot_phase).toBe("commit_build");
    expect(status.quality.hotspot_count).toBe(1);   // one row (a phase row per fail)
    expect(status.quality.total_retries).toBe(3);   // 1 + 2
    expect(status.quality.failed_phases).toBe(2);   // commit_build + review_1 rows

    // agents: one row per role, most recent model
    const planner = status.agents.find((a) => a.role === "planner")!;
    expect(planner.model).toBe("litellm/deepseek-v4-flash-official");
    expect(planner.sessions).toBe(1);
    expect(planner.context_tokens).toBe(60000);
    expect(status.agents).toHaveLength(4);

    // tickets: enabled? root has no ticketing.yaml -> null
    expect(status.tickets).toBeNull();

    // trends: non-empty days within the window, ascending
    expect(status.trends.window).toBe(30);
    expect(status.trends.buckets.map((b) => b.day)).toEqual(days);
    const d3 = status.trends.buckets.find((b) => b.day === days[2])!;
    expect(d3.runs).toBe(1);
    expect(d3.fail).toBe(1);
  });

  test("ticketing enabled when ticketing.yaml has providers", () => {
    const { dbPath, root } = setup();
    const cfgDir = join(root, "adws", "adw_sssf_config");
    mkdirSync(cfgDir, { recursive: true });
    writeFileSync(join(cfgDir, "ticketing.yaml"), "providers:\n  - internal\n");
    const status = computeStatus(dbPath, root, "fixture", 30);
    expect(status.project.ticketing_enabled).toBe(true);
    expect(status.tickets).toEqual({ backlog: 1, running: 1, done: 0, failed: 0 });
  });

  test("missing sessions table yields a zeroed payload, not an error", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-status-"));
    const dbPath = join(dir, "empty.db");
    new Database(dbPath).close();
    const status = computeStatus(dbPath, dir, "empty", 30);
    expect(status.totals.runs).toBe(0);
    expect(status.totals.success_rate).toBe(0);
    expect(status.quality.hotspot_phase).toBeNull();
    expect(status.trends.buckets).toEqual([]);
  });

  test("window bounds the trend buckets", () => {
    const { dbPath, root, days } = setup();
    // window=1 -> cutoff is yesterday, so only today's session (s4) is inside.
    const status = computeStatus(dbPath, root, "fixture", 1);
    expect(status.trends.window).toBe(1);
    expect(status.trends.buckets.map((b) => b.day)).toEqual([days[3]]);
  });
});
