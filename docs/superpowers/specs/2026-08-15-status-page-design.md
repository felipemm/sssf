# Status Page — Design Spec

**Date:** 2026-08-15 · **Branch:** `feat/status-page` · **Status:** design (awaiting review)

A new "status" view in the sssf visualizer: a project dashboard with the factory's
main information and key performance indicators. Per-project (follows the project
picker, like every other view).

## Purpose

Two audiences, equal weight:

1. **Operational** — the user glances at the factory's health: runs, failures,
   tokens burned, cost, per-agent behavior, recent activity.
2. **Presentable** — inkwell is a demo; the page shows off throughput, success
   rates, and quality.

The page is a dashboard, not a live board: **on-load fetch + manual refresh
button** (no polling). Totals are **all-time**; only trend charts are
**window-scoped** (7d / 30d / 90d).

## Approach (agreed)

**Single aggregate endpoint + one page component.** `server/status.ts` computes
everything server-side; the client is one `StatusPage.vue` with hand-rolled SVG
charts. No new dependencies, no YAML parsing, mirrors the existing
`server/tickets.ts` server-side aggregation pattern.

Rejected: multiple granular endpoints (three loading states, more plumbing for a
single screen); client-side aggregation (drags every session+phase to the
browser, duplicates logic).

## Page structure

```
┌─ status · inkwell ──────────────────────────────────── [↻ refresh] ─┐
│  project root / db path · last run · active runs · ticketing · agents │
│  ┌─────────┬─────────┬─────────┬─────────┐                           │
│  │ Runs    │ Cost    │ Quality │ Agents  │  ← KPI cards (2-4 stats)  │
│  └─────────┴─────────┴─────────┴─────────┘                           │
│  ┌───────────────────────────────┬─────────────────────────────────┐ │
│  │ Trends: runs/day · cost/day   │ window: [7d|30d|90d]            │ │
│  │ success-rate/day · tokens/day │  ← SVG charts                   │ │
│  └───────────────────────────────┴─────────────────────────────────┘ │
│  ┌─ Tickets ────────────────────────────────────────────────────────┐│
│  │ backlog · running · done · failed   (hidden when disabled)       ││
│  └──────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────┘
```

### Main information strip (6 tiles)

- project name + root path
- db path (`adws/adw_data/sssf.db`)
- last run time (most recent `started_at`)
- active runs now (count, links to the board)
- ticketing on/off (`isEnabled`, same logic as tickets.ts)
- configured agents: planner / builder / reviewer / documenter — each with the
  model actually used (most recent `agent_sessions` row per role; dash when a
  role has no sessions)

### KPI cards (4 cards, 2–4 stats each)

| Card | Stats |
|---|---|
| **Runs & health** | total runs · success rate % · failed count · avg duration (successful runs only, `started_at`→`ended_at`) · live "N active now" badge → board |
| **Cost & tokens** | total cost ($, 2dp) · avg cost per run · total tokens (compact) · avg tokens per run |
| **Quality** | quality-gate pass rate (checks passed / checks total across `gate_results`) · phase failure hotspot (phase name with most `fail` rows + count) · total retries (sum of phase `retries`) |
| **Agents** | one row per role: model · agent-session count · sum of context tokens |

**Data honesty:** derived from existing tables only; no new columns; no
cost-per-agent split (sessions carry total cost only — not derivable per agent);
failed runs excluded from duration averages.

### Trends (window 7d / 30d / 90d)

Four small hand-rolled SVG charts, day-bucketed by `started_at`:
runs/day (bars) · cost/day (area) · success-rate/day (line) · tokens/day (bars).

### Tickets panel

backlog / running / done / failed counts, reconciled from linked sessions
(same semantics as `readTickets`). Hidden entirely when ticketing is disabled.

## API contract

`GET /api/projects/:project/status?window=30d` — `window` ∈ {7, 30, 90},
default 30.

```jsonc
{
  "project":   { "name": "inkwell", "root": "/…/inkwell", "ticketing_enabled": true },
  "totals":    { "runs": 15, "active": 1, "success": 12, "failed": 2, "archived": 0,
                 "success_rate": 0.857, "avg_duration_s": 312.4,
                 "total_cost": 4.12, "avg_cost_per_run": 0.27,
                 "total_tokens": 1234567, "avg_tokens_per_run": 82304 },
  "quality":   { "gate_pass_rate": 0.917, "hotspot_phase": "commit_build",
                 "hotspot_count": 2, "total_retries": 5, "failed_phases": 4 },
  "agents":    [ { "role": "planner", "model": "…", "sessions": 15, "context_tokens": 600000 }, … ],
  "tickets":   { "backlog": 2, "running": 1, "done": 1, "failed": 0 },   // null when disabled
  "trends":    { "window": 30, "buckets": [ { "day": "2026-07-16", "runs": 2,
                "cost": 0.42, "tokens": 120000, "success": 2, "fail": 0 }, … ] }
}
```

Rules:

