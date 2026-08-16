# Mission Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cross-project Mission Control cockpit — the `#/` landing — that monitors every registered project, sandbox, session, ticket, and the healer daemon in one view, with stop/restart/run/refresh/add/remove/heal controls.

**Architecture:** One server-side `/api/cockpit` aggregation endpoint (read-only over the registry, per-project dbs, sandbox dirs, heal files) polled by a new `MissionControl.vue` landing page. Controls are POST routes that shell to the sssf CLI — the CLI stays the only writer. Routing restructures to `#/` (cockpit) + `#/p/:project[/:tab|/s/:adwId]` (drill-down).

**Tech Stack:** Python 3 (CLI: `init --refresh --auto`, healer accessors), bun:sqlite + Bun.serve (server), Vue 3 + lucide-vue-next + hand-rolled CSS (page), bun test + pytest (tests).

**Spec:** `docs/superpowers/specs/2026-08-16-mission-control-design.md`

## Global Constraints

- The CLI is the **only writer**; the server never writes project dbs (read-only connections via `openReadonly`).
- A project whose db read fails renders with **zeros + stale flag**, never fails the cockpit.
- Controls shell to the CLI exactly like the existing `/api/projects/:project/sessions/:adw_id/stop|restart` routes.
- Icons are lucide-vue-next SVGs (no emojis): `Square` (stop), `RotateCw` (restart), `RefreshCw` (refresh), `Plus`/`Trash2` (add/remove), `HeartPulse` (heal), `Activity` (feed).
- `#/` must keep working when the registry is empty (empty-state with an add-project input).
- Dates: tracer writes UTC ISO; compare with `date('now')` (UTC) — never local.

---

### Task 1: `sssf init --refresh --auto` (non-interactive accept-all)

**Files:**
- Modify: `src/sssf/commands/init.py` (`run`, `_copy_tree`)
- Modify: `src/sssf/cli.py` (init subparser: add `--auto`)
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: `_copy_tree(src, dst, force=False, confirm=False)` — existing; `confirm=True` prompts y/N/a per existing file.
- Produces: `run(root, *, refresh=False, force=False, auto=False) -> int` — `auto=True` answers yes to every prompt without reading stdin. CLI: `sssf init --refresh --auto`.

- [ ] **Step 1: Write the failing tests**

```python
def test_refresh_auto_accepts_all_without_stdin(tmp_path, monkeypatch):
    from sssf.commands import init
    # a file that already exists with different content
    target = tmp_path / "adws" / "adw_simple_sdlc.py"
    target.parent.mkdir(parents=True)
    target.write_text("OLD")
    # input() must NEVER be called — a prompt would raise here
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted!")))
    rc = init.run(tmp_path, refresh=True, auto=True)
    assert rc == 0
    assert target.read_text() != "OLD"          # overwritten by the template
    assert (tmp_path / "adws" / "adw_sssf_config" / "ticketing.yaml").exists()

def test_refresh_without_auto_still_prompts(tmp_path, monkeypatch):
    from sssf.commands import init
    target = tmp_path / "adws" / "adw_simple_sdlc.py"
    target.parent.mkdir(parents=True)
    target.write_text("OLD")
    calls = []
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(prompt) or "n")
    init.run(tmp_path, refresh=True)
    assert calls and "overwrite" in calls[0]   # the y/N/a prompt ran
    assert target.read_text() == "OLD"         # answered 'n'
```

- [ ] **Step 2: Run the tests, verify they fail** — `uv run pytest tests/test_init.py -q` → the first test fails (no `auto` kwarg).

- [ ] **Step 3: Implement `auto` in init.py**

`_copy_tree(src, dst, *, force=False, confirm=False, auto=False)`: replace the per-file branch

```python
if target.exists() and not force and not (confirm and ask(rel / item.name)):
    continue
```

with

```python
if target.exists() and not force and not (confirm and (auto or ask(rel / item.name))):
    continue
```

and `run(root, *, refresh=False, force=False, auto=False)` passes `auto=auto` to both `_copy_tree` calls (adws + prompt_engineering).

- [ ] **Step 4: Wire the CLI flag** — in `cli.py`'s init subparser add `p_init.add_argument("--auto", action="store_true", help="--refresh without prompts (accept all)")` and pass `auto=a.auto` to `init.run`.

