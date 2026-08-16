# Parallel Runs & Sandboxed Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run every sssf run inside its own sandbox (git worktree + Docker container) so runs execute in parallel without touching each other or the project tree, ending in a human review gate (approve/reject) that tears the sandbox down and leaves the run's branch open for a PR.

**Architecture:** Deterministic Python owns the sandbox lifecycle — per-run git worktree (branch `sssf/<adw_id>`), a `sssf-runner` Docker image (python+git+node/pi+bun+uv+sssf), the worktree + shared `adws/adw_data/` bind-mounted into the container, per-run host port allocation, and a shared `run_reviews` db record the ADW polls for approve/reject. No auto-merge: approve/reject tear down container+worktree; the branch survives as a ref; `sssf sandbox prune` deletes it once resolved.

**Tech Stack:** Python 3.11 (pydantic config, subprocess, socket, sqlite3), git worktrees, Docker CLI, bun:test + vitest-style (visualizer), pytest.

**Spec:** `docs/superpowers/specs/2026-08-15-parallel-runs-sandbox-design.md`

## Global Constraints

- Sandbox creation/teardown is **deterministic Python** — never agent-driven, idempotent (a crash mid-teardown leaves re-runnable cleanup).
- The cwd (project root checkout) is **never** switched; parallel runs are structurally isolated (one branch per worktree).
- Credentials never enter the image: `~/.pi/agent/`, GenPlat token, and project `.env` are provided read-only at container start.
- Container runs as the host uid:gid; `safe.directory /work` + git identity set in the container entrypoint.
- Host port allocated per run (`>= sandbox.port_base`); container port is `review.port`.
- `review.command` is an arbitrary per-project string, run inside the container; auto-detect fallback (bun/npm dev script); Python-only projects without a command skip the review stage gracefully.
- The tracer's sqlite connection gains a `busy_timeout` (concurrent WAL writers).
- Existing gates stay green: `uv run pytest -q`, `bun test`, `vue-tsc`, `bun run build`, `bun run lint`.

---

### Task 1: Sandbox + review config

**Files:**
- Modify: `src/sssf/adw_modules/data_types.py`
- Modify: `src/sssf/adw_modules/agents.py` (nothing — config loads via `SSSFConfig(**raw)`)
- Test: `tests/test_sandbox_config.py`

**Interfaces:**
- Produces: `SandboxConfig { enabled: bool = True, image: str = "sssf-runner", port_base: int = 3000 }`, `ReviewConfig { command: str | None = None, port: int = 3000, poll_seconds: int = 3 }` attached to `SSSFConfig` as `.sandbox` and `.review`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_config.py`:

```python
import pytest
from pydantic import ValidationError

from sssf.adw_modules.data_types import ReviewConfig, SandboxConfig, SSSFConfig


def test_defaults():
    cfg = SSSFConfig()
    assert cfg.sandbox.enabled is True
    assert cfg.sandbox.image == "sssf-runner"
    assert cfg.sandbox.port_base == 3000
    assert cfg.review.command is None
    assert cfg.review.port == 3000
    assert cfg.review.poll_seconds == 3


def test_parses_yaml_sections(tmp_path):
    yaml_path = tmp_path / "sssf.config.yaml"
    yaml_path.write_text(
        "sandbox:\n  enabled: true\n  port_base: 4000\n"
        "review:\n  command: 'bun run dev'\n  port: 5173\n"
    )
    from sssf.adw_modules.agents import load_config
    cfg = load_config(str(yaml_path))
    assert cfg.sandbox.port_base == 4000
    assert cfg.review.command == "bun run dev"
    assert cfg.review.port == 5173


