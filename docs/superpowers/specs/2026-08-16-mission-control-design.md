# Mission Control — a cross-project cockpit for sssf

Date: 2026-08-16 · Branch: `feat/mission-control` · Status: approved design → spec

## Problem

The visualizer is project-scoped: every page reads a single project's db and
the project picker switches between them. There is no single place to see what
sssf is doing **across** projects — running sessions, sandboxes, tickets,
costs, the healer daemon — or to control it. Operating multiple projects means
opening each one and hopping between tabs.

## Goal

A **Mission Control** cockpit: the default landing (`#/`) that monitors every
registered project, sandbox, session, ticket, and the healer daemon in one
cross-project view, and controls the operational loop from the same page —
stop/restart sessions, run backlog tickets, refresh a project's templates,
add/remove projects from the registry, and start/stop the healer.

## Non-goals

- No project lifecycle beyond registry add/remove + one-click template refresh
  (the interactive per-file `--refresh` dance stays CLI-only).
- No speculative analytics (no new trends/cost charts beyond today's KPIs).
- No new daemons: the healer stays the only background service; the cockpit
  is a read-only aggregation + CLI-shell controls.

## Architecture

**Server-side aggregation, frontend stays dumb.** A single `/api/cockpit`
endpoint reads the registry, every registered project's db (WAL-safe
read-only), the sandbox directories, and the healer state, and returns one
JSON document. The frontend polls it (~8s) and renders. Controls are POST
routes that shell out to the sssf CLI (the established pattern — the CLI is
the only writer; the SQLite dbs stay the single source of truth).

```
MissionControl.vue  ──GET /api/cockpit (8s poll)──▶  server/cockpit.ts
        │                                                │
        │ POST controls (stop/restart/run/refresh/        │ reads: registry + per-project dbs
        │          add/remove/heal)                       │       + sandbox dirs + heal state
        ▼                                                ▼
   server shell → sssf CLI  ◀──────── single writer ── SQLite dbs
```

## Server: `/api/cockpit` (GET) — `server/cockpit.ts`

One JSON document:

```ts
{
  generatedAt: string,
  kpis: {
    runningSessions, liveContainers, sandboxWorktrees,
    ticketsInFlight, costTodayUsd, healRunning, healPid
  },
  projects: [{ name, root, sessionsRunning, sessionsToday, sessionsFailedToday,
               ticketsBacklog, ticketsInFlight, containers, worktrees,
               costTodayUsd, lastActivity, stale }],
  running: [{ project, adwId, phase, phaseStarted, ageSec, status }],
  heal: { running, pid, logTail: string[], restarts: { adwId: count } },
  activity: [{ project, adwId, ts, event }]
}
```

Aggregation rules (all deterministic, all read-only):

- **Registry**: `~/.sssf/projects.json` (or `SSSF_REGISTRY`).
- **Per-project db**: open in read-only mode (`file:...?mode=ro`) — never a
  writer; a broken/locked db yields zeros for that project (never fails the
  whole cockpit).
- **Containers**: a single `docker ps -a --filter name=sssf-` call, mapped to
  projects by name prefix; count + running count.
- **Worktrees**: count of dirs under `~/.sssf/sandboxes/<project>/`.
- **Cost today**: sum of the sessions' tracked cost for sessions started today
  (the status page's existing attribution logic, extracted to a shared
  helper; see `server/status.ts`).
- **Last activity**: max `events.started_at` per project.
- **Heal state**: `heal.pid`/`heal.log` tail (last 5 lines) +
  `heal-state.json` restart counts — read-only, via small accessors on
  `sssf.healer`.
- **Activity feed**: the 30 most recent events across all projects (per-project
  latest events merged and sorted desc).

## Server: control routes

All POST; all shell to the CLI and return `{ ok, out, err }`:

| Route | CLI action |
|---|---|
| `POST /api/cockpit/projects/:project/sessions/:adwId/stop` | `sssf run stop` (reuse existing scoped handler) |
| `POST /api/cockpit/projects/:project/sessions/:adwId/restart` | `sssf run restart` (reuse existing) |
| `POST /api/cockpit/projects/:project/tickets/:id/run` | `sssf ticket run` (reuse existing) |
| `POST /api/cockpit/projects/:project/refresh` | `sssf init --refresh --auto --project <root>` (new flag) |
| `POST /api/cockpit/projects/add` `{root}` | `sssf projects add <root>` |
| `POST /api/cockpit/projects/:project/remove` | `sssf projects remove <name>` |
| `POST /api/cockpit/heal/start` · `.../heal/stop` | `sssf.healer.start()` / `stop()` |

Session/ticket controls reuse the existing per-project endpoints' shell
helpers — no duplicated spawn logic. Project lifecycle routes must validate:
`add` requires an existing dir with an `adws/` (or refuses with a clear
error); `remove` requires confirmation via a `{ confirm: true }` body.

## CLI: `sssf init --refresh --auto`

A new non-interactive accept-all mode: same files as `--refresh` (the adws/
chains), but every prompt answers "yes" instead of asking. Used by the cockpit
refresh button and safe for scripting. `--refresh` without `--auto` keeps its
interactive behavior. (`sssf projects add/remove` already exist — verify
`remove`'s exact CLI shape during implementation.)

## Frontend

**Routing** (`src/lib/router.ts` + `App.vue`):

- `#/` → **MissionControl** (cross-project, ignores the picker).
- `#/p/:project` → per-project status (default tab).
- `#/p/:project/:tab` → board / sessions / archived drill-down.
- `#/p/:project/s/:adwId` → session trace (unchanged).

The App shell keeps the project picker; tabs become `cockpit | <per-project
tabs>` where the per-project tabs are active only once a project is picked
(picker defaults to "cockpit"/no project on `#/`).

**`MissionControl.vue`** (new), top to bottom:

1. **KPI strip** — running sessions, live containers, sandbox worktrees,
   tickets in flight, cost today, healer chip (running/stopped + start/stop
   button).
2. **Projects table** — one row per project (name, root, sessions running/
   today, tickets in flight/backlog, containers, worktrees, cost today, last
   activity, stale flag). Click → `#/p/:project`. Row action: refresh
   templates, remove from registry (confirm).
3. **Running-now strip** — every running session across projects with
   project + phase + age + **stop** / **restart** buttons (the control
   surface; uses the lucide icon set: `Square`/`RotateCw`).
4. **Healer panel** — status, log tail (last 5 lines, monospace), restart
   budgets per session.
5. **Recent activity** — the last ~30 cross-project events.

Empty states: no registry → "add a project" prompt with a root-path input;
no running sessions → the strip shows "nothing running".

## Error handling

- A project's db read failing never fails the cockpit — that project renders
  with zeros and a stale flag.
- A control POST failure returns `{ ok: false, err }` and the page surfaces
  it inline (toast/line), never a hard error.
- `docker ps` unavailable → containers count 0 + a "docker not available"
  hint in the KPI row.

## Testing

- `server/cockpit.test.ts` (bun): fixture registry + two fake project dbs +
  fake sandbox dirs + fake heal state → assert the aggregate shape, zeros for
  a broken db, activity ordering, container mapping (mocked `docker ps`).
- `tests/test_init.py`: `--refresh --auto` accepts all non-interactively
  (no stdin needed) and `--refresh` alone still prompts.
- `tests/test_healer.py`: the new read-only state/log accessors.
- Viz gates: `bun test`, `vue-tsc`, `bun run build`.
- Manual: `sssf viz start` → cockpit renders; stop/restart a session; add +
  refresh + remove a project; heal start/stop.

## Files touched

- `src/sssf/apps/visualizer/server/cockpit.ts` (new — aggregate + routes)
- `src/sssf/apps/visualizer/server/index.ts` (register `/api/cockpit*`)
- `src/sssf/apps/visualizer/src/components/MissionControl.vue` (new)
- `src/sssf/apps/visualizer/src/lib/router.ts`, `App.vue` (landing + tabs)
- `src/sssf/apps/visualizer/server/status.ts` (extract cost-today helper)
- `src/sssf/commands/init.py` (`--refresh --auto`)
- `src/sssf/healer.py` (read-only state/log accessors)
- `src/sssf/commands/misc.py` (verify/`sssf projects remove` shape)
- tests as above