- [ ] **Step 5: Run the full suite** — `uv run pytest tests/test_init.py -q` (both pass) then `uv run pytest -q` (no regressions).

- [ ] **Step 6: Commit** — `git add src/sssf/commands/init.py src/sssf/cli.py tests/test_init.py && git commit -m "feat(init): --refresh --auto — non-interactive accept-all"`

---

### Task 2: healer public read-only accessors

**Files:**
- Modify: `src/sssf/healer.py` (`_state` → public `state`, add `log_tail`, `heal_summary`)
- Test: `tests/test_healer.py`

**Interfaces:**
- Consumes: `STATE_DIR`, `_log_file()` — existing module state.
- Produces (public, read-only):
  - `state() -> dict` — the parsed heal-state.json (`{"restarts": {adwId: n}}`), never raises ({} on unreadable).
  - `log_tail(n: int = 5) -> list[str]` — last n non-empty log lines.
  - `heal_summary() -> dict` — `{"running": bool, "pid": int|None, "logTail": list[str], "restarts": {adwId: n}}`.
  - Keep `_state` private helpers working (rename internal uses).

- [ ] **Step 1: Write the failing tests**

```python
def test_heal_summary_accessors(tmp_path, monkeypatch):
    import sssf.healer as h
    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    (tmp_path / "heal-state.json").write_text('{"restarts": {"a1": 2, "b2": 1}}')
    (tmp_path / "heal.log").write_text("line1\nline2\nline3\n")
    s = h.heal_summary()
    assert s["restarts"] == {"a1": 2, "b2": 1}
    assert s["logTail"] == ["line1", "line2", "line3"]
    assert s["running"] is False and s["pid"] is None

def test_heal_summary_missing_files(tmp_path, monkeypatch):
    import sssf.healer as h
    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    s = h.heal_summary()
    assert s["restarts"] == {} and s["logTail"] == [] and s["running"] is False
```

- [ ] **Step 2: Verify they fail** — `uv run pytest tests/test_healer.py::test_heal_summary_accessors -q` → AttributeError (`_state` is private; no `heal_summary`).

- [ ] **Step 3: Implement** — rename `_state()` → `state()` (update `_save_state`/`_restart_count` call sites), add:

```python
def log_tail(n: int = 5) -> list[str]:
    try:
        lines = [l for l in _log_file().read_text().splitlines() if l.strip()]
        return lines[-n:]
    except OSError:
        return []

def heal_summary() -> dict:
    pid = running_pid()
    return {"running": pid is not None, "pid": pid,
            "logTail": log_tail(), "restarts": state().get("restarts", {})}
```

- [ ] **Step 4: Run healer tests** — `uv run pytest tests/test_healer.py -q` → all pass (existing + 2 new).

- [ ] **Step 5: Commit** — `git commit -am "feat(healer): public heal_summary()/state()/log_tail() read accessors"`

---

### Task 3: server aggregate — `computeCockpit`

**Files:**
- Create: `src/sssf/apps/visualizer/server/cockpit.ts`
- Test: `src/sssf/apps/visualizer/server/cockpit.test.ts`

**Interfaces:**
- Consumes: `ProjectRegistry` from `./registry.ts` (`list()`, `dbFor(name)`), `openReadonly` from `./db.ts`, shared types from `../shared/types.ts` (Task 4 adds the cockpit types — implement the JS side with local types here, swap to shared in Task 4).
- Produces:
  - `export interface CockpitDeps { registry: ProjectRegistry; sssfHome?: string; dockerPs?: () => Promise<string>; }`
  - `export async function computeCockpit(deps: CockpitDeps): Promise<CockpitData>` — one poll's full document. `dockerPs` defaults to a real `Bun.spawn(["docker","ps","-a","--filter","name=sssf-","--format","{{.Names}} {{.Status}}"])`; tests inject a fake.
  - `export function todayCost(db): number`, `export function activityFeed(db): ActivityItem[]` — small exported helpers for unit tests.

Schema facts (from `tracer.py`): `sessions(adw_id, adw_name, request, status, engineer, started_at, ended_at, total_tokens, total_cost, archived)`; `events(event_id, adw_id, phase_id, parent_id, type, name, payload_json, tokens, started_at, ended_at)`; `tickets(id, provider, external_id, title, description, status, prompt_file, adw_id, source_url, created_at, updated_at)`; `phases(phase_id, adw_id, status, …)`.

