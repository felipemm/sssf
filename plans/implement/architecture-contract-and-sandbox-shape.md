# Architecture refactor — run-trace schema contract (pydantic) + sandbox package shape

Source: architecture review 2026-09-21 (candidates 1 & 2, grilled to shared
understanding). No ticket. Green-to-green: zero behavior change; db rows,
columns, and CLI surface stay identical.

## Scope

### Part 1 — Schema contract: pydantic models are the source of truth

1. **`src/sssf/db_schema.py`** — one pydantic `Row` model per table in the
   per-project `sssf.db`: trace (`sessions`, `phases`, `events`, `envelopes`,
   `gate_results`, `processes`, `agent_sessions`, `sandbox_run`), tickets
   (`tickets`, `ticket_runs`, `ticket_mrs`, `ticket_events`), notify
   (`notify_threads`, `notify_events`). Statuses tightened to `Literal` unions
   matching the viz's `SessionStatus`/`PhaseStatus` shapes.
   - `DDL` generator: model → `CREATE TABLE` (mapper: `str`→`TEXT`,
     `int`→`INTEGER`, `bool`→`INTEGER`, `float`→`REAL`, `datetime`→`TEXT` ISO,
     `Optional`→nullable, `Literal`→`TEXT`, field defaults, PKs, the existing
     UNIQUE/CHECK clauses).
   - `SCHEMA_VERSION` (int) + ordered `MIGRATIONS` list — schema deltas *and*
     data steps (ticketing's legacy-status remap and `tracked`/`origin`
     backfills move in here as explicit entries).
   - `apply_schema(conn)`: executes current `CREATE TABLE IF NOT EXISTS` from
     the models, reads `PRAGMA user_version`, applies `MIGRATIONS` above it in
     one transaction, sets `user_version`. Idempotent — safe on every open.
2. **Writers route through the models** (D4b — full enforcement):
   `tracer.py` (delete hand-written `SCHEMA`, the legacy `tickets` DDL, and the
   `MIGRATIONS` list — `apply_schema` replaces all three; every insert/update
   `model_validate`s first), `ticketing.py` (DDLs fold into `db_schema`;
   `ensure_schema` becomes an `apply_schema` wrapper), `notify.py` (same),
   `sandbox.sync_run_db` (copied rows validated through the models,
   `extra="ignore"` for older source dbs), `healer.py` (model-backed writes).
3. **Reader**: `db.ts` reads `PRAGMA user_version` once per connection and
   gates optional columns by version (NULL-tolerant degradation stays);
   per-column `optionalColumn()`/`hasTable()` probing removed.
4. **`shared/types.ts` generated** by `scripts/gen_viz_types.py` (pydantic
   JSON Schema → TS writer, ~100 lines, no new toolchain), committed, and
   checked in CI (regenerate → diff empty).
5. **`schema/schema.sql` derived artifact** — emitted by the same script as a
   committed, greppable snapshot; not authoritative.
6. **Cross-language contract test** — fresh db built via Python
   `apply_schema`, every `db.ts` query run against it, reader min version
   asserted == writer current. Drift fails CI, not production.

### Part 2 — `sssf/sandbox/` package shape

7. `sandbox.py` (1406 lines) becomes a package: `orchestrator.py`
   (run-lifecycle), `worktree_git.py` (worktrees + integration/merge),
   `docker.py` (image + container lifecycle), `rundb.py` (project db, per-run
   sync), `session_env.py` (env/reopen). `__init__.py` re-exports so current
   call sites keep compiling.
8. **Interface** (Q2a): `__init__` exposes only the run-lifecycle verbs —
   `spawn_sandbox`, `spawn_monitor`, `stop_run`, `abort_sandbox`,
   `teardown_sandbox`, `enabled`, `SandboxError`, `sandbox_dir`. Callers that
   need more import the concern module: `healer` → docker/rundb/orchestrator/
   session_env; `ticket` → worktree_git; `sweep` → docker verb + worktree_git
   + rundb; `sandbox_cmd` → per-module.
9. **Docker seam**: `_docker` promoted to the docker module's execution point
   (per-call PATH resolution kept — the fake-docker shim in tests keeps
   crossing the same seam). `sweep`'s `sandbox._docker` private reach ends:
   one public docker-module verb.
