# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Deploy flow: batch-to-dev release train + workbench signoff (#96)** —
  `sssf flow deploy` is now a host-side orchestration: it brings up the QA
  workbench from the `dev` branch (a container with the app's command and a
  published port, configured under `adws/config/deploy.yaml` `workbench:`),
  signs the batch off at the terminal with ONE verdict (one workbench, one
  verdict — `--yes` is the explicit automation escape), and runs the
  deterministic release train (bump → MR dev→main → e2e → release) in a
  release worktree checked out at `dev` (its per-run db merges back into the
  project db like any sandboxed run's). Machine edges, host-side: rejection
  re-queues the failing tickets `ready-for-signoff → ready-for-agent` with
  the batch verdict as fix-forward feedback and tears the workbench down
  ("rebuilt" by the next run); approval moves them
  `ready-for-signoff → ready-to-deploy` once the MR exists, registering the
  MR against each ticket so the #98 monitor watches them. The revert escape
  hatch — `sssf flow deploy --revert <ticket-id>` — reverts a genuinely
  unwanted ticket's own commits from dev before the MR (matched by `#id` or
  the run adw_ids) and returns the ticket fix-forward.
  `sssf flow deploy --down` is the human's workbench teardown. The deploy
  chain drops its in-chain sandbox/signoff phases (the signoff cannot be
  answered from a detached runner's stdin — the old sandboxed signoff always
  rejected unless `--yes`); `adw_deploy` is now the deterministic release
  train only.
- **`review.command` is dropped (ADR-0004)** — `SandboxConfig.review` and the
  `ReviewConfig` model are gone; the runner publishes no ports and the
  supervisor now runs the ADW, writes the exit marker, and EXITS with the
  ADW's code instead of idling to host the app (the workbench replaces the
  fused interactive-preview machinery). Legacy stamped configs still load
  (pydantic ignores the unknown block); the `sandbox_run` schema keeps its
  historical review columns for db compat.
- **`workbench_runs` table (schema v5)** — one row per deploy workbench
  (adw_id, container, worktree, ports, url, status); created by
  `db_schema.apply_schema`, generated into the viz TS types.
- **Issue tracker page in the visualizer (#94)** — a new `tracker` tab
  (`#/p/:project/tracker`) shows every ticket sssf knows — tracked and
  untracked — grouped by the ticket machine's state (needs-triage →
  ready-for-agent → in-progress → ready-for-signoff → ready-to-deploy →
  done/blocked), each card carrying its origin (internal / jira / linear /
  github / gitlab badge), kind (idea vs implementation), untracked marker,
  run count, and parent/child lineage (implementation tickets trace to their
  idea; idea tickets expand to their slices). The backlog is exactly the
  ready-for-agent column; untracked synced tickets sit in needs-triage and
  are adopted via an in-page action that runs the audited machine transition.
  The server's ticket read now surfaces the machine fields (kind, tracked,
  origin, parent_id, spec) and treats stored machine states as authoritative
  — never re-derived from a linked session — with a migration for pre-machine
  dbs that mirrors db_schema's defaults.