- [ ] **Step 1: Write the failing tests** — fixture builder:

```ts
import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { ProjectRegistry } from "./registry.ts";
import { computeCockpit } from "./cockpit.ts";

function fakeDb(dir: string): string {
  const path = join(dir, "adws", "adw_data", "sssf.db");
  mkdirSync(join(dir, "adws", "adw_data"), { recursive: true });
  const db = new Database(path);
  db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, started_at TEXT,
          ended_at TEXT, total_cost REAL, total_tokens INTEGER, archived INTEGER DEFAULT 0)`);
  db.run(`CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, started_at TEXT)`);
  db.run(`CREATE TABLE events (event_id TEXT PRIMARY KEY, adw_id TEXT, type TEXT, started_at TEXT)`);
  db.run(`CREATE TABLE tickets (id TEXT PRIMARY KEY, status TEXT, adw_id TEXT, updated_at TEXT)`);
  return path;
}

function makeEnv() {
  const root = mkdtempSync(join(tmpdir(), "cockpit-"));
  const home = join(root, ".sssf"); mkdirSync(home, { recursive: true });
  const regPath = join(home, "projects.json");
  const a = join(root, "proj-a"); mkdirSync(a, { recursive: true }); fakeDb(a);
  const b = join(root, "proj-b"); mkdirSync(b, { recursive: true }); fakeDb(b);
  // project A: one running session + one success + a ticket backlog + events
  const da = new Database(join(a, "adws", "adw_data", "sssf.db"));
  da.run(`INSERT INTO sessions VALUES ('run1','running','2026-08-16T10:00:00',NULL,0.5,100,0)`);
  da.run(`INSERT INTO sessions VALUES ('done1','success','2026-08-16T09:00:00','2026-08-16T09:30:00',1.2,200,0)`);
  da.run(`INSERT INTO phases VALUES ('ph1','run1','running','2026-08-16T10:00:00')`);
  da.run(`INSERT INTO events VALUES ('e1','run1','agent_end','2026-08-16T10:05:00')`);
  da.run(`INSERT INTO tickets VALUES ('internal:t1','backlog',NULL,'2026-08-16T08:00:00')`);
  da.close();
  mkdirSync(join(home, "sandboxes", "proj-a", "run1"), { recursive: true });
  writeFileSync(join(home, "heal-state.json"), '{"restarts": {"run1": 2}}');
  writeFileSync(join(home, "heal.log"), "h1\nh2\nh3\nh4\nh5\nh6\n");
  writeFileSync(regPath, JSON.stringify({ projects: [
    { name: "proj-a", root: a, db: join(a, "adws", "adw_data", "sssf.db"), lastRun: null },
    { name: "proj-b", root: b, db: join(b, "adws", "adw_data", "sssf.db"), lastRun: null },
  ]}));
  return { root, home, registry: new ProjectRegistry(regPath) };
}

