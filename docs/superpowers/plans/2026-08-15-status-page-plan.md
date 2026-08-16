# Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-project "status" dashboard to the sssf visualizer — main information + KPIs (runs/health, cost/tokens, quality, agents, trends, tickets).

**Architecture:** One aggregate endpoint (`GET /api/projects/:project/status?window=30d`) computed server-side in `server/status.ts` (same pattern as `server/tickets.ts`); one client page (`StatusPage.vue`) with hand-rolled SVG charts (`StatusCharts.vue`), no new dependencies. Totals are all-time; only trends are window-scoped (7/30/90). On-load fetch + manual refresh (no polling).

**Tech Stack:** Bun + bun:sqlite (server), Vue 3 SFC + lucide-vue-next (client), bun:test (tests).

**Spec:** `docs/superpowers/specs/2026-08-15-status-page-design.md`

## Global Constraints

- No new DB columns or schema changes — derive everything from existing tables: `sessions`, `phases`, `agent_sessions`, `gate_results`, `tickets`, `events`.
- No new dependencies (no chart library, no YAML parser).
- No YAML/config parsing for models — models come from `agent_sessions` (most recent per role); a role with no sessions shows a dash.
- No per-agent cost split (not derivable from the schema).
- Failed runs excluded from duration averages; totals count every run including archived (`archived` reported separately; active badge + trends use non-archived only).
- Icons are lucide-vue-next SVGs (no emojis).
- Existing gates must stay green: `bun run typecheck`, `bun run build`, `bun test`, `bun run lint`.

---

### Task 1: `server/status.ts` — aggregation module

**Files:**
- Create: `src/sssf/apps/visualizer/server/status.ts`
- Test: `src/sssf/apps/visualizer/server/status.test.ts`

**Interfaces:**
- Produces: `computeStatus(dbPath: string, root: string, name: string, windowDays: number): StatusResponse` — pure function (opens its own readonly connection, like `readTickets`). Types `StatusResponse`, `Totals`, `Quality`, `AgentStat`, `TicketsCounts`, `TrendBucket` exported for the client and later tasks.

- [ ] **Step 1: Write the failing test**

Create `src/sssf/apps/visualizer/server/status.test.ts`:

```ts
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
  const s1 = iso(6), s2 = iso(4), s3 = iso(2), s4 = iso(0);

  const s = db.prepare(`INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)`);
  // s1, s2 success · s3 fail · s4 running (no ended_at)
  s.run("s1", "adw_simple_sdlc", "r1", "success", "eng", s1, end(s1, 300), 100000, 0.50, 0);
  s.run("s2", "adw_simple_sdlc", "r2", "success", "eng", s2, end(s2, 120), 50000, 0.20, 0);
  s.run("s3", "adw_simple_sdlc", "r3", "fail", "eng", s3, end(s3, 60), 10000, 0.05, 0);
  s.run("s4", "adw_simple_sdlc", "r4", "running", "eng", s4, null, 0, 0, 0);

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
    expect(status.totals.runs).toBe(4);
    expect(status.totals.active).toBe(1);
    expect(status.totals.success).toBe(2);
    expect(status.totals.failed).toBe(1);
    expect(status.totals.archived).toBe(0);
    expect(status.totals.success_rate).toBeCloseTo(2 / 3, 5);   // 2 of 3 finished
    expect(status.totals.avg_duration_s).toBeCloseTo(210, 3);   // (300+120)/2, successful only; precision 3 because julianday float noise is ~µs
    expect(status.totals.total_cost).toBeCloseTo(0.75, 5);
    expect(status.totals.avg_cost_per_run).toBeCloseTo(0.1875, 5); // 0.75/4
    expect(status.totals.total_tokens).toBe(160000);
    expect(status.totals.avg_tokens_per_run).toBe(40000);
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
```

Note: `root` in tests is the temp dir; `computeStatus` reads `{root}/adws/adw_sssf_config/ticketing.yaml` for the enabled check — the first test's root has no such file, so `tickets` is null. `existsSync` is imported for the zeroed-table test guard inside the implementation.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/sssf/apps/visualizer && bun test server/status.test.ts`
Expected: FAIL — `Cannot find module "./status"` / `computeStatus is not a function`.

- [ ] **Step 3: Write the implementation**

Create `src/sssf/apps/visualizer/server/status.ts`:

