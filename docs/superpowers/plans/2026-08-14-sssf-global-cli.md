# SSSF Global CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repackage Super Simple Software Factory as a globally installed Python CLI (`sssf`) — engine in the package, thin per-project stamp, global trace service — per the approved design.

**Architecture:** The engine (`adw_modules/`) ships inside an installable Python package with a `sssf` console entry point. `sssf init` stamps only the customization surface (chains, config, prompts) into a project and registers it in `~/.sssf/projects.json`. `sssf run` executes user ADW scripts with the tool venv's python. `sssf viz` serves one global Vue/bun visualizer over all registered projects' WAL SQLite dbs.

**Tech Stack:** Python 3.11+ (pydantic, python-dotenv, pyyaml, rich — engine deps), stdlib `argparse`/`sqlite3` for the CLI, Vue 3 + Vite + bun for the visualizer, pytest for tests, `uv tool install` for distribution.

**Spec:** `docs/superpowers/specs/2026-08-14-sssf-global-cli-design.md`

**New repo root:** `~/dev/lab/mvp/sssf/` (proposed; create it in Task 1. Design open question §11.1 resolved here.)

## Global Constraints

- Package name `sssf`; console script `sssf = "sssf.cli:main"`; version `0.1.0`.
- Python `>=3.11`; runtime deps exactly: `pydantic>=2`, `python-dotenv`, `pyyaml`, `rich`. Dev: `pytest`.
- **Porting convention:** the engine and visualizer are copied verbatim from the pinned source below; the plan shows *exact* diffs where files change. Never rewrite ported code cosmetically.
- **Pinned source:** `/Users/felipe.matos/dev/lab/mvp/super-simple-software-factory/.claude/skills/sssf/` at git commit `de31374` (skill templates). The design/spec docs at HEAD (`02adadb`) live in the same repo but are not ported.
- Engine stays `sssf.adw_modules/` with **relative imports only** (already true — verify, don't change).
- Stamped ADWs import `from sssf.adw_modules import ...`; their `# /// script` dependency headers are **removed**.
- Per-project paths unchanged from SSSF: `adws/adw_*.py`, `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_data/` (runtime `sessions/` + `sssf.db` gitignored).
- Registry `~/.sssf/projects.json` is runtime state, not config; schema `{"version": 1, "projects": [...]}`.
- No global pi-skill install anywhere. `--skill <package>/SKILL.md` is passed per invocation only.
- bun stays a global prerequisite for `sssf viz`; nothing per-repo needs it.
- Dev loop: `uv run pytest` from the repo root (uv installs the project editable + dev group).

## File Structure (target repo)

```
~/dev/lab/mvp/sssf/
├── pyproject.toml              # package, entry point, hatchling, dev group
├── README.md                   # Task 10
├── LICENSE                     # MIT (Task 10)
├── .gitignore                  # Task 1
├── src/sssf/
│   ├── __init__.py             # __version__
│   ├── cli.py                  # argparse dispatch (Tasks 1, 5–9 fill subcommands)
│   ├── registry.py             # ~/.sssf/projects.json (Task 2)
│   ├── project.py              # project resolution + paths (Task 5)
│   ├── obs.py                  # sqlite queries for sessions/phases/tail/procs (Task 7)
│   ├── adw_modules/            # engine, ported (Task 3)
│   ├── templates/              # stamped by init: config, env.sample, adws/, prompt_engineering/, harness_engineering/ (Task 4)
│   ├── SKILL.md                # --skill payload, trimmed (Task 10)
│   ├── docs/                   # customizing.md, contributing.md (Task 10)
│   └── apps/visualizer/        # Vue+Vite+bun, ported + multi-project (Task 9)
└── tests/                      # pytest, one module per task
```

---

### Task 1: Repo scaffold and installable package skeleton

**Files:**
- Create: `~/dev/lab/mvp/sssf/pyproject.toml`
- Create: `~/dev/lab/mvp/sssf/src/sssf/__init__.py`
- Create: `~/dev/lab/mvp/sssf/src/sssf/cli.py` (minimal: version only)
- Create: `~/dev/lab/mvp/sssf/.gitignore`
- Create: `~/dev/lab/mvp/sssf/tests/test_cli.py`
- Create: `~/dev/lab/mvp/sssf/tests/conftest.py`

**Interfaces:**
- Produces: `sssf.cli.main(argv: list[str]) -> int` — returns exit code; called by the entry point and directly in tests.

- [ ] **Step 1: Create the repo and write the failing test**

```bash
mkdir -p ~/dev/lab/mvp/sssf/src/sssf ~/dev/lab/mvp/sssf/tests && cd ~/dev/lab/mvp/sssf && git init
```

`tests/test_cli.py`:

```python
from sssf.cli import main

def test_version_flag():
    assert main(["--version"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sssf'`

- [ ] **Step 3: Write minimal implementation**

`pyproject.toml`:

```toml
[project]
name = "sssf"
version = "0.1.0"
description = "Super Simple Software Factory — repeatable agents-plus-code workflows as a global CLI"
requires-python = ">=3.11"
dependencies = ["pydantic>=2", "python-dotenv", "pyyaml", "rich"]

[project.scripts]
sssf = "sssf.cli:main"

[dependency-groups]
dev = ["pytest"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sssf"]

# Non-python assets ship inside the package; init/viz read them via
# importlib.resources. force-include keeps them in the built wheel too.
[tool.hatch.build.targets.wheel.force-include]
"src/sssf/SKILL.md" = "sssf/SKILL.md"
"src/sssf/templates" = "sssf/templates"
"src/sssf/docs" = "sssf/docs"
"src/sssf/apps" = "sssf/apps"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/sssf/__init__.py`:

```python
__version__ = "0.1.0"
```

`src/sssf/cli.py`:

```python
"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""
import argparse
import sys

from sssf import __version__


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"sssf {__version__}")
        return 0
    print("sssf: see `sssf --help` (subcommands arrive in later tasks)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
dist/
build/
*.egg-info/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Verify the console script installs**

Run: `cd ~/dev/lab/mvp/sssf && uv tool install --editable . && sssf --version`
Expected: prints `sssf 0.1.0` and exits 0.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "chore: scaffold sssf package with cli entry point"
```

---

### Task 2: Project registry (`sssf/registry.py`)

**Files:**
- Create: `src/sssf/registry.py`
- Create: `tests/test_registry.py`

**Interfaces:**
- Produces: `registry_path() -> Path` (default `~/.sssf/projects.json`), `load_registry() -> dict`, `save_registry(data: dict) -> None`, `register_project(root: Path, db_path: Path, tool_version: str, *, added: bool = False) -> dict`, `update_last_run(root: Path) -> None`, `list_projects() -> list[dict]`, `remove_project(name: str) -> bool`.

Registry shape:

```json
{"version": 1, "projects": [
  {"name": "my-repo", "root": "/abs/path", "db": "/abs/path/adws/adw_data/sssf.db",
   "added": "2026-08-14T00:00:00Z", "last_run": null, "tool_version": "0.1.0"}
]}
```

`name` = basename of `root`. All paths absolute. Registry dir `~/.sssf/` created on write. Corrupt/missing file → empty registry (never crash).

- [ ] **Step 1: Write the failing test**

`tests/test_registry.py`:

```python
import json
from pathlib import Path

from sssf import registry


def _write_registry(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / ".sssf" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))
    return path


def test_register_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    proj = tmp_path / "repo-a"
    proj.mkdir()
    registry.register_project(proj, proj / "adws/adw_data/sssf.db", "0.1.0", added=True)
    projects = registry.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "repo-a"
    assert projects[0]["db"].endswith("adws/adw_data/sssf.db")


def test_update_last_run(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    proj = tmp_path / "repo-a"
    proj.mkdir()
    registry.register_project(proj, proj / "sssf.db", "0.1.0")
    registry.update_last_run(proj)
    projects = registry.list_projects()
    assert projects[0]["last_run"] is not None


def test_remove_project(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    proj = tmp_path / "repo-a"
    proj.mkdir()
    registry.register_project(proj, proj / "sssf.db", "0.1.0")
    assert registry.remove_project("repo-a") is True
    assert registry.list_projects() == []


def test_missing_registry_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    assert registry.list_projects() == []


def test_corrupt_registry_is_empty(tmp_path, monkeypatch):
    path = tmp_path / ".sssf" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    monkeypatch.setattr(registry, "registry_path", lambda: path)
    assert registry.list_projects() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_registry.py -v`
Expected: FAIL with import/attribute errors

- [ ] **Step 3: Write minimal implementation**

`src/sssf/registry.py`:

```python
"""Global project registry: ~/.sssf/projects.json.

Runtime state, not config. `sssf init` registers, `sssf run` refreshes
last_run, `sssf projects` lists/removes, `sssf viz` serves over it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def registry_path() -> Path:
    return Path(os.environ.get("SSSF_REGISTRY", str(Path.home() / ".sssf" / "projects.json")))


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {"version": SCHEMA_VERSION, "projects": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": SCHEMA_VERSION, "projects": []}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return {"version": SCHEMA_VERSION, "projects": []}
    data.setdefault("version", SCHEMA_VERSION)
    return data


def save_registry(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _entry(projects: list[dict], name: str) -> dict | None:
    return next((p for p in projects if p.get("name") == name), None)


def register_project(root: Path, db_path: Path, tool_version: str, *, added: bool = False) -> dict:
    root = root.resolve()
    name = root.name
    data = load_registry()
    entry = _entry(data["projects"], name)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if entry is None:
        entry = {"name": name, "root": str(root), "db": str(db_path.resolve()),
                 "added": now, "last_run": None, "tool_version": tool_version}
        data["projects"].append(entry)
    else:
        entry["root"] = str(root)
        entry["db"] = str(db_path.resolve())
        entry["tool_version"] = tool_version
        if added and entry.get("added") is None:
            entry["added"] = now
    save_registry(data)
    return entry


def update_last_run(root: Path) -> None:
    root = root.resolve()
    data = load_registry()
    entry = _entry(data["projects"], root.name)
    if entry is None:
        return
    entry["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_registry(data)


def list_projects() -> list[dict]:
    return load_registry()["projects"]


def remove_project(name: str) -> bool:
    data = load_registry()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p.get("name") != name]
    if len(data["projects"]) == before:
        return False
    save_registry(data)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_registry.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: project registry (~/.sssf/projects.json)"
```

---

### Task 3: Port the engine into `sssf/adw_modules` + `--skill` support

**Files:**
- Copy: all of `adw_modules/*.py` from pinned source `…/.claude/skills/sssf/templates/adws/adw_modules/` → `src/sssf/adw_modules/`
- Modify: `src/sssf/adw_modules/data_types.py` (add `PiRequest.skill_path`)
- Modify: `src/sssf/adw_modules/agent_pi.py` (append `--skill` to cmd)
- Modify: `src/sssf/adw_modules/agents.py` (pass `skill_path` default in `PiRequest(...)` call)
- Create: `tests/test_engine_port.py`

**Interfaces:**
- Consumes: pinned engine at commit `de31374` — 16 files: `__init__.py`, `agent_cc.py`, `agent_pi.py`, `agents.py`, `changes.py`, `console.py`, `data_types.py`, `gates.py`, `git_helper.py`, `permissions.py`, `prompts.py`, `quality.py`, `runner.py`, `session.py`, `tracer.py`, `utils.py`.
- Produces: importable `sssf.adw_modules`; `PiRequest` gains `skill_path: Optional[str] = None`; pi cmd gains `--skill <path>` when set; the `PiRequest(...)` construction site in `agents.py` gains `skill_path=SKILL_PATH` where `SKILL_PATH = str(Path(__file__).resolve().parent.parent / "SKILL.md")`.

**Porting rule:** copy each file byte-for-byte; the only edits in this task are the three diffs below. Verify with grep that no `adw_modules` absolute import and no `.claude/` reference remains anywhere in `src/sssf/adw_modules/`.

- [ ] **Step 1: Copy the engine verbatim**

```bash
SRC=/Users/felipe.matos/dev/lab/mvp/super-simple-software-factory/.claude/skills/sssf/templates/adws/adw_modules
DEST=~/dev/lab/mvp/sssf/src/sssf/adw_modules
mkdir -p "$DEST" && cp "$SRC"/*.py "$DEST"/
```

Verify: `grep -rn "from adw_modules\|import adw_modules\|\.claude/" "$DEST"` → no output (engine already uses relative imports).

- [ ] **Step 2: Write the failing test**

`tests/test_engine_port.py`:

```python
import sssf.adw_modules as m  # noqa: F401  (import surface must resolve)
from sssf.adw_modules import agents, data_types

def test_validate_accepts_starter_roster(tmp_path):
    import yaml
    cfg_path = tmp_path / "sssf.config.yaml"
    cfg_path.write_text(
        (tmp_path / "roster").read_text() if False else _STARTER_ROSTER
    )
    cfg = agents.load_config(cfg_path)
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter"])

def test_pi_request_carries_skill_path():
    req = data_types.PiRequest(
        prompt="p", system_prompt="s", model="openai/gpt-4o-mini",
        session_id="x", session_dir="/tmp", raw_output_path="/tmp/o.jsonl",
        skill_path="/pkg/SKILL.md",
    )
    assert req.skill_path == "/pkg/SKILL.md"

_STARTER_ROSTER = """\
defaults:
  coding_agent: pi
  model: openai/gpt-4o-mini
  thinking: medium
  data_dir: adws/adw_data
agents:
  - name: planner
    model: openai/gpt-4o-mini
    purpose: plan
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/planner/system.md
      user: adws/adw_data/prompt_engineering/planner/user.md
  - name: builder
    model: openai/gpt-4o-mini
    purpose: build
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/builder/system.md
      user: adws/adw_data/prompt_engineering/builder/user.md
  - name: reviewer
    model: openai/gpt-4o-mini
    purpose: review
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/reviewer/system.md
      user: adws/adw_data/prompt_engineering/reviewer/user.md
  - name: scout
    model: openai/gpt-4o-mini
    purpose: recon
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/scout/system.md
      user: adws/adw_data/prompt_engineering/scout/user.md
  - name: documenter
    model: openai/gpt-4o-mini
    purpose: docs
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/documenter/system.md
      user: adws/adw_data/prompt_engineering/documenter/user.md
"""
```

Note: `agents.validate` checks model pattern shape and required agents; this roster uses a single provider/id so it validates without touching the network. `prompt_engineering` paths are not read during validate (they are read when a call is built) — safe for the test.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_engine_port.py -v`
Expected: FAIL (`AttributeError: PiRequest has no field 'skill_path'`)

- [ ] **Step 4: Apply the three diffs**

`src/sssf/adw_modules/data_types.py` — in `PiRequest`, after `tools: Optional[list[str]] = None`:

```python
    tools: Optional[list[str]] = None
    skill_path: Optional[str] = None     # --skill payload: package SKILL.md, per invocation
    extensions: list[str] = Field(default_factory=list)
```

`src/sssf/adw_modules/agent_pi.py` — in `run()`, after the `--system-prompt` line in `cmd`:

```python
        "--session-dir", request.session_dir,
        "--system-prompt", request.system_prompt,
    ]
    if request.skill_path:
        cmd += ["--skill", request.skill_path]