describe("computeCockpit", () => {
  test("aggregates kpis, projects, running, heal, activity", async () => {
    const env = makeEnv();
    const data = await computeCockpit({
      registry: env.registry,
      sssfHome: env.home,
      dockerPs: async () => "sssf-run1 Up 2 minutes\nsssf-orphanx Up 1 hour",
    });
    expect(data.kpis.runningSessions).toBe(1);
    expect(data.kpis.liveContainers).toBe(2);
    expect(data.kpis.sandboxWorktrees).toBe(1);
    expect(data.kpis.ticketsInFlight).toBe(0);
    expect(data.kpis.costTodayUsd).toBeGreaterThan(0);
    expect(data.kpis.healRunning).toBe(false);
    const pa = data.projects.find((p) => p.name === "proj-a")!;
    expect(pa.sessionsRunning).toBe(1);
    expect(pa.sessionsToday).toBe(2);
    expect(pa.ticketsBacklog).toBe(1);
    expect(pa.containers).toBe(1);          // sssf-run1 owned by proj-a
    expect(data.projects.find((p) => p.name === "proj-b")!.containers).toBe(0);
    expect(data.running[0]!.adwId).toBe("run1");
    expect(data.running[0]!.project).toBe("proj-a");
    expect(data.running[0]!.phase).toBe("ph1");
    expect(data.heal.restarts).toEqual({ run1: 2 });
    expect(data.heal.logTail).toEqual(["h3", "h4", "h5", "h6"]);
    expect(data.activity[0]!.event).toBe("agent_end");
    rmSync(env.root, { recursive: true, force: true });
  });

  test("broken db renders zeros + stale, never throws", async () => {
    const env = makeEnv();
    rmSync(join(env.root, "proj-a", "adws", "adw_data", "sssf.db"));
    const data = await computeCockpit({ registry: env.registry, sssfHome: env.home, dockerPs: async () => "" });
    const pa = data.projects.find((p) => p.name === "proj-a")!;
    expect(pa.stale).toBe(true);
    expect(pa.sessionsRunning).toBe(0);
    expect(data.projects.length).toBe(2);   // both projects still listed
    rmSync(env.root, { recursive: true, force: true });
  });
});
```

- [ ] **Step 2: Run, verify fail** — `bun test server/cockpit.test.ts` → "cannot find module cockpit.ts".

- [ ] **Step 3: Implement `computeCockpit`** — mirror the status.ts SQL style (`SUM(status='running')`):

```ts
export async function computeCockpit(deps: CockpitDeps): Promise<CockpitData> {
  const home = deps.sssfHome ?? process.env.SSSF_HOME ?? join(homedir(), ".sssf");
  const projects = deps.registry.list();
  const containers = await parseContainers(deps.dockerPs ?? realDockerPs); // [{adwId, running}]
  const heal = readHeal(home);                       // pid/kill(0) alive check, log tail, restarts
  const out: CockpitData = { generatedAt: new Date().toISOString(), kpis: {...}, projects: [], running: [], heal, activity: [] };
  let activity: ActivityItem[] = [];
  const known: Set<string> = new Set();              // adwId -> owning project built below
  for (const p of projects) {
    const row = projectRow(deps.registry, p.name, home, containers, known); // zeros + stale on failure
    out.projects.push(row);
    out.kpis.runningSessions += row.sessionsRunning;
    out.kpis.sandboxWorktrees += row.worktrees;
    out.kpis.ticketsInFlight += row.ticketsInFlight;
    out.kpis.costTodayUsd += row.costTodayUsd;
    out.running.push(...row._running);               // internal per-project running detail
    activity = activity.concat(row._activity);
  }
  out.kpis.liveContainers = containers.length;
  out.kpis.orphanContainers = containers.filter((c) => !known.has(c.adwId)).length;
  out.activity = activity.sort((x, y) => y.ts.localeCompare(x.ts)).slice(0, 30);
  return out;
}
```

`projectRow` per project (open `openReadonly(db)`; any sqlite error → zeros + `stale: true`):
- `sessionsRunning = SUM(status='running')`, `sessionsToday = COUNT(*) WHERE date(started_at)=date('now')`, `sessionsFailedToday = SUM(status='fail' AND date(started_at)=date('now'))`
- `costTodayUsd = COALESCE(SUM(total_cost),0) WHERE date(started_at)=date('now')`
- `ticketsBacklog = SUM(status='backlog')`, `ticketsInFlight = SUM(status IN ('starting','running'))`
- `lastActivity = MAX(started_at)` from events (null → `lastRun` from the registry)
- `worktrees = count dirs in <home>/sandboxes/<name>`
- `containers = containers.filter(c => knownAdwIds.has(c.adwId) || sandboxDirs.has(c.adwId)).length` — build `known` from sessions' adw_ids ∪ sandbox dir names; register each container's adwId into `known`
- `_running`: for each running session, the latest phase (`SELECT phase_id, status FROM phases WHERE adw_id=? ORDER BY started_at DESC LIMIT 1`) + `ageSec = (Date.now()/1000) - Date.parse(started_at)/1000`; project + adwId + phase + phaseStatus + ageSec
- `_activity`: `SELECT adw_id, started_at, type FROM events ORDER BY started_at DESC LIMIT 30` → `{project, adwId, ts, event}`

`readHeal(home)`: pid from `heal.pid` (parse int), `running = pid>0 && alive(pid)` via `process.kill(pid, 0)` in try/catch (ESRCH/EPERM handling: EPERM counts as alive); `logTail` = last 5 non-empty lines of `heal.log`; `restarts` from `heal-state.json` (parse; {} on error).

- [ ] **Step 4: Run tests, verify pass** — `bun test server/cockpit.test.ts` → 2 pass.

- [ ] **Step 5: Commit** — `git add src/sssf/apps/visualizer/server/cockpit.ts src/sssf/apps/visualizer/server/cockpit.test.ts && git commit -m "feat(viz): computeCockpit — cross-project aggregate (kpis, projects, running, heal, activity)"`

---

### Task 4: shared cockpit types + `/api/cockpit` routes + control handlers

**Files:**
- Modify: `src/sssf/apps/visualizer/shared/types.ts`
- Modify: `src/sssf/apps/visualizer/server/cockpit.ts` (add `handleControl` + wire types from shared)
- Modify: `src/sssf/apps/visualizer/server/index.ts` (register routes)
- Test: extend `server/cockpit.test.ts`

**Interfaces:**
- Consumes: Task 3's `computeCockpit(deps)`; existing `json`, `notFound`, `param`, `safely`, `scoped` helpers in index.ts; existing `/api/projects/:project/...` stop/restart/tickets/:id/run routes (reused as-is — the cockpit page calls them, no new endpoints for those controls).
- Produces (shared/types.ts):
  - `CockpitKpis`, `CockpitProject`, `RunningSession`, `HealSummary`, `ActivityItem`, `CockpitData` (exact fields as the spec's JSON block).
  - `ControlResult = { ok: boolean; output?: string; error?: string }`.
  - `export async function handleControl(kind: "refresh" | "add" | "remove", params: { project?: string; root?: string; confirm?: boolean }, deps: CockpitDeps & { spawnCli?: (args: string[]) => Promise<{ code: number; out: string }> }): Promise<ControlResult>` — `spawnCli` defaults to `Bun.spawn(["sssf", ...args])` capture; tests inject a fake.
- Routes registered in index.ts (all `safely`-wrapped, all POST):
  - `POST /api/cockpit/projects/:project/refresh` → `handleControl("refresh", {project}, deps)` with `spawnCli((args) => ["sssf","init","--refresh","--auto","--project", root, ...args])` — actually pass the root resolved from the registry.
  - `POST /api/cockpit/projects/add` (body `{root}`) → validate dir exists + has `adws/` → `spawnCli(["projects","add", root])`.
  - `POST /api/cockpit/projects/:project/remove` (body `{confirm: true}` required) → `spawnCli(["projects","remove", name])`.
  - `POST /api/cockpit/heal/start` · `POST /api/cockpit/heal/stop` → `spawnCli(["heal","start"|"stop"])`.
  - `GET /api/cockpit` → `computeCockpit({registry: projects})`.

- [ ] **Step 1: Add the shared types + write the failing control tests**

```ts
import { handleControl } from "./cockpit.ts";
test("add validates the dir before spawning", async () => {
  const env = makeEnv();
  const calls: string[][] = [];
  const res = await handleControl("add", { root: join(env.root, "no-such") },
    { registry: env.registry, sssfHome: env.home, spawnCli: async (a) => { calls.push(a); return { code: 0, out: "ok" }; } });
  expect(res.ok).toBe(false);
  expect(calls.length).toBe(0);                       // never spawned
  const good = join(env.root, "proj-b");
  const res2 = await handleControl("add", { root: good },
    { registry: env.registry, sssfHome: env.home, spawnCli: async (a) => { calls.push(a); return { code: 0, out: "ok" }; } });
  expect(res2.ok).toBe(true);
  expect(calls[0]).toEqual(["projects", "add", good]);
  rmSync(env.root, { recursive: true, force: true });
});

