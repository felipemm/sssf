# Super Simple Software Factory (sssf)

A global CLI that stamps **repeatable agents-plus-code workflows** into any
codebase. Deterministic Python ADW scripts own sequencing, retries, and
acceptance; coding agents (Pi) work inside bounded phases; typed JSON envelopes
carry context between them; everything streams into SQLite for a polled
visualizer. **Agent proposes, code disposes.**

> **Origin:** a repackaging of [Super Simple Software Factory](https://github.com/disler/super-simple-software-factory)
> by [IndyDevDan](https://github.com/disler) — the engine and visualizer are ported from that
> project's skill; this repo ships it as a globally installed CLI. MIT, same license.

## Quickstart

```bash
uv tool install --editable .     # or: uv tool install sssf
sssf init                        # stamp the factory into this repo
sssf run scout "map the repo"    # run a chain (scout is read-only)
sssf sessions                    # see the trace
sssf viz                         # open the global visualizer on :4600
```

Three principles:

- **Observable** — every run, phase, envelope, gate, and process streams into
  `adws/adw_data/sssf.db` (WAL, so reads never block writers). Watch live in
  `sssf viz` or from the terminal with `sssf sessions / phases / tail / procs`.
- **Customizable** — `sssf init` stamps only the customization surface: your
  chains (`adws/adw_*.py`), your roster (`adws/adw_sssf_config/sssf.config.yaml`),
  your prompts (`adws/adw_data/prompt_engineering/`). The engine is package code
  — see `src/sssf/docs/customizing.md`.
- **Reusable** — one global install, any number of projects, registered in
  `~/.sssf/projects.json`. `sssf viz` is a global service over all of them with
  a project picker in the UI.

## Visualizer

- **Status dashboard** — per-project KPIs: runs/health, cost & tokens (actual
  billing + token-share per agent and per model), quality gates, per-agent
  models, git repo stats + yearly contributions heatmap, trend charts
  (7/30/90d), ticket pipeline. Served at `#/status`.

## Run semantics

- **Re-runs reap the previous run.** Running an ADW again with the same
  `--adw-id` terminates any processes the previous run left in flight (the pid
  is verified against the recorded command first, so a recycled pid is spared)
  and marks its open phases and session `failed` — no zombie `running` sessions.
- **Failures always land in the trace.** Per-phase exceptions are recorded by
  the phase manager; a failsafe hook marks the session failed on *any* other
  uncaught exception in the ADW process.
- **No-op re-runs are green.** If `commit_build` finds nothing to commit and the
  builder reported no changes, the work is already implemented and verified —
  the run succeeds with a note and skips the document chain. A builder that
  *claims* changes that never landed is a hard failure.
- **WAL sidecars stay untracked.** `sssf init` gitignores `sssf.db-wal` /
  `sssf.db-shm` — if they get committed, agents treat them as clutter and
  `git checkout` them over the live db, which breaks open reader connections.

## Sandboxed runs (parallel-safe)

Each run executes in its own sandbox — a git worktree (branch `sssf/<adw_id>`)
bind-mounted into a `sssf-runner` container — so multiple runs proceed in
parallel without touching the project tree. The ADW runs its normal stages
inside the container; when it exits (success or fail) a monitor tears the
sandbox down automatically (container + worktree). The branch `sssf/<adw_id>`
survives as the deliverable — the engineer merges it or opens a PR with their
own tooling.

- `sssf sandbox build` — build/refresh the runner image
- `sssf sandbox list` — show sandboxes and their branches
- `sssf sandbox prune [<adw_id>|--all]` — delete a resolved run's branch + leftovers
- `sssf run stop <adw_id>` — kill a live run (sandbox torn down, run marked failed)
- `--no-sandbox` — run in the current dir (today's behavior), for debugging

## Commands

| Command | What it does |
|---|---|
| `sssf init [--refresh] [--force]` | stamp chains/config/prompts into the project, register it |
| `sssf run <adw> "<prompt>" [--adw-id X]` | execute a chain (the `adw_` prefix is optional) |
| `sssf sessions / phases <id> / tail <id> / procs <id>` | trace queries over the WAL db |
| `sssf projects [list|remove <name>]` | manage the registry |
| `sssf viz [start|stop] [--port N] [--db PATH]` | global trace visualizer as a background service (bun required); `start` opens the browser, `stop` shuts it down |
| `sssf doctor` | check global prerequisites (`uv`, `pi`, `bun`, `sqlite3`) |
| `sssf ticket add/sync/list/run [--project]` | ticketing integration (internal add, external sync, backlog run) — optional, per-project `adws/adw_sssf_config/ticketing.yaml` |
| `sssf upgrade` | `uv tool upgrade sssf` |

## Requirements

Python 3.11+ (installed as a uv tool), `pi` as the coding agent, `bun` for
`sssf viz`, and a registered model in the coding agent's catalog
(`sssf doctor` checks all of it).

## Docs

- `src/sssf/docs/customizing.md` — chains, roster, definition of done
- `src/sssf/docs/contributing.md` — engine layout and how to ship changes
- `docs/superpowers/specs/2026-08-14-sssf-global-cli-design.md` — original design
- `docs/superpowers/plans/2026-08-14-sssf-global-cli.md` — original implementation plan
- `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md` — post-implementation changes (this doc records them)
- `docs/superpowers/plans/2026-08-15-sssf-global-cli-revisions.md` — as-executed record with commit map
