# Status Page v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the status dashboard with real per-agent/per-model cost attribution (actual billing + token-share), git repo stats, a GitHub-style contributions heatmap, and layout fixes (full width, model-name overflow).

**Architecture:** Stays a single aggregate endpoint. `server/status.ts` gains cost attribution from `agent_end` events (payload `cost` + `tokens`, which reconcile exactly with `sessions.total_cost`/`total_tokens` — verified against inkwell). New `server/git.ts` shells `git -C <root>` (Bun.spawnSync, fixed commands — same permission model as the ticket routes). Client gains `ContributionsHeatmap.vue`, a Repo KPI card, per-agent/per-model cost tables with a footnote, and CSS fixes.

**Tech Stack:** Bun + bun:sqlite + Bun.spawnSync (server), Vue 3 SFC + hand-rolled CSS grid heatmap (client), bun:test (tests).

**Spec:** `docs/superpowers/specs/2026-08-15-status-page-design.md` (Revision 2 section)

## Global Constraints

- No new DB columns or schema changes — cost/tokens come from existing `events` (`type='agent_end'`, `payload_json`, `tokens`), `phases` (owner), `agent_sessions` (model).
- No new dependencies.
- `cost_actual` = SUM(`json_extract(payload_json,'$.cost')`) over agent_end per agent/model; `cost_share` = per-session `total_cost × (agent tokens / session total_tokens)`; both must be shown, footnote explains the difference.
- `agents[]` becomes dynamic (any role present in the data).
- Git shelling: fixed commands only (no user input interpolated into args); a non-git root yields zeroed/null stats + empty contributions, never an error.
- Contributions: last 364 days, `[{date: 'YYYY-MM-DD', count}]`, dates in UTC.
- Full width page (drop `max-width: 1100px`); model names truncate (ellipsis), never overflow.
- Existing gates stay green: `bun run typecheck`, `bun run build`, `bun test`, `bun run lint`; existing status tests keep passing (fixture additions only).
- The user-facing viz server on :4600 is never touched by tests or smoke checks (use throwaway ports).

---

### Task 1: Cost attribution in `server/status.ts`

**Files:**
- Modify: `src/sssf/apps/visualizer/server/status.ts`
- Modify: `src/sssf/apps/visualizer/server/status.test.ts`

**Interfaces:**
- Produces: `AgentStat` gains `tokens: number`, `cost_actual: number`, `cost_share: number`; new `ModelStat { model: string; tokens: number; sessions: number; cost_actual: number; cost_share: number }`; `StatusResponse.agents` becomes dynamic roles; `StatusResponse.models: ModelStat[]`. Later tasks consume these.

- [ ] **Step 1: Write/extend the failing test**

In `server/status.test.ts`, extend the fixture with `agent_end` events so per-call billing reconciles with the session rows, and add assertions. The fixture already has sessions s1(0.50, 100000 tokens), s2(0.20, 50000), s3(0.05, 10000), s4(running, 0), s5(archived, 0) and agent_sessions mapping s1→planner=deepseek-v4-flash-official, s1→builder=gpt-5.5, s2→reviewer=gemini-2.5-flash, s3→documenter=gemini-2.5-flash.

Add to `fixtureDb` (after the agent_sessions inserts):

```ts
  // agent_end events: per-call billing must reconcile with session totals.
  // s1: planner 25000 tok / $0.10, builder 75000 tok / $0.40  (sums: 100000, $0.50)
  // s2: reviewer 50000 tok / $0.20                            (sums:  50000, $0.20)
  // s3: documenter 10000 tok / $0.05                          (sums:  10000, $0.05)
  db.run(`CREATE TABLE IF NOT EXISTS events (
    event_id TEXT, adw_id TEXT, phase_id TEXT, type TEXT, name TEXT,
    payload_json TEXT, tokens INTEGER)`);
  const ev = db.prepare(`INSERT INTO events VALUES (?,?,?,?,?,?,?)`);
  ev.run("e1", "s1", "s1_02", "agent_end", "planner", '{"cost":0.10,"usage":{"total_tokens":25000}}', 25000);
  ev.run("e2", "s1", "s1_03", "agent_end", "builder", '{"cost":0.40,"usage":{"total_tokens":75000}}', 75000);
  ev.run("e3", "s2", "s2_01", "agent_end", "reviewer", '{"cost":0.20,"usage":{"total_tokens":50000}}', 50000);
  ev.run("e4", "s3", "s3_01", "agent_end", "documenter", '{"cost":0.05,"usage":{"total_tokens":10000}}', 10000);
```

