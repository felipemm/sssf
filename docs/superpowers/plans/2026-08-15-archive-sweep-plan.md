# Archive & Sweep — Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-08-15-archive-sweep-design.md`
> **Repo:** `~/dev/lab/mvp/sssf` (visualizer under `src/sssf/apps/visualizer/`)
> **Status:** in progress — tasks 1–4 landed, tasks 5–9 remaining.

## Tasks

### Task 1: Server — archived filter + sweep endpoint ✅ (done)

- `server/db.ts`: `sessions(limit, onlyArchived)` — the archived filter is now a bound param (`= ?`, 0/1) instead of a hardcoded `= 0`.
- `server/index.ts`: `sessionsHandler` reads `?archived=` (`intQuery(...) === 1`); new **`POST /api/sweep`** route returning `{results}` from `sweepAll`.

### Task 2: Registry-wide sweep module (TS) ✅ (done)

- `server/sweep.ts` (new): `sweepDb(dbPath, interval)` opens a short-lived writable connection and runs the shared SQL; `sweepAll(registry, adhocDbPath, interval)` iterates every registered project (+ adhoc), skipping missing dbs, never throwing.
- `server/index.ts`: `runSweep()` on boot + `setInterval` every 6 h; logs per-project results only when something was archived or errored.

### Task 3: CLI sweep ✅ (done)

- `src/sssf/commands/sweep.py` (new): `sweep_db()` (identical SQL, parametrized interval) + `run(project_root, days)` over the registry (or one project); reports per-project counts; empty registry is a friendly no-op.
- `src/sssf/cli.py`: `sssf sweep [--project DIR] [--days N]` (default 30).

### Task 4: API client + card icons ✅ (done)

- `src/lib/api.ts`: `fetchSessions(archived = false)` → `?archived=1`; `runSweep()` → POST `/api/sweep`; `SweepResult` type.
- `src/components/SessionCard.vue`: `×` → lucide **`Archive`**, restore variant **`ArchiveRestore`** via an `archived` prop (`archiveSession(id, !archived)`).

### Task 5: List mode + archive page 🚧 (remaining)

- `src/components/SessionsList.vue`: `archived` prop → `fetchSessions(props.archived)`; header/empty-state text ("archived runs" / "no archived sessions"); pass `:archived` to `SessionCard`.
- `src/App.vue`: `#/archived` view (`view === 'archived'`), topbar link `archived`, main branch renders `<SessionsList archived />`.

### Task 6: Archive button on the kanban card 🚧

- `src/components/KanbanBoard.vue`: lucide `Archive` button top-right of each card (button inside the `<a>`, `preventDefault`/`stopPropagation`); on success re-poll (`void tick()`).

### Task 7: Archive button on the trace page 🚧

- `src/components/SessionTrace.vue`: `Archive`/`ArchiveRestore` button in the `run-strip` (uses `session.archived`); on success `navigate()` to `#/`.

### Task 8: Topbar sweep button 🚧

- `src/App.vue`: lucide `Broom` button next to the project picker; `runSweep()` on click with a transient result note ("N session(s) archived · M error(s)"), disabled while running.

### Task 9: Tests + verification 🚧

- pytest `tests/test_sweep.py`: old success/fail archived, recent + running untouched, `--project`, empty registry.
- bun test `server/sweep.test.ts`: `sweepDb`/`sweepAll` against a real temp sqlite db; `?archived=1` returns only archived rows.
- `bun run typecheck && bun run lint && bun run build && bun test`; `uv run pytest -q`.
- Manual smoke: archive from list/kanban/trace; restore from archive page; sweep via button + `sssf sweep`.

## Commit map

| Commit | What landed |
|---|---|
| (after Task 9) | the whole feature — this plan's commit map is filled in on completion |

## Verification (final state)

```bash
cd ~/dev/lab/mvp/sssf && uv run pytest -q
cd src/sssf/apps/visualizer && bun run typecheck && bun run lint && bun run build && bun test
```
