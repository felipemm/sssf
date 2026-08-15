# Archive & Sweep — Design

**Status:** approved · implementing 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` §7 (visualizer) + CLI surface (`sssf sweep`) · **Plan:** `2026-08-15-archive-sweep-plan.md`
**Scope record:** captured from the approved chat design (brainstorming, bounded path) for the archive feature update.

Archive is review triage — *"I have looked at this run"* — the one deliberate write in an otherwise read-only visualizer. This spec extends it from a single list-card button into a complete lifecycle: buttons wherever runs are reviewed, an archive page, and automatic + manual sweeps.

## 1. Archive buttons (icons: lucide SVG, matching the app)

| Surface | Button | Action |
|---|---|---|
| Sessions list card (exists) | `×` → **`Archive`** icon | archive (POST `archived:true`) |
| Kanban card (new) | **`Archive`** icon | archive, then the board re-polls |
| Trace page run-strip (new) | **`Archive`** / **`ArchiveRestore`** (when the run is already archived) | archive or restore, then navigate to `#/` |
| Archive page card (new) | **`ArchiveRestore`** icon | restore (POST `archived:false`) |

Icons are `lucide-vue-next` — already a dependency, and the same iconography as the rest of the trace UI (StatusChip, PhaseDetail). The `×` text char is retired.

## 2. Archive page — `#/archived`

- **Server**: the sessions endpoint gains `?archived=1` — `db.sessions(limit, onlyArchived)` flips the `COALESCE(archived,0) = 0` filter to `= 1`. Default unchanged (list + board keep hiding archived rows).
- **Frontend**: `SessionsList` gains an `archived` mode prop (same polling/cards/navigation); the empty state reads "no archived sessions". Topbar toggle becomes `sessions | board | archived`.
- **Restore** works from here; there is no delete — sessions are the record.

## 3. Auto-archive after 30 days — two mechanisms, one policy

Same policy everywhere: `archived=0 AND status IN ('success','fail') AND ended_at IS NOT NULL AND datetime(ended_at) < datetime('now', '-N days')` (SQLite's own time math parses the tracer's ISO timestamps — verified against real rows).

| Mechanism | When | Scope |
|---|---|---|
| **viz server timer** | on boot, then every 6 h | **all registered projects** (registry-wide, same as the CLI) + the adhoc db when serving one |
| **`sssf sweep`** CLI | on demand | all registered projects, or `--project <root>`; `--days N` (default 30) |

The sweep logic exists in both runtimes (Python CLI vs bun/TS server cannot share code) — identical SQL, noted as a deliberate duplication. Each project's db is opened on a short-lived **writable** connection (the sweep is the same class of write as `setArchived`); missing/unreadable dbs are skipped with a log line, never fatal.

## 4. Manual sweep button in viz

A dedicated button in the topbar (lucide icon, e.g. `Broom`) POSTs to a new **`POST /api/sweep`** endpoint, which runs the registry-wide sweep immediately and returns per-project results. The UI shows a transient result note ("N session(s) archived · M error(s)"). This is the in-UI equivalent of `sssf sweep`.

## What did NOT change

- The `sessions` default (active-only) payload — list, board, and trace keep working untouched.
- The archive POST contract (`{archived: true|false}`, one writer connection, `busy_timeout`).
- No delete, no drag-to-archive, no per-session retention overrides (a session can always be restored).

## Verification

- pytest: `sssf sweep` against a temp registry + db with old/recent sessions (old success + old fail archived; recent + running untouched); `--project` and empty-registry paths.
- bun test: `sweepDb`/`sweepAll` against a real temp sqlite db; the `?archived=1` filter returns only archived rows.
- Frontend: typecheck, lint, build; manual smoke — archive from list/kanban/trace, restore from the archive page, sweep button + CLI both archive an old session.