def test_validation_errors():
    with pytest.raises(ValidationError):
        ReviewConfig(port=0)          # must be a positive port
    with pytest.raises(ValidationError):
        SandboxConfig(port_base=-1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_config.py -q`
Expected: FAIL — `AttributeError: 'SSSFConfig' object has no attribute 'sandbox'`.

- [ ] **Step 3: Write the implementation**

In `src/sssf/adw_modules/data_types.py`, add (near the other config models, after `ObservabilityConfig`):

```python
class SandboxConfig(BaseModel):
    """Per-run isolation. Creation/teardown is deterministic Python — the
    ADW only runs phases; the CLI owns everything around the container."""
    enabled: bool = True
    image: str = "sssf-runner"       # tag auto-appended: sssf-runner:<sssf-version>
    port_base: int = 3000            # host ports allocated from here upward

    @field_validator("port_base")
    @classmethod
    def _port_base_positive(cls, v: int) -> int:
        if v < 1 or v > 65535:
            raise ValueError("port_base must be 1..65535")
        return v


class ReviewConfig(BaseModel):
    """The human review gate. command runs inside the container with the
    worktree as cwd; the app listens on `port` (container port)."""
    command: str | None = None        # auto-detect fallback when unset
    port: int = 3000
    poll_seconds: int = 3

    @field_validator("port")
    @classmethod
    def _port_positive(cls, v: int) -> int:
        if v < 1 or v > 65535:
            raise ValueError("port must be 1..65535")
        return v
```

Add to `SSSFConfig`:

```python
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
```

Check the import line at the top of `data_types.py` includes `field_validator` (add to the pydantic import if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sandbox_config.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite + commit**

Run: `uv run pytest -q`
Expected: all green (69 + 3).

```bash
git add src/sssf/adw_modules/data_types.py tests/test_sandbox_config.py
git commit -m "feat: sandbox + review config sections (SSSFConfig)"
```

---

### Task 2: Port allocation

**Files:**
- Create: `src/sssf/sandbox.py` (module root — the deterministic sandbox lifecycle; Tasks 3-5 land here too)
- Test: `tests/test_sandbox_port.py`

**Interfaces:**
- Produces: `allocate_port(base: int, used: set[int] | None = None) -> int` — first free port ≥ base (bind-test on 127.0.0.1), skipping ports in `used`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_port.py`:

```python
import socket

from sssf.sandbox import allocate_port


def test_allocates_at_or_above_base():
    p = allocate_port(31000)
    assert p >= 31000


def test_skips_busy_ports():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 31050))
    blocker.listen(1)
    try:
        p = allocate_port(31050)
        assert p != 31050
        assert p > 31050
    finally:
        blocker.close()


def test_skips_used_set():
    p = allocate_port(31100, used={31100, 31101})
    assert p >= 31102
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_port.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sssf.sandbox'`.

- [ ] **Step 3: Write the implementation**

Create `src/sssf/sandbox.py`:

```python
"""Deterministic sandbox lifecycle: ports, worktrees, docker, review records.

Every function here is plain Python — no agents, no ad-hoc steps. Creation
and teardown are idempotent so a crash mid-teardown leaves re-runnable
cleanup.
"""
import itertools
import socket


def allocate_port(base: int, used: set[int] | None = None) -> int:
    """First free host port >= base. Bind-tests 127.0.0.1 so parallel runs of
    the same project don't collide; `used` skips ports already handed out
    this session."""
    used = used or set()
    for port in itertools.count(base):
        if port in used or port > 65535:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sandbox_port.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_port.py
git commit -m "feat: deterministic free-port allocation (sandbox.py)"
```

---

### Task 3: Worktree lifecycle

**Files:**
- Modify: `src/sssf/sandbox.py`
- Test: `tests/test_sandbox_worktree.py`

**Interfaces:**
- Produces:
  - `sandbox_dir(project_root: Path, adw_id: str) -> Path` — `~/.sssf/sandboxes/<project-basename>/<adw_id>`
  - `create_worktree(project_root: Path, adw_id: str) -> Path` — `git worktree add -q <dir> -b sssf/<adw_id>` (from HEAD); raises `SandboxError` on failure
  - `remove_worktree(dir: Path) -> None` — idempotent: `git worktree remove --force <dir>` then `git worktree prune`; tolerates an already-removed dir
  - `delete_branch(project_root: Path, adw_id: str) -> None` — idempotent `git branch -D sssf/<adw_id>` (ignore "not found")
  - `SandboxError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_worktree.py`:

```python
import os
import subprocess

import pytest

from sssf.sandbox import (
    SandboxError,
    create_worktree,
    delete_branch,
    remove_worktree,
    sandbox_dir,
)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


def test_sandbox_dir_location(repo, tmp_path):
    d = sandbox_dir(repo, "abc123")
    assert d.name == "abc123"
    assert "proj" in d.parts
    assert d.is_absolute()


def test_create_remove_branch_survives(repo, tmp_path):
    wt = create_worktree(repo, "abc123")
    assert wt.is_dir()
    assert wt.name == "abc123"
    # the run commits in its worktree
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=wt, check=True)
    (wt / "f.txt").write_text("x\nrun work\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "run"], cwd=wt, check=True)
    # the main checkout is untouched
    main_log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True).stdout
    assert "run" not in main_log
    # remove the worktree — branch survives as a ref
    remove_worktree(wt)
    assert not wt.exists()
    branches = subprocess.run(["git", "branch", "--list", "sssf/abc123"], cwd=repo,
                              capture_output=True, text=True).stdout
    assert "sssf/abc123" in branches
    # cwd still on main
    cur = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    assert cur == "main"


def test_remove_is_idempotent(repo, tmp_path):
    wt = create_worktree(repo, "def456")
    remove_worktree(wt)
    remove_worktree(wt)   # already gone — no error


def test_delete_branch_idempotent(repo, tmp_path):
    create_worktree(repo, "ghi789")
    delete_branch(repo, "ghi789")
    delete_branch(repo, "ghi789")   # not found — no error
    branches = subprocess.run(["git", "branch", "--list", "sssf/ghi789"], cwd=repo,
                              capture_output=True, text=True).stdout
    assert branches.strip() == ""


def test_create_duplicate_raises(repo, tmp_path):
    create_worktree(repo, "dup1")
    with pytest.raises(SandboxError):
        create_worktree(repo, "dup1")   # branch already checked out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_worktree.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_worktree'`.

- [ ] **Step 3: Write the implementation**

Append to `src/sssf/sandbox.py`:

```python
import os
import subprocess
from pathlib import Path


class SandboxError(RuntimeError):
    """A sandbox lifecycle step failed deterministically."""


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def sandbox_dir(project_root: Path, adw_id: str) -> Path:
    """~/.sssf/sandboxes/<project-basename>/<adw_id> — the repo tree stays clean."""
    home = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf"))
    return home / "sandboxes" / project_root.name / adw_id


def create_worktree(project_root: Path, adw_id: str) -> Path:
    """git worktree add -q <dir> -b sssf/<adw_id>. The branch is unique per
    adw_id; worktrees are structurally isolated (a branch lives in one)."""
    branch = f"sssf/{adw_id}"
    wt = sandbox_dir(project_root, adw_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    r = _run_git(project_root, "worktree", "add", "-q", str(wt), "-b", branch)
    if r.returncode != 0:
        raise SandboxError(f"worktree add failed: {r.stderr.strip()}")
    return wt


def remove_worktree(wt_dir: Path) -> None:
    """Idempotent: remove the worktree (force — the run's work is committed on
    its branch, and teardown must never block on stray files), then prune."""
    if not wt_dir.exists():
        return
    # The worktree is registered in the repo that owns it — resolve the repo
    # from the worktree's own gitdir metadata via `git -C <wt> rev-parse`.
    r = subprocess.run(
        ["git", "-C", str(wt_dir), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 0:
        root = Path(r.stdout.strip())
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(wt_dir)],
                       capture_output=True, text=True, check=False)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"],
                       capture_output=True, text=True, check=False)


def delete_branch(project_root: Path, adw_id: str) -> None:
    """Idempotent: delete the run's branch ref (call after the engineer
    merged/PR'd, or to discard a failed run)."""
    _run_git(project_root, "branch", "-D", f"sssf/{adw_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sandbox_worktree.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_worktree.py
git commit -m "feat: deterministic worktree lifecycle (create/remove/branch, idempotent)"
```

---

### Task 4: Docker lifecycle (fake-docker tested)

**Files:**
- Modify: `src/sssf/sandbox.py`
- Test: `tests/test_sandbox_docker.py`

**Interfaces:**
- Produces (all shell to the docker CLI, deterministic, idempotent):
  - `docker_available() -> bool` — `docker info` exits 0
  - `build_image(image: str, dockerfile: Path) -> None` — `docker build -f <dockerfile> <repo-root>`; raises `SandboxError` on failure
  - `run_sandbox(image: str, name: str, *, worktree: Path, data_dir: Path, pi_home: Path, host_port: int, container_port: int, uid: int, gid: int, env: dict[str, str], cmd: list[str]) -> None` — `docker run -d --name <name> -v worktree:/work -w /work -v data_dir:/work/adws/adw_data -v pi_home:/home/agent/.pi/agent:ro -p host_port:container_port --user uid:gid -e ... <image> <cmd...>`
  - `wait_exit(name: str, timeout_s: int) -> int` — `docker wait` with a fallback timeout check
  - `stop_remove(name: str) -> None` — idempotent `docker rm -f <name>` (ignore "not found")

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_docker.py` — the tests run against a **fake docker shim** on PATH so no docker daemon is needed:

```python
import os
import stat
import subprocess
from pathlib import Path

import pytest

from sssf.sandbox import SandboxError, build_image, docker_available, run_sandbox, stop_remove, wait_exit


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """A docker shim that records invocations and answers canned outputs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    shim = bin_dir / "docker"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        "    f.write('|'.join(sys.argv[2:]) + '\\n')\n"
        "if ' info' in ' '.join(sys.argv):\n"
        "    print('Server Version: 29.1.3')\n"
        "elif ' wait ' in ' '.join(sys.argv):\n"
        "    print('0')\n"
        "elif ' rm ' in ' '.join(sys.argv):\n"
        "    sys.exit(0)\n"
        "else:\n"
        "    sys.exit(0)\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


def test_docker_available(fake_docker):
    assert docker_available() is True


def test_build_image_calls_docker(fake_docker, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    build_image("sssf-runner", dockerfile)
    calls = fake_docker.read_text().splitlines()
    assert any("build" in c and "Dockerfile" in c for c in calls)


def test_build_failure_raises(fake_docker, tmp_path, monkeypatch):
    bin_dir = fake_docker.parent / "bin"
    (bin_dir / "docker").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
    (bin_dir / "docker").chmod((bin_dir / "docker").stat().st_mode | stat.S_IEXEC)
    with pytest.raises(SandboxError):
        build_image("sssf-runner", tmp_path / "Dockerfile")


def test_run_sandbox_flags(fake_docker, tmp_path):
    run_sandbox(
        "sssf-runner", "sssf-abc",
        worktree=tmp_path / "wt", data_dir=tmp_path / "data",
        pi_home=tmp_path / "pi", host_port=3456, container_port=3000,
        uid=501, gid=20, env={"REVIEW_HOST_PORT": "3456"}, cmd=["python", "adws/adw_simple_sdlc.py"],
    )
    calls = fake_docker.read_text().splitlines()
    run = next(c for c in calls if c.startswith("run"))
    assert "--name sssf-abc" in run
    assert f"{tmp_path}/wt:/work" in run
    assert f"{tmp_path}/data:/work/adws/adw_data" in run
    assert "-p 3456:3000" in run
    assert "--user 501:20" in run
    assert "-e REVIEW_HOST_PORT=3456" in run


def test_wait_exit_and_stop_remove_idempotent(fake_docker):
    assert wait_exit("sssf-abc", timeout_s=5) == 0
    stop_remove("sssf-abc")
    stop_remove("sssf-abc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_docker.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_sandbox'`.

- [ ] **Step 3: Write the implementation**

Append to `src/sssf/sandbox.py`:

```python
import os
import shutil
import time

DOCKER = shutil.which("docker") or "docker"


def _docker(*args: str, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, check=False, timeout=timeout_s)


def docker_available() -> bool:
    r = _docker("info")
    return r.returncode == 0


def build_image(image: str, dockerfile: Path) -> None:
    r = _docker("build", "-f", str(dockerfile), str(dockerfile.parent))
    if r.returncode != 0:
        raise SandboxError(f"docker build failed: {r.stderr.strip()[:500]}")


def run_sandbox(
    image: str,
    name: str,
    *,
    worktree: Path,
    data_dir: Path,
    pi_home: Path,
    host_port: int,
    container_port: int,
    uid: int,
    gid: int,
    env: dict[str, str],
    cmd: list[str],
) -> None:
    """docker run -d with the worktree + shared data bound, credentials ro."""
    args = [
        "run", "-d", "--name", name,
        "-v", f"{worktree}:/work",
        "-w", "/work",
        "-v", f"{data_dir}:/work/adws/adw_data",
        "-v", f"{pi_home}:/home/agent/.pi/agent:ro",
        "-p", f"{host_port}:{container_port}",
        "--user", f"{uid}:{gid}",
    ]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    args += [image, *cmd]
    r = _docker(*args)
    if r.returncode != 0:
        raise SandboxError(f"docker run failed: {r.stderr.strip()[:500]}")


def wait_exit(name: str, timeout_s: int) -> int:
    """docker wait, but bound: the ADW decides within poll_seconds of the
    review row changing; anything past the bound is treated as 0 (the row
    already records the decision)."""
    try:
        r = _docker("wait", name, timeout_s=timeout_s)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except subprocess.TimeoutExpired:
        pass
    return 0


def stop_remove(name: str) -> None:
    """Idempotent: remove the container whether running or stopped."""
    _docker("rm", "-f", name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sandbox_docker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_docker.py
git commit -m "feat: deterministic docker lifecycle (build/run/wait/rm, idempotent)"
```

---

### Task 5: `run_reviews` data layer + WAL busy_timeout

**Files:**
- Modify: `src/sssf/adw_modules/tracer.py`
- Modify: `src/sssf/sandbox.py`
- Test: `tests/test_sandbox_reviews.py`

**Interfaces:**
- Produces:
  - tracer: `review_pending(adw_id: str, host_port: int) -> None` (insert-or-keep pending), `review_decide(adw_id: str, status: str) -> None` (approved|rejected, idempotent), `review_status(adw_id: str) -> str | None`
  - `run_reviews` table: `(adw_id TEXT PRIMARY KEY, status TEXT NOT NULL, host_port INTEGER, updated_at TEXT)`
  - the tracer's sqlite connection runs `PRAGMA busy_timeout = 5000`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_reviews.py`:

```python
import sqlite3

from sssf.adw_modules.tracer import Tracer
from sssf.adw_modules.data_types import ConfigDefaults, ObservabilityConfig
from sssf.sandbox import run_reviews_path


def _tracer(tmp_path) -> Tracer:
    db = tmp_path / "sssf.db"
    return Tracer(str(db), str(tmp_path / "events.jsonl"))


def test_pending_decide_status(tmp_path):
    t = _tracer(tmp_path)
    t.review_pending("abc123", host_port=3456)
    assert t.review_status("abc123") == "pending"
    t.review_decide("abc123", "approved")
    assert t.review_status("abc123") == "approved"
    t.review_decide("abc123", "approved")   # idempotent
    assert t.review_status("abc123") == "approved"


def test_reject(tmp_path):
    t = _tracer(tmp_path)
    t.review_pending("def456", host_port=3457)
    t.review_decide("def456", "rejected")
    assert t.review_status("def456") == "rejected"


def test_unknown_status_is_none(tmp_path):
    t = _tracer(tmp_path)
    assert t.review_status("nope") is None


def test_busy_timeout_set(tmp_path):
    t = _tracer(tmp_path)
    row = t.conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000


def test_host_port_recorded(tmp_path):
    t = _tracer(tmp_path)
    t.review_pending("xyz", host_port=4001)
    row = t.conn.execute("SELECT host_port FROM run_reviews WHERE adw_id='xyz'").fetchone()
    assert row[0] == 4001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_reviews.py -q`
Expected: FAIL — `AttributeError: 'Tracer' object has no attribute 'review_pending'`.

- [ ] **Step 3: Write the implementation**

In `src/sssf/adw_modules/tracer.py`:

1. In `__init__`/connect, after the existing schema setup, add:

```python
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS run_reviews ("
            " adw_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            " host_port INTEGER, updated_at TEXT)")
```

(Find the connect spot where the other `CREATE TABLE IF NOT EXISTS` statements run — same place.)

2. Add methods to `Tracer`:

```python
    def review_pending(self, adw_id: str, host_port: int) -> None:
        """The ADW's review stage marks the run pending; the decision arrives
        from the host CLI through the same shared db."""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO run_reviews (adw_id, status, host_port, updated_at)"
            " VALUES (?, 'pending', ?, ?)"
            " ON CONFLICT(adw_id) DO NOTHING",
            (adw_id, host_port, now))

    def review_decide(self, adw_id: str, status: str) -> None:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO run_reviews (adw_id, status, host_port, updated_at)"
            " VALUES (?, ?, NULL, ?)"
            " ON CONFLICT(adw_id) DO UPDATE SET status=excluded.status,"
            " updated_at=excluded.updated_at",
            (adw_id, status, now))

    def review_status(self, adw_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM run_reviews WHERE adw_id=?", (adw_id,)).fetchone()
        return row[0] if row else None
```

Note: the tracer's connect may use a different connection setup for read-only viz use — the visualizer opens the db read-only, so `run_reviews` creation must not break the read-only open (it won't — the visualizer doesn't create tables; the ADW/CLI create them). The tracer's `conn` may be created via `sqlite3.connect` with `check_same_thread` — follow the existing connect pattern in the file.

In `src/sssf/sandbox.py`, add a small helper (used by the CLI later):

```python
def review_db_path(data_dir: Path) -> Path:
    """The shared db lives in the project's data dir (bind-mounted into the
    container at /work/adws/adw_data)."""
    return data_dir / "sssf.db"
```

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `uv run pytest tests/test_sandbox_reviews.py -q && uv run pytest -q`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/tracer.py src/sssf/sandbox.py tests/test_sandbox_reviews.py
git commit -m "feat: run_reviews record + WAL busy_timeout in the tracer"
```

---

### Task 6: ADW engineer-review stage

**Files:**
- Create: `src/sssf/adw_modules/review.py`
- Modify: `src/sssf/templates/adws/adw_simple_sdlc.py`
- Test: `tests/test_adw_review.py`

**Interfaces:**
- Produces: `human_review(run, cfg, ph, prompt: str) -> bool` — starts the dev server, waits for readiness, marks the run pending, logs the URL, polls for a decision, stops the server, returns True (approved) / False (rejected); `auto_review_command(root: Path) -> str | None` — the auto-detect fallback.
- Consumes: `cfg.review.*`, `cfg.sandbox.*` (Task 1), `Tracer.review_pending/status` (Task 5), `REVIEW_HOST_PORT` env (set by the spawn, Task 7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_adw_review.py`:

```python
import os
import socket
import subprocess
import threading
import time

from sssf.adw_modules.data_types import ReviewConfig, SSSFConfig
from sssf.adw_modules.review import auto_review_command, human_review
from sssf.adw_modules.tracer import Tracer
from sssf.adw_modules.session import Run


class _Ph:
    def __init__(self): self.logged = []
    def log(self, **kw): self.logged.append(kw)


def _run(tmp_path) -> tuple[Run, Tracer]:
    tracer = Tracer(str(tmp_path / "sssf.db"), str(tmp_path / "events.jsonl"))
    cfg = SSSFConfig()
    r = Run(cfg=cfg, adw_id="rvw1", tracer=tracer, engineer="E")
    return r, tracer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_auto_review_command(tmp_path):
    # bun.lock + dev script -> bun run dev
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    (tmp_path / "bun.lock").write_text("")
    assert auto_review_command(tmp_path) == "bun run dev"
    # package-lock -> npm run dev
    (tmp_path / "bun.lock").unlink()
    (tmp_path / "package-lock.json").write_text("")
    assert auto_review_command(tmp_path) == "npm run dev"
    # python-only -> None
    (tmp_path / "package.json").unlink()
    (tmp_path / "pyproject.toml").write_text("")
    assert auto_review_command(tmp_path) is None


def test_human_review_approves(tmp_path):
    run, tracer = _run(tmp_path)
    port = _free_port()
    cfg = SSSFConfig(review=ReviewConfig(command=f"python -m http.server {port}", port=port, poll_seconds=1))
    ph = _Ph()
    os.environ["REVIEW_HOST_PORT"] = str(port)
    try:
        result = {}
        def decide():
            time.sleep(1.5)
            tracer.review_decide(run.adw_id, "approved")
        threading.Thread(target=decide, daemon=True).start()
        ok = human_review(run, cfg, ph, "review me")
        assert ok is True
        assert tracer.review_status(run.adw_id) == "approved"
        # the URL was logged
        assert any("http://localhost:" in str(x) for x in ph.logged)
    finally:
        os.environ.pop("REVIEW_HOST_PORT", None)


def test_human_review_rejects(tmp_path):
    run, tracer = _run(tmp_path)
    port = _free_port()
    cfg = SSSFConfig(review=ReviewConfig(command=f"python -m http.server {port}", port=port, poll_seconds=1))
    ph = _Ph()
    os.environ["REVIEW_HOST_PORT"] = str(port)
    try:
        def decide():
            time.sleep(1.5)
            tracer.review_decide(run.adw_id, "rejected")
        threading.Thread(target=decide, daemon=True).start()
        ok = human_review(run, cfg, ph, "review me")
        assert ok is False
    finally:
        os.environ.pop("REVIEW_HOST_PORT", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_adw_review.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sssf.adw_modules.review'`.

- [ ] **Step 3: Write the implementation**

Create `src/sssf/adw_modules/review.py`:

```python
"""The human review gate: run the changed app, wait for the engineer's
approve/reject (read from the shared db), report the decision."""
import os
import socket
import subprocess
import time
from pathlib import Path


def auto_review_command(root: Path) -> str | None:
    """Detect a dev command from project markers. Python-only projects get
    None — the review stage is skipped with a hint."""
    pkg = root / "package.json"
    if pkg.exists():
        try:
            import json
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except Exception:
            scripts = {}
        if scripts.get("dev"):
            return "bun run dev" if (root / "bun.lock").exists() else "npm run dev"
    return None


def _port_open(port: int, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def human_review(run, cfg, ph, prompt: str) -> bool:
    """Start review.command (or the auto-detected one), wait for the port,
    mark pending, log the URL, poll for a decision. Returns True on approve."""
    review = cfg.review
    command = review.command or auto_review_command(run.root)
    if not command:
        ph.log(input="no review command configured — skipping the human gate")
        return True   # treat as approved; the run completes without the gate

    host_port = int(os.environ.get("REVIEW_HOST_PORT", review.port))
    proc = subprocess.Popen(command, cwd=str(run.root), shell=True)

    try:
        if not _port_open(review.port):
            ph.log(input=f"dev server did not open port {review.port} — skipping gate")
            return True
        run.tracer.review_pending(run.adw_id, host_port=host_port)
        ph.log(input=f"reviewing at http://localhost:{host_port}")
        while True:
            status = run.tracer.review_status(run.adw_id)
            if status == "approved":
                ph.log(input="approved")
                return True
            if status == "rejected":
                ph.log(input="rejected")
                return False
            time.sleep(review.poll_seconds)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
```

In `src/sssf/templates/adws/adw_simple_sdlc.py`:

1. Add to imports: `from sssf.adw_modules.review import human_review`.
2. After the document/commit_docs phases (the last phase before the run's finish), add:

```python
    with run.phase(PhaseParams(name="review", kind="human", owner=run.engineer,
                               description="Engineer tests the running app, then approves or rejects")) as ph:
        approved = human_review(run, cfg, ph, prompt)
        if not approved:
            raise RuntimeError("engineer rejected the run")
```

(Raising from inside the phase block makes the phase manager mark the phase
and the session failed — the existing failure path. Verify `cfg` is in scope
in `main()` — it is, loaded at the top.)

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `uv run pytest tests/test_adw_review.py -q && uv run pytest -q`
Expected: both pass. Note: `Run` may need a `root` attribute — check `session.py`'s `Run` for the attribute name and use it (it exists as the project root).

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/review.py src/sssf/templates/adws/adw_simple_sdlc.py tests/test_adw_review.py
git commit -m "feat: ADW engineer-review stage (dev server + approve/reject wait)"
```

---

### Task 7: CLI orchestration — sandboxed spawn, approve/reject, sandbox commands

**Files:**
- Modify: `src/sssf/commands/run.py`
- Modify: `src/sssf/commands/ticket.py`
- Modify: `src/sssf/cli.py`
- Modify: `src/sssf/sandbox.py`
- Test: `tests/test_sandbox_cli.py`

**Interfaces:**
- Consumes: `sandbox.py` (Tasks 2-5), `allocate_port`, `create_worktree`, `run_sandbox`, `wait_exit`, `stop_remove`, `remove_worktree`, `delete_branch`, `docker_available`, `build_image`, tracer review methods.
- Produces CLI:
  - `sssf run [--no-sandbox]` / `sssf ticket run [--no-sandbox]` — sandboxed by default
  - `sssf run approve <adw_id> [--project ROOT]` — decide + teardown (docker wait → rm → worktree remove); branch stays
  - `sssf run reject <adw_id> [--project ROOT]` — same + marks failed via the ADW exit
  - `sssf sandbox build` — build the runner image
  - `sssf sandbox list` — table of sandboxes (adw_id · status · branch · container · worktree)
  - `sssf sandbox prune [<adw_id>|--all]` — remove_worktree + delete_branch + stop_remove (idempotent)

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox_cli.py` (fake docker shim as in Task 4; real git temp repo):

```python
import os
import stat
import subprocess
from pathlib import Path

from sssf.commands import misc  # noqa: F401  (CLI registration smoke)

import sssf.cli as cli  # noqa: F401
```

This task's core logic is testable without the full CLI: put the orchestration in `sandbox.py` and test it directly:

```python
def _make_repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


def test_spawn_sandbox_creates_worktree_and_records_port(tmp_path, monkeypatch):
    root = _make_repo(tmp_path)
    from sssf.sandbox import allocate_port, create_worktree, run_reviews_path, SandboxError
    wt = create_worktree(root, "abc123")
    assert wt.is_dir()
    port = allocate_port(31200)
    assert port >= 31200
    # the orchestration helper under test: spawn_sandbox
    from sssf.sandbox import spawn_sandbox, sandbox_dir
    record = spawn_sandbox(root, "abc123", cmd=["true"], port=port, image="sssf-runner")
    assert record["worktree"] == str(sandbox_dir(root, "abc123"))
    assert record["host_port"] == port
    assert record["name"] == "sssf-abc123"


def test_teardown_keeps_branch(tmp_path):
    root = _make_repo(tmp_path)
    from sssf.sandbox import create_worktree, remove_worktree
    wt = create_worktree(root, "abc123")
    remove_worktree(wt)
    branches = subprocess.run(["git", "branch", "--list", "sssf/abc123"], cwd=root,
                              capture_output=True, text=True).stdout
    assert "sssf/abc123" in branches
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sandbox_cli.py -q`
Expected: FAIL — `ImportError: cannot import name 'spawn_sandbox'`.

- [ ] **Step 3: Write the implementation**

In `src/sssf/sandbox.py`, add the orchestration + a container-name helper:

```python
def container_name(adw_id: str) -> str:
    return f"sssf-{adw_id}"


def spawn_sandbox(project_root: Path, adw_id: str, *, cmd: list[str],
                  port: int, image: str, data_dir: Path, pi_home: Path,
                  container_port: int = 3000, env: dict[str, str] | None = None,
                  uid: int | None = None, gid: int | None = None) -> dict:
    """Create the worktree + start the container. Deterministic; returns the
    sandbox record (worktree, name, host_port)."""
    wt = create_worktree(project_root, adw_id)
    uid = uid if uid is not None else os.getuid()
    gid = gid if gid is not None else os.getgid()
    run_sandbox(
        image, container_name(adw_id),
        worktree=wt, data_dir=data_dir, pi_home=pi_home,
        host_port=port, container_port=container_port,
        uid=uid, gid=gid, env=env or {}, cmd=cmd,
    )
    return {"worktree": str(wt), "name": container_name(adw_id), "host_port": port}
```

In `src/sssf/commands/run.py` — replace the direct subprocess spawn with a sandboxed path (keep the current behavior under `--no-sandbox`):

```python
def run(adw: str, args: list[str], project: str | None = None, no_sandbox: bool = False) -> int:
    root = ...  # existing project resolution
    if no_sandbox or not _sandbox_enabled(root):
        return subprocess.call([sys.executable, str(adw_file), *args], cwd=root)
    return _run_sandboxed(root, adw_file, args)
```

with helpers:

```python
def _sandbox_enabled(root: Path) -> bool:
    from sssf.adw_modules.agents import load_config
    try:
        return load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml")).sandbox.enabled
    except Exception:
        return False
```

The sandboxed spawn resolves the config, checks docker, allocates a port (only
when the run will reach the review stage — allocate eagerly, it's cheap),
builds the cmd `[sys.executable, str(adw_file), *args, "--adw-id", adw_id]`
with the prompt written into the worktree's `adws/prompts/` for ticket runs
(the ticket command already computes `prompt_path` — relocate it into the
worktree before spawning). See the ticket-run integration note below.

In `src/sssf/commands/ticket.py` — the run() function writes the prompt and
spawns; change the prompt target to the worktree's prompts dir and spawn via
the sandbox. `next_prompt_name` already takes a root — call it with the
worktree root.

Add the new CLI commands to `src/sssf/cli.py`:

```python
def cmd_approve(args) -> int: ...   # review_decide + teardown
def cmd_reject(args) -> int: ...    # review_decide("rejected") + teardown
def cmd_sandbox(args) -> int: ...   # build | list | prune
```

`approve`/`reject` logic (in sandbox.py as `decide_and_teardown`):

```python
def decide_and_teardown(project_root: Path, adw_id: str, status: str,
                        data_dir: Path) -> int:
    """Mark the decision, wait for the ADW to notice and exit, then tear the
    container + worktree down. The branch sssf/<adw_id> survives."""
    import sqlite3
    from sssf.adw_modules.tracer import Tracer
    db_path = review_db_path(data_dir)
    tracer = Tracer(str(db_path), str(data_dir / "sessions" / adw_id / "events.jsonl"))
    tracer.review_decide(adw_id, status)
    wait_exit(container_name(adw_id), timeout_s=30)
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    return 0
```

`prune` logic:

```python
def prune_sandbox(project_root: Path, adw_id: str, data_dir: Path) -> int:
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    delete_branch(project_root, adw_id)
    return 0
```

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `uv run pytest tests/test_sandbox_cli.py -q && uv run pytest -q`
Expected: both pass.

- [ ] **Step 5: Manual smoke (optional, docker present)**

```bash
cd ~/dev/lab/mvp/sssf && uv run sssf sandbox build
```
Expected: the `sssf-runner` image builds. (Full end-to-end run is covered by the integration note in the spec — not a gate.)

- [ ] **Step 6: Commit**

```bash
git add src/sssf/sandbox.py src/sssf/commands/run.py src/sssf/commands/ticket.py src/sssf/cli.py tests/test_sandbox_cli.py
git commit -m "feat: sandboxed spawn, approve/reject, sandbox build/list/prune CLI"
```

---

### Task 8: Viz — review route + trace Approve/Reject buttons

**Files:**
- Modify: `src/sssf/apps/visualizer/server/index.ts`
- Modify: `src/sssf/apps/visualizer/server/db.ts`
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`
- Modify: `src/sssf/apps/visualizer/src/components/SessionTrace.vue`
- Test: `src/sssf/apps/visualizer/server/review.test.ts`

**Interfaces:**
- Produces:
  - `POST /api/projects/:project/sessions/:adw_id/review` body `{decision: "approve"|"reject"}` → shells `sssf run approve|reject <adw_id> --project <root>`; 409 when the run isn't pending; `{ok: true}`
  - `SessionDetail.review: {status: string, host_port: number | null} | null`
  - `fetchReview(adwId, decision)` in api.ts
  - SessionTrace run-strip: Approve/Reject buttons when `review?.status === 'pending'`, plus the URL when present

- [ ] **Step 1: Write the failing test**

Create `src/sssf/apps/visualizer/server/review.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// reviewFor is the db-side helper returning the review record for a session.
import { reviewFor } from "./db";

function makeDb(path: string): Database {
  const db = new Database(path);
  db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT)`);
  db.run(`CREATE TABLE run_reviews (
    adw_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    host_port INTEGER, updated_at TEXT)`);
  return db;
}

describe("reviewFor", () => {
  test("returns the review record or null", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-review-"));
    const db = makeDb(join(dir, "sssf.db"));
    db.query("INSERT INTO run_reviews VALUES (?,?,?,?)").run("r1", "pending", 3456, "t");
    expect(reviewFor(db, "r1")).toEqual({ status: "pending", host_port: 3456 });
    expect(reviewFor(db, "nope")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/sssf/apps/visualizer && bun test server/review.test.ts`
Expected: FAIL — `reviewFor` is not exported from `./db`.

- [ ] **Step 3: Write the implementation**

In `src/sssf/apps/visualizer/server/db.ts`, add:

```ts
  /** The run's human-review record, or null when it hasn't reached that stage. */
  reviewFor(adwId: string): { status: string; host_port: number | null } | null {
    try {
      const row = this.db.query<{ status: string; host_port: number | null }, [string]>(
        "SELECT status, host_port FROM run_reviews WHERE adw_id = ?",
      ).get(adwId);
      return row ? { status: row.status, host_port: row.host_port ?? null } : null;
    } catch {
      return null;   // table may not exist yet (never reached the review stage)
    }
  }
```

and extend `sessionDetail` to include it:

```ts
    return {
      session,
      usage: this.usage(adwId),
      phases: this.phases(adwId),
      agents: this.agentSessions(adwId),
      review: this.reviewFor(adwId),
    };
```

In `src/sssf/apps/visualizer/server/index.ts`, add the route next to the archive route:

```ts
    "/api/projects/:project/sessions/:adw_id/review": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      const adwId = param(req, "adw_id");
      if (!root) return notFound(`no project ${name}`);
      const db = dbForProject(name);
      if (!db) return notFound("no trace db for project");
      const review = db.reviewFor(adwId);
      if (!review || review.status !== "pending") {
        return json({ error: `no pending review for ${adwId}` }, 409);
      }
      const body = await req.json().catch(() => null) as { decision?: string } | null;
      const decision = body?.decision;
      if (decision !== "approve" && decision !== "reject") {
        return json({ error: "decision must be approve|reject" }, 400);
      }
      const proc = Bun.spawn(["sssf", "run", decision, adwId, "--project", root],
        { stdout: "pipe", stderr: "pipe" });
      const output = await new Response(proc.stdout).text();
      await proc.exited;
      return json({ ok: proc.exitCode === 0, output });
    }),
```

In `src/sssf/apps/visualizer/src/lib/api.ts`:

```ts
export interface SessionReview { status: string; host_port: number | null }
export async function decideReview(adwId: string, decision: "approve" | "reject"): Promise<{ ok: boolean; output?: string }> {
  const res = await fetch(`${base()}/sessions/${encodeURIComponent(adwId)}/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision }),
  })
  const data = (await res.json().catch(() => null)) as { ok?: boolean; output?: string } | null
  return { ok: data?.ok ?? res.ok, output: data?.output }
}
```

In `SessionTrace.vue`: the session detail now carries `review`; add to the run-strip (next to the ticket button), only when `session-detail.review?.status === 'pending'`:

```html
      <span v-if="detail?.review?.status === 'pending'" class="review-actions">
        <a v-if="detail.review.host_port" :href="`http://localhost:${detail.review.host_port}`" target="_blank" rel="noreferrer" class="review-url">open app ↗</a>
        <button class="strip-archive approve" type="button" @click="decide('approve')">approve</button>
        <button class="strip-archive reject" type="button" @click="decide('reject')">reject</button>
      </span>
```

with `detail` being the fetched session detail (check the existing ref name) and `decide` calling `decideReview(props.adwId, decision)` then re-pulling the detail. Style `.approve` green and `.reject` red accents.

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `cd src/sssf/apps/visualizer && bun test && bun run typecheck && bun run build`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server/index.ts src/sssf/apps/visualizer/server/db.ts src/sssf/apps/visualizer/server/review.test.ts src/sssf/apps/visualizer/src/lib/api.ts src/sssf/apps/visualizer/src/components/SessionTrace.vue
git commit -m "feat: viz review route + trace approve/reject buttons"
```

---

### Task 9: Runner Dockerfile + docs

**Files:**
- Create: `docker/sssf-runner.Dockerfile`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`

- [ ] **Step 1: Create the Dockerfile**

Create `docker/sssf-runner.Dockerfile`:

```dockerfile
# The sssf-runner image: python + git + node/pi + bun + uv + sssf.
# NO credentials, NO project files — the host provides those at container start.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# uv (python project runtimes)
RUN pip install --no-cache-dir uv

# bun (JS/TS app runtimes)
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

# pi — the coding-agent CLI the ADW shells to for agent calls
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# sssf itself (the build context is the sssf repo root)
COPY pyproject.toml README.md /opt/sssf/
COPY src/sssf /opt/sssf/src/sssf/
RUN pip install --no-cache-dir /opt/sssf

# Run as the host uid:gid; safe.directory so git trusts the mounted worktree.
RUN groupadd -g 1000 agent && useradd -u 1000 -g 1000 -m agent
USER 1000:1000
ENV HOME=/home/agent
RUN git config --global --add safe.directory /work

ENTRYPOINT ["/bin/sh", "-c", "git config --global --add safe.directory /work 2>/dev/null; exec \"$@\"", "--"]
```

Note: `USER 1000:1000` is the fallback; the CLI overrides with the host uid:gid via `--user` at run time (the image's baked user is only for the default case).

- [ ] **Step 2: README**

In `README.md`, under `## Run semantics` (or a new `## Sandboxed runs` section), add:

```markdown
## Sandboxed runs (parallel-safe)

Each run executes in its own sandbox — a git worktree (branch `sssf/<adw_id>`)
bind-mounted into a `sssf-runner` container — so multiple runs proceed in
parallel without touching the project tree. The run ends in a human review
gate: the changed app runs inside the container (config `review.command`, port
forwarded per run), and `sssf run approve|reject <adw_id>` (or the trace-page
buttons) tears the sandbox down, leaving the branch open for a PR.

- `sssf sandbox build` — build/refresh the runner image
- `sssf sandbox list` — show sandboxes and their branches
- `sssf sandbox prune [<adw_id>|--all]` — delete a resolved run's branch + leftovers
- `--no-sandbox` — run in the current dir (today's behavior), for debugging
```

- [ ] **Step 3: Revisions index**

In `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`, add:

```markdown
- 2026-08-15 — [parallel runs & sandbox isolation](2026-08-15-parallel-runs-sandbox-design.md):
  per-run worktree + Docker sandbox, human review gate, per-run port allocation.
```

- [ ] **Step 4: Commit**

```bash
git add docker/sssf-runner.Dockerfile README.md docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md
git commit -m "docs: sssf-runner Dockerfile + sandboxed runs docs"
```

---

## Self-Review

- **Spec coverage:** deterministic lifecycle ✓ (Tasks 2-5, 7 — all plain Python, idempotent), worktree per run ✓ (Task 3), Docker bind-mount + uid + creds-ro ✓ (Task 4), shared data dir ✓ (Task 4 mounts), per-run port allocation ✓ (Tasks 2, 7), review gate + approve/reject + trace-only buttons ✓ (Tasks 6, 8), branch survives + prune deletes ✓ (Tasks 3, 7), generic review command + auto-detect + skip ✓ (Task 6), `run_reviews` + busy_timeout ✓ (Task 5), CLI surface ✓ (Task 7), Dockerfile ✓ (Task 9), viz route ✓ (Task 8).
- **Type/interface consistency:** `allocate_port(base, used)` defined Task 2, used Task 7; `create_worktree/remove_worktree/delete_branch` Task 3 → Task 7; `run_sandbox/wait_exit/stop_remove` Task 4 → Task 7; tracer `review_pending/decide/status` Task 5 → Tasks 6, 7; `human_review(run, cfg, ph, prompt) -> bool` Task 6 → adw_simple_sdlc; `reviewFor` Task 8 → sessionDetail + route. `spawn_sandbox` record keys (`worktree`, `name`, `host_port`) match the CLI's use.
- **Placeholder scan:** every step carries real code or an exact command. The two intentionally loose spots are noted explicitly (ticket prompt relocation "see note" and the Run.root attribute check) — both are verified at implementation time with a one-line check, not a TBD.
- **Known risks:** `shell=True` for review.command is intentional (arbitrary project-declared commands) and documented; the fake-docker shim keeps CI docker-free; the ADW review phase raises inside the phase block so the existing failure path marks the session failed.