```

`src/sssf/adw_modules/agents.py` — where `PiRequest(` is constructed, add the skill payload:

```python
SKILL_PATH = str(Path(__file__).resolve().parent.parent / "SKILL.md")
```

and in the `PiRequest(...)` call add:

```python
        request = PiRequest(
            ...
            skill_path=SKILL_PATH,
        )
```

(`agents.py` must gain `from pathlib import Path` if not already imported.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_engine_port.py -v`
Expected: 2 PASS

- [ ] **Step 6: Verify the cmd assembly includes --skill**

Run: `cd ~/dev/lab/mvp/sssf && python - <<'EOF'
import inspect, re
src = inspect.getsource(__import__("sssf.adw_modules.agent_pi", fromlist=["x"]).run)
assert "skill_path" in src and 'cmd += ["--skill", request.skill_path]' in src
print("ok")
EOF`
Expected: prints `ok`

- [ ] **Step 7: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: port engine to sssf.adw_modules, add --skill payload support"
```

---

### Task 4: Port templates (starter chains, config, prompts) into the package

**Files:**
- Copy: pinned `…/templates/sssf.config.yaml` → `src/sssf/templates/sssf.config.yaml` (verbatim)
- Copy: pinned `…/templates/env.sample` → `src/sssf/templates/env.sample` (verbatim)
- Copy: pinned `…/templates/prompt_engineering/` → `src/sssf/templates/prompt_engineering/` (verbatim)
- Copy: pinned `…/templates/harness_engineering/` → `src/sssf/templates/harness_engineering/` (verbatim)
- Copy + modify: pinned `…/templates/adws/adw_*.py` (12 files) → `src/sssf/templates/adws/`
- Create: `tests/test_templates.py`

**Interfaces:**
- Produces: `sssf.templates.adws/adw_*.py` — each imports `from sssf.adw_modules import ...` / `from sssf.adw_modules.data_types import ...` and has **no** `# /// script` header. All other content byte-identical to pinned source.

The 12 chains: `adw_prompt.py`, `adw_scout.py`, `adw_plan.py`, `adw_build.py`, `adw_quality.py`, `adw_plan_build.py`, `adw_build_test.py`, `adw_build_review.py`, `adw_plan_build_test.py`, `adw_plan_build_test_quality.py`, `adw_document.py`, `adw_simple_sdlc.py`.

- [ ] **Step 1: Copy templates and rewrite ADW imports**

```bash
T=/Users/felipe.matos/dev/lab/mvp/super-simple-software-factory/.claude/skills/sssf/templates
D=~/dev/lab/mvp/sssf/src/sssf/templates
mkdir -p "$D/adws"
cp "$T/sssf.config.yaml" "$D/"
cp "$T/env.sample" "$D/"
cp -r "$T/prompt_engineering" "$D/"
cp -r "$T/harness_engineering" "$D/"
cp "$T"/adws/adw_*.py "$D/adws/"

cd "$D/adws"
# 1) strip the inline uv dependency header (first 5 lines of every ADW)
for f in adw_*.py; do
  sed -i '' '1,5{/^# \/\/\/ script$/,/^# \/\/\/$/d}' "$f" 2>/dev/null || \
  sed -i '1,5{/^# \/\/\/ script$/,/^# \/\/\/$/d}' "$f"
done
# 2) rewrite absolute engine imports to the package
for f in adw_*.py; do
  sed -i '' 's/^from adw_modules import /from sssf.adw_modules import /; s/^from adw_modules\.data_types import /from sssf.adw_modules.data_types import /' "$f" 2>/dev/null || \
  sed -i 's/^from adw_modules import /from sssf.adw_modules import /; s/^from adw_modules\.data_types import /from sssf.adw_modules.data_types import /' "$f"
done
```

Verify: `grep -rn "adw_modules" "$D/adws" | grep -v "sssf.adw_modules"` → no output; `grep -rln "# /// script" "$D/adws"` → no output.

- [ ] **Step 2: Write the failing test**

`tests/test_templates.py`:

```python
import importlib.util
import sys
from pathlib import Path

import yaml

from sssf.adw_modules import agents

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "sssf" / "templates"


def test_twelve_starter_chains():
    adws = sorted((TEMPLATES / "adws").glob("adw_*.py"))
    assert len(adws) == 12
    for adw in adws:
        spec = importlib.util.spec_from_file_location(adw.stem, adw)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[adw.stem] = mod
        spec.loader.exec_module(mod)   # imports sssf.adw_modules — proves engine link


def test_no_inline_uv_headers():
    for adw in (TEMPLATES / "adws").glob("adw_*.py"):
        assert "# /// script" not in adw.read_text()


def test_starter_config_validates():
    cfg = agents.load_config(TEMPLATES / "sssf.config.yaml")
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter"])
```

Note: `test_twelve_starter_chains` imports each ADW module without running `main()` — safe, no pi spawned. The starter config validation needs the five named agents present (they are).

- [ ] **Step 3: Run test to verify it fails before the copy**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_templates.py -v`
Expected: FAIL — templates dir missing

- [ ] **Step 4: Run test to verify it passes after the copy**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_templates.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: port starter templates (chains, config, prompts) into package"
```

---

### Task 5: `sssf init`

**Files:**
- Create: `src/sssf/project.py`
- Create: `src/sssf/commands/__init__.py`
- Create: `src/sssf/commands/init.py`
- Modify: `src/sssf/cli.py` (subparser dispatch)
- Create: `tests/test_init.py`

**Interfaces:**
- Consumes: `registry.register_project` (Task 2); `sssf.templates` package data (Task 4).
- Produces: `project.find_project(cwd: Path, explicit: str | None) -> Path | None` — cwd if it contains `adws/`, else walk up to git root/`adws/`; `project.data_dir(root: Path) -> Path` (= `root/adws/adw_data`); `sssf.commands.init.run(root: Path, *, refresh: bool, force: bool) -> int`.
- `sssf init [--project DIR] [--refresh] [--force]`:
  - Copies `templates/adws/` → `<root>/adws/` (skip existing unless `--force`)
  - Copies `templates/sssf.config.yaml` → `<root>/adws/adw_sssf_config/sssf.config.yaml`
  - Copies `templates/prompt_engineering/` → `<root>/adws/adw_data/prompt_engineering/`
  - Copies `templates/harness_engineering/` → `<root>/adws/adw_data/harness_engineering/`
  - Copies `templates/env.sample` → `<root>/.env.sample` (never overwrites existing)
  - Appends an AGENTS.md block (idempotent marker `<!-- sssf -->`)
  - Appends gitignore entries for `adws/adw_data/sessions/` and `adws/adw_data/sssf.db` (idempotent)
  - Registers the project in the registry (Task 2)
  - `--refresh`: copy only files missing at destination (same skip rule as default, no `--force` overwrite)

- [ ] **Step 1: Write the failing test**

`tests/test_init.py`:

```python
import subprocess
from pathlib import Path

from sssf import registry
from sssf.commands import init


def _run_init(root: Path, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr(registry, "registry_path",
                        lambda: root.parent / ".sssf" / "projects.json")
    return init.run(root, refresh="--refresh" in (argv or []),
                    force="--force" in (argv or []))


def test_init_stamps_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    assert (root / "adws/adw_sssf_config/sssf.config.yaml").exists()
    assert (root / "adws/adw_prompt.py").exists()
    assert (root / "adws/adw_data/prompt_engineering/planner/system.md").exists()
    assert (root / ".env.sample").exists()
    agents_md = (root / "AGENTS.md").read_text()
    assert "sssf" in agents_md
    gitignore = (root / ".gitignore").read_text()
    assert "adws/adw_data/sssf.db" in gitignore
    assert len(registry.list_projects()) == 1


def test_init_is_idempotent_and_does_not_clobber(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    adw = root / "adws/adw_prompt.py"
    original = adw.read_text()
    adw.write_text(original + "\n# user edit\n")
    assert _run_init(root, monkeypatch) == 0
    assert adw.read_text() == original + "\n# user edit\n"


def test_refresh_adds_missing_only(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    (root / "adws/adw_prompt.py").unlink()
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert (root / "adws/adw_prompt.py").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_init.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.commands`)

