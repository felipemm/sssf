# SSSF Global CLI — Revisions (post-implementation hardening)

**Status:** implemented · **Date:** 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` · **Plan:** `2026-08-15-sssf-global-cli-revisions.md`

This spec records every change made to the SSSF global CLI **after** the
original design was implemented (`4ebb094..388288d` in `~/dev/lab/mvp/sssf`),
the bugs it found in the field, and the operational semantics it added. The
original design remains the authoritative architecture; this document is the
delta.

## Summary of changes

| Area | Change | Why |
|---|---|---|
| `sssf init` | gitignore entries now include `adws/adw_data/sssf.db-wal` + `sssf.db-shm` | Tracked WAL sidecars make agents treat them as clutter and git-checkout them over a live db → `SQLITE_IOERR_SHMOPEN` on every open reader connection (inkwell incident) |
| `sssf init` / engine | template trees read via the `Traversable` API (`iterdir`/`read_text`), not `Path(resources.files(...))` | Python 3.13 `resources.files()` returns `MultiplexedPath` whose `__fspath__` yields a `Path`, not `str` — `Path(...)` chokes; the Traversable API is also zip-safe for the wheel |
| `sssf init` | `--version` handled explicitly, returns 0 | argparse `action="version"` raises `SystemExit`, breaking the `main(argv) -> int` contract |
| `sssf run` | unchanged | — |
| `sssf sessions` | query selects `adw_name` (rest verbatim from the justfile recipe) | the plan's own test asserted the ADW name appears; without the column it can't |
| wheel build | `exclude` for `apps/`/`templates/`/`docs/`/`SKILL.md` in the wheel target; force-include is the single source | hatchling auto-includes everything under `src/sssf`, double-adding force-included files and failing the build |
| `sssf viz` | api layer prefixes `base()/sessions` (was `base()/api/sessions`) | double `/api/` prefix → `no route /api/projects/:p/api/sessions` in the built UI |
| `sssf viz` | server reads `PORT` env (was passing `--port` argv) | the bun server's pinned contract is `PORT`; `--port` was silently ignored |
| `sssf viz` | `openReadonly()` probe-and-fallback in `db.ts` | a cold WAL db (no `-shm`, no live tracer) cannot be read by a readonly connection — the pinned app only worked while tracers held the db open |
| `sssf viz` | on any `SQLITE_IOERR*`, drop cached connections and retry once | a db file replaced under an open connection (agent checkout, restore) wedged the UI until restart |
| `sssf viz` | bun test uses `mkdtempSync` | `Bun.tmpdir` does not exist in bun 1.3.14 |
| engine | `commit_all(message, allow_empty=False)` — returns `None` instead of raising when allowed | idempotent re-runs |
| ADW `simple_sdlc` | `commit_build` distinguishes **already implemented** from **claim-mismatch**; a no-op skips the document chain | a re-run whose work was already landed is success; a builder that claims changes that never landed is a hard fail |
| engine | `session.ensure()` **reaps** a previous run still marked in flight under the same `adw_id` | re-running an adw_id must kill the stale run (verified against the recorded command) and mark its open phases/session failed — no more zombie "running" sessions |
| engine | failsafe `sys.excepthook` marks the session failed on **any** uncaught exception | per-phase handling covered in-phase errors; between-phase and `finish()` errors left the session reading `running` forever |

## 1. The inkwell incident (why the field fixes exist)

Sequence observed on a demo project (`~/dev/lab/demos/inkwell`):

1. `sssf init`'s gitignore covered `sssf.db` and `sessions/` but **not** the
   `-wal`/`-shm` sidecars → they got committed into the project's git.
2. Every run dirties git (the WAL changes), so the reviewer flags the modified
   `sssf.db-wal` as a blocking scope violation.
3. The builder obeys: `git checkout -- adws/adw_data/sssf.db-wal` **while the
   tracer and the visualizer have the db open**.
4. The visualizer's cached connection loses its WAL vnode → every poll fails
   with `SQLITE_IOERR_SHMOPEN` (errno 6922) until the server is restarted.
5. Separately, the run ended `✗ commit_build: nothing to commit` — honest, but
   the run was a no-op re-run of an already-implemented prompt, so it failed
   for the wrong reason.

Fixes: gitignore the sidecars (prevention), untrack them in the affected repo
(repair), and make the visualizer recover from `SQLITE_IOERR` (robustness).

## 2. Operational semantics (new behavior)

### 2.1 Re-running an adw_id reaps the previous run

`session.ensure(cfg, adw_id)` now, before starting:

1. Reads live process rows (`ended_at IS NULL`) for the adw_id.
2. Terminates each — SIGTERM, then SIGKILL after a 0.5 s grace — **only when
   the pid's current command line matches the first token of the recorded
   command** (a recycled pid must never kill an innocent process).
3. Marks open phases `running`/`queued` → `fail` (`reaped: superseded by a
   re-run`) and a `running` session → `fail`.
4. Closes the process rows.

Fresh adw_ids are unaffected (empty tables, no-op). This makes Ctrl+C'd,
killed, or stalled runs disappear from the trace on the next re-run.

### 2.2 Failsafe: uncaught exceptions never leave a `running` session

`ensure()` installs a `sys.excepthook` that writes `session_finish(ok=False)`
before the original hook runs. The phase manager already handled exceptions
inside phases; this covers everything else after the session row exists
(between phases, `run.finish()`, console, …). Idempotent — an already-failed
session is written again harmlessly.

### 2.3 `commit_build`: already implemented ≠ builder failed

An empty commit at `commit_build` now resolves two ways:

- **Builder reported no changes** (`changed_files: []`), suite green, review
  approved → the work is **already implemented and verified** → the run
  succeeds with a note, and the `changes`/`document`/`commit_docs` phases are
  **skipped** (they would fail on an empty diff).
- **Builder claimed changes** (non-empty `changed_files`) but the working tree
  has no diff → **hard fail** (`builder reported changed files, but the working
  tree has no diff — the changes never landed`).

`commit_plan` and `commit_docs` stay strict: a plan or doc that produced
nothing is a real failure.

## 3. What did NOT change

- The hard rules in `SKILL.md` (rules 1–10, verbatim from the pinned source).
- The porting convention: engine and visualizer copied verbatim, minimal
  reviewable diffs only.
- The CLI surface (`init/run/sessions/phases/tail/procs/projects/doctor/
  upgrade/viz`).
- The registry schema (`~/.sssf/projects.json`, version 1).
- The visualizer's query code (route handlers unchanged; only the connection
  lifecycle and cache recovery changed).

## 4. Verification

- 34 pytest tests, 2 bun tests, `vue-tsc` typecheck, oxlint — all green.
- New coverage: `tests/test_git_helper.py` (allow_empty), `tests/test_session.py`
  (reap kills/marks/spares, failsafe), `tests/test_init.py` (sidecar gitignore
  entries).
- Field e2e: real ADW runs against a live model, session continuity across
  `plan → build_test`, global visualizer over multiple projects.

## 5. Open follow-ups

- `commit_plan` still fails on a byte-identical regenerated spec (rare; LLM
  plans vary). If idempotent plan re-runs matter, relax it the same way as
  `commit_build`.
- The placeholder test command in the starter `quality.py` (`echo PLACEHOLDER`)
  means a no-op run's "green suite" is only as strong as the project's real
  test wiring.
- Wheel-installed `sssf viz` resolves the app dir via `importlib.resources`
  (zip path) — `subprocess cwd=` needs a real directory. The dev loop uses
  `uv tool install --editable .`, which works; a non-editable install needs
  `resources.as_file(...)` extraction.
