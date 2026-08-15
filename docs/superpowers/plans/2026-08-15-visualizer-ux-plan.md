# Visualizer UX — Header Home Link, Newest-First Events, Kanban Board — Implementation (as-executed)

> **Status:** DONE — committed, typechecked, built, and data-smoke-verified.
> Retrospective record written 2026-08-15 from the approved chat designs
> (the kanban design went through the brainstorming skill's bounded path:
> design in chat → approval → implementation).
>
> **Spec:** `docs/superpowers/specs/2026-08-15-visualizer-ux-design.md`
> **Implementation repo:** `~/dev/lab/mvp/sssf` (visualizer under `src/sssf/apps/visualizer/`)

## Commit map

| Commit | What landed |
|---|---|
| `c06feb9` | header logo/brand → home link (`App.vue`); phase detail events newest-first (`PhaseDetail.vue`); (same commit also carried the viz background service) |
| `110edca` | kanban board: `KanbanBoard.vue`, `sessions | board` toggle + `#/board` routing in `App.vue` |
| `99564e1` | kanban stage breakdown: Backlog (stub) + Planning/Building/Reviewing for running sessions (nearest-agent-phase rule); success/fail renamed Done/Blocked |
| `f666b71` | kanban cards gain the archive button; stages collapsible (chevron, localStorage) — the rest of the archive lifecycle is in the archive-sweep plan |

## Files

- `src/App.vue` — logo wrapped in `<a :href="hrefFor()">`; `view` computed (`board`/`list`/`trace`) drives the crumbs toggle and the main branch; `traceAdwId` computed for the template (TS can't narrow the ref in the `v-else` branch)
- `src/components/PhaseDetail.vue` — sort flip `b.rowid - a.rowid`
- `src/components/KanbanBoard.vue` (new) — 500 ms poll of `fetchSessions()`, status grouping, three columns, compact cards, empty/error states

## Design decisions captured during implementation

1. **`#/board` rides the existing hash router** — the router treats it as an adwId segment; the `view` computed disambiguates. No router changes needed.
2. **Typecheck caught the template narrowing gap** (`route.adwId: string | null` in the trace `v-else`) — fixed with a computed, not a `!` hack.
3. **Kanban card is an `<a :href="hrefFor(adw_id)">`** — native hash navigation (hashchange → router), same pattern as `SessionsList`/`SessionCard`; no imperative `navigate()` needed.
4. **Reused the existing visual language** — `PhaseDots`, `fmtDate`/`fmtTokens`/`fmtCost`, CSS variables — instead of inventing new primitives.

## Verification

```bash
cd src/sssf/apps/visualizer
bun run typecheck && bun run lint && bun run build && bun test   # all green
```

Data smoke: booted the server, `GET /api/projects/inkwell/sessions` → counts `{running: 1, success: 4}` (fail column exercises the empty state); built bundle contains the board's empty-state markup.