test("remove requires confirm", async () => {
  const env = makeEnv();
  const calls: string[][] = [];
  const spawn = async (a: string[]) => { calls.push(a); return { code: 0, out: "" }; };
  const no = await handleControl("remove", { project: "proj-a" }, { registry: env.registry, sssfHome: env.home, spawnCli: spawn });
  expect(no.ok).toBe(false);
  const yes = await handleControl("remove", { project: "proj-a", confirm: true }, { registry: env.registry, sssfHome: env.home, spawnCli: spawn });
  expect(yes.ok).toBe(true);
  expect(calls[0]).toEqual(["projects", "remove", "proj-a"]);
  rmSync(env.root, { recursive: true, force: true });
});

test("refresh spawns init --refresh --auto at the project root", async () => {
  const env = makeEnv();
  const calls: string[][] = [];
  const res = await handleControl("refresh", { project: "proj-a" },
    { registry: env.registry, sssfHome: env.home, spawnCli: async (a) => { calls.push(a); return { code: 0, out: "" }; } });
  expect(res.ok).toBe(true);
  expect(calls[0]).toEqual(["init", "--refresh", "--auto", "--project", join(env.root, "proj-a")]);
  rmSync(env.root, { recursive: true, force: true });
});
```

- [ ] **Step 2: Run, verify fail** — `bun test server/cockpit.test.ts` → `handleControl` missing.

- [ ] **Step 3: Implement `handleControl`** — validations exactly as the tests assert; `spawnCli` default uses `Bun.spawn(["sssf", ...args], { stdout: "pipe", stderr: "pipe" })` collecting output + exit code. On non-zero exit → `{ ok: false, error: out }`.

- [ ] **Step 4: Register the routes in index.ts** — GET `/api/cockpit` + the five POST routes using the existing `safely()` guard; resolve project roots via the registry like `projectRoot()` does; add-project body parsing `await req.json()` with try/catch → 400 on malformed JSON.

- [ ] **Step 5: Run server tests** — `bun test server/` → all pass (existing + 3 new).

- [ ] **Step 6: Commit** — `git commit -am "feat(viz): /api/cockpit routes — GET aggregate + refresh/add/remove/heal controls"`

---

### Task 5: frontend API client

**Files:**
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`
- Test: none (thin fetch wrappers; covered by typecheck + the manual E2E) — verify with `bun run typecheck`.