```ts
/** Status dashboard: one aggregate payload per project, computed from the trace db. */
import { Database } from "bun:sqlite";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface ProjectInfo {
  name: string;
  root: string;
  ticketing_enabled: boolean;
  last_run: string | null;   // most recent sessions.started_at (ISO); null when no runs
}

export interface Totals {
  runs: number;
  active: number;
  success: number;
  failed: number;
  archived: number;
  success_rate: number;      // success / (success + failed); 0 when none finished
  avg_duration_s: number;    // successful runs only
  total_cost: number;
  avg_cost_per_run: number;  // total_cost / runs (0 when no runs)
  total_tokens: number;
  avg_tokens_per_run: number;
}

export interface Quality {
  gate_pass_rate: number;    // ok checks / total checks in gate_results.checks_json
  hotspot_phase: string | null;
  hotspot_count: number;
  total_retries: number;
  failed_phases: number;
}

export interface AgentStat {
  role: string;
  model: string | null;      // most recent agent_sessions.model; null if never used
  sessions: number;          // distinct adw_ids (one agent_sessions row per run+agent)
  context_tokens: number;    // sum across rows
}

export interface TicketsCounts {
  backlog: number;
  running: number;
  done: number;
  failed: number;
}

export interface TrendBucket {
  day: string;               // YYYY-MM-DD (UTC, from started_at)
  runs: number;
  cost: number;
  tokens: number;
  success: number;           // finished-success sessions started that day
  fail: number;              // finished-fail sessions started that day
}

export interface StatusResponse {
  project: ProjectInfo;
  totals: Totals;
  quality: Quality;
  agents: AgentStat[];
  tickets: TicketsCounts | null;
  trends: { window: number; buckets: TrendBucket[] };
}

const AGENT_ROLES = ["planner", "builder", "reviewer", "documenter"];

/** Same enabled check as tickets.ts — ticketing.yaml with an uncommented providers line. */
function ticketingEnabled(root: string): boolean {
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

export function computeStatus(dbPath: string, root: string, name: string, windowDays: number): StatusResponse {
  const db = new Database(dbPath);
  const empty: StatusResponse = {
    project: { name, root, ticketing_enabled: ticketingEnabled(root), last_run: null },
    totals: { runs: 0, active: 0, success: 0, failed: 0, archived: 0, success_rate: 0,
              avg_duration_s: 0, total_cost: 0, avg_cost_per_run: 0,
              total_tokens: 0, avg_tokens_per_run: 0 },
    quality: { gate_pass_rate: 0, hotspot_phase: null, hotspot_count: 0,
               total_retries: 0, failed_phases: 0 },
    agents: AGENT_ROLES.map((role) => ({ role, model: null, sessions: 0, context_tokens: 0 })),
    tickets: null,
    trends: { window: windowDays, buckets: [] },
  };
  try {
    const has = (table: string): boolean =>
      (db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(table) !== null);

    // ── totals ────────────────────────────────────────────────────────────
    const t = has("sessions")
      ? db.query<{ n: number; active: number; success: number; failed: number; archived: number;
                   total_cost: number; total_tokens: number;
                   avg_duration_s: number; } & Record<string, unknown>, []>(
        `SELECT COUNT(*) n,
                SUM(status='running') active,
                SUM(status='success') success,
                SUM(status='fail') failed,
                COALESCE(SUM(archived),0) archived,
                COALESCE(SUM(total_cost),0) total_cost,
                COALESCE(SUM(total_tokens),0) total_tokens,
                AVG(CASE WHEN status='success' AND ended_at IS NOT NULL
                         THEN (julianday(ended_at)-julianday(started_at))*86400 END) avg_duration_s
           FROM sessions`).get()!
      : null;
    const totals: Totals = t
      ? { runs: t.n, active: Number(t.active ?? 0), success: Number(t.success ?? 0),
          failed: Number(t.failed ?? 0), archived: Number(t.archived ?? 0),
          success_rate: (Number(t.success ?? 0) + Number(t.failed ?? 0)) > 0
            ? Number(t.success ?? 0) / (Number(t.success ?? 0) + Number(t.failed ?? 0)) : 0,
          avg_duration_s: t.avg_duration_s ?? 0,
          total_cost: Number(t.total_cost ?? 0),
          avg_cost_per_run: t.n > 0 ? Number(t.total_cost ?? 0) / t.n : 0,
          total_tokens: Number(t.total_tokens ?? 0),
          avg_tokens_per_run: t.n > 0 ? Math.round(Number(t.total_tokens ?? 0) / t.n) : 0 }
      : empty.totals;

    // ── quality ───────────────────────────────────────────────────────────
    let quality = empty.quality;
    if (has("phases")) {
      const failed = db.query<{ name: string; count: number }, []>(
        "SELECT name, COUNT(*) count FROM phases WHERE status='fail' GROUP BY name ORDER BY count DESC, name"
      ).all();
      const retries = db.query<{ r: number }, []>(
        "SELECT COALESCE(SUM(retries),0) r FROM phases"
      ).get()!;
      quality = {
        gate_pass_rate: 0,
        hotspot_phase: failed.length ? failed[0]!.name : null,
        hotspot_count: failed.length ? failed[0]!.count : 0,
        total_retries: Number(retries.r ?? 0),
        failed_phases: failed.reduce((n, f) => n + f.count, 0),
      };
    }
    if (has("gate_results")) {
      const rows = db.query<{ checks_json: string | null }, []>(
        "SELECT checks_json FROM gate_results"
      ).all();
      let ok = 0, total = 0;
      for (const row of rows) {
        if (!row.checks_json) continue;
        try {
          const checks = JSON.parse(row.checks_json) as { ok?: boolean }[];
          if (!Array.isArray(checks)) continue;
          for (const c of checks) { total++; if (c.ok) ok++; }
        } catch { /* unparseable checks_json — skip */ }
      }
      quality.gate_pass_rate = total > 0 ? ok / total : 0;
    }

    // ── agents ────────────────────────────────────────────────────────────
    const agents: AgentStat[] = AGENT_ROLES.map((role) => ({ role, model: null, sessions: 0, context_tokens: 0 }));
    if (has("agent_sessions")) {
      // Most recent model per role: max last_used_at wins.
      const rows = db.query<{ agent: string; model: string | null; n: number; tokens: number }, []>(
        `SELECT a.agent, a.model, COUNT(*) n, COALESCE(SUM(a.context_tokens),0) tokens
           FROM agent_sessions a
           JOIN (SELECT agent, MAX(last_used_at) m FROM agent_sessions GROUP BY agent) m
             ON m.agent = a.agent AND m.m = a.last_used_at
          GROUP BY a.agent`
      ).all();
      for (const row of rows) {
        const stat = agents.find((x) => x.role === row.agent);
        if (!stat) continue;
        stat.model = row.model;
      }
      const counts = db.query<{ agent: string; n: number; tokens: number }, []>(
        `SELECT agent, COUNT(DISTINCT adw_id) n, COALESCE(SUM(context_tokens),0) tokens
           FROM agent_sessions GROUP BY agent`
      ).all();
      for (const row of counts) {
        const stat = agents.find((x) => x.role === row.agent);
        if (!stat) continue;
        stat.sessions = row.n;
        stat.context_tokens = Number(row.tokens ?? 0);
      }
    }

    // ── tickets ───────────────────────────────────────────────────────────
    let tickets: TicketsCounts | null = null;
    if (ticketingEnabled(root) && has("tickets")) {
      const rows = db.query<{ status: string; adw_id: string | null }, []>(
        "SELECT status, adw_id FROM tickets"
      ).all();
      const counts: TicketsCounts = { backlog: 0, running: 0, done: 0, failed: 0 };
      for (const row of rows) {
        let status = row.status;
        if (row.adw_id) {
          try {
            const s = db.query<{ status: string }, [string]>(
              "SELECT status FROM sessions WHERE adw_id = ?"
            ).get(row.adw_id);
            if (s) status = s.status === "success" ? "done" : s.status === "fail" ? "failed" : "running";
          } catch { /* sessions table may not exist yet */ }
        }
        if (status in counts) (counts as unknown as Record<string, number>)[status]++;
      }
      tickets = counts;
    }

    // ── trends ────────────────────────────────────────────────────────────
    const buckets: TrendBucket[] = [];
    let lastRun: string | null = null;
    if (has("sessions")) {
      const row = db.query<{ started_at: string | null }, []>(
        "SELECT MAX(started_at) started_at FROM sessions"
      ).get();
      lastRun = row?.started_at ?? null;
      const cutoff = new Date(Date.now() - windowDays * 86400_000).toISOString().slice(0, 10);
      const rows = db.query<{ day: string; n: number; cost: number; tokens: number; success: number; fail: number }, [string]>(
        `SELECT date(started_at) day, COUNT(*) n,
                COALESCE(SUM(total_cost),0) cost, COALESCE(SUM(total_tokens),0) tokens,
                SUM(status='success') success, SUM(status='fail') fail
           FROM sessions
          WHERE started_at IS NOT NULL AND date(started_at) >= ?
          GROUP BY day ORDER BY day ASC`,
      ).all(cutoff);
      for (const row of rows) {
        buckets.push({ day: row.day, runs: row.n, cost: Number(row.cost ?? 0),
                       tokens: Number(row.tokens ?? 0),
                       success: Number(row.success ?? 0), fail: Number(row.fail ?? 0) });
      }
    }

    return {
      project: { name, root, ticketing_enabled: ticketingEnabled(root), last_run: lastRun },
      totals, quality, agents, tickets,
      trends: { window: windowDays, buckets },
    };
  } catch (err) {
    // Any read problem degrades to the zeroed payload — a dashboard never 500s.
    console.error(`[sssf] status for ${name} failed:`, err);
    return empty;
  } finally {
    db.close();
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/sssf/apps/visualizer && bun test server/status.test.ts`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server/status.ts src/sssf/apps/visualizer/server/status.test.ts
git commit -m "feat: status aggregation module + tests (server/status.ts)"
```

---

### Task 2: Route registration

**Files:**
- Modify: `src/sssf/apps/visualizer/server/index.ts`

**Interfaces:**
- Consumes: `computeStatus` from `server/status.ts` (Task 1), `projectRoot(name)` and `param(req, key)`/`intQuery(req, key, fallback)` already in `index.ts`.
- Produces: `GET /api/projects/:project/status?window=7|30|90` → `json(StatusResponse)`.

- [ ] **Step 1: Add the import**

At the top of `server/index.ts`, next to the existing `import { readTickets, isEnabled } from "./tickets"`:

```ts
import { computeStatus } from "./status";
```

- [ ] **Step 2: Add the route**

In the route map, directly after the `"/api/projects/:project/tickets"` entry (around line 328):

```ts
    "/api/projects/:project/status": scoped((req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root) return notFound(`no project ${name}`);
      const db = dbForProject(name);
      if (!db) return notFound("no trace db for project");
      const windowDays = [7, 30, 90].includes(intQuery(req, "window", 30))
        ? intQuery(req, "window", 30) : 30;
      return json(computeStatus(db.path, root, name, windowDays));
    }),
