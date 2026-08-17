# Stamped adws/ Layout v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the stamped `adws/` layout to v2 (`modules/`, `config/`, `data/`, `prompts/`, `specs/`, `kb/`) with strict engine resolution and a warn → backup → migrate path in `sssf init --refresh`.

**Architecture:** A single `paths.py` module centralizes the v2 paths (strict — no runtime fallback). Templates move to a v2 tree that mirrors the stamped layout. ADWs resolve their config at runtime instead of baking a literal path. `sssf init --refresh` detects a legacy layout, backs up `adws/`, moves it to v2, and rewrites path literals in moved chains. Every engine command banners on legacy projects.

**Tech Stack:** Python (sssf engine, pytest), YAML, git, shell.

**Spec:** `docs/superpowers/specs/2026-08-17-adws-layout-v2-design.md`

## Global Constraints

- Work in the isolated worktree `.worktrees/adws-layout-v2` on branch `feat/adws-layout-v2` — main stays untouched; PR at the end.
- **Strict, no runtime fallback**: the engine resolves the v2 paths only. Legacy detection exists solely to warn and to drive migration.
- The `adw_plan_build_test_quality_design` → `adw_design_sdlc` rename is already committed (`0c6cfa4`) — do not rename again.
- `templates/adws/` must mirror the stamped v2 layout exactly (modules/, config/, data/, prompts/, specs/, kb/).
- Migration never overwrites: if a v2 target exists, the legacy item is not moved.
- Every ADW template resolves `--config` at runtime (`None` default); no layout literals remain in chain defaults.
- `adws.backup.*/` must be gitignored; the backup is a full copy of `adws/` created before any move.
- Commit per task with conventional messages.
- Run `uv run pytest` (venv synced) for Python tests; `cd src/sssf/apps/visualizer && bun test` for the visualizer (unchanged by this plan, but the full suite runs at the end).

---

### Task 1: `paths.py` — strict v2 resolution + legacy detection

**Files:**
- Create: `src/sssf/adw_modules/paths.py`
- Create: `tests/test_paths.py`

**Interfaces:**
- Produces the module every later task imports:
  `modules_dir(root) -> Path`, `config_dir(root) -> Path`, `config_file(root) -> Path`,
  `ticketing_file(root) -> Path`, `data_dir(root) -> Path`, `kb_dir(root) -> Path`,
  `prompts_dir(root) -> Path`, `specs_dir(root) -> Path`,
  `is_legacy_layout(root) -> bool`, `warn_if_legacy(root, *, command) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/test_paths.py`:

```python
from pathlib import Path

import pytest

from sssf.adw_modules import paths

V2 = {
    "modules_dir": "adws/modules",
    "config_dir": "adws/config",
    "config_file": "adws/config/sssf.config.yaml",
    "ticketing_file": "adws/config/ticketing.yaml",
    "data_dir": "adws/data",
    "kb_dir": "adws/kb",
    "prompts_dir": "adws/prompts",
    "specs_dir": "adws/specs",
}


@pytest.mark.parametrize("fn,rel", V2.items())
def test_v2_paths(tmp_path, fn, rel):
    assert getattr(paths, fn)(tmp_path) == tmp_path / rel


@pytest.mark.parametrize("marker", [
    "adws/adw_ssfs_config/sssf.config.yaml",
    "adws/adw_data",
    "adws/app_docs",
    "adws/adw_simple_sdlc.py",
])
def test_legacy_detected(tmp_path, marker):
    target = tmp_path / marker
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    assert paths.is_legacy_layout(tmp_path) is True


def test_v2_layout_not_legacy(tmp_path):
    (tmp_path / "adws" / "modules").mkdir(parents=True)
    assert paths.is_legacy_layout(tmp_path) is False


def test_warn_if_legacy_prints_and_returns(capsys, tmp_path):
    (tmp_path / "adws" / "adw_data").mkdir(parents=True)
    assert paths.warn_if_legacy(tmp_path, command="run") is True
    out = capsys.readouterr().out
    assert "legacy adws layout" in out and "sssf init --refresh" in out


def test_warn_if_legacy_silent_on_v2(capsys, tmp_path):
    (tmp_path / "adws" / "modules").mkdir(parents=True)
    assert paths.warn_if_legacy(tmp_path, command="run") is False
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `No module named 'sssf.adw_modules.paths'`

- [ ] **Step 3: Create `src/sssf/adw_modules/paths.py`**

```python
"""The v2 stamped adws/ layout — the single source of truth for its paths.

Strict, no runtime fallback: the engine resolves these paths only. A project
that still lives at the v1 layout must run `sssf init --refresh` to migrate
(which warns, backs up adws/, and moves it). Legacy detection exists solely
to warn and to drive that migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

ADWS = "adws"


def modules_dir(root: Path) -> Path:
    return root / ADWS / "modules"


def config_dir(root: Path) -> Path:
    return root / ADWS / "config"


def config_file(root: Path) -> Path:
    return root / ADWS / "config" / "sssf.config.yaml"


def ticketing_file(root: Path) -> Path:
    return root / ADWS / "config" / "ticketing.yaml"


def data_dir(root: Path) -> Path:
    return root / ADWS / "data"


def kb_dir(root: Path) -> Path:
    return root / ADWS / "kb"


def prompts_dir(root: Path) -> Path:
    return root / ADWS / "prompts"


def specs_dir(root: Path) -> Path:
    return root / ADWS / "specs"


# The v1 markers — any one present means the project predates v2.
_LEGACY_MARKERS = (
    "adws/adw_ssfs_config",
    "adws/adw_data",
    "adws/app_docs",
)


def is_legacy_layout(root: Path) -> bool:
    for marker in _LEGACY_MARKERS:
        if (root / marker).exists():
            return True
    # v1 chains sat directly under adws/ — e.g. adws/adw_simple_sdlc.py
    if any((root / ADWS).glob("adw_*.py")):
        return True
    return False


def warn_if_legacy(root: Path, *, command: str) -> bool:
    if not is_legacy_layout(root):
        return False
    print(
        f"sssf: legacy adws layout detected in {root} — chains/config/data live at "
        "the v1 paths. Run `sssf init --refresh` to migrate (it backs up adws/ "
        "first, then moves to the v2 layout: modules/, config/, data/, prompts/, "
        "specs/, kb/).",
        file=sys.stderr,
    )
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/paths.py tests/test_paths.py
git commit -m "feat(paths): v2 adws layout resolution + legacy detection"
```

---

### Task 2: Templates → v2 tree + scaffold READMEs + config content

**Files:**
- Move: `src/sssf/templates/adws/adw_*.py` → `src/sssf/templates/adws/modules/`
- Move: `src/sssf/templates/sssf.config.yaml` → `src/sssf/templates/adws/config/sssf.config.yaml`
- Move: `src/sssf/templates/ticketing.yaml` → `src/sssf/templates/adws/config/ticketing.yaml`
- Move: `src/sssf/templates/prompt_engineering/` → `src/sssf/templates/adws/data/prompt_engineering/`
- Move: `src/sssf/templates/harness_engineering/` → `src/sssf/templates/adws/data/harness_engineering/`
- Create: `src/sssf/templates/adws/prompts/README.md`, `adws/specs/README.md`, `adws/kb/README.md`
- Modify: `src/sssf/templates/adws/config/sssf.config.yaml` (content, below)
- Modify: `tests/test_templates.py`, `tests/test_engine_port.py` (path expectations)

**Interfaces:**
- Produces the v2 template tree that init (Task 6) stamps and run.py (Task 4) uses as the installed fallback.

- [ ] **Step 1: Write the failing tests first**

Update `tests/test_templates.py`:
- `test_thirteen_starter_chains`: glob becomes `(TEMPLATES / "adws" / "modules").glob("adw_*.py")`
- `test_quality_design_variant_has_impeccable_phases`: read path becomes `TEMPLATES / "adws" / "modules" / "adw_design_sdlc.py"`
- `test_artifact_folders_live_under_adws`: `for folder in ("specs/", "app_docs/")` becomes `("specs/", "kb/")`
- `test_starter_config_validates` and any other config references: `TEMPLATES / "sssf.config.yaml"` becomes `TEMPLATES / "adws" / "config" / "sssf.config.yaml"`; the copied prompt dir becomes `TEMPLATES / "adws" / "data" / "prompt_engineering"`.

Add:

```python
def test_template_scaffolds_prompts_specs_kb():
    for folder in ("prompts", "specs", "kb"):
        readme = TEMPLATES / "adws" / folder / "README.md"
        assert readme.is_file(), f"missing scaffold README in {folder}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_templates.py -q`
Expected: FAIL — old paths missing, count glob finds nothing

- [ ] **Step 3: Move the templates**

```bash
cd src/sssf/templates/adws
mkdir -p modules config data prompts specs kb
git mv adw_*.py modules/
git mv ../sssf.config.yaml config/sssf.config.yaml
git mv ../ticketing.yaml config/ticketing.yaml
git mv ../prompt_engineering data/prompt_engineering
git mv ../harness_engineering data/harness_engineering
```

Create the three scaffold READMEs:

`src/sssf/templates/adws/prompts/README.md`:
```markdown
# prompts

Your prompt files, e.g. `sssf run <adw> "run prompt adws/prompts/x.md"`.
Ticketing writes ticket prompts here as `NN-<slug>.md`.
```

`src/sssf/templates/adws/specs/README.md`:
```markdown
# specs

Plans the planner commits (`adws/specs/<adw_id>_<slug>.md`).
```

`src/sssf/templates/adws/kb/README.md`:
```markdown
# kb

Write-ups the documenter commits (`adws/kb/<adw_id>_<slug>.md`).
```

- [ ] **Step 4: Update the config template content**

In `src/sssf/templates/adws/config/sssf.config.yaml`:
- `data_dir: adws/adw_data` → `data_dir: adws/data`
- `protected_files:` list: `adws/adw_*.py` → `adws/modules/`; keep `adws/adw_ssfs_config/` + `adws/adw_data/` entries replaced by `adws/config/` and `adws/data/` (also keep `adws/adw_modules/` if present)
- documenter `writes: - adws/app_docs/` → `- adws/kb/`

Also update the documenter user prompt and any ADW docstrings that name
`adws/app_docs/` → `adws/kb/` (grep `app_docs` under `src/sssf/templates/`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_templates.py tests/test_engine_port.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A src/sssf/templates tests/test_templates.py tests/test_engine_port.py
git commit -m "feat(templates): relocate to the v2 adws tree (modules/config/data) + scaffold prompts/specs/kb"
```

---

### Task 3: ADW templates — runtime config resolution

**Files:**
- Modify: all 13 `src/sssf/templates/adws/modules/adw_*.py`
- Modify: `tests/test_templates.py`

**Interfaces:**
- Produces: chains whose `--config` default is `None`, resolved at runtime via
  `paths.config_file(Path.cwd())` — no layout literal in any chain.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_templates.py`:

```python
def test_adws_resolve_config_at_runtime():
    """--config defaults to None and main() resolves via paths — a chain must
    never bake a layout literal that the v2 migration rewrites."""
    for adw in (TEMPLATES / "adws" / "modules").glob("adw_*.py"):
        text = adw.read_text()
        assert "paths.config_file" in text
        assert "adws/adw_ssfs_config" not in text
        assert "adws/app_docs" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_templates.py::test_adws_resolve_config_at_runtime -v`
Expected: FAIL

- [ ] **Step 3: Apply the pattern to all 13 ADWs**

Each ADW has (same shape in every file):

```python
def main(prompt: str, config: str = "adws/adw_ssfs_config/sssf.config.yaml", adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
```

and

```python
    parser.add_argument("--config", default="adws/adw_ssfs_config/sssf.config.yaml")
```

Change to:

```python
def main(prompt: str, config: str | None = None, adw_id: str | None = None) -> int:
    from pathlib import Path
    from sssf.adw_modules import paths
    cfg = agents.load_config(config or str(paths.config_file(Path.cwd())))
```

and

```python
    parser.add_argument("--config", default=None,
                        help="path to sssf.config.yaml (default: adws/config/sssf.config.yaml)")
```

Also update each ADW's docstring usage line `[--config adws/adw_ssfs_config/sssf.config.yaml]`
→ `[--config adws/config/sssf.config.yaml]`.

Use `sed` for the mechanical parts across the 13 files:

```bash
cd src/sssf/templates/adws/modules
sed -i '' \
  -e 's|config: str = "adws/adw_ssfs_config/sssf.config.yaml"|config: str | None = None|' \
  -e 's|--config adws/adw_ssfs_config/sssf.config.yaml|--config adws/config/sssf.config.yaml|g' \
  -e 's|parser.add_argument("--config", default="adws/adw_ssfs_config/sssf.config.yaml")|parser.add_argument("--config", default=None, help="path to sssf.config.yaml (default: adws/config/sssf.config.yaml)")|' \
  adw_*.py
```

Then, per file, insert the runtime resolution right after the `main(` line — replace:

```python
    cfg = agents.load_config(config)
```

with:

```python
    from sssf.adw_modules import paths
    cfg = agents.load_config(config or str(paths.config_file(Path.cwd())))
```

(Note: `from pathlib import Path` is already imported in every ADW — verify per file; add it if the imports list lacks it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_templates.py -q`
Expected: PASS (13 chains still import cleanly; runtime resolution present; no legacy literals)

- [ ] **Step 5: Commit**

```bash
git add src/sssf/templates/adws/modules tests/test_templates.py
git commit -m "refactor(adws): resolve --config at runtime — no layout literal in chain defaults"
```

---

### Task 4: Engine core — strict resolution (run/ticket/project/config/permissions)

**Files:**
- Modify: `src/sssf/commands/run.py`, `src/sssf/commands/ticket.py`, `src/sssf/project.py`,
  `src/sssf/adw_modules/data_types.py`, `src/sssf/adw_modules/permissions.py`
- Modify: `tests/test_run.py`, `tests/test_ticket_cli.py`, `tests/test_sandbox_config.py`, `tests/test_sandbox_docker.py`

**Interfaces:**
- Consumes: `paths` (Task 1).
- Produces: strict v2 resolution + legacy banner at `run`/`ticket` entry.

- [ ] **Step 1: Write the failing tests**

In `tests/test_run.py`, update the existing `_adw_file` tests: project ADW now resolves from
`adws/modules/<name>.py`; the installed fallback resolves from
`Path(sssf.__file__).parent / "templates" / "adws" / "modules" / f"{name}.py"`.
Add a test that `sssf run` on a legacy project prints the banner:

```python
def test_run_warns_on_legacy_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "adws" / "adw_data").mkdir(parents=True)
    from sssf.commands import run
    assert run.run(tmp_path, "scout", [], None, no_sandbox=True) == 1
    assert "legacy adws layout" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run.py tests/test_ticket_cli.py -q`
Expected: FAIL

- [ ] **Step 3: Modify `src/sssf/commands/run.py`**

```python
def run(cwd: Path, adw: str, args: list[str], explicit_project: str | None = None,
        no_sandbox: bool = False) -> int:
    # ... (existing stop/restart handling)
    from sssf.adw_modules import paths
    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/ directory). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="run")
    name = adw if adw.startswith("adw_") else f"adw_{adw}"
    adw_file = _adw_file(root, name)
    if adw_file is None:
        print(f"sssf: no ADW named '{adw}' (looked for adws/modules/{name}.py)", file=sys.stderr)
        return 1
    ...
```

```python
def _adw_file(root: Path, name: str) -> Path | None:
    """Prefer the INSTALLED template ... (comment preserved)."""
    from sssf.adw_modules import paths
    project_file = paths.modules_dir(root) / f"{name}.py"
    import sssf
    installed = Path(sssf.__file__).parent / "templates" / "adws" / "modules" / f"{name}.py"
    if installed.exists():
        return installed
    return project_file if project_file.exists() else None
```

```python
def _sandbox_enabled(root: Path) -> bool:
    try:
        from sssf.adw_modules import paths
        from sssf.adw_modules.agents import load_config
        cfg = load_config(str(paths.config_file(root)))
        return cfg.sandbox.enabled
    except Exception:
        return False
```

In `_run_sandboxed`: `load_config(str(root / "adws" / "adw_ssfs_config" / "sssf.config.yaml"))`
→ `load_config(str(paths.config_file(root)))`; the sandbox cmd
`["python", "adws/adw_simple_sdlc.py", ...]` → `["python", "adws/modules/adw_simple_sdlc.py", ...]`.
Also add `paths.warn_if_legacy(root, command="run")` before the sandbox branch if not already
printed (it is — the banner is at the top of `run()`).

- [ ] **Step 4: Modify `src/sssf/commands/ticket.py`**

- At entry (after project resolution): `paths.warn_if_legacy(root, command="ticket")`
- Line ~121 error text and the default ADW path `adws/adw_simple_sdlc.py` →
  `adws/modules/adw_simple_sdlc.py` (two places: the existence check and the spawn cmd)
- Config path resolution via `paths.config_file(root)` where load_config is called.

- [ ] **Step 5: Modify `src/sssf/project.py`**

```python
def data_dir(root: Path) -> Path:
    from sssf.adw_modules import paths
    return paths.data_dir(root)
```

- [ ] **Step 6: Modify `src/sssf/adw_modules/data_types.py`**

`data_dir: str = "adws/adw_data"` → `"adws/data"` (line ~355) and
`db: str = "adws/adw_data/sssf.db"` → `"adws/data/sssf.db"` (line ~359).

- [ ] **Step 7: Modify `src/sssf/adw_modules/permissions.py`**

Wherever protected-file paths or repo paths are derived from `adws/adw_ssfs_config` /
`adws/adw_data` / `adws/adw_*.py`, switch to the `paths` helpers (config_dir, data_dir,
modules_dir). Grep `adws/adw_` in the file and replace each site with the helper call.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_run.py tests/test_ticket_cli.py tests/test_sandbox_config.py tests/test_sandbox_docker.py tests/test_paths.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/sssf/commands/run.py src/sssf/commands/ticket.py src/sssf/project.py \
        src/sssf/adw_modules/data_types.py src/sssf/adw_modules/permissions.py \
        tests/test_run.py tests/test_ticket_cli.py tests/test_sandbox_config.py tests/test_sandbox_docker.py
git commit -m "feat(engine): strict v2 path resolution + legacy banner in run/ticket"
```

---

### Task 5: Data/observability callers — sandbox, healer, ticketing, obs, viz, sweep, registry

**Files:**
- Modify: `src/sssf/sandbox.py`, `src/sssf/healer.py`, `src/sssf/ticketing.py`,
  `src/sssf/commands/sweep.py`, `src/sssf/commands/obs_cmds.py`, `src/sssf/commands/viz.py`,
  `src/sssf/registry.py`
- Modify: `tests/test_sandbox_env.py`, `tests/test_sandbox_docker.py`, `tests/test_healer.py`,
  `tests/test_sweep.py`, `tests/test_ticketing.py`, `tests/test_obs.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: `paths` (Task 1).
- Produces: every `sssf.db` / data-dir reference resolving via `paths`; banner at
  obs/viz/sweep/healer entry.

- [ ] **Step 1: Write the failing tests**

Update path expectations in the listed test files: `adws/adw_data/sssf.db` →
`adws/data/sssf.db`; `adws/adw_ssfs_config/sssf.config.yaml` → `adws/config/sssf.config.yaml`.
Add one banner test to `tests/test_sweep.py` (or `test_obs.py`):

```python
def test_sweep_warns_on_legacy_layout(tmp_path, monkeypatch, capsys):
    (tmp_path / "adws" / "adw_data").mkdir(parents=True)
    from sssf.commands import sweep
    monkeypatch.chdir(tmp_path)
    sweep.run(tmp_path)  # or the entry used by the CLI
    assert "legacy adws layout" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sandbox_env.py tests/test_healer.py tests/test_sweep.py -q`
Expected: FAIL

- [ ] **Step 3: Modify `src/sssf/sandbox.py`**

- `sandbox_env` (line ~393): `data_dir = project_root / "adws" / "adw_data"` →
  `data_dir = paths.data_dir(project_root)` (import `paths` at top of the function or module)
- per-run db (line ~334): `sandbox_dir(project_root, adw_id) / "adws" / "adw_data" / "sssf.db"`
  → `... / "adws" / "data" / "sssf.db"`
- `project_db_path` docstring (`/work/adws/adw_data/sssf.db`) → `/work/adws/data/sssf.db`
- Add `paths.warn_if_legacy(project_root, command="sandbox")` where appropriate (prune/list/run paths).

- [ ] **Step 4: Modify `src/sssf/healer.py`**

Lines ~163 and ~241: `wt / "adws" / "adw_data" / "sssf.db"` →
`wt / "adws" / "data" / "sssf.db"`. Add the banner in the healer's per-project entry.

- [ ] **Step 5: Modify `src/sssf/ticketing.py`**

Line ~192: `db_path = root / "adws" / "adw_data" / "sssf.db"` →
`db_path = paths.data_dir(root) / "sssf.db"`. `next_prompt_name` already targets
`adws/prompts` — switch to `paths.prompts_dir(root)`. Add the banner at ticket entry.

- [ ] **Step 6: Modify `src/sssf/commands/sweep.py`**

Line ~38: `root / "adws" / "adw_data" / "sssf.db"` → `paths.data_dir(root) / "sssf.db"`.
Add the banner.

- [ ] **Step 7: obs/viz/registry**

Grep for `adw_data` / `adw_ssfs_config` in `obs_cmds.py`, `viz.py`, `registry.py`; replace
any hardcoded references with the `paths` helpers and add the banner at entry. (These files
mostly go through `project.data_dir` / `registry` — verify and adjust only what's hardcoded.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_sandbox_env.py tests/test_sandbox_docker.py tests/test_healer.py tests/test_sweep.py tests/test_ticketing.py tests/test_obs.py tests/test_registry.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/sssf/sandbox.py src/sssf/healer.py src/sssf/ticketing.py src/sssf/commands/sweep.py \
        src/sssf/commands/obs_cmds.py src/sssf/commands/viz.py src/sssf/registry.py \
        tests/test_sandbox_env.py tests/test_sandbox_docker.py tests/test_healer.py \
        tests/test_sweep.py tests/test_ticketing.py tests/test_obs.py tests/test_registry.py
git commit -m "feat(engine): strict v2 data paths across sandbox/healer/ticketing/obs/viz/sweep/registry"
```

---

### Task 6: `sssf init` — stamp v2 + migration (warn → backup → move → rewrite)

**Files:**
- Modify: `src/sssf/commands/init.py`
- Modify: `tests/test_init.py`

**Interfaces:**
- Consumes: `paths` (Task 1), the v2 template tree (Task 2).
- Produces: fresh stamps at v2; `init --refresh` migrates legacy projects
  (backup → move → literal rewrite → scaffold), idempotently.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_init.py`:

```python
import re
import shutil


def test_init_stamps_v2_layout(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    assert (root / "adws/modules/adw_prompt.py").exists()
    assert (root / "adws/config/sssf.config.yaml").exists()
    assert (root / "adws/config/ticketing.yaml").exists()
    assert (root / "adws/data/prompt_engineering/planner/system.md").exists()
    for folder in ("prompts", "specs", "kb"):
        assert (root / "adws" / folder / "README.md").is_file()
    assert not (root / "adws/adw_ssfs_config").exists()
    assert not (root / "adws/adw_data").exists()
    assert not (root / "adws/adw_prompt.py").exists()


def test_refresh_migrates_legacy_layout(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    # Build a v1 project by hand
    (root / "adws" / "adw_ssfs_config").mkdir(parents=True)
    (root / "adws" / "adw_ssfs_config" / "sssf.config.yaml").write_text("roster: v1\n")
    (root / "adws" / "adw_data").mkdir(parents=True)
    (root / "adws" / "adw_data" / "sssf.db").write_text("db")
    (root / "adws" / "app_docs").mkdir(parents=True)
    (root / "adws" / "app_docs" / "note.md").write_text("note")
    custom = root / "adws" / "adw_custom.py"
    custom.write_text('config: str = "adws/adw_ssfs_config/sssf.config.yaml"\n')
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    # moved to v2
    assert (root / "adws/config/sssf.config.yaml").read_text() == "roster: v1\n"
    assert (root / "adws/data/sssf.db").read_text() == "db"
    assert (root / "adws/kb/note.md").read_text() == "note"
    assert (root / "adws/modules/adw_custom.py").exists()
    # literal rewritten in the moved chain
    moved = (root / "adws/modules/adw_custom.py").read_text()
    assert "adws/adw_ssfs_config" not in moved and "adws/config/" in moved
    # backup exists and is gitignored
    backups = list(root.glob("adws.backup.*"))
    assert len(backups) == 1 and backups[0].is_dir()
    assert "adws.backup." in (root / ".gitignore").read_text()
    # legacy names gone
    assert not (root / "adws/adw_ssfs_config").exists()
    assert not (root / "adws/adw_data").exists()
    assert not (root / "adws/app_docs").exists()


def test_refresh_on_v2_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert not list(root.glob("adws.backup.*"))
    assert (root / "adws/modules/adw_prompt.py").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_init.py -q`
Expected: FAIL

- [ ] **Step 3: Rewrite `run()` in `src/sssf/commands/init.py`**

```python
import shutil
import time
from sssf.adw_modules import paths

_BACKUP_PREFIX = "adws.backup."
_LEGACY_MOVES = (  # (legacy relpath under adws/, v2 relpath under adws/)
    ("adw_ssfs_config", "config"),
    ("adw_data", "data"),
    ("app_docs", "kb"),
)
_LITERAL_REWRITES = (  # applied to moved chain files
    ("adws/adw_ssfs_config/", "adws/config/"),
    ("adws/adw_data", "adws/data"),
    ("adws/app_docs", "adws/kb"),
)


def _backup_adws(root: Path) -> Path | None:
    adws = root / "adws"
    if not adws.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = root / f"{_BACKUP_PREFIX}{stamp}"
    shutil.copytree(adws, dest)
    return dest


def _migrate_legacy(root: Path) -> None:
    """Move v1 items to v2 in place; never overwrite an existing v2 target."""
    adws = root / "adws"
    for legacy, v2 in _LEGACY_MOVES:
        src = adws / legacy
        dst = adws / v2
        if src.exists() and not dst.exists():
            src.rename(dst)
    # root-level chains → modules/
    modules = paths.modules_dir(root)
    modules.mkdir(parents=True, exist_ok=True)
    for chain in adws.glob("adw_*.py"):
        target = modules / chain.name
        if not target.exists():
            chain.rename(target)
    # rewrite layout literals inside moved chains
    for chain in modules.glob("adw_*.py"):
        text = chain.read_text()
        for old, new in _LITERAL_REWRITES:
            text = text.replace(old, new)
        chain.write_text(text)
    # gitignore the backup
    gitignore = root / ".gitignore"
    entry = f"{_BACKUP_PREFIX}*/"
    if gitignore.exists():
        text = gitignore.read_text()
        if entry not in text:
            gitignore.write_text(text.rstrip() + "\n" + entry + "\n")
    else:
        gitignore.write_text(entry + "\n")
```

In `run()`:

```python
def run(root: Path, *, refresh: bool = False, force: bool = False,
        auto: bool = False) -> int:
    templates = resources.files("sssf.templates")
    root.mkdir(parents=True, exist_ok=True)

    if refresh and paths.is_legacy_layout(root):
        print(f"sssf: legacy adws layout detected in {root} — migrating to v2 "
              "(backup of adws/ first, then move).", file=sys.stderr)
        backup = _backup_adws(root)
        if backup is not None:
            print(f"sssf: backed up adws/ -> {backup.relative_to(root)}", file=sys.stderr)
        _migrate_legacy(root)

    # Stamp the whole v2 tree (templates/adws mirrors the stamped layout)
    _copy_tree(templates / "adws", root / "adws", force=force, confirm=refresh,
               auto=auto, label="adws")

    env_dest = root / ".env.sample"
    if not env_dest.exists() or force:
        env_dest.write_text((templates / "env.sample").read_text())

    # AGENTS.md block (update the copy to name the v2 paths)
    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        text = agents_md.read_text()
        if "<!-- sssf -->" not in text:
            agents_md.write_text(text.rstrip() + "\n" + AGENTS_BLOCK)
    else:
        agents_md.write_text("# Project\n" + AGENTS_BLOCK)

    # .gitignore (GITIGNORE_ENTRIES unchanged — paths still under adws/data/...)
    gitignore = root / ".gitignore"
    if gitignore.exists():
        text = gitignore.read_text()
        missing = [line for line in GITIGNORE_ENTRIES if line not in text]
        if missing:
            gitignore.write_text(text.rstrip() + "\n" + "\n".join(missing) + "\n")
    else:
        gitignore.write_text("\n".join(GITIGNORE_ENTRIES) + "\n")

    registry.register_project(root, paths.data_dir(root) / "sssf.db",
                              __version__, added=True)
    return 0
```

Note: `GITIGNORE_ENTRIES` keeps `adws/adw_data/sessions/` and `adws/adw_data/sssf.db`
as-is only if the backup/migration never leaves stale entries — change them to
`adws/data/sessions/` and `adws/data/sssf.db` (plus the WAL sidecars). Update
`AGENTS_BLOCK` to say `adws/modules/adw_*.py` and `adws/config/sssf.config.yaml`.
The old per-file config/ticket copies are gone — the tree copy covers them.
`registry.register_project` already takes an explicit db path (pass `paths.data_dir(root) / "sssf.db"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_init.py tests/test_templates.py tests/test_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/commands/init.py tests/test_init.py
git commit -m "feat(init): stamp v2 layout + legacy migration (warn, backup, move, rewrite)"
```

---

### Task 7: Docs — v2 layout everywhere

**Files:**
- Modify: `src/sssf/docs/customizing.md`, `src/sssf/docs/quality-gates.md`
- Modify: `site/src/pages/docs/configuration.astro`, `run-semantics.astro`, `cli.astro`,
  `sandbox.astro`, `core-concepts.astro`, `quickstart.astro` (only the pages that name the
  old paths — grep `adw_ssfs_config|adw_data|app_docs|adw_\.py` and update)
- Modify: `README.md` (bullets naming `adws/adw_*.py`, `adws/adw_ssfs_config`, `adws/adw_data`)

- [ ] **Step 1: Update each file**

Replace the v1 layout tree and path mentions with the v2 tree (from the spec's Goal
section). `customizing.md` gets the new tree verbatim:

```
adws/
├── modules/    adw_*.py chains
├── config/     sssf.config.yaml, ticketing.yaml
├── data/       sssf.db, sessions/, prompt_engineering/, harness_engineering/
├── prompts/    your prompt files, e.g. sssf run <adw> "run prompt adws/prompts/x.md"
├── specs/      plans the planner commits (adws/specs/<adw_id>_<slug>.md)
└── kb/         write-ups the documenter commits (adws/kb/<adw_id>_<slug>.md)
```

- [ ] **Step 2: Verify the site builds**

Run: `cd site && npm run build`
Expected: builds clean

- [ ] **Step 3: Commit**

```bash
git add src/sssf/docs site/src/pages/docs README.md
git commit -m "docs: v2 adws layout in customizing, site pages, README"
```

---

### Task 8: End-to-end verification (fresh + legacy fixture + inkwell untouched)

**Files:** none — verification only.

- [ ] **Step 1: Full Python suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 2: Fresh v2 project runs**

```bash
rm -rf /tmp/v2proj && mkdir -p /tmp/v2proj && cd /tmp/v2proj && git init -q
sssf init
sssf run scout "map the repo" --no-sandbox
```

Expected: init stamps v2 (modules/, config/, data/, prompts/, specs/, kb/); scout runs.

- [ ] **Step 3: Legacy fixture migrates**

```bash
rm -rf /tmp/legacy && mkdir -p /tmp/legacy && cd /tmp/legacy && git init -q
# hand-build a v1 project (adws/adw_simple_sdlc.py at root, adw_ssfs_config/, adw_data/, app_docs/)
sssf run scout "x" --no-sandbox        # → legacy banner + loud failure
sssf init --refresh                    # → warn + backup + migrate
sssf run scout "map the repo" --no-sandbox   # → works
ls adws.backup.*                        # backup intact
```

- [ ] **Step 4: inkwell untouched**

Run: `cd ~/dev/lab/demos/inkwell && git status --short`
Expected: no changes (the feature branch work never touched it)

- [ ] **Step 5: Confirm the tree is clean**

Run: `git -C /Users/felipe.matos/dev/lab/mvp/.worktrees/adws-layout-v2 status --short`
Expected: clean (all commits in)

---

## Self-Review

**Spec coverage:** paths.py strict resolution ✓ (T1) · legacy detection + banner ✓ (T1, T4, T5) · templates mirror v2 + scaffolds ✓ (T2) · ADW runtime config ✓ (T3) · engine touch points run/ticket/project/sandbox/healer/ticketing/obs/viz/sweep/registry/data_types/permissions ✓ (T4–T5) · init stamp v2 + migration warn→backup→move→rewrite→scaffold + idempotent ✓ (T6) · config content (data_dir, protected_files, kb) ✓ (T2, T6) · docs ✓ (T7) · tests incl. migration + banner ✓ (T1–T6) · verification incl. inkwell untouched ✓ (T8). Rename → `adw_design_sdlc` already committed.