**Interfaces:**
- Consumes: shared cockpit types (Task 4); existing `getJson` pattern.
- Produces:
  - `fetchCockpit(): Promise<CockpitData>` — GET `/api/cockpit`.
  - `refreshProject(name: string): Promise<ControlResult>` — POST `/api/cockpit/projects/${name}/refresh`.
  - `addProject(root: string): Promise<ControlResult>` — POST `/api/cockpit/projects/add` body `{root}`.
  - `removeProject(name: string, confirm: boolean): Promise<ControlResult>` — POST `/api/cockpit/projects/${name}/remove` body `{confirm}`.
  - `healControl(action: "start" | "stop"): Promise<ControlResult>` — POST `/api/cockpit/heal/${action}`.

- [ ] **Step 1: Implement** — add the five functions (POST bodies with `headers: {"content-type": "application/json"}`).

- [ ] **Step 2: Typecheck** — `bun run typecheck` passes.

- [ ] **Step 3: Commit** — `git commit -am "feat(viz): cockpit api client (fetchCockpit + refresh/add/remove/heal)"`

---

### Task 6: router restructure — `#/` cockpit + `#/p/:project` drill-down

**Files:**
- Modify: `src/sssf/apps/visualizer/src/lib/router.ts`
- Modify: `src/sssf/apps/visualizer/src/App.vue` (view computed + tabs + picker wiring)
- Test: `src/sssf/apps/visualizer/src/lib/router.test.ts` (new)

**Interfaces:**
- Consumes: `useProjects()` from api.ts (`selectedProject`).
- Produces:
  - `export interface Route { cockpit: boolean; project: string | null; tab: "status" | "board" | "sessions" | "archived" | null; adwId: string | null; phaseId: string | null }`
  - `hrefFor(opts?: { project?: string | null; tab?: string | null; adwId?: string | null; phaseId?: string | null }): string` — `#/` for cockpit, `#/p/<project>`, `#/p/<project>/<tab>`, `#/p/<project>/s/<adwId>[/<phaseId>]`.
  - `navigate(opts)` — sets `window.location.hash`.
  - `parse()` mapping:
    - `#/`, `#/cockpit` → `{cockpit: true, ...}`
    - `#/p/<p>` → tab `status`
    - `#/p/<p>/<board|sessions|archived>` → tab
    - `#/p/<p>/s/<id>[/<phase>]` → trace
    - legacy `#/<id>` (8-hex or non-tab segment) → trace in `selectedProject` (null project → first known project)
    - legacy `#/status|board|sessions|archived` → tab in `selectedProject` (null → cockpit)
  - App.vue: `view` computed returns `"cockpit" | "status" | "board" | "list" | "archived" | "trace"`; tabs = cockpit + status/board/sessions/archived (per-project tabs render but are inert until a project is picked; clicking them with no project → cockpit); picker onSelect → `navigate({project: name, tab: "status"})`; MissionControl renders for cockpit.