```

- [ ] **Step 3: Smoke-test the endpoint against a real project**

Run (server must be running, or start one):

```bash
curl -s "localhost:4600/api/projects/inkwell/status?window=30" | python3 -m json.tool | head -30
```

Expected: a JSON payload with `project`, `totals`, `quality`, `agents`, `tickets`, `trends`; `totals.runs` matches `sqlite3 adws/adw_data/sssf.db "SELECT COUNT(*) FROM sessions"`.

- [ ] **Step 4: Run the full bun test suite**

Run: `cd src/sssf/apps/visualizer && bun test`
Expected: all existing tests + the new status tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server/index.ts
git commit -m "feat: /api/projects/:project/status route"
```

---

### Task 3: Client API — `fetchStatus` + types

**Files:**
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`

**Interfaces:**
- Consumes: `useProjects()` → `{ selectedProject }` (existing).
- Produces: `fetchStatus(windowDays: number): Promise<StatusResponse>` and the `StatusResponse` type family (re-exported from the server module shape) for Task 5/6.

- [ ] **Step 1: Write the failing test (type-level is not enough — add a real fetch test against the dev API in the browser is not feasible; instead verify via typecheck)**

Run: `cd src/sssf/apps/visualizer && bun run typecheck`
Expected: currently PASS. (The typecheck stays green after the edit — this step is the guard.)

- [ ] **Step 2: Add the types + fetch function**

Append to `src/sssf/apps/visualizer/src/lib/api.ts` (after the tickets section):

```ts
export interface StatusProject {
  name: string
  root: string
  ticketing_enabled: boolean
  last_run: string | null
}
export interface StatusTotals {
  runs: number
  active: number
  success: number
  failed: number
  archived: number
  success_rate: number
  avg_duration_s: number
  total_cost: number
  avg_cost_per_run: number
  total_tokens: number
  avg_tokens_per_run: number
}
export interface StatusQuality {
  gate_pass_rate: number
  hotspot_phase: string | null
  hotspot_count: number
  total_retries: number
  failed_phases: number
}
export interface StatusAgent {
  role: string
  model: string | null
  sessions: number
  context_tokens: number
}
export interface StatusTickets {
  backlog: number
  running: number
  done: number
  failed: number
}
export interface StatusTrendBucket {
  day: string
  runs: number
  cost: number
  tokens: number
  success: number
  fail: number
}
export interface StatusResponse {
  project: StatusProject
  totals: StatusTotals
  quality: StatusQuality
  agents: StatusAgent[]
  tickets: StatusTickets | null
  trends: { window: number; buckets: StatusTrendBucket[] }
}

