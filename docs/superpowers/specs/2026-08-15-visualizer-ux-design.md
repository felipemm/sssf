# Visualizer UX — Header Home Link, Newest-First Events, Kanban Board — Design

**Status:** implemented 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` §7 (visualizer frontend) · **Plan:** `2026-08-15-visualizer-ux-plan.md`

Three frontend changes to the trace visualizer. All are UI-only — **no server changes**; everything reuses the existing `/api/projects/:p/sessions` payload and the hash router.

## 1. Header logo links home

The topbar logo + brand are wrapped in `<a :href="hrefFor()">` (sessions home, `#/`). One click from any depth — trace, phase panel, board — returns to the list.

## 2. Phase detail events: newest first

`PhaseDetail.vue` sorted a phase's events by `rowid` ascending (oldest first). Flipped to `b.rowid - a.rowid` — the newest event is the first thing you read in a phase's log, which is what you're usually looking for mid-run. The polling/cursor logic in `SessionTrace.vue` is untouched.

## 3. Kanban board of sessions (`#/board`)

A read-only stage board over the same data as the sessions list:

- **Columns, in order**: `Backlog` (stub) · `Planning` · `Building` · `Reviewing` ·
  `Done` (was success) · `Blocked` (was fail), each with a count.
- **Stage mapping for running sessions**: the running phase decides the stage —
  owner `planner`/`builder`/`reviewer` maps directly; engineer/code phases
  (request, commit, test, …) inherit the stage of the **nearest agent phase at
  or before their `seq`** (mid-test → Building, mid-commit_plan → Planning,
  mid-request → Planning, no reported phase → Planning).
- **Backlog is a stub**: the future first phase of the factory; nothing feeds it
  yet, the column renders empty with "backlog — not wired yet", and the grouping
  bucket exists so wiring it later is a one-line change.
- **Cards**: `adw_name`, request snippet (2-line clamp), engineer, started time,
  tokens/cost, and the per-phase progress dots. Click → the existing trace view
  (`#/<adw_id>`).
- **Routing**: `#/board` is a peer of `#/`; the topbar gains a `sessions | board`
  toggle next to the breadcrumb. `App.vue` branches `board` / `list` / `trace`.
- **Live**: polls `fetchSessions()` on the same 500 ms cadence as `SessionsList`,
  so a running run moves through the board in real time.
- **Collapsible stages**: each stage header is a chevron toggle; collapsed state
  persists in `localStorage['sssf.boardCollapsed']`, the count stays visible,
  and the archive button lives on each card.

**Deliberately cut (per the approved design):**
- **No drag-and-drop.** Session status is run state produced by the factory, not human triage — dragging a card between columns would be fiction. (Drag-to-archive or custom columns would be a follow-up.)
- **Backlog is unimplemented by design** — a stub column only; the factory's backlog concept (queued work before a session exists) is a future feature.
- No archived column (list parity), no per-card event tails (that's the list's job), no server aggregation endpoint.

## Data flow

```
KanbanBoard.vue
  └─ fetchSessions()            # /api/projects/:p/sessions (project-scoped base())
  └─ group by status (running/success/fail), each sorted newest-first
  └─ columns render compact cards → hrefFor(adw_id) → trace view
```

## Error & empty states

Per-column "no runs" when empty; overall "no sessions yet — run an ADW" and "loading sessions…" mirrors; API errors show the same retrying banner as the list.

## Testing

`vue-tsc` typecheck, oxlint, vite build, bun tests (unchanged); data smoke — sessions payload grouped into all three statuses (inkwell had running + success; the fail column exercises the empty state).