- [ ] **Step 3: Write minimal implementation**

`src/sssf/project.py`:

```python
"""Project resolution: where the per-project stamp lives."""
from __future__ import annotations

from pathlib import Path


def find_project(cwd: Path, explicit: str | None = None) -> Path | None:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        return root if (root / "adws").is_dir() else None
    cur = cwd.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "adws").is_dir():
            return candidate
        if (candidate / ".git").exists():
            return None          # hit a repo root without adws/ — stop
    return None


def data_dir(root: Path) -> Path:
    return root / "adws" / "adw_data"
```

`src/sssf/commands/__init__.py`:

```python
"""sssf subcommands. Each module exposes run(...) -> int."""
```

`src/sssf/commands/init.py`:

```python
"""`sssf init` — stamp the customization surface into a project and register it."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from sssf import __version__
from sssf import registry

AGENTS_BLOCK = """
<!-- sssf -->
This repo runs the **sssf** software factory (global CLI). Run `sssf` commands
to operate it: `sssf run <adw> "<prompt>"`, `sssf sessions`, `sssf viz`.
Edit your chains in `adws/adw_*.py` and your roster in
`adws/adw_sssf_config/sssf.config.yaml`. See `sssf --help`.
<!-- /sssf -->
"""

GITIGNORE_ENTRIES = ["adws/adw_data/sessions/", "adws/adw_data/sssf.db"]


def _copy_tree(src: Path, dest: Path, *, force: bool) -> list[str]:
    copied = []
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dest / rel
        if target.exists() and not force:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.read_text())
        copied.append(str(rel))
    return copied


def run(root: Path, *, refresh: bool = False, force: bool = False) -> int:
    templates = Path(resources.files("sssf.templates"))
    root.mkdir(parents=True, exist_ok=True)

    _copy_tree(templates / "adws", root / "adws", force=force or refresh)
    config_dest = root / "adws" / "adw_sssf_config" / "sssf.config.yaml"
    if not config_dest.exists() or force:
        config_dest.parent.mkdir(parents=True, exist_ok=True)
        config_dest.write_text((templates / "sssf.config.yaml").read_text())
    _copy_tree(templates / "prompt_engineering", root / "adws" / "adw_data" / "prompt_engineering",
               force=force)
    _copy_tree(templates / "harness_engineering", root / "adws" / "adw_data" / "harness_engineering",
               force=force)

    env_dest = root / ".env.sample"
    if not env_dest.exists() or force:
        env_dest.write_text((templates / "env.sample").read_text())

    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        text = agents_md.read_text()
        if "<!-- sssf -->" not in text:
            agents_md.write_text(text.rstrip() + "\n" + AGENTS_BLOCK)
    else:
        agents_md.write_text("# Project\n" + AGENTS_BLOCK)

    gitignore = root / ".gitignore"
    if gitignore.exists():
        text = gitignore.read_text()
        missing = [line for line in GITIGNORE_ENTRIES if line not in text]
        if missing:
            gitignore.write_text(text.rstrip() + "\n" + "\n".join(missing) + "\n")
    else:
        gitignore.write_text("\n".join(GITIGNORE_ENTRIES) + "\n")

    registry.register_project(root, registry.data_dir if False else root / "adws" / "adw_data" / "sssf.db",
                              __version__, added=True)
    return 0
```