10. **Tests** mirror the modules (`test_sandbox_worktree/integration` →
    worktree_git, `test_sandbox_docker/env` → docker, `test_sandbox_cli` →
    orchestrator) plus new orchestrator-level tests running
    spawn→monitor→teardown through the run-lifecycle interface with the
    fake-docker shim. `sandbox.py` deleted.

## Seams

- **Contract-1** (`tests/test_db_schema.py`): model→DDL output equals the
  current schema column-for-column (snapshot); `apply_schema` idempotent;
  migrations apply in order; `user_version` bumps; data backfills land.
- **Contract-2**: existing tracer/ticketing/notify tests stay green; new
  validation tests prove a bad row fails `model_validate` before SQL.
- **Contract-3** (visualizer server): `db.ts` version-driven reads against a
  fresh db and a simulated old-version db (missing column → NULL, never
  throw).
- **Contract-4** (bun): the cross-language test — every `db.ts` SELECT against
  a Python-built db; reader min version == writer current.
- **Sandbox-1**: reorganized `tests/test_sandbox_*` mirror the package
  modules; orchestrator tests use the existing `fake_docker` PATH shim.
- **Sandbox-2**: call-site tests (`test_healer`, `test_sweep`, `test_flow`,
  `test_ticket_cli`, `test_sandbox_cli`) unchanged and green — behavior
  preserved across the move.

## Steps

1. RED: `tests/test_db_schema.py` → GREEN: `db_schema.py` (models + mapper +
   `MIGRATIONS` + `apply_schema`). Snapshot equals today's schema.
2. RED: Literal tightening (generated JSON Schema unions match
   `shared/types.ts`) → GREEN: model Literal pass.
3. RED→GREEN: `tracer.py` through the models (delete `SCHEMA`/legacy
   `tickets`/`MIGRATIONS`; `apply_schema`; validated inserts). Keep
   `test_engine_port`, `test_session`, `test_chains` green.
4. RED→GREEN: `ticketing.py` + `notify.py` through the models (DDLs fold in;
   backfills become migration entries; `ensure_schema` → `apply_schema`
   wrapper). `test_ticketing`, `test_ticket_cli`, `test_mr_cli`,
   `test_notify` green.
5. RED→GREEN: `sync_run_db` validates copied rows; `healer` model-backed
   writes.
6. RED→GREEN: `db.ts` version-driven reads (fresh + old-version db tests).
7. `scripts/gen_viz_types.py`: regenerate `shared/types.ts` (diff-only),
   emit `schema/schema.sql`; commit; wire CI check.
8. Cross-language contract test (bun) lands in CI.
9. Full suite + docs/AGENTS/CHANGELOG updates. **Part 1 done.**
10. Package scaffold: move `sandbox.py` → `sandbox/` modules with `__init__`
    re-exports; suite stays green (pure move).
11. Interface trim (Q2a) + caller import updates (healer, sweep, sandbox_cmd,
    flow, ticket); `sweep`'s `_docker` leak → docker-module verb.
12. Reorganize tests to mirror modules; add orchestrator spawn→monitor→
    teardown tests via `fake_docker`.
13. Delete `sandbox.py`; verify imports; full suite. **Part 2 done.**
14. Docs/AGENTS/CHANGELOG updates; code-review; commit; PR.

## Global constraints

- Local pytest with `--ignore=tests/test_sandbox_docker.py` (docker-dependent
  tests excluded); `uv run ruff check src/sssf tests`; `uv run mypy src/sssf`;
  `bun test` in `src/sssf/apps/visualizer/`; `npm run build`
  (vue-tsc + vite build); pre-commit hooks (ruff + format + whitespace).
- Engine changes alter the runner-image fingerprint → the healer auto-rebuilds
  `sssf-runner`; sandboxed runs are blocked until then (existing mechanism —
  do not hand-build the image during tasks; final task notes verification).
- Old dbs keep working: migrations are additive only; the TS reader stays
  NULL-tolerant for pre-version columns.
- No behavior change: identical DDL output, identical rows, identical CLI
  surface. Any diff is a bug, not a feature.
