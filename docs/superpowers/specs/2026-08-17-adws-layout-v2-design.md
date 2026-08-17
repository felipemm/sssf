# Stamped adws/ Layout v2 — Design

Date: 2026-08-17
Status: Draft for review

## Goal

Restructure the stamped `adws/` layout to its v2 contract, with a strict
migration path for existing projects:

```
adws/
├── modules/    adw_*.py chains                        (was: at adws/ root)
├── config/     sssf.config.yaml, ticketing.yaml       (was: adw_ssfs_config)
├── data/       sssf.db, sessions/, prompt_engineering/,
│               harness_engineering/                   (was: adw_data)
├── prompts/    your prompt files (ticketing writes NN-<slug>.md here)
├── specs/      planner-committed plans
└── kb/         documenter-committed write-ups         (was: app_docs)
```

**Strict, no runtime fallback**: the engine resolves the new paths only.
Existing projects using the old layout must run `sssf init --refresh` to
migrate — the command warns, backs up `adws/`, then moves it to v2.

## 1. Central path resolution: `src/sssf/adw_modules/paths.py`

New module, the single place the v2 paths are defined. Every engine caller
uses it; no scattered path strings.

```python
def modules_dir(root: Path) -> Path:   # adws/modules
def config_dir(root: Path) -> Path     # adws/config
def config_file(root: Path) -> Path    # adws/config/sssf.config.yaml
def ticketing_file(root: Path) -> Path # adws/config/ticketing.yaml
def data_dir(root: Path) -> Path       # adws/data
def kb_dir(root: Path) -> Path         # adws/kb
def prompts_dir(root: Path) -> Path    # adws/prompts
def specs_dir(root: Path) -> Path      # adws/specs
def is_legacy_layout(root: Path) -> bool
def warn_if_legacy(root: Path, *, command: str) -> bool
```

`is_legacy_layout` is true when any of these exist: `adws/adw_ssfs_config/`,
`adws/adw_data/`, `adws/app_docs/`, or a root-level `adws/adw_*.py`.

`warn_if_legacy` prints a clear banner on every engine command entry and
returns whether legacy was detected:

```
sssf: legacy adws layout detected in <root> — chains/config/data live at the
      v1 paths. Run `sssf init --refresh` to migrate (it backs up adws/ first,
      then moves to the v2 layout: modules/, config/, data/, prompts/, specs/, kb/).
```

## 2. `sssf init --refresh` = the migration path

Fresh `sssf init` stamps v2: templates move to the v2 tree
(`templates/adws/modules/…`, `templates/adws/config/sssf.config.yaml`,
`templates/adws/data/…`), and init scaffolds `prompts/`, `specs/`, `kb/` with a
one-line README each (git cannot track empty dirs; the README says what lives
there).

When `--refresh` runs on a legacy project, migration proceeds in this order:

1. **Warn** — the legacy banner (Section 1), plus: "migration will back up
   `adws/` and move it to the v2 layout". Respects existing `--confirm` /
   `--auto` semantics (auto accepts).
2. **Backup** — full copy of `adws/` to a timestamped sibling
   `adws.backup.<YYYYmmdd-HHMMSS>/`; adds `adws.backup.*/` to `.gitignore`
   so the backup is never committed.
3. **Migrate (move)** — `adw_ssfs_config/` → `config/`, `adw_data/` → `data/`,
   `app_docs/` → `kb/`, root `adw_*.py` → `modules/`. If a v2 target already
   exists, the legacy item is NOT moved (never overwrite migrated data).
4. **Rewrite path literals** in the moved chain files: the known literals
   `adws/adw_ssfs_config/`, `adws/adw_data`, `adws/app_docs` are replaced with
   `adws/config/`, `adws/data`, `adws/kb` respectively — so even custom chains
   resolve post-migration without relying on runtime fallback.
5. **Scaffold** — stamp anything still missing from templates (prompts/,
   specs/ READMEs, chains if the project has none), per the existing
   copy semantics.
6. **Idempotent** — a project already at v2 (or already migrated) sees no
   banner, no backup, no moves; refresh behaves exactly as today.

`adw_data` content (db, sessions) is moved, not copied — the DB path changes
once, atomically with the rest of the migration. The backup is the safety net.

## 3. Engine touch points (strict)

All path references move to `paths.py`; legacy projects fail loudly via
`warn_if_legacy` (the banner names the fix — no confusing file-not-found):

- `src/sssf/commands/run.py` — `_adw_file` resolves `modules_dir(root)/{name}.py`
  then the installed-templates fallback (`templates/adws/modules/{name}.py`);
  sandbox cmd path; `load_config` path; "no ADW named" error text.
- `src/sssf/commands/ticket.py` — default ADW path (dual-resolved into the
  sandbox cmd), config path, banner at entry.
