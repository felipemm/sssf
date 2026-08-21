# Contributing to sssf

Thanks for contributing! sssf is a small project with strong conventions —
the "golden rules" below keep the engine deterministic and the trace
contract honest. Please read this before opening a PR.

## The short version

1. **Open an issue first** for anything non-trivial (a bug report, a behavior
   change, or a new feature). Small fixes are welcome as direct PRs.
2. **Work on a branch** off `main`, named `feat/<thing>` or `fix/<thing>`.
3. **Push and open a PR** against `main`. CI must pass; a PR that breaks
   `pytest` or the visualizer's `bun test` suite will not be merged.
4. **Keep the diff minimal and reviewable.** Ported/upstream code is the one
   exception (see below) — never rewrite it cosmetically.

## Dev setup

```bash
# Python engine + CLI
uv sync --group dev        # installs the project editable + pytest
uv run pytest              # run the suite

# Visualizer (Vue 3 + Vite + bun)
cd src/sssf/apps/visualizer
bun install
bun test                   # server unit tests
bun run typecheck          # vue-tsc
bun run lint               # oxlint
bun run build              # vite build
```

## The golden rules

- **Porting is verbatim.** The engine and visualizer are a repackaging of
  [Super Simple Software Factory](https://github.com/disler/super-simple-software-factory);
  ported code is copied verbatim and changes land as minimal, reviewable diffs.
- **Relative imports only** inside `sssf.adw_modules/`.
- **The output contract is a synced triad.** Changing an envelope type in
  `data_types.py` means updating the matching `## Report` example in the
  agent's `user.md` and every `output_type=` call site — in the same edit.
- **Tests are the gate.** A change ships with its test.

## Engine internals

The package ships its own deep-dive: `src/sssf/docs/contributing.md` covers
the engine layout (`cli.py`, `commands/`, `adw_modules/`, the tracer), the
session-lifecycle invariants (reap-on-rerun, the failsafe `sys.excepthook`,
single-decision `session_finish`), and the robustness notes learned in the
field (WAL sidecars, cold readonly dbs, `SQLITE_IOERR` recovery).

## Commit messages

Concise, prefixed, lowercase — e.g. `feat: ...`, `fix: ...`, `docs: ...`,
`refactor: ...`, `e2e: ...`. The history is the changelog source; write
messages that read well in a list.

## Local checks

Before pushing, run the same gates CI enforces:

- `uv run ruff check src/sssf tests`
- `uv run mypy src/sssf`
- `uv run pytest`
- `cd src/sssf/apps/visualizer && bun test`

Install the git hooks once: `uv run pre-commit install` (ruff + hygiene run on every commit).