(Note: the odd `registry.data_dir if False else ...` is a placeholder guard; replace with the direct db path expression `root / "adws" / "adw_data" / "sssf.db"` in the final file.)

- [ ] **Step 4: Wire the subcommand into cli.py**

In `src/sssf/cli.py`, replace the body of `main` with argparse subparsers; for Task 5 register only `init` (later tasks add theirs):

```python
"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""
import argparse
import sys
from pathlib import Path

from sssf import __version__
from sssf import registry
from sssf.commands import init
from sssf.project import find_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sssf", description="Super Simple Software Factory CLI")
    parser.add_argument("--version", action="version", version=f"sssf {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="stamp the sssf footprint into this project")
    p_init.add_argument("--project", default=None, help="project root (default: cwd)")
    p_init.add_argument("--refresh", action="store_true", help="copy only missing files")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.set_defaults(func=lambda a: init.run(Path(a.project or ".").resolve(),
                                                refresh=a.refresh, force=a.force))

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args) if callable(args.func) else 1
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_init.py -v`
Expected: 3 PASS

- [ ] **Step 6: Manual smoke — real project stamp**

```bash
cd /tmp && rm -rf sssf-smoke && mkdir sssf-smoke && cd sssf-smoke && git init -q
sssf init
sssf projects          # lists sssf-smoke
find adws -maxdepth 2 | head -20
```

Expected: config + chains + prompts present; registry lists the project.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: sssf init stamps and registers a project"
```

---

### Task 6: `sssf run`

**Files:**
- Create: `src/sssf/commands/run.py`
- Modify: `src/sssf/cli.py` (register `run`)
- Create: `tests/test_run.py`

**Interfaces:**
- Consumes: `project.find_project` (Task 5), `registry.update_last_run` (Task 2).
- Produces: `sssf.commands.run.run(cwd: Path, adw: str, args: list[str], explicit_project: str | None) -> int` — exit code.
- Semantics: `sssf run <adw> [args...]`; `adw` may omit the `adw_` prefix; the ADW file is `<root>/adws/adw_<adw>.py`; executed via `[sys.executable, str(adw_file), *args]` with `cwd=root` and inherited env; `registry.update_last_run(root)` before exit; non-project cwd → error message + exit 1.

- [ ] **Step 1: Write the failing test**

`tests/test_run.py`:

```python
from pathlib import Path

from sssf import registry
from sssf.commands import init, run


def _setup_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(registry, "registry_path",
                        lambda: tmp_path / ".sssf" / "projects.json")
    init.run(root)
    # a stub ADW that proves the engine import works end-to-end
    stub = root / "adws" / "adw_stub_check.py"
    stub.write_text(
        "import sssf.adw_modules\n"
        "from sssf.adw_modules.data_types import EnvelopeBase\n"
        "print('STUB_OK')\n"
    )
    return root


def test_run_with_prefix(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "stub_check", [], None) == 0


def test_run_without_prefix(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "stub_check", [], None) == 0


def test_run_missing_adw_fails(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "does_not_exist", [], None) == 1


