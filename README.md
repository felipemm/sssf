# Super Simple Software Factory (sssf)

A global CLI that stamps **repeatable agents-plus-code workflows** into any
codebase. Deterministic Python ADW scripts own sequencing, retries, and
acceptance; coding agents (Pi) work inside bounded phases; typed JSON envelopes
carry context between them; everything streams into SQLite for a polled
visualizer. **Agent proposes, code disposes.**

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

## Commands

| Command | What it does |
|---|---|
| `sssf init [--refresh] [--force]` | stamp chains/config/prompts into the project, register it |
| `sssf run <adw> "<prompt>" [--adw-id X]` | execute a chain (the `adw_` prefix is optional) |
| `sssf sessions / phases <id> / tail <id> / procs <id>` | trace queries over the WAL db |
| `sssf projects [list|remove <name>]` | manage the registry |
| `sssf viz [--port N] [--db PATH]` | global trace visualizer (bun required) |
| `sssf doctor` | check global prerequisites (`uv`, `pi`, `bun`, `sqlite3`) |
| `sssf upgrade` | `uv tool upgrade sssf` |

## Requirements

Python 3.11+ (installed as a uv tool), `pi` as the coding agent, `bun` for
`sssf viz`, and a registered model in the coding agent's catalog
(`sssf doctor` checks all of it).

## Docs

- `src/sssf/docs/customizing.md` — chains, roster, definition of done
- `src/sssf/docs/contributing.md` — engine layout and how to ship changes
- Design spec: `docs/superpowers/specs/2026-08-14-sssf-global-cli-design.md`
  (in the source repo)