export async function fetchStatus(windowDays = 30): Promise<StatusResponse> {
  const res = await fetch(`/api/projects/${selectedProject.value}/status?window=${windowDays}`)
  if (!res.ok) throw new Error(`status ${res.status}`)
  return (await res.json()) as StatusResponse
}
```

(Verify `selectedProject` is in scope in api.ts — it is, from `useProjects()`'s shared ref, same as `fetchTickets`.)

- [ ] **Step 3: Verify typecheck passes**

Run: `cd src/sssf/apps/visualizer && bun run typecheck`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/sssf/apps/visualizer/src/lib/api.ts
git commit -m "feat: fetchStatus client API + StatusResponse types"
```

---

### Task 4: `StatusCharts.vue` — SVG trend charts

**Files:**
- Create: `src/sssf/apps/visualizer/src/components/StatusCharts.vue`

**Interfaces:**
- Consumes: `StatusTrendBucket[]` from Task 3.
- Produces: `<StatusCharts :buckets="trendBuckets" />` — renders four labeled SVG charts: runs/day (bars), cost/day (area), success-rate/day (line), tokens/day (bars). All math inside the component; emits nothing.

- [ ] **Step 1: Write the component**

Create `src/sssf/apps/visualizer/src/components/StatusCharts.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { StatusTrendBucket } from '../lib/api'
import { fmtCost, fmtTokens } from '../lib/format'

const props = defineProps<{ buckets: StatusTrendBucket[] }>()

const W = 300
const H = 84
const PAD = 4

function max(vals: number[]): number {
  return Math.max(1, ...vals)
}

// runs/day — bars
const runBars = computed(() => {
  const vals = props.buckets.map((b) => b.runs)
  const m = max(vals)
  return props.buckets.map((b, i) => {
    const h = (b.runs / m) * (H - PAD * 2)
    return {
      x: PAD + (i * (W - PAD * 2)) / props.buckets.length,
      w: Math.max(2, (W - PAD * 2) / props.buckets.length - 2),
      y: H - PAD - h,
      h,
      day: b.day,
      runs: b.runs,
    }
  })
})

// cost/day — area
const costArea = computed(() => {
  const vals = props.buckets.map((b) => b.cost)
  const m = max(vals)
  const pts = props.buckets.map((b, i) => {
    const x = PAD + (i * (W - PAD * 2)) / Math.max(1, props.buckets.length - 1)
    const y = H - PAD - (b.cost / m) * (H - PAD * 2)
    return { x, y, day: b.day, cost: b.cost }
  })
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const area = pts.length
    ? `${line} L${pts[pts.length - 1]!.x.toFixed(1)},${H - PAD} L${pts[0]!.x.toFixed(1)},${H - PAD} Z`
    : ''
  return { line, area, pts }
})

// success-rate/day — line (rate of finished runs that succeeded)
const rateLine = computed(() => {
  const pts = props.buckets.map((b, i) => {
    const fin = b.success + b.fail
    const rate = fin > 0 ? b.success / fin : 0
    const x = PAD + (i * (W - PAD * 2)) / Math.max(1, props.buckets.length - 1)
    const y = H - PAD - rate * (H - PAD * 2)
    return { x, y, day: b.day, rate: Math.round(rate * 100) }
  })
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  return { line, pts }
})

// tokens/day — bars
const tokenBars = computed(() => {
  const vals = props.buckets.map((b) => b.tokens)
  const m = max(vals)
  return props.buckets.map((b, i) => {
    const h = (b.tokens / m) * (H - PAD * 2)
    return {
      x: PAD + (i * (W - PAD * 2)) / props.buckets.length,
      w: Math.max(2, (W - PAD * 2) / props.buckets.length - 2),
      y: H - PAD - h,
      h,
      day: b.day,
      tokens: b.tokens,
    }
  })
})

const empty = computed(() => props.buckets.length === 0)
</script>

<template>
  <div class="charts">
    <figure class="chart">
      <figcaption>runs / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="runs per day">
        <rect v-for="b in runBars" :key="b.day" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="2" fill="var(--purple)" />
        <title v-for="b in runBars" :key="'t' + b.day">{{ b.day }}: {{ b.runs }} run(s)</title>
      </svg>
      <div v-else class="chart-empty">no runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>cost / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="cost per day">
        <path v-if="costArea.area" :d="costArea.area" fill="rgba(34,211,238,0.15)" />
        <path :d="costArea.line" fill="none" stroke="var(--cyan)" stroke-width="2" stroke-linejoin="round" />
        <title v-for="p in costArea.pts" :key="'t' + p.day">{{ p.day }}: {{ fmtCost(p.cost) }}</title>
      </svg>
      <div v-else class="chart-empty">no runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>success rate / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="success rate per day">
        <path :d="rateLine.line" fill="none" stroke="var(--green)" stroke-width="2" stroke-linejoin="round" />
        <title v-for="p in rateLine.pts" :key="'t' + p.day">{{ p.day }}: {{ p.rate }}%</title>
      </svg>
      <div v-else class="chart-empty">no finished runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>tokens / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="tokens per day">
        <rect v-for="b in tokenBars" :key="b.day" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="2" fill="var(--blue)" />
        <title v-for="b in tokenBars" :key="'t' + b.day">{{ b.day }}: {{ fmtTokens(b.tokens) }}</title>
      </svg>
      <div v-else class="chart-empty">no runs in window</div>
    </figure>
  </div>
</template>

<style scoped>
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}
.chart {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--surface);
}
.chart figcaption {
  font-size: 12px;
  color: var(--faint);
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.chart svg {
  width: 100%;
  height: auto;
  display: block;
}
.chart-empty {
  height: 84px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--faint);
  font-size: 13px;
}
</style>
```

