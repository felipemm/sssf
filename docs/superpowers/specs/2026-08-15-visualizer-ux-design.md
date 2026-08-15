# Visualizer UX — Header Home Link, Newest-First Events, Kanban Board — Design

**Status:** implemented 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` §7 (visualizer frontend) · **Plan:** `2026-08-15-visualizer-ux-plan.md`

Three frontend changes to the trace visualizer. All are UI-only — **no server changes**; everything reuses the existing `/api/projects/:p/sessions` payload and the hash router.

## 1. Header logo links home

The topbar logo + brand are wrapped in `<a :href="hrefFor()">` (sessions home, `#/`). One click from any depth — trace, phase panel, board — returns to the list.

## 2. Phase detail events: newest first

`PhaseDetail.vue` sorted a phase's events by `rowid` ascending (oldest first). Flipped to `b.rowid - a.rowid` — the newest event is the first thing you read in a phase's log, which is what you're usually looking for mid-run. The polling/cursor logic in `SessionTrace.vue` is untouched.

## 3. Kanban board of sessions (`#/board`)

A read-only status board over the same data as the sessions list:

- **Columns = session status**: `running` / `success` / `fail`, with per-column counts. Archived sessions stay excluded, matching the list.
- **Cards**: `adw_name`, request snippet (2-line clamp), engineer, started time, tokens/cost, and the per-phase progress dots. Click → the existing trace view (`#/<adw_id>`).
- **Routing**: `#/board` is a peer of `#/`; the topbar gains a `sessions | board` toggle next to the breadcrumb. `App.vue` branches `board` / `list` / `trace`.
- **Live**: polls `fetchSessions()` on the same 500 ms cadence as `SessionsList`, so a running run moves through the board in real time.

**Deliberately cut (per the approved design):**
- **No drag-and-drop.** Session status is run state produced by the factory, not human triage — dragging a card between columns would be fiction. (Drag-to-archive or custom columns would be a follow-up.)
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
