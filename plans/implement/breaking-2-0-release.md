# Breaking 2.0: `.adws` reset, flows-only verbs, bidirectional sync, machine/viz hardening

Decided in a grill-with-docs session; recorded in ADR-0007 (init reset, DB-only
survival) and ADR-0008 (verb surface, sync absorbs writeback). 2.0 is a
**breaking** major release: old stamped projects must run `sssf init` before
any flow verb works.

## The constraints that shape the design

- **The UI shells the CLI** (`cockpit.ts`, `ticketRoutes.ts`): the board's
  stop/restart call `sssf sandbox …`, the sync button calls `sssf ticket sync`,
  the modal's steer calls `sssf ticket context`, sweep runs `sssf sweep` every
  6h inside viz. Verb removal must keep every shell target alive.
- **Flows are the only ticket writers; `ticket sync` is the only tracker
  writer.** Nothing pushes to trackers on its own (ADR-0008 consequence) —
  human checkpoints stay in the tools (ADR-0006).
- **`tickets` is project-owned** (sandbox per-run dbs never carry tickets) —
  every machine edge runs host-side in `flow.py` / `ticketing.py`, exactly like
  #91/#92/#96.
- **Only the DB survives the init reset** — legacy chains/prompts/config are
  superseded, not migrated; the backup preserves them for hand-recovery.

## Execution order for the open issues

The GitHub issue bodies remain the acceptance criteria; this section sequences
implementation to avoid conflicting edits and schema migrations. Land one issue
per PR, then base the next on updated `main`.