- [ ] **Step 2: Verify typecheck**

Run: `cd src/sssf/apps/visualizer && bun run typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/sssf/apps/visualizer/src/components/StatusCharts.vue
git commit -m "feat: SVG trend charts (StatusCharts)"
```

---

### Task 5: `StatusPage.vue` — the dashboard page

**Files:**
- Create: `src/sssf/apps/visualizer/src/components/StatusPage.vue`

**Interfaces:**
- Consumes: `fetchStatus` + types (Task 3), `StatusCharts` (Task 4), `useProjects()` → `{ selectedProject, projectsLoaded }`, `hrefFor` from `lib/router`, `fmtCost`/`fmtTokens`/`fmtDate`/`ts` from `lib/format`.
- Produces: `<StatusPage />` for Task 6 to mount at `#/status`.

- [ ] **Step 1: Write the component**

Create `src/sssf/apps/visualizer/src/components/StatusPage.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { useProjects, fetchStatus } from '../lib/api'
import type { StatusResponse } from '../lib/api'
import { hrefFor } from '../lib/router'
import { fmtCost, fmtTokens } from '../lib/format'
import StatusCharts from './StatusCharts.vue'

const { selectedProject, projectsLoaded } = useProjects()
const status = ref<StatusResponse | null>(null)
const apiError = ref<string | null>(null)
const loading = ref(false)
const windowDays = ref(30)
const WINDOWS = [7, 30, 90] as const

async function load() {
  if (!selectedProject.value || !projectsLoaded.value) return
  loading.value = true
  apiError.value = null
  try {
    status.value = await fetchStatus(windowDays.value)
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function setWindow(d: number) {
  if (windowDays.value === d) return
  windowDays.value = d
  void load()
}

const hasData = computed(() => (status.value?.totals.runs ?? 0) > 0)

onMounted(() => {
  void load()
})
watch(projectsLoaded, () => {
  if (projectsLoaded.value) void load()
})
</script>

<template>
  <div class="status-page">
    <header class="s-head">
      <div>
        <h1 class="s-title">status · {{ status?.project.name ?? selectedProject ?? '…' }}</h1>
        <p v-if="status" class="s-sub dim">
          {{ status.project.root }}
          <span v-if="status.project.last_run"> · last run {{ status.project.last_run.slice(0, 10) }}</span>
        </p>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">
        <RefreshCw :size="15" :class="{ spin: loading }" /> refresh
      </button>
    </header>

    <div v-if="apiError" class="banner">{{ apiError }} — <button class="link" @click="load">retry</button></div>

    <template v-if="status">
      <!-- main info strip -->
      <div class="strip">
        <div class="tile"><span class="k">db</span><span class="v code">adws/adw_data/sssf.db</span></div>
        <div class="tile"><span class="k">ticketing</span><span class="v">{{ status.project.ticketing_enabled ? 'on' : 'off' }}</span></div>
        <div class="tile">
          <span class="k">active runs</span>
          <a class="v" :href="hrefFor()">{{ status.totals.active }}</a>
        </div>
        <div class="tile"><span class="k">success rate</span><span class="v">{{ Math.round(status.totals.success_rate * 100) }}%</span></div>
      </div>

      <div v-if="!hasData" class="board-empty">no sessions yet — run an ADW to see stats here</div>

      <template v-else>
        <!-- KPI cards -->
        <div class="cards">
          <section class="kpi">
            <h2 class="kpi-title">runs & health</h2>
            <dl>
              <dt>total</dt><dd>{{ status.totals.runs }}</dd>
              <dt>failed</dt><dd>{{ status.totals.failed }}</dd>
              <dt>avg duration</dt><dd>{{ Math.round(status.totals.avg_duration_s / 60) }}m</dd>
              <dt>archived</dt><dd>{{ status.totals.archived }}</dd>
            </dl>
          </section>
          <section class="kpi">
            <h2 class="kpi-title">cost & tokens</h2>
            <dl>
              <dt>total cost</dt><dd>{{ fmtCost(status.totals.total_cost) }}</dd>
              <dt>avg / run</dt><dd>{{ fmtCost(status.totals.avg_cost_per_run) }}</dd>
              <dt>total tokens</dt><dd>{{ fmtTokens(status.totals.total_tokens) }}</dd>
              <dt>avg / run</dt><dd>{{ fmtTokens(status.totals.avg_tokens_per_run) }}</dd>
            </dl>
          </section>
          <section class="kpi">
            <h2 class="kpi-title">quality</h2>
            <dl>
              <dt>gate pass</dt><dd>{{ Math.round(status.quality.gate_pass_rate * 100) }}%</dd>
              <dt>hotspot</dt><dd>{{ status.quality.hotspot_phase ?? '—' }}<span v-if="status.quality.hotspot_phase" class="x">{{ status.quality.hotspot_count }}×</span></dd>
              <dt>retries</dt><dd>{{ status.quality.total_retries }}</dd>
              <dt>failed phases</dt><dd>{{ status.quality.failed_phases }}</dd>
            </dl>
          </section>
          <section class="kpi">
            <h2 class="kpi-title">agents</h2>
            <dl>
              <template v-for="a in status.agents" :key="a.role">
                <dt>{{ a.role }}</dt>
                <dd class="agent">
                  <span class="model">{{ a.model ?? '—' }}</span>
                  <span class="x">{{ a.sessions }} run{{ a.sessions === 1 ? '' : 's' }}</span>
                </dd>
              </template>
            </dl>
          </section>
        </div>

        <!-- trends -->
        <section class="trends">
          <div class="trends-head">
            <h2 class="kpi-title">trends</h2>
            <div class="seg" role="group" aria-label="window">
              <button
                v-for="d in WINDOWS"
                :key="d"
                type="button"
                :class="{ on: windowDays === d }"
                @click="setWindow(d)"
              >{{ d }}d</button>
            </div>
          </div>
          <StatusCharts :buckets="status.trends.buckets" />
        </section>

        <!-- tickets -->
        <section v-if="status.tickets" class="tickets">
          <h2 class="kpi-title">tickets</h2>
          <div class="ticket-row">
            <span class="t-cell">backlog <b>{{ status.tickets.backlog }}</b></span>
            <span class="t-cell">running <b>{{ status.tickets.running }}</b></span>
            <span class="t-cell">done <b>{{ status.tickets.done }}</b></span>
            <span class="t-cell">failed <b>{{ status.tickets.failed }}</b></span>
          </div>
        </section>
      </template>
    </template>

    <div v-else-if="!apiError" class="board-empty">loading status…</div>
  </div>
</template>

<style scoped>
.status-page {
  padding: 22px 28px 40px;
  max-width: 1100px;
}
.s-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.s-title { font-size: 20px; margin: 0; }
.s-sub { margin: 4px 0 0; font-size: 13px; word-break: break-all; }
.btn {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 8px 14px; border-radius: 8px;
  border: 1px solid var(--border); background: rgba(255,255,255,0.04);
  color: var(--text); cursor: pointer;
}
.btn:disabled { opacity: 0.5; cursor: default; }
.spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.banner {
  margin-bottom: 14px; padding: 10px 14px;
  border: 1px solid rgba(248,113,113,0.4); border-radius: 10px;
  background: rgba(248,113,113,0.08); color: var(--red); font-size: 13px;
}
.link { background: none; border: none; color: var(--purple); cursor: pointer; text-decoration: underline; }
.strip {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px; margin-bottom: 18px;
}
.tile {
  padding: 10px 14px; border: 1px solid var(--border-soft);
  border-radius: 10px; background: var(--surface);
  display: flex; flex-direction: column; gap: 3px;
}
.tile .k { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--faint); }
.tile .v { font-size: 14px; }
.tile .v.code { font-size: 12px; font-family: ui-monospace, monospace; }
.tile a.v { color: var(--purple); }
.cards {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px; margin-bottom: 20px;
}
.kpi {
  padding: 14px 16px; border: 1px solid var(--border-soft);
  border-radius: 12px; background: var(--surface);
}
.kpi-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--faint); margin: 0 0 10px; }
.kpi dl { margin: 0; display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; }
.kpi dt { font-size: 13px; color: var(--faint); }
.kpi dd { margin: 0; font-size: 14px; text-align: right; }
.kpi dd.agent { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.model { font-size: 12px; color: var(--cyan); max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.x { font-size: 11px; color: var(--faint); margin-left: 6px; }
.trends { margin-bottom: 20px; }
.trends-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button {
  background: none; border: none; color: var(--faint);
  padding: 5px 12px; font-size: 13px; cursor: pointer;
}
.seg button.on { background: rgba(167,139,250,0.15); color: var(--purple); }
.tickets {
  padding: 14px 16px; border: 1px solid var(--border-soft);
  border-radius: 12px; background: var(--surface);
}
.ticket-row { display: flex; gap: 18px; flex-wrap: wrap; }
.t-cell { font-size: 13px; color: var(--faint); }
.t-cell b { color: var(--text); font-size: 16px; margin-left: 4px; }
.board-empty {
  padding: 40px 0; text-align: center; color: var(--faint); font-size: 14px;
}
</style>
```

