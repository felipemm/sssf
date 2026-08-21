# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Visualizer ticket actions** — the kanban Run/Backlog/Sync buttons threw
  `ReferenceError: runTicket is not defined` (the handlers were never imported
  after the `ticketRoutes.ts` extraction) and the spawned CLI's exit code was
  read before the process exited, so every action reported failure. Both are
  fixed; a boot-level regression test now drives the real server against a
  fake `sssf` CLI.

### Added

- **Auto-rebuild of the runner image** — `sssf heal` now rebuilds the sandbox
  image when its baked engine fingerprint no longer matches the local sssf
  (previously every sandboxed run died on spawn until someone ran
  `sssf sandbox build` by hand). Images are deduped across projects, skipped
  when docker is unavailable or sandbox is disabled, and failed builds are
  retried after a 30-minute cooldown. `sssf sandbox build` reuses the same
  build path.

## [1.0.0] - 2026-08-16

First release — the full feature set as of the GitHub import.

### Added

- **Global CLI** (`sssf`, installed as a uv tool): `init`, `run`,
  `sessions` / `phases` / `tail` / `procs`, `projects`, `doctor`, `upgrade`,
  `sweep`, `ticket`, `sandbox`, `heal`, and `viz`.
- **Project registry** (`~/.sssf/projects.json`) — one install, any number of
  projects; `sssf init` stamps the customization surface (chains, roster,
  prompts) into a project and registers it.
- **Deterministic ADW engine** (`sssf.adw_modules/`) — ported verbatim from
  Super Simple Software Factory: Python-owned sequencing/retries/acceptance,
  bounded coding-agent phases, typed JSON envelopes, and the agent-skills
  payload (`SKILL.md`).
- **Observability** — every run/phase/envelope/gate streams into a WAL-mode
  SQLite trace; live views from the terminal and the visualizer.
- **Run semantics** — re-runs reap the previous run (recycled-pid safe),
  a failsafe hook marks sessions failed on any uncaught exception, no-op
  re-runs are green, and builder-claims-vs-actual divergence is a hard failure.
- **Visualizer** (`sssf viz`, Vue 3 + Vite + bun, background service on
  :4600): status dashboard, kanban board, sessions list, archived view, and
  drill-down traces across all registered projects.
- **Status dashboard** — per-project KPIs (runs/health, cost & tokens with
  actual + token-share per agent and per model, quality gates, git stats,
  yearly contributions heatmap, 7/30/90d trends, ticket pipeline).
- **Ticketing integration** — opt-in `ticketing.yaml` with internal / Jira
  (acli) / Linear providers; `sssf ticket add|sync|list|run` and the kanban
  Backlog stage.
- **Sandboxed parallel runs** — each run executes in a docker container over
  a git worktree (`sssf/<adw_id>` branch) with deterministic free-port
  allocation, per-run dbs, monitor-driven sync, and auto-teardown;
  `sssf run stop|restart` and `sssf sandbox build|list|prune`.
- **Self-healing monitor daemon** (`sssf heal`) — diagnoses stuck runs across
  all projects (dead sandbox, hung agent, spawn-failed ticket) and recovers
  them: finalize, sync-teardown, restart with a budget, or return the ticket
  to the backlog.
- **Mission Control cockpit** — cross-project aggregate API
  (`/api/cockpit`): global KPIs, per-project status, running sessions, heal
  state, and control endpoints (refresh / add / remove / heal) with the
  TypeScript client.
- **E2E stress scripts** — 10-concurrent-runs, 20-ticket burst, healer
  scenarios, and multi-project runs (`scripts/e2e_*.py`).
- **CI** (GitHub Actions) — pytest + visualizer `bun test` on every push/PR.

### Fixed

- WAL sidecars (`sssf.db-wal`/`-shm`) never committed; the visualizer recovers
  from `SQLITE_IOERR*` by dropping cached connections and retrying.
- Builders never commit; `commit_build` tells the truth about who moved HEAD.
- `sssf init --refresh` confirms before overwriting chains (y/N/a) — never
  silently clobbers project customizations.
- Torn mid-run copies can't downgrade terminal session/phase state
  (forward-only sync); monitor sync no longer touches the tickets table.
- Docker-run retry under load, abort cleanup on spawn failure, and teardown
  of mount-race leftover worktrees.
- Startup failures leave a visible `failed` session instead of a dead gap
  (session row is created before config validation).
- Kanban no longer duplicates running sessions into Blocked; the trace
  waits for the project before fetching (no 404 flash on refresh).
- `sssf viz` api double-prefix and `no such table` retries for stale cached
  connections.
