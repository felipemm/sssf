# Contributing to sssf

sssf is a global CLI package: the engine (`sssf/adw_modules/`), the starter
templates (`sssf/templates/`), the `--skill` payload (`sssf/SKILL.md`), and the
visualizer (`sssf/apps/visualizer/`) all ship in the wheel. `sssf init` copies
templates into a project; the engine and visualizer are read from the package.

## Layout

```
src/sssf/
├── cli.py                  # argparse dispatch — subcommands live in commands/
├── commands/               # init, run, obs_cmds, misc, viz — each run(...) -> int
├── registry.py             # ~/.sssf/projects.json (runtime state, not config)
├── project.py              # project resolution + paths
├── obs.py                  # read-only trace queries
├── adw_modules/            # the engine: runner, agents, tracer, gates, …
├── templates/              # stamped by `sssf init` (chains, config, prompts)
├── SKILL.md                # the --skill payload agents run under
├── docs/                   # this file + customizing.md
└── apps/visualizer/        # Vue 3 + Vite + bun, served by `sssf viz`
```

## The rules

- **Porting is verbatim.** The engine and visualizer are copied from the pinned
  SSSF source and must never be rewritten cosmetically. Changes land as minimal,
  reviewable diffs.
- **Relative imports only** inside `sssf.adw_modules/`.
- **The output contract is a synced triad.** Changing an envelope type in
  `data_types.py` means updating the matching `## Report` example in the agent's
  `user.md` and every `output_type=` call site — in the same edit.
- **Tests are the gate.** Everything is covered in `tests/` (pytest) and the
  visualizer has `server/*.test.ts` (bun test). A change ships with its test.

## Working on the engine

`adw_modules/` is where sequencing, retries, gates, and the trace live. The
usual flow:

1. Change the module (e.g. add a gate in `gates.py`).
2. Add/extend a test under `tests/`.
3. `uv run pytest` from the repo root (uv installs the project editable + the
   dev group).
4. Ship it: `uv tool upgrade sssf` re-installs the tool for projects that use it.

### Session lifecycle (don't break these invariants)

- `session.ensure()` **reaps** a previous run still marked in flight under the
  same `adw_id` before starting: terminates its recorded processes (pid
  verified against the recorded command via `_matches_recorded` — a recycled
  pid must never be killed) and marks its open phases/session `failed`.
- A failsafe `sys.excepthook` (installed by `ensure()`) writes
  `session_finish(ok=False)` on any uncaught exception — the phase manager
  covers in-phase errors; the hook covers everything else.
- `_finalize_when_killed` turns SIGTERM/SIGINT into `SystemExit` so a killed
  run closes its own trace.
- `session_finish(ok)` is the single place the session row's status is decided
  (with `run.finish(accepted=)`); keep it that way.

### Robustness notes (learned in the field)

- **WAL sidecars must stay untracked.** If `sssf.db-wal`/`-shm` are committed,
  agents git-checkout them over a live db and every open reader hits
  `SQLITE_IOERR_SHMOPEN`. `sssf init` gitignores them; a pre-fix project needs
  `git rm --cached`.
- **Cold WAL dbs are unreadable readonly** (no `-shm`, no live tracer). The
  visualizer's `openReadonly()` probes and falls back to a read-write open;
  never regress to a bare `new Database(path, { readonly: true })`.
- **The visualizer recovers from `SQLITE_IOERR*`** by dropping cached
  connections and retrying once — a replaced db file must not wedge the UI.

## Working on the visualizer

```bash
cd src/sssf/apps/visualizer
bun install
bun test          # server unit tests
bun run typecheck # vue-tsc
bun run lint      # oxlint
bun run build     # vite build → dist/
```

`sssf viz` serves the app over the registry (`~/.sssf/projects.json`); the
server also supports adhoc single-db mode via `--db`/`SSSF_DB` (the old
unscoped `/api/sessions` routes).