1. **[#120 — board/modal machine states](https://github.com/felipemm/sssf/issues/120).** Do first: #121 explicitly depends on replacing the UI's `ticket run` route with `flow implement`. Verify board/modal behavior for every machine state and ensure `needs-triage` is never runnable.
2. **[#122 — stale-ticket reconciliation](https://github.com/felipemm/sssf/issues/122).** Add the idempotent settle at `flow implement` entry and visualizer boot, then normalize legacy statuses. On current schema v5, this owns migration 6 for status normalization only.
3. **[#123 — bidirectional sync](https://github.com/felipemm/sssf/issues/123).** Add the Jira writer, controlled-label mirroring, close/comment behavior, and `mirrored_at` idempotence. This owns migration 7 and the removal of `ticket writeback`; land it before #121's breaking cutover so the replacement sync path is already complete.
4. **[#124 — visualizer card/history UX](https://github.com/felipemm/sssf/issues/124).** After #120 because both touch board/tracker components; finish the card constraints, unified sessions filter, and sweep-only archive behavior.
5. **[#121 — 2.0 footprint and verb cutover](https://github.com/felipemm/sssf/issues/121).** Integrate only after #120, #122, and #123 (and preferably #124): move to `.adws`, preserve/migrate the DB on init, gate legacy projects, remove obsolete verbs, and release as 2.0.0. Do not reimplement #122/#123 here.
6. **[#125 — deploy-flow notifications](https://github.com/felipemm/sssf/issues/125).** Finish after #121 so the notifier uses the final `.adws/config/notify.yaml` path; wire the four alerts without moving human decisions out of the terminal.

**Integration gates:** run each issue's focused tests before merging its PR. After #121/#125, run the full pytest, ruff, mypy, visualizer tests/typecheck/build, and site build. Add a legacy-project init fixture that proves backup + fresh `.adws` + DB survival, and a fresh-project smoke pass through plan → implement → deploy. Close each GitHub issue only when its acceptance criteria—not just its code changes—are verified.

## Scope

### 1. `.adws` reset + path migration (ADR-0007)

- `paths.py` v2 layout moves `adws/` → `.adws/` (hidden): modules/, config/,
  data/, prompts/, specs/, kb/. All references update: `paths.py`, `sweep.py`,
  the viz server db paths, the registry's db entry, `init` stamping.
- `sssf init` on a legacy project: back up the old `adws/` tree
  (`sssf-<timestamp>/`), write a fresh `.adws/` from templates, **warn the
  user**, migrate the DB in place (only survivor). Flow verbs on a legacy
  (pre-`.adws`) project fail with an upgrade warning instead of running.
- Config files move with the tree: `ticketing.yaml`, `deploy.yaml`
  (CONTEXT.md's `Promote` already updated), `notify.yaml` (`NOTIFY_FILE`).
- Version gate: `sssf --version` → 2.0.0; CHANGELOG entry.

### 2. Verb removal (ADR-0008)

- **Remove**: `spec create`, `ticket add`, `ticket run`,
  `sessions`/`phases`/`tail`/`procs`, `mr` CLI. Delete their command modules,
  CLI parsers, tests, and the legacy `adw_simple_sdlc` support: the runner
  fallback in `flow.py`, the `ticket run` in-progress guard (flow.py:1113
  already mirrors it), `adwIcons.ts` entries, template refs.
- **Keep**: `flow` (plan/implement/afk/deploy/revert/down), `ticket`
  (new/sync/list/context/backlog), `sandbox` (board shells stop/restart;
  `build` is the stale-image recovery), `heal` (auto-started by `viz start`),
  `sweep` (auto 6h + topbar), `notify`, `viz`, `init`/`upgrade`/`doctor`/
  `projects`.
- **Flow gate**: `flow *` checks for the `.adws` layout first; legacy layout →
  "run `sssf init` to upgrade" (1).
- The board/modal run path moves to `flow implement` (4).

### 3. Bidirectional `ticket sync` (ADR-0008 + Q13)

- **Pull** unchanged: per-provider fetch, JQL filter from `ticketing.yaml`
  for jira, born `needs-triage`/untracked, conflict-update refreshes content
  only.
- **Push** (after pull, idempotent via a new `mirrored_at` column on
  `tickets`):
  - control labels on every sync: ensure the machine-state label on the origin
    (`needs-triage`, `ready-for-agent`, `blocked`, …) add/remove, never touch
    foreign labels — jira included (jira tickets may already carry labels
    from sssf or elsewhere);
  - `done` → transition the origin to closed + append the closing comment;
  - rejection/requeue with feedback → append the feedback comment.
- **jira writer**: `acli workitem` transition + comment + label (exact
  subcommand syntax to confirm against acli ≥ 1.3 at implementation);
  gh/glab writers unchanged. `ORIGIN_WRITERS` gains `"jira"`; `_split_external`
  generalizes (jira keys have no `owner/repo#` shape).
- **[#123 owns this cutover](https://github.com/felipemm/sssf/issues/123):** delete the `ticket writeback` verb only after its writer behavior is part of `ticket sync`. The tracker page sync button then performs the merged pull + push.

### 4. Machine vocabulary in the UI (Q7)

- `KanbanBoard` backlog filter: `ready-for-agent` (+ nothing else; drop the
  legacy `backlog`/`starting` special-cases). Failed column → `blocked`
  tickets + failed sessions.
- `TicketCard` retry/failed conditions re-vocabularied (`retry` = re-queued
  ready-for-agent with runs; `failed` = failed session card).
- `TicketModal` `runnable` = `ready-for-agent`; the modal + board run buttons
  dispatch `flow implement <id>` (server `runTicket` route change), keeping
  `ticket sync`'s adw-id parse for the trace link.
- Shared `MACHINE_STATUSES` constant so tracker/board/modal can't disagree.

### 5. Stale in-progress reconcile (Q8)

- Deterministic, idempotent settle: ticket `in-progress` whose
  `tickets.adw_id` session is terminal → `success → ready-for-signoff`,
  `fail → ready-for-agent`. Runs at `flow implement` entry (before claim) and
  at viz server boot (and on the board/tracker poll? no — boot + flow entry
  only, to keep the writer set small). Fixes datafabrik's 4 stale
  in-progress rows on first run.

### 6. Ordered DB migrations (#122 then #123)

Current `main` is at schema version 5. Keep ownership and migration order
aligned with the split GitHub issues:

- **Migration 6 — #122:** normalize legacy synced statuses
  (`backlog/starting/running/failed/success` → machine equivalents, matching
  migration 3's mapping), guarded so it only touches rows still carrying
  legacy values. This lets `SWDATAP-1995` (datafabrik) land in a real machine
  column rather than the `needs-triage` fallback.
- **Migration 7 — #123:** add `mirrored_at` to `tickets` for idempotent
  push-on-sync. Do not fold this column into #122; that issue explicitly
  leaves it to the sync issue.

### 7. Viz UI (Q3/Q4/Q9/Q10)

- **Uniform cards**: fixed card height + 2-line title clamp (ellipsis) on
  tracker `.tk` and board `.ticket`; meta/actions fit inside the box.
- **Tabs**: merge `sessions` + `archived` into one tab with a filter —
  All / Done / Blocked / Archived (server: filter param; `SessionsList` gains
  the chip row).
- **Archive**: per-card Archive/ArchiveRestore buttons removed; archived
  sessions show no buttons (terminal). Manual sweep button (topbar, exists)
  + 6h auto-sweep are the only archivers.

### 8. Notify wiring (ADR-0006 completion)

- `flow.py deploy` fires in-thread Slack alerts at the human checkpoints:
  workbench ready (signoff waiting, + workbench URL), promote confirmation
  waiting (+ tompero URL), canary failure (tickets blocked, + tompero URL),
  release done. `NOTIFY_FILE` → `.adws/config/notify.yaml`.

## Tests

- **pytest**: sync push (labels idempotent, jira writer, done→closed,
  feedback comments, mirrored_at watermark), migration 6 (legacy status
  normalization), reconcile settle, flow gate on legacy layout, removed
  verbs absent from the parser.
- **bun test**: `runTicket` dispatches `flow implement`; sessions filter
  (all/done/blocked/archived); card/title clamp snapshot-ish checks; archive
  button absent on archived sessions.
- `uv run ruff check src/sssf tests`, `uv run mypy src/sssf`,
  `bun test` in `src/sssf/apps/visualizer`, `npm run build` in `site`.

## Open implementation notes

- Exact `acli` subcommands for jira transition/comment/label (verify against
  the installed acli version; sync already uses `acli workitem search`).
- Whether control labels for machine-only states (`ready-for-signoff`,
  `ready-to-deploy`) belong on trackers at all — triage-labels.md says those
  are factory states, not tracker labels; the push uses the triage set only
  (`needs-triage`, `ready-for-agent`, `blocked`, …).