Note: the fixture's agent_end events must point at phases whose `owner`
matches the event agent (real-system invariant: an agent phase is owned by the
agent that runs it, and `agent_end` fires in the agent's own phase). In the
existing fixture, `s1_02`/`s1_03` were owned by `git` and `s3_01` by `planner` —
realign those owners to planner/builder/documenter respectively so the cost
queries (which key on `phases.owner`) attribute correctly. Only the fixture
owner values change; nothing else asserts on them.

Note: the `events` DDL must be created BEFORE the inserts — add the `CREATE TABLE IF NOT EXISTS events` line before the `ev` prepare (the block above shows them together; keep that order). The fixture DDL section already creates sessions/phases/agent_sessions/gate_results/tickets — add the events table there instead of inline if cleaner; the inserts stay at the bottom.

Add assertions in the "known dataset" test (after the existing agents assertions):

```ts
    // agents: dynamic roles, cost attribution
    expect(status.agents.map((a) => a.role).sort()).toEqual(["builder", "documenter", "planner", "reviewer"]);
    const p = status.agents.find((a) => a.role === "planner")!;
    expect(p.tokens).toBe(25000);
    expect(p.cost_actual).toBeCloseTo(0.10, 5);
    expect(p.cost_share).toBeCloseTo(0.125, 5);   // 0.50 × (25000/100000)
    const b = status.agents.find((a) => a.role === "builder")!;
    expect(b.cost_actual).toBeCloseTo(0.40, 5);
    expect(b.cost_share).toBeCloseTo(0.375, 5);
    // per-agent actual costs sum to the sessions' total cost
    expect(status.agents.reduce((n, a) => n + a.cost_actual, 0)).toBeCloseTo(0.75, 5);

    // models: per-model cost attribution
    expect(status.models.map((m) => m.model).sort()).toEqual([
      "litellm/deepseek-v4-flash-official", "litellm/gemini-2.5-flash", "litellm/gpt-5.5",
    ]);
    const gpt = status.models.find((m) => m.model === "litellm/gpt-5.5")!;
    expect(gpt.tokens).toBe(75000);              // s1 builder
    expect(gpt.cost_actual).toBeCloseTo(0.40, 5);
    expect(gpt.cost_share).toBeCloseTo(0.375, 5);
    const gem = status.models.find((m) => m.model === "litellm/gemini-2.5-flash")!;
    expect(gem.tokens).toBe(60000);              // s2 reviewer + s3 documenter
    expect(gem.cost_actual).toBeCloseTo(0.25, 5);
    expect(status.models.reduce((n, m) => n + m.cost_actual, 0)).toBeCloseTo(0.75, 5);
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/sssf/apps/visualizer && bun test server/status.test.ts`
Expected: FAIL — `status.agents.map` returns the old fixed 4 roles without cost fields; `status.models` is undefined.

- [ ] **Step 3: Write the implementation**

In `server/status.ts`:

1. Extend the interfaces:

```ts
export interface AgentStat {
  role: string;
  model: string | null;
  sessions: number;
  context_tokens: number;
  tokens: number;         // actual tokens from agent_end events
  cost_actual: number;    // summed provider billing (agent_end payload .cost)
  cost_share: number;     // session cost apportioned by token share
}

export interface ModelStat {
  model: string;
  tokens: number;
  sessions: number;
  cost_actual: number;
  cost_share: number;
}
```

2. Add `models: ModelStat[]` to `StatusResponse` and `empty` (`models: []`).

3. Replace the agents block (the fixed `AGENT_ROLES` mapping + counts) with a dynamic build. The `AGENT_ROLES` constant stays as the *ordering preference* (canonical four first, extras after, alphabetically), but roles come from the data:

```ts
    // ── agents: dynamic roles with cost attribution ──────────────────────
    const agents: AgentStat[] = [];
    const models: ModelStat[] = [];
    if (has("events") && has("phases")) {
      // per-role tokens + actual cost (agent_end events carry provider billing)
      const rows = db.query<{ agent: string; tokens: number; cost: number }, []>(
        `SELECT p.owner agent, SUM(e.tokens) tokens,
                SUM(json_extract(e.payload_json, '$.cost')) cost
           FROM events e JOIN phases p ON p.phase_id = e.phase_id
          WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL
          GROUP BY p.owner`,
      ).all();
      const costByAgent = new Map<string, number>();
      const tokensByAgent = new Map<string, number>();
      for (const row of rows) {
        tokensByAgent.set(row.agent, Number(row.tokens ?? 0));
        costByAgent.set(row.agent, Number(row.cost ?? 0));
      }
      // per-session agent tokens → token-share cost
      const perSession = db.query<{ adw_id: string; agent: string; tokens: number }, []>(
        `SELECT e.adw_id, p.owner agent, SUM(e.tokens) tokens
           FROM events e JOIN phases p ON p.phase_id = e.phase_id
          WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL
          GROUP BY e.adw_id, p.owner`,
      ).all();
      const sessionTotals = new Map<string, { cost: number; tokens: number }>();
      if (has("sessions")) {
        for (const s of db.query<{ adw_id: string; total_cost: number; total_tokens: number }, []>(
          "SELECT adw_id, total_cost, total_tokens FROM sessions",
        ).all()) {
          sessionTotals.set(s.adw_id, { cost: Number(s.total_cost ?? 0), tokens: Number(s.total_tokens ?? 0) });
        }
      }
      const shareByAgent = new Map<string, number>();
      for (const row of perSession) {
        const tot = sessionTotals.get(row.adw_id);
        if (!tot || tot.tokens <= 0) continue;
        shareByAgent.set(row.agent, (shareByAgent.get(row.agent) ?? 0) + tot.cost * (Number(row.tokens) / tot.tokens));
      }
      // models: per-model tokens/cost/share via agent_sessions join
      const modelRows = db.query<{ model: string; tokens: number; cost: number; n: number }, []>(
        `SELECT ag.model model, SUM(e.tokens) tokens,
                SUM(json_extract(e.payload_json, '$.cost')) cost, COUNT(DISTINCT e.adw_id) n
           FROM events e
           JOIN phases p ON p.phase_id = e.phase_id
           JOIN agent_sessions ag ON ag.adw_id = e.adw_id AND ag.agent = p.owner
          WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL AND ag.model IS NOT NULL
          GROUP BY ag.model`,
      ).all();
      const modelShare = new Map<string, number>();
      const perSessionModel = db.query<{ adw_id: string; model: string; tokens: number }, []>(
        `SELECT e.adw_id, ag.model model, SUM(e.tokens) tokens
           FROM events e
           JOIN phases p ON p.phase_id = e.phase_id
           JOIN agent_sessions ag ON ag.adw_id = e.adw_id AND ag.agent = p.owner
          WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL AND ag.model IS NOT NULL
          GROUP BY e.adw_id, ag.model`,
      ).all();
      for (const row of perSessionModel) {
        const tot = sessionTotals.get(row.adw_id);
        if (!tot || tot.tokens <= 0) continue;
        modelShare.set(row.model, (modelShare.get(row.model) ?? 0) + tot.cost * (Number(row.tokens) / tot.tokens));
      }
      for (const row of modelRows) {
        models.push({
          model: row.model,
          tokens: Number(row.tokens ?? 0),
          sessions: row.n,
          cost_actual: Number(row.cost ?? 0),
          cost_share: modelShare.get(row.model) ?? 0,
        });
      }
      models.sort((a, b) => b.cost_actual - a.cost_actual);

      // merge agent_sessions metadata (model, sessions, context_tokens) for each role
      const meta = has("agent_sessions")
        ? db.query<{ agent: string; model: string | null; n: number; tokens: number }, []>(
            `SELECT agent, MAX(model) model, COUNT(DISTINCT adw_id) n, COALESCE(SUM(context_tokens),0) tokens
               FROM agent_sessions GROUP BY agent`,
          ).all()
        : [];
      const metaByAgent = new Map(meta.map((m) => [m.agent, m]));

      const allRoles = new Set([...AGENT_ROLES, ...tokensByAgent.keys()]);
      const roleOrder = [...AGENT_ROLES, ...Array.from(allRoles).filter((r) => !AGENT_ROLES.includes(r)).sort()];
      for (const role of roleOrder) {
        const m = metaByAgent.get(role);
        agents.push({
          role,
          model: m?.model ?? null,
          sessions: m?.n ?? 0,
          context_tokens: Number(m?.tokens ?? 0),
          tokens: tokensByAgent.get(role) ?? 0,
          cost_actual: costByAgent.get(role) ?? 0,
          cost_share: shareByAgent.get(role) ?? 0,
        });
      }
    }
```

Note: `roleOrder` should use `Array.from(allRoles)` carefully — `allRoles` is a Set of roles; the canonical four come first, extras alphabetical. Replace the old agents block entirely (the old `AGENT_ROLES.map(...)` + the two agent_sessions queries).

4. Add `models` to the returned object and to `empty`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/sssf/apps/visualizer && bun test server/status.test.ts`
Expected: all status tests pass (existing + new cost assertions). Also run `bun test` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server/status.ts src/sssf/apps/visualizer/server/status.test.ts
git commit -m "feat: per-agent and per-model cost attribution (actual + token-share)"
```

---

### Task 2: `server/git.ts` — repo stats + contributions

**Files:**
- Create: `src/sssf/apps/visualizer/server/git.ts`
- Test: `src/sssf/apps/visualizer/server/git.test.ts`

**Interfaces:**
- Produces: `gitStats(root: string): GitStats` and `contributions(root: string): ContributionDay[]` — used by Task 3.

```ts
export interface GitContributor { name: string; commits: number }
export interface GitStats {
  commits: number; commits_30d: number; commits_year: number;
  contributors: GitContributor[];
  branches: number; current_branch: string | null;
  last_commit: { date: string; subject: string } | null;
  dirty: number; first_commit: string | null;
}
export interface ContributionDay { date: string; count: number }
```

- [ ] **Step 1: Write the failing test**

Create `src/sssf/apps/visualizer/server/git.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gitStats, contributions } from "./git";

function git(root: string, args: string[], env: Record<string, string> = {}): void {
  const res = spawnSync("git", ["-C", root, ...args], { env: { ...process.env, ...env }, encoding: "utf8" });
  if (res.status !== 0) throw new Error(`git ${args.join(" ")} failed: ${res.stderr}`);
}

function makeRepo(daysAgo: number[], extraFile = false): { root: string; today: string } {
  const root = mkdtempSync(join(tmpdir(), "sssf-git-"));
  git(root, ["init", "-b", "main", "-q"]);
  git(root, ["config", "user.email", "t@t"], );
  git(root, ["config", "user.name", "Test"]);
  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  daysAgo.forEach((n, i) => {
    const when = new Date(Date.now() - n * 86400_000);
    writeFileSync(join(root, "f.txt"), `line ${i}\n`, { flag: "a" });
    const env = {
      GIT_AUTHOR_DATE: when.toISOString(),
      GIT_COMMITTER_DATE: when.toISOString(),
    };
    git(root, ["add", "."], env);
    git(root, ["commit", "-m", `c${i}`, "-q"], env);
  });
  if (extraFile) {
    writeFileSync(join(root, "dirty.txt"), "uncommitted\n");
  }
  return { root, today: iso(today) };
}

describe("gitStats", () => {
  test("parses commits, contributors, branches, last/first commit, dirty", () => {
    // 2 today, 1 yesterday, 1 40 days ago, 1 400 days ago (outside the 1-year window)
    const { root, today } = makeRepo([0, 0, 1, 40, 400]);
    const s = gitStats(root);
    expect(s.commits).toBe(5);
    expect(s.commits_30d).toBe(3);       // 2 today + 1 yesterday
    expect(s.commits_year).toBe(4);      // excludes the 400-day-old commit
    expect(s.contributors).toEqual([{ name: "Test <t@t>", commits: 5 }]);
    expect(s.branches).toBe(1);
    expect(s.current_branch).toBe("main");
    expect(s.last_commit?.subject).toBe("c4");
    expect(s.last_commit?.date).toBe(today);
    expect(s.first_commit).toBe(new Date(Date.now() - 400 * 86400_000).toISOString().slice(0, 10));
    expect(s.dirty).toBe(0);
  });

  test("dirty count and non-repo root degrade", () => {
    const { root } = makeRepo([0], true);
    expect(gitStats(root).dirty).toBe(1);
    const notRepo = mkdtempSync(join(tmpdir(), "sssf-git-"));
    const z = gitStats(notRepo);
    expect(z.commits).toBe(0);
    expect(z.contributors).toEqual([]);
    expect(z.last_commit).toBeNull();
    expect(z.current_branch).toBeNull();
  });
});

describe("contributions", () => {
  test("returns 364 days, counts commits per day, excludes out-of-window", () => {
    const { root } = makeRepo([0, 0, 1, 40, 400]);
    const days = contributions(root);
    expect(days).toHaveLength(364);
    const byDate = new Map(days.map((d) => [d.date, d.count]));
    const today = new Date(Date.now()).toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400_000).toISOString().slice(0, 10);
    const d40 = new Date(Date.now() - 40 * 86400_000).toISOString().slice(0, 10);
    const d400 = new Date(Date.now() - 400 * 86400_000).toISOString().slice(0, 10);
    expect(byDate.get(today)).toBe(2);
    expect(byDate.get(yesterday)).toBe(1);
    expect(byDate.get(d40)).toBe(1);
    expect(byDate.get(d400)).toBeUndefined();
  });

  test("non-repo root returns empty", () => {
    const notRepo = mkdtempSync(join(tmpdir(), "sssf-git-"));
    expect(contributions(notRepo)).toEqual([]);
  });
});
```

Note: `git config` calls are formatted `git(root, ["config", "user.email", "t@t"])` — there is a trailing-comma typo in the first `git(root, ["config", "user.email", "t@t"], )` line above; write it without the stray comma: `git(root, ["config", "user.email", "t@t"]);`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/sssf/apps/visualizer && bun test server/git.test.ts`
Expected: FAIL — `Cannot find module "./git"`.

- [ ] **Step 3: Write the implementation**

Create `src/sssf/apps/visualizer/server/git.ts`:

```ts
/** Git repo stats + daily contributions for a project root, via `git -C`. */
import { spawnSync } from "node:child_process";

export interface GitContributor { name: string; commits: number }
export interface GitStats {
  commits: number;
  commits_30d: number;
  commits_year: number;
  contributors: GitContributor[];
  branches: number;
  current_branch: string | null;
  last_commit: { date: string; subject: string } | null;
  dirty: number;
  first_commit: string | null;
}
export interface ContributionDay { date: string; count: number }

const EMPTY: GitStats = {
  commits: 0, commits_30d: 0, commits_year: 0, contributors: [],
  branches: 0, current_branch: null, last_commit: null, dirty: 0, first_commit: null,
};

/** Run a fixed git command; null on any failure (non-repo, no commits, etc). */
function run(root: string, args: string[]): { ok: true; out: string } | { ok: false } {
  const res = spawnSync("git", ["-C", root, ...args], { encoding: "utf8" });
  if (res.status !== 0) return { ok: false };
  return { ok: true, out: String(res.stdout ?? "").trim() };
}

function count(root: string, args: string[]): number {
  const r = run(root, args);
  if (!r.ok) return 0;
  const n = Number.parseInt(r.out, 10);
  return Number.isFinite(n) ? n : 0;
}

export function gitStats(root: string): GitStats {
  try {
    const year = new Date().getUTCFullYear();
    const commits = count(root, ["rev-list", "--count", "HEAD"]);
    if (commits === 0) return EMPTY;   // not a repo or no commits — nothing to show
    const commits30d = count(root, ["rev-list", "--count", "--since=30 days ago", "HEAD"]);
    const commitsYear = count(root, ["rev-list", "--count", `--since=${year}-01-01`, "HEAD"]);

    const contributors: GitContributor[] = [];
    const sl = run(root, ["shortlog", "-sne", "HEAD"]);
    if (sl.ok) {
      for (const line of sl.out.split("\n")) {
        const m = line.match(/^\s*(\d+)\s+(.+)$/);
        if (m) contributors.push({ name: m[2]!.trim(), commits: Number.parseInt(m[1]!, 10) });
      }
    }

    const branches = count(root, ["for-each-ref", "--count", "refs/heads"]);
    const cb = run(root, ["branch", "--show-current"]);

    const last = run(root, ["log", "-1", "--format=%ad|%s", "--date=short"]);
    let lastCommit: GitStats["last_commit"] = null;
    if (last.ok) {
      const [date, ...rest] = last.out.split("|");
      lastCommit = { date: date!, subject: rest.join("|") };
    }
    const first = run(root, ["log", "--reverse", "-1", "--format=%ad", "--date=short"]);
    const dirty = run(root, ["status", "--porcelain"]).ok
      ? run(root, ["status", "--porcelain"]).out.split("\n").filter((l) => l.length > 0).length
      : 0;

    return {
      commits, commits_30d: commits30d, commits_year: commitsYear,
      contributors, branches, current_branch: cb.ok ? cb.out : null,
      last_commit: lastCommit, dirty, first_commit: first.ok ? first.out : null,
    };
  } catch {
    return EMPTY;
  }
}

export function contributions(root: string): ContributionDay[] {
  try {
    const since = new Date(Date.now() - 364 * 86400_000).toISOString().slice(0, 10);
    const r = run(root, ["log", `--since=${since}`, "--format=%ad", "--date=short"]);
    if (!r.ok) return [];
    const counts = new Map<string, number>();
    for (const day of r.out.split("\n")) {
      if (/^\d{4}-\d{2}-\d{2}$/.test(day)) counts.set(day, (counts.get(day) ?? 0) + 1);
    }
    const days: ContributionDay[] = [];
    for (let i = 363; i >= 0; i--) {
      const date = new Date(Date.now() - i * 86400_000).toISOString().slice(0, 10);
      days.push({ date, count: counts.get(date) ?? 0 });
    }
    return days;
  } catch {
    return [];
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/sssf/apps/visualizer && bun test server/git.test.ts`
Expected: all git tests pass. (If the sandbox has no `git` on PATH, report BLOCKED — do not mock it.)

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server/git.ts src/sssf/apps/visualizer/server/git.test.ts
git commit -m "feat: git repo stats + daily contributions (server/git.ts)"
```

---

### Task 3: Wire git + contributions into `/status`

**Files:**
- Modify: `src/sssf/apps/visualizer/server/status.ts`
- Modify: `src/sssf/apps/visualizer/server/status.test.ts`

**Interfaces:**
- Consumes: `gitStats`, `contributions` from Task 2.
- Produces: `StatusResponse.git: GitStats` and `StatusResponse.contributions: ContributionDay[]`.

- [ ] **Step 1: Extend the failing test**

In `server/status.test.ts`:

1. Add imports: `import { gitStats, contributions } from "./git"` is NOT needed in the test — instead assert on the payload. Add to the "known dataset" test:

```ts
    // git: the fixture root is NOT a git repo → graceful zeros
    expect(status.git.commits).toBe(0);
    expect(status.git.contributors).toEqual([]);
    expect(status.contributions).toEqual([]);
```

2. Add a new test that turns the fixture root into a real git repo and asserts the git payload:

```ts
  test("git stats + contributions when the root is a git repo", () => {
    const { dbPath, root } = setup();
    const { spawnSync } = require("node:child_process") as typeof import("node:child_process");
    const git = (args: string[], env: Record<string, string> = {}) => {
      const r = spawnSync("git", ["-C", root, ...args], { env: { ...process.env, ...env }, encoding: "utf8" });
      if (r.status !== 0) throw new Error(`git ${args.join(" ")}: ${r.stderr}`);
    };
    git(["init", "-b", "main", "-q"]);
    git(["config", "user.email", "t@t"]);
    git(["config", "user.name", "Test"]);
    const { writeFileSync } = require("node:fs") as typeof import("node:fs");
    writeFileSync(join(root, "f.txt"), "x\n");
    const now = new Date();
    git(["add", "."]);
    git(["commit", "-m", "c0", "-q"], {
      GIT_AUTHOR_DATE: now.toISOString(),
      GIT_COMMITTER_DATE: now.toISOString(),
    });
    const status = computeStatus(dbPath, root, "fixture", 30);
    expect(status.git.commits).toBe(1);
    expect(status.git.current_branch).toBe("main");
    expect(status.git.last_commit?.subject).toBe("c0");
    expect(status.contributions).toHaveLength(364);
    expect(status.contributions[363]!.count).toBe(1);   // today
  });
```

(Add `join` to the existing `import { tmpdir } from "os"; import { join } from "path";` — already imported in the file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/sssf/apps/visualizer && bun test server/status.test.ts`
Expected: FAIL — `status.git` is undefined.

- [ ] **Step 3: Write the implementation**

In `server/status.ts`:

1. Add imports: `import { gitStats, contributions } from "./git";`
2. Add to `StatusResponse`: `git: GitStats; contributions: ContributionDay[];` — import the types (`import type { GitStats, ContributionDay } from "./git";`).
3. Add to `empty`: `git: EMPTY_GIT, contributions: []` where `EMPTY_GIT` is a local zeroed constant (or call `gitStats` with a path that fails — simplest: build a `ZERO_GIT` literal matching the GitStats shape; but Task 2 exports `EMPTY`? It doesn't export it. Add `export const EMPTY_GIT: GitStats = {...}` to git.ts, or construct inline. Cleanest: `git: { commits: 0, commits_30d: 0, commits_year: 0, contributors: [], branches: 0, current_branch: null, last_commit: null, dirty: 0, first_commit: null }` inline in `empty`.)
4. In the return object, add:

```ts
      git: gitStats(root),
      contributions: contributions(root),
```

(Only in the success path — the catch returns `empty`.)

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `cd src/sssf/apps/visualizer && bun test`
Expected: all tests pass (status + git + existing).

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server/status.ts src/sssf/apps/visualizer/server/status.test.ts src/sssf/apps/visualizer/server/git.ts
git commit -m "feat: /status includes git stats and contributions"
```

---

### Task 4: Client types

**Files:**
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `GitContributor`, `GitStats`, `ContributionDay`, `StatusModel`; `StatusAgent` gains `tokens/cost_actual/cost_share`; `StatusResponse` gains `git` + `contributions` + `models`. Task 5/6 consume these.

- [ ] **Step 1: Add the types**

Append to `src/sssf/apps/visualizer/src/lib/api.ts` (after the existing status types):

```ts
export interface GitContributor { name: string; commits: number }
export interface GitStats {
  commits: number
  commits_30d: number
  commits_year: number
  contributors: GitContributor[]
  branches: number
  current_branch: string | null
  last_commit: { date: string; subject: string } | null
  dirty: number
  first_commit: string | null
}
export interface ContributionDay { date: string; count: number }
export interface StatusModel {
  model: string
  tokens: number
  sessions: number
  cost_actual: number
  cost_share: number
}
```

Extend the existing `StatusAgent` (add three fields) and `StatusResponse` (add `models: StatusModel[]`, `git: GitStats`, `contributions: ContributionDay[]`):

```ts
export interface StatusAgent {
  role: string
  model: string | null
  sessions: number
  context_tokens: number
  tokens: number
  cost_actual: number
  cost_share: number
}
```

And in `StatusResponse`:

```ts
  agents: StatusAgent[]
  models: StatusModel[]
  git: GitStats
  contributions: ContributionDay[]
```

- [ ] **Step 2: Verify typecheck**

Run: `cd src/sssf/apps/visualizer && bun run typecheck`
Expected: PASS (StatusPage doesn't use the new fields yet — they're additive; if Task 5/6 code already exists from a parallel edit, ensure it typechecks there).

- [ ] **Step 3: Commit**

```bash
git add src/sssf/apps/visualizer/src/lib/api.ts
git commit -m "feat: client types for git stats, contributions, model costs"
```

---

### Task 5: `ContributionsHeatmap.vue`

**Files:**
- Create: `src/sssf/apps/visualizer/src/components/ContributionsHeatmap.vue`

**Interfaces:**
- Consumes: `ContributionDay[]` (364 entries, oldest first, today last).
- Produces: `<ContributionsHeatmap :days="status.contributions" />` — renders the GitHub-style grid.

- [ ] **Step 1: Write the component**

Create `src/sssf/apps/visualizer/src/components/ContributionsHeatmap.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { ContributionDay } from '../lib/api'

const props = defineProps<{ days: ContributionDay[] }>()

// Weekday rows (Sun..Sat) via CSS grid; month labels where the month changes.
const LEVELS = [0, 1, 2, 3, 4] as const

function level(count: number): number {
  if (count === 0) return 0
  if (count <= 2) return 1
  if (count <= 5) return 2
  if (count <= 9) return 3
  return 4
}

const cells = computed(() =>
  props.days.map((d) => {
    const dow = new Date(`${d.date}T00:00:00Z`).getUTCDay()
    return { ...d, dow, level: level(d.count) }
  }),
)

const months = computed(() => {
  const out: { label: string; col: number }[] = []
  let prev: string | null = null
  cells.value.forEach((c, i) => {
    const m = c.date.slice(0, 7)
    if (m !== prev) {
      out.push({ label: new Date(`${c.date}T00:00:00Z`).toLocaleString('en', { month: 'short' }), col: i })
      prev = m
    }
  })
  return out
})

const total = computed(() => props.days.reduce((n, d) => n + d.count, 0))
</script>

<template>
  <figure class="heatmap">
    <figcaption class="hm-head">
      <span>{{ total }} commits in the last year</span>
      <span class="hm-legend">
        <span class="hm-less">less</span>
        <span v-for="l in LEVELS" :key="l" class="cell" :class="'lvl-' + l" />
        <span class="hm-more">more</span>
      </span>
    </figcaption>
    <div class="hm-scroll">
      <div class="hm-grid-wrap">
        <div class="hm-dows">
          <span>Mon</span><span /><span>Wed</span><span /><span>Fri</span><span /><span />
        </div>
        <div class="hm-body">
          <div class="hm-months">
            <span
              v-for="m in months"
              :key="m.col"
              class="hm-month"
              :style="{ gridColumnStart: m.col + 1 }"
            >{{ m.label }}</span>
          </div>
          <div class="hm-grid">
            <span
              v-for="(c, i) in cells"
              :key="c.date"
              class="cell"
              :class="'lvl-' + c.level"
              :style="{ gridRow: c.dow + 1, gridColumn: Math.floor(i / 7) + 1 }"
              :title="`${c.count} commit${c.count === 1 ? '' : 's'} · ${c.date}`"
            />
          </div>
        </div>
      </div>
    </div>
  </figure>
</template>

<style scoped>
.heatmap {
  margin: 0;
  padding: 14px 16px;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--surface);
}
.hm-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--faint);
  margin-bottom: 12px;
}
.hm-legend { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; }
.hm-scroll { overflow-x: auto; }
.hm-grid-wrap { display: flex; gap: 8px; min-width: 760px; }
.hm-dows {
  display: grid;
  grid-template-rows: repeat(7, 12px);
  gap: 3px;
  font-size: 10px;
  color: var(--faint);
  padding-top: 18px;
}
.hm-body { flex: 1; }
.hm-months {
  display: grid;
  grid-template-columns: repeat(53, 12px);
  gap: 3px;
  height: 16px;
  font-size: 10px;
  color: var(--faint);
}
.hm-month { grid-row: 1; white-space: nowrap; }
.hm-grid {
  display: grid;
  grid-template-columns: repeat(53, 12px);
  grid-template-rows: repeat(7, 12px);
  gap: 3px;
}
.cell {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.05);
}
.cell.lvl-1 { background: rgba(74, 222, 128, 0.25); }
.cell.lvl-2 { background: rgba(74, 222, 128, 0.5); }
.cell.lvl-3 { background: rgba(74, 222, 128, 0.75); }
.cell.lvl-4 { background: #4ade80; }
.hm-month { color: var(--faint); }
</style>
```

Note: `grid-template-columns: repeat(53, ...)` — 364 days / 7 = 52 columns; the last partial week makes 53 columns max; use `repeat(53, 12px)` to be safe (extra empty columns are harmless). Verify visually later.

- [ ] **Step 2: Verify typecheck**

Run: `cd src/sssf/apps/visualizer && bun run typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/sssf/apps/visualizer/src/components/ContributionsHeatmap.vue
git commit -m "feat: contributions heatmap (GitHub-style grid)"
```

---

### Task 6: `StatusPage.vue` — full width, repo card, cost tables, footnote

**Files:**
- Modify: `src/sssf/apps/visualizer/src/components/StatusPage.vue`

**Interfaces:**
- Consumes: `StatusResponse` extended fields (Tasks 1-5), `ContributionsHeatmap` (Task 5).

- [ ] **Step 1: Apply the changes**

Edit `src/sssf/apps/visualizer/src/components/StatusPage.vue`:

1. **Import** the heatmap:
```ts
import ContributionsHeatmap from './ContributionsHeatmap.vue'
```

2. **Full width** — replace `.status-page` rule:
```css
.status-page {
  padding: 22px 28px 40px;
}
```
(remove `max-width: 1100px`)

3. **Overflow fix** — replace the `.kpi dd.agent` / `.model` rules:
```css
.kpi dd.agent { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; min-width: 0; }
.model { font-size: 12px; color: var(--cyan); max-width: 100%; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

4. **Repo card** — add a 5th KPI card after the quality card (before agents):
```html
          <section class="kpi">
            <h2 class="kpi-title">repo</h2>
            <dl>
              <dt>commits</dt><dd>{{ status.git.commits }}<span class="x" v-if="status.git.commits_30d">+{{ status.git.commits_30d }}/30d</span></dd>
              <dt>contributors</dt><dd>{{ status.git.contributors.length }}</dd>
              <dt>branch</dt><dd class="agent"><span class="model">{{ status.git.current_branch ?? '—' }}</span><span class="x">{{ status.git.branches }} total</span></dd>
              <dt>last commit</dt><dd v-if="status.git.last_commit" class="agent"><span class="model">{{ status.git.last_commit.subject }}</span><span class="x">{{ status.git.last_commit.date }}</span></dd>
              <dd v-else>—</dd>
              <dt>uncommitted</dt><dd>{{ status.git.dirty }}<span class="x" v-if="status.git.dirty">dirty</span></dd>
            </dl>
          </section>
```

5. **Agents card** — replace the existing agents card body to show cost per role:
```html
          <section class="kpi kpi-wide">
            <h2 class="kpi-title">agents — cost</h2>
            <table class="cost-tbl">
              <thead><tr><th>role</th><th>model</th><th>tokens</th><th>actual</th><th>share</th></tr></thead>
              <tbody>
                <tr v-for="a in status.agents" :key="a.role">
                  <td>{{ a.role }}</td>
                  <td class="m">{{ a.model ?? '—' }}</td>
                  <td>{{ fmtTokens(a.tokens) }}</td>
                  <td>{{ fmtCost(a.cost_actual) }}</td>
                  <td class="dim">{{ fmtCost(a.cost_share) }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="kpi kpi-wide">
            <h2 class="kpi-title">models — cost</h2>
            <table class="cost-tbl">
              <thead><tr><th>model</th><th>tokens</th><th>runs</th><th>actual</th><th>share</th></tr></thead>
              <tbody>
                <tr v-for="m in status.models" :key="m.model">
                  <td class="m">{{ m.model }}</td>
                  <td>{{ fmtTokens(m.tokens) }}</td>
                  <td>{{ m.sessions }}</td>
                  <td>{{ fmtCost(m.cost_actual) }}</td>
                  <td class="dim">{{ fmtCost(m.cost_share) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="footnote">
              actual = summed provider billing per agent call · share = each run's cost split by
              token count — the gap reflects models with different $/token.
            </p>
          </section>
```

6. **Grid + styles** — the `.cards` grid currently wraps KPI cards; make agents/models span wider and add the table + footnote styles:

```css
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.kpi-wide { grid-column: span 2; }
.cost-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.cost-tbl th {
  text-align: left; font-weight: 500; color: var(--faint);
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 2px 8px 6px 0; border-bottom: 1px solid var(--border-soft);
}
.cost-tbl td { padding: 5px 8px 5px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.cost-tbl td.m { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--cyan); font-size: 12px; }
.cost-tbl td.dim { color: var(--faint); }
.footnote { margin: 10px 0 0; font-size: 11px; color: var(--faint); line-height: 1.5; }
```

7. **Contributions heatmap** — add a section between trends and tickets:

```html
        <section v-if="status.contributions.length" class="hm-sec">
          <h2 class="kpi-title">contributions</h2>
          <ContributionsHeatmap :days="status.contributions" />
        </section>
```

And style `.hm-sec { margin-bottom: 20px; }` (the `.kpi-title` margin handles the heading gap).

8. **Empty-git guard**: the repo card and heatmap render fine with zeroed git — the `v-if="status.contributions.length"` guards the heatmap; the repo card shows zeros/dashes when `status.git.commits === 0`. If you prefer, hide the repo card when `status.git.commits === 0 && status.git.last_commit === null` — keep it simple: always render, zeros are honest.

- [ ] **Step 2: Verify typecheck + build**

Run: `cd src/sssf/apps/visualizer && bun run typecheck && bun run build`
Expected: both pass.

- [ ] **Step 3: Smoke test on a throwaway port (do NOT touch :4600)**

```bash
cd src/sssf/apps/visualizer && PORT=4696 bun run server/index.ts &
curl -s "localhost:4696/api/projects/inkwell/status?window=30" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('agents:', [(a['role'], round(a['cost_actual'],4)) for a in d['agents']])
print('models:', [(m['model'], round(m['cost_actual'],4)) for m in d['models']])
print('git:', d['git']['commits'], d['git']['current_branch'], d['git']['last_commit'])
print('contributions:', len(d['contributions']))
"
lsof -tnP -iTCP:4696 | xargs kill
```
Expected: real inkwell numbers (reviewer ≈ $0.746, gpt-5.5 ≈ $9.37, git commits 57, contributions 364).

- [ ] **Step 4: Full suite**

Run: `cd src/sssf/apps/visualizer && bun test && bun run lint`
Expected: all pass (lint warnings pre-existing only).

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/src/components/StatusPage.vue
git commit -m "feat: status page — full width, repo card, cost tables with footnote, heatmap"
```

---

### Task 7: Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`

- [ ] **Step 1: README**

In the `## Visualizer` section (added in v1), extend the status dashboard bullet:

```markdown
- **Status dashboard** — per-project KPIs: runs/health, cost & tokens (actual
  billing + token-share per agent and per model), quality gates, per-agent
  models, git repo stats + yearly contributions heatmap, trend charts
  (7/30/90d), ticket pipeline. Served at `#/status`.
```

- [ ] **Step 2: Revisions index**

In `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`, extend the status page entry (or add a line):

```markdown
- 2026-08-15 — [status page design rev 2](2026-08-15-status-page-design.md): cost
  attribution (actual + token-share per agent/model), git stats, contributions heatmap.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md
git commit -m "docs: status page v2 — cost attribution, git stats, contributions"
```

---

## Self-Review

- **Spec coverage (Rev 2):** cost per agent/model ✓ (Task 1), actual + token-share with footnote ✓ (Tasks 1, 6), dynamic agents ✓ (Task 1), git stats (6 items) ✓ (Task 2), contributions heatmap ✓ (Tasks 2, 5), full width ✓ (Task 6), overflow fix ✓ (Task 6), graceful non-git ✓ (Tasks 2, 3), tests ✓ (Tasks 1-3), docs ✓ (Task 7).
- **Type consistency:** `GitStats`/`ContributionDay` defined in `server/git.ts` (Task 2) and mirrored in `api.ts` (Task 4); `StatusResponse.models/git/contributions` added in Task 1/3 and consumed in Task 6; `AgentStat`/`ModelStat` fields match between Task 1 and Task 4.
- **Reconciliation invariant:** fixture agent_end costs sum to session totals (0.75) — pinned by the `reduce` assertions in Task 1; any double-count or join bug fails loudly.
- **Placeholder scan:** all steps carry real code or exact commands; the two intentional notes (git unavailable → BLOCKED; 53-column grid) are explicit, not placeholders.
- **Known risk:** Task 1's per-model join requires `agent_sessions` rows for every agent_end event's phase owner; if the fixture or real data ever lacks them, that model's rows are silently excluded (COUNT DISTINCT guards). The inkwell verification showed all rows join.