- [ ] **Step 1: Write the failing router tests**

```ts
import { describe, expect, test } from "bun:test";
import { parseHash, hrefFor } from "./router.ts";

describe("router", () => {
  test("cockpit routes", () => {
    expect(parseHash("#/")).toEqual({ cockpit: true, project: null, tab: null, adwId: null, phaseId: null });
    expect(parseHash("#/cockpit").cockpit).toBe(true);
  });
  test("per-project drill-down", () => {
    const r = parseHash("#/p/inkwell");
    expect(r.project).toBe("inkwell");
    expect(r.tab).toBe("status");
    expect(parseHash("#/p/inkwell/board").tab).toBe("board");
    expect(parseHash("#/p/inkwell/s/abc123").adwId).toBe("abc123");
    expect(parseHash("#/p/inkwell/s/abc123/ph2").phaseId).toBe("ph2");
  });
  test("hrefFor round-trips", () => {
    expect(hrefFor({})).toBe("#/");
    expect(hrefFor({ project: "inkwell", tab: "board" })).toBe("#/p/inkwell/board");
    expect(hrefFor({ project: "inkwell", adwId: "abc" })).toBe("#/p/inkwell/s/abc");
  });
});
```

(For testability, export `parseHash(hash: string)` that does the pure parse; the module keeps a `ref` updated on hashchange as today.)

- [ ] **Step 2: Run, verify fail** — `bun test src/lib/router.test.ts`.

- [ ] **Step 3: Implement the router** — new Route interface + parseHash + hrefFor/navigate; keep `phaseCrumb`. Backwards-compat branches per the mapping above (legacy adwId: `/^[0-9a-f]{8,}$/`-style or simply "anything not a known word is a trace id").

- [ ] **Step 4: Rewire App.vue** — view computed from `route.cockpit`/`route.project`/`route.tab`/`route.adwId`; add the cockpit tab (`HeartPulse` icon); per-project tab click handlers navigate to `#/p/<project>/<tab>`; when `route.project` is null the status/board/sessions/archived tabs show but `onProjectSelect` is what activates them. `onProjectSelect(name)` → `setProject(name)` + `navigate({project: name, tab: "status"})`.

- [ ] **Step 5: Verify** — `bun test` (16 + router tests pass), `bun run typecheck`.

- [ ] **Step 6: Commit** — `git commit -am "feat(viz): router — #/ cockpit landing + #/p/:project drill-down (legacy routes preserved)"`

---

### Task 7: MissionControl.vue — the cockpit page

**Files:**
- Create: `src/sssf/apps/visualizer/src/components/MissionControl.vue`
- Modify: `src/sssf/apps/visualizer/src/App.vue` (import + render)