- **Totals are always all-time**; only `trends` respects the window.
- **Totals count every run, including archived ones** (the sweep archives old
  runs; totals stay honest about total work done). `archived` is reported
  separately; the active badge and trends use non-archived runs only.
- `agents[].model` from `agent_sessions` (most recent per role) — no config/YAML parsing.
- Missing `sessions` table (never-run project) → zeroed payload, HTTP 200.
- Registered next to the existing project-scoped routes (`scoped(...)` wrapper).

## Client

- `lib/api.ts` — `fetchStatus(project, window)` with a `StatusResponse` type.
- `components/StatusPage.vue` — fetches on mount after `projectsLoaded` (same
  pattern as KanbanBoard); refresh button re-fetches; window toggle re-fetches;
  `apiError` banner + empty state (no sessions yet).
- `components/StatusCharts.vue` — the four SVG charts, no chart library.
- Router: `#/status` → `status` view (new branch in the `view` computed in App.vue).
- **Tab order: board | status | sessions | archived.**

## Error handling

- API failure → banner with retry (same `apiError` pattern as the board).
- Empty project → friendly empty state, not an error page.
- Window toggle failure → keep showing previous data + banner.

## Testing

- `server/status.test.ts` (bun) against a temp fixture db:
  - totals math (runs/success rate/cost/tokens/duration) on a known dataset
  - trend bucketing + `window` param (7/30/90)
  - missing sessions table → zeroed payload
  - ticketing disabled → `tickets: null`
- Existing gates stay green: `bun run typecheck`, `bun run build`, `bun test`,
  `bun run lint`.
- Manual smoke: open `#/status` for inkwell; refresh; flip windows; confirm the
  running-session badge and ticket panel reflect the live project.

## Out of scope

- No polling / websockets / live updates.
- No per-agent cost split (not derivable).
- No CSV/export, no cross-project aggregate view.
- No new DB columns or schema changes.

---

# Revision 2 (2026-08-15): contributions, cost attribution, git stats

Extensions to the status page, same architecture (one aggregate `/status` endpoint + hand-rolled client).

## Cost per agent & per model (actual + token-share)

**Discovery:** `agent_end` events carry the real provider billing per call
(`payload_json.cost`) and full usage (`payload_json.usage.total_tokens`;
also mirrored in the `events.tokens` column). Verified against inkwell:
`SUM(agent_end cost)` == `SUM(sessions.total_cost)` exactly, and
`SUM(agent_end tokens)` per session == `sessions.total_tokens` exactly.

- **cost_actual per agent** = SUM(`json_extract(payload_json,'$.cost')`) over
  `agent_end` events, grouped by the phase owner (JOIN phases).
- **cost_actual per model** = same, grouped by model via `agent_sessions`
  (JOIN on `adw_id` + phase owner = agent).
- **tokens per agent/model** = SUM(`events.tokens`) over `agent_end` events,
  same joins.
- **cost_share (token-share estimate)** = per session, `session.total_cost ×
  (agent tokens / session total_tokens)`, summed — shown alongside to expose
  price-per-token differences (e.g. gpt-5.5: ~19% of tokens, ~93% of cost).
- `agents[]` becomes **dynamic** — one entry per role present in the data
  (canonical four + extras like `scout`), each with `{role, model, sessions,
  context_tokens, tokens, cost_actual, cost_share}`.
- New `models[]` — one entry per model `{model, tokens, sessions, cost_actual,
  cost_share}`.
- **Footnote** (client, under the cost section): *"actual = summed provider
  billing per agent call; token-share = each run's cost split by token count —
  the gap reflects models with different $/token."*
- No embedded price table (actual billing supersedes it).

## Git repo stats + contributions heatmap

New `server/git.ts` — shells `git -C <root>` (Bun.spawnSync; same permission
model as the ticket routes — fixed commands, no user input).

- `gitStats(root)` → `{ commits, commits_30d, commits_year, contributors:
  [{name, commits}], branches, current_branch, last_commit: {date, subject} |
  null, dirty, first_commit: date | null }` — all nullable/zeroed when the
  root is not a git repo.
- `contributions(root)` → last 364 days: `[{date: 'YYYY-MM-DD', count}]`
  (commits/day from `git log --since=1 year`).
- Rendered as a **Repo KPI card** (commits, contributors, branches, last
  commit, dirty badge, repo age) and a **GitHub-style contributions heatmap**
  (52 weeks × 7 days, intensity buckets 0/1-2/3-5/6-9/10+, hover tooltip,
  month labels) — hand-rolled, no deps.

## Layout & fixes

- **Full width:** remove `max-width: 1100px` from `.status-page`; grids span
  the viewport (padding retained).
- **Model overflow:** agent card `.model` gets `min-width: 0` on flex children
  + proper ellipsis so model names truncate instead of overflowing.
- Page order: main info strip → KPI cards (Runs | Cost | Quality | Repo) →
  Agents & Models side-by-side → Trends → Contributions heatmap → Tickets →
  footnote under the cost section.
