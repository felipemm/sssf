# Super Simple Software Factory (sssf)

A global CLI that stamps repeatable agents-plus-code workflows into a codebase.
Deterministic Python owns sequencing, retries, and acceptance; coding agents work
inside bounded phases; typed envelopes and SQLite traces make every run
observable. **Agent proposes, code disposes.**

> **Website:** [felipemm.github.io/sssf](https://felipemm.github.io/sssf)

## Quickstart

```bash
uv tool install sssf
sssf init
sssf ticket new "Explore dark mode"
sssf flow plan                 # or: sssf flow plan <ticket-id>
sssf flow implement afk        # implement ready-for-agent tickets
sssf viz                       # inspect the project in the visualizer
```

`sssf flow plan`, `sssf flow implement`, and `sssf flow deploy` are the only
work-entry points. Every run belongs to a ticket. Use `sssf ticket sync` to
pull external tickets and mirror their machine state back to the tracker.

## The 2.0 project footprint

`sssf init` stamps the factory under `.adws/`:

- `.adws/modules/` — the plan, implement, and deploy chains
- `.adws/config/` — roster, ticketing, deploy, monitor, and notification config
- `.adws/data/` — the project database, session traces, and agent prompts
- `.adws/prompts/`, `.adws/specs/`, `.adws/kb/` — project work artifacts

Upgrading a project with the old visible `adws/` footprint backs that tree up
to `sssf-<timestamp>/`, warns that its customizations are not migrated, then
stamps a fresh `.adws/`. The project SQLite database is the only state carried
forward and is migrated to the current schema. Review the backup and reapply any
custom config or chain edits after init. Flow commands on a legacy project stop
with an instruction to run `sssf init`.

## Visualizer

`sssf viz` starts the global multi-project UI. It provides the status dashboard,
ticket board, ticket details, session history, and run traces. The visualizer is
the trace-reading surface; the removed terminal trace verbs are not part of the
2.0 CLI.

The board uses the same ticket machine as the CLI. Its stop/restart controls
shell to `sssf sandbox stop` and `sssf sandbox restart`; ticket run actions use
`sssf flow implement`.

## Ticket lifecycle

- `sssf ticket new "<title>"` captures a title-only idea in `needs-triage`.
- `sssf flow plan <ticket-id>` turns an idea into a spec and implementation
  tickets.
- `sssf flow implement <ticket-id>` runs one `ready-for-agent` ticket;
  `sssf flow implement afk` works the queue one ticket per run.
- `sssf ticket backlog <ticket-id>` requeues a ticket with optional feedback.
- `sssf ticket sync` pulls tickets and mirrors machine labels, completion, and
  rejection comments to their origin. It is the only tracker writer.

## Operational commands

| Command | Purpose |
|---|---|
| `sssf init [--refresh] [--force]` | Stamp or refresh a project and register it |
| `sssf flow plan [<ticket-id>]` | Explore and plan an idea into implementation tickets |
| `sssf flow implement <ticket-id>\|afk` | Implement one ticket or the ready queue |
| `sssf flow deploy [--down] [--revert <ticket-id>]` | Sign off and release the dev batch |
| `sssf ticket new\|sync\|list\|context\|backlog` | Capture, mirror, inspect, steer, and requeue tickets |
| `sssf sandbox build\|list\|stop\|restart` | Manage runner image and active sandboxes |
| `sssf sweep [--project P] [--days N]` | Archive old runs and clean their sandbox resources |
| `sssf heal start\|stop\|status` | Run the self-healing monitor |
| `sssf viz [start\|stop]` | Open or stop the global visualizer |
| `sssf projects [list\|remove <name>]` | Manage registered projects |
| `sssf doctor` / `sssf upgrade` | Check prerequisites / upgrade the CLI |

## Requirements

Python 3.11+ (installed as a uv tool), `pi` as the coding agent, `bun` for the
visualizer, `docker` for sandboxed runs, and a registered model in the coding
agent's catalog (`sssf doctor` checks prerequisites).

## Development

```bash
uv sync --group dev && uv run pytest
cd src/sssf/apps/visualizer && bun test
```

See `src/sssf/docs/customizing.md` for the supported customization surface and
`CONTRIBUTING.md` for development guidance.

## Docs

- [CHANGELOG.md](CHANGELOG.md) — release history
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- `src/sssf/docs/customizing.md` — chains, roster, definition of done
- `docs/adr/` — accepted architecture decisions