**Interfaces:**
- Consumes: Task 5 api client; `hrefFor`/`navigate` from router.ts; lucide icons `HeartPulse`, `Square`, `RotateCw`, `RefreshCw`, `Plus`, `Trash2`, `Activity`, `LoaderCircle`; existing CSS vars (`--text`, `--purple`, etc. — the kanban/status pages' palette).
- Produces: a self-contained page component with `onMounted` + 8s `setInterval` polling `fetchCockpit()`; exposes nothing.

- [ ] **Step 1: Write the page** — sections top→bottom:

1. **KPI strip** (6 chips + heal chip): running sessions, live containers (+orphan hint), sandbox worktrees, tickets in flight, cost today (USD, 2dp), heal status chip (`HeartPulse` icon, green "running · pid N" / gray "stopped", with start/stop buttons).
2. **Projects table**: one row per project — name (link → `navigate({project, tab:"status"})`), root (truncated), sessions running / today, tickets in flight / backlog, containers, worktrees, cost today, last activity (relative), stale flag (`title="db unreadable or idle"`). Row actions: refresh (`RefreshCw`, spinner while pending, success/error inline note), remove (`Trash2`, `window.confirm("remove <name> from the registry?")` → `removeProject(name, true)`).
3. **Running-now strip**: every `data.running` item — project chip, adw_id, phase, age (`mm:ss`), **stop** (`Square`) + **restart** (`RotateCw`) buttons (disabled while a control is pending for that id; on error show the message inline under the row). Empty → "nothing running".
4. **Healer panel**: running status, log tail (`<pre>` last 5 lines), restart budgets (`adw_id → n/3`).
5. **Recent activity**: `data.activity` — `ts` (HH:MM) · project · adw_id · event type; empty state.
6. **Add project**: root-path input + Add button (`Plus`) → `addProject(root)`; error inline.

Empty registry: KPI strip hidden; big empty-state with the add-project form ("no registered projects — add one"). Control failures: a transient inline note line per control (like the sweep note in App.vue).

Polling: `let timer: ReturnType<typeof setInterval>`; `onMounted` → `void refresh()`; `onBeforeUnmount` → clearInterval. `refresh()` guards concurrent polls (`if (loading) return`).

- [ ] **Step 2: Register in App.vue** — import + `<MissionControl v-if="view === 'cockpit'" />` in the main slot; the cockpit tab first in the tab bar (`HeartPulse`, active when `view === 'cockpit'`).

- [ ] **Step 3: Verify gates** — `bun test` (all pass), `bun run typecheck`, `bun run build` (dist rebuilt).

- [ ] **Step 4: Commit** — `git commit -am "feat(viz): MissionControl.vue — cross-project cockpit page (kpis, projects, running strip, healer panel, activity, add-project)"`

---

### Task 8: end-to-end verification

**Files:** none (manual + docs).

- [ ] **Step 1: Restart the viz** — `sssf viz stop && sssf viz start` (healer auto-starts).

- [ ] **Step 2: Manual pass over the live cockpit** — open `http://localhost:4600`:
  - `#/` renders MissionControl with real kpis (inkwell + any sbx-* projects in the registry).
  - Projects table lists every registered project; clicking one lands on `#/p/<name>` (status page).
  - Start a run from a project's board, verify it appears in the running-now strip; **stop** it, verify the session finalizes; **restart** one, verify it re-runs.
  - **refresh** a project (spinner + success note); **add** `/tmp/sbx-heal` (appears in the table), **remove** it (confirm dialog).
  - **heal stop** → chip flips to stopped; **heal start** → running.
  - Legacy URL `#/abc123` still opens a trace.
  - Resize window — no horizontal scroll (page scrolls naturally).

- [ ] **Step 3: Full gates** — `uv run pytest -q` (92 + new init/healer tests), `bun test`, `bun run typecheck`, `bun run build`.

- [ ] **Step 4: Commit** — any fixes made during verification + `git commit -am "chore(viz): mission control — verified end-to-end"` (or individual fix commits).

---

## Self-Review notes

- **Spec coverage:** KPI strip → Task 3/7 · projects table → Task 3/7 · running strip + stop/restart → Task 3/7 (reuses existing `/api/projects/:project/sessions/:adw_id/stop|restart`) · refresh → Tasks 1+4+5 · add/remove → Task 4/5 (removal of registry entry via `sssf projects remove`) · heal start/stop → Tasks 2+4/5 · activity feed → Task 3/7 · routing `#/` + `#/p/:project` → Task 6 · stale/zeros on broken db → Task 3 test · empty registry → Task 7 empty state.
- **`sssf projects remove <name>`** — confirmed to exist in `commands/misc.py` (usage text at line 23); `projects add <root>` used by the E2E scripts.
- **Cost today** — `sessions.total_cost` summed for `date(started_at) = date('now')` (UTC), consistent with status.ts's column usage.
- **Container→project mapping** — via each project's sessions adw_ids + sandbox dir names; unknown adw_ids surface as `kpis.orphanContainers`.
- **Legacy routing** — old `#/<adwId>` and `#/board`-style hashes still work (Task 6 backwards-compat branches), so bookmarks survive.