def test_run_updates_last_run(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    run.run(root, "stub_check", [], None)
    entry = registry.list_projects()[0]
    assert entry["last_run"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_run.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.commands.run`)

- [ ] **Step 3: Write minimal implementation**

`src/sssf/commands/run.py`:

```python
"""`sssf run` — execute a user ADW chain with the tool venv's python."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sssf import registry
from sssf.project import find_project


def run(cwd: Path, adw: str, args: list[str], explicit_project: str | None = None) -> int:
    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/ directory). Run `sssf init` first.", file=sys.stderr)
        return 1
    name = adw if adw.startswith("adw_") else f"adw_{adw}"
    adw_file = root / "adws" / f"{name}.py"
    if not adw_file.exists():
        print(f"sssf: no ADW named '{adw}' (looked for adws/{name}.py)", file=sys.stderr)
        return 1
    registry.update_last_run(root)
    return subprocess.call([sys.executable, str(adw_file), *args], cwd=root)
```

- [ ] **Step 4: Register the subcommand in cli.py**

Add to `main`, after the `init` subparser block:

```python
    p_run = sub.add_parser("run", help="execute an ADW chain: sssf run <adw> \"<prompt>\" [--adw-id X]")
    p_run.add_argument("adw", help="chain name; the adw_ prefix is optional")
    p_run.add_argument("args", nargs=argparse.REMAINDER, help="passed through to the ADW")
    p_run.add_argument("--project", default=None)
    p_run.set_defaults(func=lambda a: run.run(Path.cwd(), a.adw, a.args, a.project))
```

(import `from sssf.commands import init, run` at the top of cli.py)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_run.py -v`
Expected: 4 PASS

- [ ] **Step 6: Manual smoke — real ADW, no API key needed (read-only, fails at pi cleanly)**

```bash
cd /tmp/sssf-smoke && sssf run scout "list top-level dirs" ; echo "exit=$?"
```

Expected: engine boots (config validated, session minted, envelope path attempted), then pi fails with a model/key error — the trace db `adws/adw_data/sssf.db` now exists.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: sssf run executes ADW chains via the tool venv"
```

---

### Task 7: Observability commands (`sessions`, `phases`, `tail`, `procs`)

**Files:**
- Create: `src/sssf/obs.py`
- Create: `src/sssf/commands/obs_cmds.py`
- Modify: `src/sssf/cli.py` (register four subcommands)
- Create: `tests/test_obs.py`

**Interfaces:**
- Consumes: `project.find_project`, `project.data_dir` (Task 5); Tracer schema from ported `sssf.adw_modules.tracer` (Task 3).
- Produces: `obs.query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]`; four CLI commands mapping 1:1 to the pinned `justfile` recipes (queries verbatim from `templates/justfile`):
  - `sessions` → `select adw_id, status, substr(request,1,50), total_tokens, round(total_cost,4) from sessions order by started_at desc limit ?;`
  - `phases <adw_id>` → `select seq, name, kind, owner, status, attempt from phases where adw_id=? order by seq;`
  - `tail <adw_id>` → `select rowid, type, name, started_at from events where adw_id=? order by rowid desc limit 25;`
  - `procs <adw_id>` → `select kind, name, pid, command, started_at from processes where adw_id=? and ended_at is null order by id;`
- Output via `rich` tables (rich is already an engine dep). Missing db → friendly message, exit 0.

- [ ] **Step 1: Write the failing test**

`tests/test_obs.py`:

```python
import sqlite3
from pathlib import Path

from sssf.adw_modules import tracer as tracer_mod
from sssf.commands import obs_cmds


def _make_db(path: Path) -> None:
    t = tracer_mod.Tracer(db_path=path, events_jsonl=path.with_suffix(".jsonl"))
    t.session_start("abc123", "tester", "adw_prompt")
    t.session_request("abc123", "hello")
    t.session_finish("abc123", True)
    t.session_add_usage("abc123", 100, 0.001)
    t.conn.close()


def test_sessions_lists_runs(tmp_path, capsys):
    db = tmp_path / "sssf.db"
    _make_db(db)
    assert obs_cmds.sessions(db) == 0
    out = capsys.readouterr().out
    assert "abc123" in out and "adw_prompt" in out


def test_phases_empty_ok(tmp_path, capsys):
    db = tmp_path / "sssf.db"
    _make_db(db)
    assert obs_cmds.phases(db, "nope") == 0


def test_missing_db_is_friendly(tmp_path, capsys):
    assert obs_cmds.sessions(tmp_path / "missing.db") == 0
    out = capsys.readouterr().out
    assert "no trace db" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_obs.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.commands.obs_cmds`)

- [ ] **Step 3: Write minimal implementation**

`src/sssf/obs.py`:

```python
"""Read-only trace queries over a project's WAL sssf.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
```

`src/sssf/commands/obs_cmds.py`:

```python
"""sssf sessions / phases / tail / procs — the justfile recipes as commands."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from sssf import obs

SESSIONS_SQL = ("select adw_id, status, substr(request,1,50), total_tokens, "
                "round(total_cost,4) from sessions order by started_at desc limit ?;")
PHASES_SQL = "select seq, name, kind, owner, status, attempt from phases where adw_id=? order by seq;"
TAIL_SQL = ("select rowid, type, name, started_at from events "
            "where adw_id=? order by rowid desc limit 25;")
PROCS_SQL = ("select kind, name, pid, command, started_at from processes "
             "where adw_id=? and ended_at is null order by id;")

console = Console()


def _render(db: Path, sql: str, params: tuple, title: str, limit: int | None = None) -> int:
    if not db.exists():
        console.print(f"[yellow]no trace db at {db}[/yellow] — run an ADW first")
        return 0
    rows = obs.query(db, sql, params)
    table = Table(title=title)
    if rows:
        for col in rows[0].keys():
            table.add_column(col)
        for row in rows:
            table.add_row(*[str(v) if v is not None else "" for v in row])
    console.print(table)
    return 0


def sessions(db: Path, limit: int = 10) -> int:
    return _render(db, SESSIONS_SQL, (limit,), "recent runs")


def phases(db: Path, adw_id: str) -> int:
    return _render(db, PHASES_SQL, (adw_id,), f"phases {adw_id}")


def tail(db: Path, adw_id: str) -> int:
    return _render(db, TAIL_SQL, (adw_id,), f"events {adw_id}")


def procs(db: Path, adw_id: str) -> int:
    return _render(db, PROCS_SQL, (adw_id,), f"live processes {adw_id}")
```

Register in cli.py — a shared helper resolves the project db once:

```python
    def _db_path(explicit: str | None) -> Path:
        root = find_project(Path.cwd(), explicit)
        if root is None:
            print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
            raise SystemExit(1)
        return project.data_dir(root) / "sssf.db"

    for name, fn in (("sessions", obs_cmds.sessions), ("phases", obs_cmds.phases),
                     ("tail", obs_cmds.tail), ("procs", obs_cmds.procs)):
        p = sub.add_parser(name, help=f"trace: {name}")
        p.add_argument("--project", default=None)
        if name != "sessions":
            p.add_argument("adw_id")
        p.set_defaults(func=lambda a, n=name, f=fn: f(_db_path(a.project),
                                                      a.adw_id) if n != "sessions"
                                                      else f(_db_path(a.project),
                                                             getattr(a, "limit", 10)))
```

(If the closure looks cramped in practice, extract a `_register_obs(parser, sub)` helper function in cli.py — keep the four registrations in one loop.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_obs.py -v`
Expected: 3 PASS

- [ ] **Step 5: Manual smoke**

```bash
cd /tmp/sssf-smoke && sssf sessions
```

Expected: rich table with the failed `scout` run from Task 6 Step 6 (or "no trace db" if that run never minted one).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: observability commands (sessions/phases/tail/procs)"
```

---

### Task 8: `sssf projects`, `sssf doctor`, `sssf upgrade`

**Files:**
- Create: `src/sssf/commands/misc.py`
- Modify: `src/sssf/cli.py` (register three subcommands)
- Create: `tests/test_misc.py`

**Interfaces:**
- Consumes: `registry` (Task 2).
- Produces:
  - `misc.projects(action: str, name: str | None) -> int` — `list` (default) renders a rich table of name/root/db/last_run; `remove <name>` removes and reports.
  - `misc.doctor() -> int` — checks `uv`, `pi`, `bun`, `sqlite3` via `shutil.which`, `~/.local/bin` on PATH, and (when cwd is a project) that config parses; prints `[green]ok[/green]`/`[red]missing[/red]` per check; returns 1 if any core check fails.
  - `misc.upgrade() -> int` — runs `uv tool upgrade sssf` via subprocess, returns its exit code.

- [ ] **Step 1: Write the failing test**

`tests/test_misc.py`:

```python
from pathlib import Path

from sssf import registry
from sssf.commands import misc


def test_projects_list_and_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    root = tmp_path / "proj"
    root.mkdir()
    registry.register_project(root, root / "adws/adw_data/sssf.db", "0.1.0")
    assert misc.projects("list", None) == 0
    assert "proj" in capsys.readouterr().out
    assert misc.projects("remove", "proj") == 0
    assert registry.list_projects() == []


def test_doctor_reports_missing_binary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(misc, "which", lambda name: None)
    assert misc.doctor() == 1
    out = capsys.readouterr().out
    assert "missing" in out


def test_doctor_ok_when_all_present(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    assert misc.doctor() == 0
    assert "ok" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_misc.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.commands.misc`)

- [ ] **Step 3: Write minimal implementation**

`src/sssf/commands/misc.py`:

```python
"""sssf projects / doctor / upgrade."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from sssf import registry

which = shutil.which
console = Console()

CORE_TOOLS = ("uv", "pi", "bun", "sqlite3")


def projects(action: str, name: str | None) -> int:
    if action == "remove":
        if not name:
            console.print("[red]usage: sssf projects remove <name>[/red]")
            return 1
        if registry.remove_project(name):
            console.print(f"removed {name}")
            return 0
        console.print(f"[red]no project named {name}[/red]")
        return 1
    rows = registry.list_projects()
    table = Table(title="registered projects")
    for col in ("name", "root", "last_run"):
        table.add_column(col)
    for row in rows:
        table.add_row(row.get("name", ""), row.get("root", ""), row.get("last_run") or "—")
    console.print(table)
    return 0


def doctor() -> int:
    ok = True
    for tool in CORE_TOOLS:
        found = which(tool)
        if found:
            console.print(f"[green]ok[/green]  {tool} -> {found}")
        else:
            console.print(f"[red]missing[/red]  {tool}")
            ok = False
    bin_dir = Path.home() / ".local" / "bin"
    on_path = str(bin_dir) in (sys.path if False else "") or str(bin_dir) in (__import__("os").environ.get("PATH", ""))
    console.print(f"[{'green' if on_path else 'red'}]{'ok' if on_path else 'missing'}[/]  ~/.local/bin on PATH")
    return 0 if ok else 1


def upgrade() -> int:
    return subprocess.call(["uv", "tool", "upgrade", "sssf"])
```

(The `on_path` line reads PATH from `os.environ`; clean it up — the `sys.path if False else` is a placeholder to remove. Final form: `on_path = str(bin_dir) in os.environ.get("PATH", "")` with `import os` at top.)

Register in cli.py:

```python
    p = sub.add_parser("projects", help="list / remove registered projects")
    p.add_argument("action", nargs="?", default="list", choices=["list", "remove"])
    p.add_argument("name", nargs="?")
    p.set_defaults(func=lambda a: misc.projects(a.action, a.name))

    sub.add_parser("doctor", help="check global prerequisites and project state") \
       .set_defaults(func=lambda a: misc.doctor())
    sub.add_parser("upgrade", help="uv tool upgrade sssf") \
       .set_defaults(func=lambda a: misc.upgrade())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_misc.py -v`
Expected: 3 PASS

- [ ] **Step 5: Manual smoke**

```bash
sssf doctor        # all four tools + PATH line
sssf projects      # table with sssf-smoke
```

- [ ] **Step 6: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: projects/doctor/upgrade commands"
```

---

### Task 9: `sssf viz` — port the visualizer and make it a global service

**Files:**
- Copy: pinned `…/apps/visualizer/` (all files except `node_modules`, `dist`) → `src/sssf/apps/visualizer/`
- Create: `src/sssf/apps/visualizer/server/registry.ts` (new: project registry + multi-db)
- Modify: `src/sssf/apps/visualizer/server/index.ts` (project-scoped routes)
- Modify: `src/sssf/apps/visualizer/server/db.ts` (lazy per-project db cache)
- Modify: `src/sssf/apps/visualizer/src/` (project selector level in the Vue app)
- Create: `src/sssf/commands/viz.py`
- Modify: `src/sssf/cli.py` (register `viz`)
- Create: `tests/test_viz.py`

**Interfaces:**
- Consumes: ported app at pinned commit `de31374`; `registry` (Task 2) — same `projects.json` shape.
- Produces: `sssf.commands.viz.run(port: int, db_override: str | None, project: str | None) -> int` — execs `bun run server/index.ts` (cwd = package app dir) with env `SSSF_REGISTRY` (default `~/.sssf/projects.json`) and optionally `SSSF_DB` (adhoc override). Non-zero exit if `bun` missing (`misc.which("bun") is None`).
- Server behavior:
  - Env `SSSF_REGISTRY` (default `~/.sssf/projects.json`) — if absent/unreadable → `[]` projects.
  - Env `SSSF_DB` set → single "adhoc" project mode (backwards compat; the old routes keep working).
  - New route `GET /api/projects` → `[{name, root, dbExists, lastRun}]`.
  - Existing routes gain a project scope via path prefix: `GET /api/projects/:project/sessions`, `/api/projects/:project/sessions/:adw_id`, `/api/projects/:project/sessions/:adw_id/events|envelopes|gates`, `/api/projects/:project/sessions/:adw_id/agents/:agent/prompts`. The old unscoped routes remain for adhoc mode.
  - Each project db is opened lazily, read-only (`{readOnly: true}`), cached by name; missing db → 404 with `{error: "no trace db"}`.
- Frontend: add a project selector (dropdown) at the top; the api layer prefixes `/api/projects/{name}`; keep L1/L2/L3 views unchanged below it. Default selection: first project; adhoc mode hides the selector.

- [ ] **Step 1: Copy the visualizer app verbatim**

```bash
V=/Users/felipe.matos/dev/lab/mvp/super-simple-software-factory/.claude/skills/sssf/apps/visualizer
D=~/dev/lab/mvp/sssf/src/sssf/apps/visualizer
mkdir -p "$D" && cd "$V" && tar --exclude=node_modules --exclude=dist -cf - . | (cd "$D" && tar -xf -)
```

Verify: `ls "$D/server" "$D/src"` shows `index.ts`, `db.ts`, `lib/`, Vue sources.

- [ ] **Step 2: Write the failing tests**

`tests/test_viz.py` (Python side):

```python
from sssf import registry
from sssf.commands import viz, misc


def test_viz_rejects_missing_bun(monkeypatch):
    monkeypatch.setattr(misc, "which", lambda name: None)
    assert viz.run(4600, None, None) == 1
```

Add `src/sssf/apps/visualizer/server/registry.test.ts` (bun test, run with `bun test`):

```ts
import { describe, expect, test } from "bun:test";
import { ProjectRegistry } from "./registry";

describe("ProjectRegistry", () => {
  test("reads projects.json and lists dbs", () => {
    const dir = Bun.tmpdir(true);
    const reg = new ProjectRegistry(dir + "/missing.json");
    expect(reg.list()).toEqual([]);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_viz.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.commands.viz`)

- [ ] **Step 4: Implement the server side**

`src/sssf/apps/visualizer/server/registry.ts`:

```ts
/** Reads ~/.sssf/projects.json (SSSF_REGISTRY override) — runtime state, not config. */
import { readFileSync } from "fs";
import { homedir } from "os";
import { resolve } from "path";
import { Database } from "bun:sqlite";

export interface Project {
  name: string;
  root: string;
  db: string;
  lastRun: string | null;
}

export class ProjectRegistry {
  readonly path: string;
  private cache = new Map<string, Database>();

  constructor(path?: string) {
    this.path = path ?? process.env.SSSF_REGISTRY ?? resolve(homedir(), ".sssf", "projects.json");
  }

  list(): Project[] {
    try {
      const data = JSON.parse(readFileSync(this.path, "utf8"));
      return (data.projects ?? []).map((p: any) => ({
        name: p.name, root: p.root, db: p.db, lastRun: p.lastRun ?? null,
      }));
    } catch {
      return [];
    }
  }

  dbFor(name: string): Database | null {
    const cached = this.cache.get(name);
    if (cached) return cached;
    const project = this.list().find((p) => p.name === name);
    if (!project) return null;
    try {
      const db = new Database(project.db, { readOnly: true });
      this.cache.set(name, db);
      return db;
    } catch {
      return null;
    }
  }
}
```

In `server/index.ts`, replace the single `db` construction with:

```ts
import { ProjectRegistry } from "./registry";
const projects = new ProjectRegistry();
const adhocDb = process.env.SSSF_DB ? new Db(resolve(process.env.SSSF_DB)) : null;
```

Add a route table change: `GET /api/projects` and a `:project` scope. The cleanest diff: keep the existing route handlers but bind them to a resolved `Db`:

```ts
// resolve which db a request targets: /api/projects/:project/... or adhoc
function dbForProject(param: string | null): Db | null {
  if (!param) return adhocDb;                    // unscoped routes = adhoc mode
  const raw = projects.dbFor(param);
  return raw ? new DbAdapter(raw) : null;        // DbAdapter wraps bun:sqlite for the existing queries
}
```

(`db.ts` exports `class Db` over `bun:sqlite`. Add a small `DbAdapter` in `db.ts` that takes an existing `Database` and exposes the same query methods, or refactor `Db` to accept an optional `Database` — pick whichever makes the diff smallest; the route handlers must not change their query code.)

Route registration (add before the static-file fallthrough):

```ts
"/api/projects": safely(() => json(projects.list().map((p) => ({
  name: p.name, root: p.root,
  dbExists: projects.dbFor(p.name) !== null,
  lastRun: p.lastRun,
})))),
"/api/projects/:project/sessions": safely((req) => {
  const d = dbForProject(param(req, "project"));
  return d ? json(d.sessions(intQuery(req, "limit", 200))) : notFound("no trace db for project");
}),
// ...same pattern for sessions/:adw_id, events, envelopes, gates, agents/:agent/prompts
```

- [ ] **Step 5: Implement the frontend selector**

In `src/sssf/apps/visualizer/src/`: add `ProjectPicker.vue` — a `<select>` bound to a `projects` ref loaded from `/api/projects`; store the selection in the existing app store (or a module-level ref); the api helper (`src/lib/api.ts` or equivalent) prefixes all calls with `/api/projects/${selected}`. When the server runs in adhoc mode (`/api/projects` returns `[]`), hide the picker. The L1 sessions list refetches on project change. Keep all existing components otherwise untouched.

- [ ] **Step 6: Implement `sssf viz`**

`src/sssf/commands/viz.py`:

```python
"""`sssf viz` — boot the global trace visualizer (Vue + bun)."""
from __future__ import annotations

import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

from sssf.commands import misc

APP_DIR = Path(resources.files("sssf.apps") / "visualizer")


def run(port: int, db_override: str | None, project: str | None) -> int:
    if misc.which("bun") is None:
        print("sssf: bun is required for `sssf viz` — install it globally once.", file=sys.stderr)
        return 1
    env = dict(os.environ)
    if db_override:
        env["SSSF_DB"] = str(Path(db_override).resolve())
    if project:
        env["SSSF_REGISTRY"] = str(Path(project).resolve() / ".sssf" / "projects.json")
    print(f"sssf viz: http://localhost:{port} (api on the same port)")
    return subprocess.call(["bun", "run", "server/index.ts", "--port", str(port)],
                           cwd=APP_DIR, env=env)
```

Register in cli.py:

```python
    p_viz = sub.add_parser("viz", help="boot the global trace visualizer")
    p_viz.add_argument("--port", type=int, default=4600)
    p_viz.add_argument("--db", default=None, help="adhoc single-db mode")
    p_viz.add_argument("--project", default=None, help="use this project's registry")
    p_viz.set_defaults(func=lambda a: viz.run(a.port, a.db, a.project))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_viz.py -v`
Expected: 1 PASS

Run: `cd ~/dev/lab/mvp/sssf/src/sssf/apps/visualizer && bun install && bun test`
Expected: registry test PASS

- [ ] **Step 8: Manual smoke — global service over the smoke project**

```bash
sssf viz --port 4600 &
sleep 2 && curl -s localhost:4600/api/projects | head -c 400
curl -s "localhost:4600/api/projects/sssf-smoke/sessions" | head -c 400
```

Expected: `/api/projects` lists `sssf-smoke`; sessions route returns rows (or `[]`).

- [ ] **Step 9: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "feat: sssf viz — global multi-project trace service"
```

---

### Task 10: SKILL.md payload and package docs

**Files:**
- Create: `src/sssf/SKILL.md` (trimmed operating manual — the `--skill` payload)
- Create: `src/sssf/docs/customizing.md`
- Create: `src/sssf/docs/contributing.md`
- Create: `README.md` (repo root)
- Create: `LICENSE` (MIT, copyright line "IndyDevDan / sssf contributors")
- Create: `tests/test_skill_payload.py`

**Interfaces:**
- Produces: `SKILL.md` — the file `agents.py` passes via `--skill`; must exist at `src/sssf/SKILL.md` and ship in the wheel (Task 1 force-include already covers it).
- Content: the pinned `SKILL.md`'s **Hard rules** section (rules 1–10, verbatim from commit `de31374`) plus a short "operating model" intro replacing the startup/cookbook routing sections — the CLI is the orchestrator now.

- [ ] **Step 1: Write the failing test**

`tests/test_skill_payload.py`:

```python
from importlib import resources
from pathlib import Path

from sssf import adw_modules


def test_skill_exists_and_ships():
    skill = Path(resources.files("sssf") / "SKILL.md")
    assert skill.exists()
    text = skill.read_text()
    assert "Agent proposes, code disposes" in text
    assert "hard rule" in text.lower() or "Hard rules" in text


def test_agents_points_at_package_skill():
    import inspect
    src = inspect.getsource(adw_modules.agents)
    assert "SKILL.md" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_skill_payload.py -v`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: Write SKILL.md**

`src/sssf/SKILL.md`:

```markdown
---
name: sssf
description: Super Simple Software Factory — repeatable agents-plus-code workflows (ADWs) in any codebase. Use when running sssf ADWs, managing the roster in sssf.config.yaml, or observing agent workflows.
---

# Super Simple Software Factory (SSSF)

Reusable combination of **agents plus code**: deterministic Python ADW scripts own
sequencing, retries, and acceptance; coding agents (Pi) work inside bounded
phases; typed JSON envelopes carry context between them; everything streams into
SQLite for the polled visualizer. Agent proposes, code disposes.

## Operating model

You are one bounded node in a factory run by the `sssf` CLI. Your job is the work
inside your phase: read the context handed to you, do the phase's task, and
return a valid envelope JSON. The CLI owns sequencing, retries, gates, and the
trace. You do not decide what comes next.

## Hard rules (enforced across everything the factory generates)

1. **Validate before running** — every ADW declares `REQUIRED_AGENTS` and calls `agents.validate()` first; a missing/misnamed agent fails before anything spawns.
2. **Typed outputs only** — every agent call pairs with a concrete `EnvelopeBase` subclass in `adw_modules/data_types.py`; parse failures re-prompt the same session (context intact), never restart. **The output contract is a synced triad**: (a) the type in `data_types.py`, (b) the JSON example in the agent's `user.md` `## Report` section, (c) `output_type=` at every call site. Change any one, update all three in the same edit.
3. **Gates validate claims, not guesses** — `gate(envelope, run) -> list[str]` violations; failures return to the same session as corrections.
4. **Four-param rule** — any function with more than 4 parameters takes one concrete data type instead (`AgentCall`, `PhaseParams` are the pattern).
5. **One agent, one prompt, one purpose** — identity lives in `system.md`; task shape (user prompt + output type) lives at the call site.
6. **ADW scripts stay thin** — all low-level logic lives in `adw_modules/`.
7. **Every phase earns a description** — one sentence on what it does and why, never a restatement of its name.
8. **A known command is code, not an agent** — if you can write the invocation down (`bun test`, `ruff check`), it belongs in a `kind="code"` phase via `adw_modules/quality.py`.
9. **`tools:` is a capability list, `writes:` is the boundary** — unauthorized changes are detected and rolled back after every agent call (`adw_modules/permissions.py`).
10. **Every ADW ends in `run.finish()`** — pass `accepted=` so the exit code, the session status, and the banner are decided together.

## v1 scope

Pi coding agent only (`coding_agent: pi`). `claude_code` is schema-valid but
stubbed. Observe via `sssf sessions / phases / tail / procs` or `sssf viz`.
```

- [ ] **Step 4: Write the docs**

`src/sssf/docs/customizing.md` — the compressed cookbook content:
- "Your chains" — copy the closest `adws/adw_*.py`, edit the phase list; imports come from `sssf.adw_modules`; the `## Report` example, the type, and `output_type=` are one contract.
- "Your roster" — `adws/adw_sssf_config/sssf.config.yaml`: models as `provider/model-id`, thinking levels, `tools:`, `writes:`, `protected_files`.
- "Your definition of done" — one gate is one function in `adw_modules/gates.py` (pointer: the pinned `references/config.md` gate list).
- "Your quality commands" — wire real commands into `adws/adw_modules/quality.py`... *(see note)*

> Implementation note: in the new model `adw_modules` lives in the package, so "your gates/quality" are **package-level** — point users at the contributing path instead: custom gates ship as a project-local module the ADW imports, or become a PR to the tool repo. Write the customizing page accordingly: per-project customization = chains + config + prompts; engine-level changes = contributing.

`src/sssf/docs/contributing.md` — the inverted `update_modules` content: engine layout, the synced-triad rule, test command (`uv run pytest`), how to change `adw_modules` and ship it (`uv tool upgrade sssf`).

`README.md` (repo root) — quickstart (`uv tool install .`, `sssf init`, `sssf run`, `sssf viz`), the three principles (observable/customizable/reusable), pointer to the design spec and `docs/`.

`LICENSE` — MIT text with the copyright line above.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/dev/lab/mvp/sssf && uv run pytest tests/test_skill_payload.py -v`
Expected: 2 PASS

- [ ] **Step 6: Verify the wheel carries the payload**

Run:

```bash
cd ~/dev/lab/mvp/sssf && uv build && unzip -l dist/sssf-0.1.0-py3-none-any.whl | grep -E "SKILL.md|templates/adws/adw_prompt.py|apps/visualizer/server/index.ts"
```

Expected: all three paths listed in the wheel.

- [ ] **Step 7: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A && git commit -m "docs: SKILL.md payload, customizing/contributing guides, README, LICENSE"
```

---

### Task 11: End-to-end smoke (manual, credentials required)

**Files:** none (verification only).

The goal: one full green path — install, stamp, run a real read-only ADW against a live provider, watch the trace in the global visualizer. Requires an API key reachable by pi (e.g. `OPENROUTER_API_KEY` in the project's `.env`).

- [ ] **Step 1: Fresh global install**

```bash
cd ~/dev/lab/mvp/sssf && uv tool install --editable . && sssf --version && sssf doctor
```

Expected: version prints; doctor all-green.

- [ ] **Step 2: Fresh project + smoke run**

```bash
rm -rf /tmp/sssf-e2e && mkdir /tmp/sssf-e2e && cd /tmp/sssf-e2e && git init -q
sssf init
# point the roster at one key: edit adws/adw_sssf_config/sssf.config.yaml defaults.model
cp .env.sample .env   # fill OPENROUTER_API_KEY (or set defaults.model to a single-provider model)
sssf run prompt --agent scout "reply with a one-line summary of this repo"
sssf sessions
```

Expected: exit 0; `sssf sessions` shows the run with `status=accepted` and token counts.

- [ ] **Step 3: Chain with session continuity**

```bash
sssf run plan "add a /health endpoint" --adw-id e2e01
sssf run build_test "implement the plan" --adw-id e2e01
sssf phases e2e01
```

Expected: phases show plan → commit_plan → build → test; the second run resumed the session (`agent_map.json` continuity).

- [ ] **Step 4: Global visualizer**

```bash
sssf viz --port 4600 & sleep 2
curl -s localhost:4600/api/projects
curl -s localhost:4600/api/projects/sssf-e2e/sessions | head -c 300
```

Expected: project listed; sessions rows visible; open http://localhost:4600 and see the waterfall.

- [ ] **Step 5: Report results back** — note anything that deviated from the plan (schema drift, missing columns, path surprises) so it can be fixed as follow-ups.

---

## Self-Review

**Spec coverage:**
- §2 package/install → Tasks 1, 8 (`upgrade`), 11.
- §2 engine-in-package → Task 3.
- §2 visualizer kept, bun global → Tasks 9, 11.
- §3 per-project footprint → Tasks 4, 5.
- §4 run mechanism (tool venv, `adw_` prefix, passthrough, registry last_run) → Task 6.
- §5 CLI surface (`init/run/doctor/upgrade/sessions/phases/tail/procs/projects/viz`) → Tasks 5–9.
- §6 skill on demand, `--skill` payload, no global install, AGENTS.md pointer → Tasks 3, 5, 10.
- §7 visualizer global service, registry, `--db` override, project level in UI → Task 9.
- §8 upgrade + `init --refresh` (missing-only, never overwrites) → Tasks 5, 8.
- §9 cut list (nothing built for Jira/GitLab/knowledge/worktrees/human gates; no `claude_code`; no global config; registry is runtime state) → respected throughout; registry is state-only.
- §10 porting inventory → Tasks 3, 4, 9, 10.

**Placeholder scan:** two flagged placeholder expressions (Task 5 `registry.data_dir if False else ...`, Task 8 `sys.path if False else ...`) are explicitly marked for replacement in the final file — they are deliberate instructions, not silent TODOs. No TBD/TODO/「similar to」patterns otherwise.

**Type consistency:** `registry.register_project(root, db, version, added=True)` — used identically in Task 5 and 8; `init.run(root, refresh=, force=)` matches the cli lambda; `run.run(cwd, adw, args, explicit_project)` matches its cli lambda; `obs_cmds.*(db, adw_id)` matches the cli loop; `viz.run(port, db_override, project)` matches its cli lambda. `PiRequest.skill_path` defined in Task 3, used in Task 3 and asserted in Task 10. `sssf.adw_modules` import path used consistently in Tasks 4, 6, 7.