- [ ] **Step 2: Verify typecheck**

Run: `cd src/sssf/apps/visualizer && bun run typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/sssf/apps/visualizer/src/components/StatusPage.vue
git commit -m "feat: status dashboard page (StatusPage)"
```

---

### Task 6: Router + tab — `#/status`

**Files:**
- Modify: `src/sssf/apps/visualizer/src/App.vue`

**Interfaces:**
- Consumes: `StatusPage` (Task 5).
- Produces: the `status` view reachable at `#/status`, tab order **board | status | sessions | archived**.

- [ ] **Step 1: Import StatusPage**

In App.vue's import block, after `import KanbanBoard from './components/KanbanBoard.vue'`:

```ts
import StatusPage from './components/StatusPage.vue'
```

- [ ] **Step 2: Extend the view computed**

Current (around line 13–21):

```ts
const view = computed(() => {
  const id = route.value.adwId
  if (!id || id === 'board') return 'board'
  if (id === 'sessions') return 'list'
  if (id === 'archived') return 'archived'
  return 'trace'
})
```

Replace with:

```ts
const view = computed(() => {
  const id = route.value.adwId
  if (!id || id === 'board') return 'board'
  if (id === 'status') return 'status'
  if (id === 'sessions') return 'list'
  if (id === 'archived') return 'archived'
  return 'trace'
})
```

