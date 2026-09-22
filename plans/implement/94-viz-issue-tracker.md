# #94 — Viz issue tracker: machine-state kanban, untracked adopt, lineage

Parent: #86. Blocked-by (#01 ticket machine core, #04 multi-source sync) are
effectively met: the machine is closed/merged, and sync's untracked model
(upserted tickets born `needs-triage` + `tracked=0`) already lives on main —
the open #90 PR adds providers/writeback without touching the viz. This slice
is the viz-side tracer bullet: one page that reads the whole ticket surface.

## What the tracker must do (acceptance criteria)

1. List every known ticket with origin + state, including untracked.
2. Mark an untracked ticket `ready-for-agent` from the page; the backlog shows
   only `ready-for-agent`.
3. Group tickets by machine state (the kanban).
4. Render parent/child lineage so implementation tickets trace to their idea.
5. Cover it with the viz server test pattern.

## Design decisions

- **The server already has the data; it just wasn't surfaced.** `readTickets`
  (server/tickets.ts) SELECTed only the legacy columns and re-derived status
  from the linked session. The tracker needs `kind`, `tracked`, `origin`,
  `parent_id`, `spec` — all present in the db (db_schema) but never read.
  Extend the SELECT + `Ticket` interface, add `ensureMachineColumns()` (ALTER
  defaults mirror db_schema exactly, like `ensureContextColumn` does for
  `context`), and treat stored **machine states as authoritative**: a
  `ready-for-agent` ticket that keeps a failed run's adw_id (history) must
  NOT re-derive `failed`. Only legacy statuses (`backlog`/`starting`/…)
  keep deriving from the session; `backlog` keeps its pre-machine retry-state
  exemption.
- **Adopt = the audited machine transition, no new CLI.** The existing
  `sssf ticket backlog <id>` command already implements the legal
  `needs-triage → ready-for-agent` edge (the machine's only entry into the
  queue) — the tracker's "add to backlog" button posts to the existing
  `/tickets/:id/backlog` route. No new endpoint, no duplicate semantics.
- **The backlog is a column, not a state.** `ready-for-agent` is the kanban
  column; the header shows live counts (total / backlog / untracked).
  Untracked tickets sit in `needs-triage` until adopted. A `Done` column
  starts collapsed so the page opens on the live pipeline (persisted collapse,
  same pattern as the board).
- **Lineage is client-side from `parent_id`.** Implementation cards render a
  `← <parent title>` line (click opens the parent's modal); idea cards expand
  to their slices. The server just returns `parent_id`.

## Files

- `src/sssf/apps/visualizer/server/tickets.ts` — machine columns + SELECT +
  pass-through + migration
- `src/sssf/apps/visualizer/server/tickets.test.ts` — machine pass-through,
  fields surface, untracked included, pre-machine migration
- `src/sssf/apps/visualizer/server/ticketRoutes.boot.test.ts` — booted-server
  proof: seeded machine rows read back with fields + lineage + passthrough
- `src/sssf/apps/visualizer/src/lib/api.ts` — `Ticket` gains kind/tracked/
  origin/parent_id/spec
- `src/sssf/apps/visualizer/src/lib/router.ts` (+ test) — `tracker` tab
- `src/sssf/apps/visualizer/src/App.vue` — tab + view + project-switch mapping
- `src/sssf/apps/visualizer/src/components/IssueTracker.vue` — the page
- site docs (visualizer.astro), CHANGELOG.md

## Out of scope / notes

- Running tickets from the tracker (`sssf flow implement` from the page) is
  #92/#93 territory (the machine run is a flow, not the legacy `ticket run`);
  the tracker is read + adopt.
- The board tab keeps its session-staged view; the tracker owns the
  machine-state view of tickets.