- `src/sssf/project.py` — `data_dir` helper; `find_project` marker unchanged
  (`adws/` dir).
- `src/sssf/sandbox.py` — `sandbox_env` data dir, mounts, per-run db path
  (`adws/data/sssf.db` inside the sandbox worktree), `project_db_path`.
- `src/sssf/healer.py` — per-run db path in the sandbox worktree (lines
  referencing `adws/adw_data/sssf.db` → `adws/data/sssf.db`).
- `src/sssf/ticketing.py` — `next_prompt_name` (already `adws/prompts`), db
  paths via helper, banner at entry.
- `src/sssf/commands/obs_cmds.py`, `commands/viz.py`, `commands/sweep.py`,
  `registry.py` — `sssf.db` paths via helper, banner at entry.
- `src/sssf/adw_modules/data_types.py` — `data_dir` default
  `adws/adw_data` → `adws/data`.
- `src/sssf/adw_modules/permissions.py` — protected-path resolution via helper.
- `src/sssf/commands/init.py` — stamps v2 (Section 2), AGENTS.md block text,
  gitignore entries, migration logic.

## 4. ADW templates — runtime config resolution

All 13 ADW templates change `--config` from a literal default to runtime
resolution: `--config` defaults to `None`; when absent,
`main(prompt, config=None, …)` resolves `paths.config_file(Path.cwd())`.
This removes the layout literal from every chain — a chain works identically
under v1 (pre-migration) and v2 (post-migration), because the ADW never bakes
a path that the migration rewrites.

The migration's literal rewrite (Section 2.4) still runs on moved files as a
safety net for custom chains that predate runtime resolution.

## 5. Config content updates

`src/sssf/templates/sssf.config.yaml`:
- `data_dir: adws/adw_data` → `adws/data`
- `protected_files`: `adws/adw_*.py` → `adws/modules/`; keep `adws/adw_ssfs_config/`
  and `adws/adw_data/` coverage via the new names `adws/config/` + `adws/data/`
- planner `writes: [adws/specs/]` (unchanged — spec folder is new)
- documenter `writes: [adws/app_docs/]` → `adws/kb/` (plus `**/*.md`)

Agent prompts that name `adws/app_docs/` (documenter user prompt, ADW
docstrings) → `adws/kb/`.

## 6. Docs

- `src/sssf/docs/customizing.md` — the layout tree (Section Goal) + prose.
- Site pages referencing adws paths: `configuration`, `run-semantics`,
  `cli`, `sandbox`, `core-concepts` (audit + update).
- `README.md` (feature bullets referencing `adws/adw_*.py` /
  `adws/adw_ssfs_config` / `adws/adw_data`).
- `AGENTS.md` stamp block in `init.py`.

## 7. Tests

- `tests/test_paths.py` (new) — every helper returns the v2 path;
  `is_legacy_layout` for each legacy marker; `warn_if_legacy` prints + returns
  true on legacy, silent on v2.
- `tests/test_init.py` — fresh init stamps v2 (modules/, config/, data/,
  prompts/, specs/, kb/ incl. READMEs); **migration tests**: legacy fixture →
  refresh warns, creates `adws.backup.<ts>/` (gitignored), moves the four
  legacy items, rewrites literals in moved chains, scaffolds prompts/specs,
  idempotent re-run (no second backup, no moves), v2 project unaffected.
- `tests/test_templates.py` — 13-chain glob moves to `templates/adws/modules/`;
  artifact-folder test covers `kb/`; ADW `--config` default is None + resolves.
- `tests/test_run.py`, `test_ticket_cli.py`, `test_sandbox_docker.py`,
  `test_healer.py`, `test_sweep.py`, `test_misc.py`, `test_registry.py`,
  `test_engine_port.py` — path expectation updates (v2), plus a legacy
  `sssf run` warns (banner) test.

## 8. Verification

1. Full pytest green.
2. Fresh-init project: layout is exactly the v2 tree; `sssf run scout` works.
3. Legacy fixture project (chains at root, `adw_ssfs_config/`, `adw_data/`,
   `app_docs/`): `sssf run scout` prints the migration banner and fails
   loudly; `sssf init --refresh` backs up + migrates; post-migration the
   project runs clean; the backup tree is intact and untracked.
4. inkwell (a live v1 project) is left untouched until the operator runs
   `sssf init --refresh` on it deliberately.

## Out of scope

- Runtime dual lookup / fallback (deliberately strict — see Goal).
- Automatic migration of live projects (the operator opts in via refresh).
- Changes to the sandbox image or runner Dockerfile (layout only).
- Renaming `adws/adw_modules/` (the engine package import path stays
  `sssf.adw_modules`).