- [ ] **Step 3: Add the tab**

In the tabs block, between the board tab and the sessions tab:

```html
          <a
            :href="hrefFor('status')"
            class="tab"
            :class="{ active: view === 'status' }"
            role="tab"
            :aria-selected="view === 'status'"
            >status</a
          >
```

- [ ] **Step 4: Mount the view**

Find where the other views render (the `<main>` region with `v-if="view === ..."`). Add the status branch next to the board branch:

```html
      <StatusPage v-if="view === 'status'" />
```

- [ ] **Step 5: Verify typecheck + build + manual smoke**

Run:

```bash
cd src/sssf/apps/visualizer && bun run typecheck && bun run build
```

Then open `http://localhost:4600/#/status` in a browser (or headless):
- the status tab is active
- the page shows the main info strip, 4 KPI cards, trend charts, tickets panel (inkwell has ticketing on)
- the refresh button re-fetches; the 7d/30d/90d toggle changes the charts
- `#/` still shows the board; `#/sessions` and `#/archived` unchanged

- [ ] **Step 6: Run full test suite**

Run: `cd src/sssf/apps/visualizer && bun test && bun run lint`
Expected: all tests PASS, lint clean.

- [ ] **Step 7: Commit**

```bash
git add src/sssf/apps/visualizer/src/App.vue
git commit -m "feat: #/status route + tab (board | status | sessions | archived)"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md` (the aggregate index — add the status page entry)