- **Multi-source sync + untracked tickets (#90)** — the internal db is the
  truth for a mixed project: `sssf ticket sync` fetches from all four origins
  (`internal`, `jira` via acli, `github` via gh, `gitlab` via glab) and
  records `origin` + `external_id`. GitHub/GitLab providers resolve their repo
  from the git remote origin (yaml `repo:` override wins; otherwise the origin
  host must match the cloud standard URL, or `custom_url` when
  `self_hosted`); a host mismatch skips the provider with a warning, never an
  error. Synced tickets are born `needs-triage` + `untracked` (permanent —
  re-syncs refresh content only) and never appear in the backlog until marked
  `ready-for-agent`. New `sssf ticket writeback <id> --state/--comment/
  --label/--remove-label` mirrors state/label/comment changes to origin
  trackers best-effort through gh/glab; failures are recorded as
  `ticket_events` (`writeback_failed`) and never block the flow. Requeueing a
  synced ticket (`sssf ticket backlog`) reopens it on its origin. `sync
  --provider <p>` syncs one provider; per-provider skip warnings print
  distinctly.
- **afk: the unattended implement loop (#93)** — `sssf flow implement afk`
  works the whole `ready-for-agent` queue without an operator: one ticket
  per round, each round a fresh run (a new ADW process/container under its
  own adw_id — never a shared context window), until the queue is empty or
  `--cap` rounds are hit (default 30; re-run afk to continue). Every round
  delegates to the single-ticket implement flow (claim → spawn → settle),
  so a failed round requeues its ticket fix-forward and the next round
  re-picks it — the cap bounds the retries, and every attempt stays in the
  ticket's run history. Sandboxed rounds spawn detached and settle in the
  monitor, so afk waits for the machine settle before dispatching the next
  ticket (rounds never stack concurrent sandboxes); `--wait-seconds` bounds
  one round's wait (default 7200) — a timeout exits 1 without stacking a
  second run on a live one. A sandboxed spawn failure aborts the loop (the
  environment is broken; retrying would just burn the cap).

- **Implement flow drives the ticket machine (#92)** — `sssf flow implement
  <ticket>` now runs one `ready-for-agent` ticket unattended end-to-end
  (triage → build → quality → builder self-review → review) and settles the
  machine: it claims the ticket (`ready-for-agent → in-progress`) and records
  the run (`ticket_runs` + `tickets.adw_id`) BEFORE the run spawns; success
  moves the ticket `in-progress → ready-for-signoff`; failure returns it
  `in-progress → ready-for-agent` with the run's feedback attached
  (fix-forward) — the reviewer's blocking findings when the review rejected
  the build, else the run's last error event. Sandboxed runs settle in the
  monitor (the host process that sees a sandboxed run's end); `--no-sandbox`
  runs settle in the flow command from the ADW's exit code. Run history
  accumulates across retries (every attempt and outcome visible in the
  ticket modal). A sandbox spawn failure requeues the claim instead of
  leaving the ticket stuck in-progress. The settle is guarded: only
  implement-flow runs (`sessions.adw_name = adw_implement`) settle, and only
  a ticket whose `adw_id` links the run and whose status is `in-progress` is
  moved — legacy `ticket run` tickets and operator-moved tickets are never
  yanked. New `ticketing.finish_implement_run()`; the no-sandbox dispatch
  now forwards `--adw-id` so the ticket link points at the run that actually
  executes.

- **Plan flow lands its db transform (#91)** — `sssf flow plan` turns an idea
  ticket into a spec reference plus `ready-for-agent` implementation children
  (parent_id lineage recorded, `planned` audit event). No-args mode runs
  exploration → brief → idea ticket → spec → slices in one pass (the idea
  ticket is created from the landed spec's `# ` title). Each plan step runs in
  its own fresh agent session (`--revise` is the plan-once escape: only idea
  tickets are plan inputs, implementation tickets are terminal, an
  already-planned idea needs `--revise`, stale unclaimed children of a re-plan
  are commented as superseded). Host-side settle mirrors the implement flow:
  the sandbox monitor lands a sandboxed run's transform, `--no-sandbox` lands
  it from the ADW's exit code.

### Changed

- **Sandbox lifecycle split into a package (#109, architecture review)** —
  the monolithic `src/sssf/sandbox.py` (1406 lines) is now
  `src/sssf/sandbox/` with one concern per module: `orchestrator` (run
  lifecycle: spawn/monitor/stop/abort/teardown + the sandbox decision),
  `worktree_git` (worktrees + the integration merge), `docker` (image +
  container lifecycle, incl. the fingerprint guard and the runner-image
  upkeep), `rundb` (project db + the forward-only per-run sync), and
  `session_env` (container env + session reopen). `sssf.sandbox` re-exports
  only the run-lifecycle verbs — `spawn_sandbox`, `spawn_monitor`,
  `stop_run`, `abort_sandbox`, `teardown_sandbox`, `enabled`,
  `SandboxError`, `sandbox_dir` — and callers that need more import the
  concern module (healer → docker/rundb/orchestrator/session_env, ticket →
  worktree_git, sweep → docker verb + worktree_git + rundb, `sandbox` cmd →
  per-module). `sweep`'s private `sandbox._docker` reach is replaced by the
  public docker-module verb `list_container_names()`. Zero behavior change:
  same db rows, same CLI surface, same fake-docker test seam; the test files
  now mirror the modules and a new orchestrator-level test drives the full
  spawn → monitor → teardown lifecycle through the verbs.

- **Schema contract: the pydantic models are the single source of truth for
  the per-project db (#109, architecture review)** — `src/sssf/db_schema.py`
  declares one `Row` model per table (trace + tickets + notify), and everything
  derives from it: the DDL (`apply_schema` replaces the hand-written
  `SCHEMA`/`TICKETS_DDL`/`NOTIFY_*_DDL` blocks — the legacy pre-machine
  `tickets` DDL is deleted), the TS row types (`shared/rows.generated.ts`,
  re-exported by `shared/types.ts` under the UI-facing names; the generated
  `EventType` gains `integration`, which the hand-written union missed), and a
  greppable `schema/schema.sql` snapshot. Migrations are versioned: an ordered
  list applied via `PRAGMA user_version` (the old ALTERs and the legacy-status
  / tracked-origin backfills are now migration steps 2–4; Alembic was
  considered and rejected — SQLAlchemy dependency, no autogenerate for pydantic
  models, open-time idempotent upgrades). Every writer validates through the
  models before SQL (`tracer`, `ticketing`, `notify`, `sync_run_db`, the
  healer's requeue); the read-only viz reader (`db.ts`) reads `user_version`
  once and gates migration-added columns by version instead of per-column
  probing. A cross-language contract test builds a db with Python and runs
  every `SssfDb` query against it, so reader/writer drift fails CI.

- **Flows as the only entry point + chain set reduction (#89)** — work starts
  only through `sssf flow plan|implement|deploy`; ad-hoc `sssf run <adw> "<prompt>"`
  is removed. The shipped `adws/modules/` template set collapses from the 13
  one-off combos to the three flow chains: `adw_plan` (exploration →
  grill-with-docs → spec → tickets, exploration skippable), `adw_implement`
  (triage → build → quality → builder self-review → review), and `adw_deploy`
  (sandbox → terminal signoff → bump → MR → e2e → release, with `--yes` as the
  explicit signoff automation escape). `init --refresh` lands the three chains
  alongside legacy combos without touching edited chains; legacy removal is
  manual. Already-stamped projects' legacy ADWs stay runnable via the legacy
  `sssf ticket run` path and `sssf sandbox restart` (which re-runs the original
  ADW). Run control moved to `sssf sandbox stop|restart`; the viz trace page
  shells the new form. Per-flow deep semantics (ticket transforms, machine
  transitions, workbench, canary/promote/close-by-commits) land in #91/#92/#96/#97.

### Added

- **MR monitor inside the viz service (#98)** — the viz server now sweeps every
  registered project's `ready-to-deploy` tickets with a registered MR every 10
  minutes, polls GitLab for pipeline/merge state, and alerts through the notify
  adapter (`sssf notify`) on pipeline green, failed, and merged — exactly once
  per transition (last-seen state in `monitor_state`; a replaced MR resets it).
  Lives and dies with the server: boot sweep + interval, cleared on SIGINT. No
  separate daemon. MRs attach to tickets via the new `sssf mr add/list/rm`
  command (ticket_mrs table, audited); per-project config in
  `adws/config/monitor.json` (gitlab_url + token_env, token from env or the
  project .env). New `/api/projects/:project/monitor` status route.

### Added

- **Integration branch (post-success auto-merge)** — when enabled (default) in
  `sssf.config.yaml`, fresh sandboxed runs branch from an integration branch
  (`integration.branch`, default `dev`) and successful runs merge back into it
  automatically, created from `main` when missing. Configurable: `enabled`,
  `branch`, `push` (origin push on remote repos), `resolve` (merge conflicts
  cleared by the coding agent with the resolving-merge-conflicts skill). The
  merge never touches a dirty/detached operator checkout and always restores
  the branch it switched away from; every outcome lands as an `integration`
  event on the run's trace.

- **Auto-rebuild of the runner image** — `sssf heal` now rebuilds the sandbox
  image when its baked engine fingerprint no longer matches the local sssf
  (previously every sandboxed run died on spawn until someone ran
  `sssf sandbox build` by hand). Images are deduped across projects, skipped
  when docker is unavailable or sandbox is disabled, and failed builds are
  retried after a 30-minute cooldown. `sssf sandbox build` reuses the same
  build path.
- **Kanban cards cap their phase dots to a rolling window** — a restarted
  session keeps appending phases at rising seq, so its card accumulated
  dozens of dots and overflowed the card (f9e445e9: 26 across three
  attempts). Cards now show the newest 12 phases — the latest attempt — with
  a dimmed +N marker for the phases that rolled off.


- **Quality failures carry their output into the trace** — a failing
  `quality.checks` command (e.g. snyk) now embeds the output tail in the gate
  note (the viz's gates panel renders it as a block — no container exec to
  read `command.log`) and in the `quality:<name>` event payload.

- **Credential rejections are environment errors, not code failures** — snyk
  auth failures (SNYK-0005/0003, `Authentication error`) classify as an
  environment error, so the builder repair loop never burns agent calls
  trying to "fix" a rejected token.
- **Designer runs impeccable in full multi-context mode** — the designer agent
  now declares the subagents harness extension and the `subagent_*` tools
  (create/continue/list/remove), so impeccable's audit/critique passes spawn
  their shipped sub-agents instead of degrading to an in-thread
  single-context run ("no sub-agent tools exposed").

- **Headless Chrome in the runner image** — impeccable's browser engine
  (audit/critique/visual contrast over a real rendered page) now works in
  sandboxed runs instead of degrading to the node-only detector. Chrome for
  Testing publishes no linux-arm64 build and impeccable's pinned puppeteer
  resolves arm64 to the x86_64 binary, so the image installs Google Chrome's
  arm64 .deb and pins puppeteer to it via `PUPPETEER_EXECUTABLE_PATH`; every
  launch goes through a wrapper adding the container flags
  (`--no-sandbox --disable-dev-shm-usage` — docker seccomp blocks namespace
  cloning and impeccable only disables the sandbox under CI).


### Fixed

- **Sandbox config validation failure on cold containers** — the runner image's
  entrypoint copied the operator's `settings.json` wholesale into the sandbox
  pi home; pi 0.84.x then installed the operator's interactive `packages`
  (honcho-memory, voice-stt, context7, web-search, `git:obra/superpowers`) on
  first use — a network bootstrap that exceeded the ADW's 30s `pi
  --list-models` catalog timeout, so every agent's model reported "not found"
  and the run died at config validation. The entrypoint now copies only the
  model catalog (`models.json`, `models-store.json`, `auth.json`) and writes a
  minimal sandbox `settings.json` (no packages/skills/extensions/themes, no
  host MCP cache). Catalog resolves in ~1s on a fresh container.

- **Sandbox snyk auth is OAuth-only** — `SNYK_TOKEN` is never forwarded into
  the container (a token would outrank the mounted OAuth session; stale/UAT
  tokens 401 against prod -> SNYK-0005). Docs and the config template say so.

- **Restarted runs can supersede a frozen host row** — the monitor's
  forward-merge only updated un-ended host rows, so a restart that missed
  `reopen_session` left the host session ended at the previous run's terminal
  state forever (f9e445e9: run C succeeded, the UI still showed run B's
  failure). A strictly newer ended source row now supersedes an ended host
  row; an older or torn copy still never downgrades a newer terminal state.
- **Visualizer ticket actions** — the kanban Run/Backlog/Sync buttons threw
  `ReferenceError: runTicket is not defined` (the handlers were never imported
  after the `ticketRoutes.ts` extraction) and the spawned CLI's exit code was
  read before the process exited, so every action reported failure. Both are
  fixed; a boot-level regression test now drives the real server against a
  fake `sssf` CLI.

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