**Interfaces:**
- Consumes: nothing new; documents the feature.

- [ ] **Step 1: README**

In `README.md`, in the visualizer features list (where the kanban board is described), add:

```markdown
- **Status dashboard** — per-project KPIs: runs/health, cost & tokens, quality
  gates, per-agent models, trend charts (7/30/90d), ticket pipeline. Served at
  `#/status`.
```

- [ ] **Step 2: Revisions index**

In `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`, add a row/entry linking the status page spec:

```markdown
- 2026-08-15 — [status page design](2026-08-15-status-page-design.md): project
  dashboard with KPIs (runs/health, cost/tokens, quality, agents, trends, tickets).
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md
git commit -m "docs: status page in README + revisions index"
```

---

## Self-Review

- **Spec coverage:** purpose ✓ (Task 5 layout), single-endpoint approach ✓ (Tasks 1–2), main info strip ✓ (Task 5), 4 KPI cards ✓ (Task 5), trends + window ✓ (Tasks 1, 4, 5), tickets panel ✓ (Tasks 1, 5), API contract ✓ (Tasks 1–3), error handling ✓ (Task 5 banner + zeroed payload in Task 1), testing ✓ (Tasks 1, 2, 6), out-of-scope respected (no polling — on-load + refresh only, Task 5).
- **Placeholder scan:** no TBD/TODO; every step has real code or a concrete verification command.
- **Type consistency:** `StatusResponse` defined once in `server/status.ts` (Task 1) and mirrored in `src/lib/api.ts` (Task 3); `fetchStatus(windowDays)` in Task 3 is called as `fetchStatus(windowDays.value)` in Task 5; `StatusCharts` consumes `StatusTrendBucket[]` (= `status.trends.buckets`) in Task 5. `fmtCost`/`fmtTokens`/`hrefFor`/`useProjects` names match `lib/format.ts` and `lib/api.ts` as used by KanbanBoard.
- **Known fixture caveat:** the `hotspot_count` assertion expects 1 (one `commit_build` fail row) — if the aggregation ever counts distinct sessions instead of rows, the test fails loudly and the definition (rows) is the agreed one.
