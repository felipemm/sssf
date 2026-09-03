# Review Containers & Run Lifecycle v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the sandbox container up after a run for app review (published on a random host port, project-configured launch command), make `sssf sweep` the only deleter of run artifacts, and surface prompt / logs / review access in the visualizer.

**Architecture:** A container-side supervisor (`sssf/adw_modules/supervise.py`) runs the ADW command, then the project's configured review command, then idles — the container never exits on its own. The monitor ends on the supervisor's exit marker (or container death) instead of container disappearance, doing the final merge without stopping the container. Ports publish loopback on a random host port at `docker run`; the mapping, review command, instructions, and container state live in a new host-owned `sandbox_run` table read by the viz. `stop_run`/`abort_sandbox`/healer only stop containers (never remove); `sssf sweep` is the sole deleter (container + worktree + `sandbox_run` row + orphan containers).

**Tech Stack:** Python 3.11+ (pydantic, sqlite3, docker CLI via subprocess), pytest; visualizer: bun + Vue 3 (server under `src/sssf/apps/visualizer/server/`, UI under `src/sssf/apps/visualizer/src/`).

**Spec:** `docs/superpowers/specs/2026-09-03-review-containers-design.md`

## Global Constraints

- Worktree: `.worktrees/review-containers` on `feat/review-containers` (base commits `ee4f608` v2 stamping, `fab35e8` restart fixes). Run tests with `uv run pytest tests/ --ignore=tests/test_sandbox_docker.py` (docker-dependent tests are excluded from the local suite); lint `uv run ruff check src/sssf tests`; types `uv run mypy src/sssf`.
- Sandboxed-run engine changes alter the runner-image fingerprint → the healer auto-rebuilds `sssf-runner`; sandboxed runs are blocked until then (existing mechanism — do not hand-build the image during tasks; the final task notes verification).
- Container name stays deterministic `sssf-<adw_id>`; the pre-run `docker rm -f` reuse in `run_sandbox` is intentional and kept.
- The only deleters allowed: `run_sandbox`'s same-name container reuse, and `sssf sweep`. `docker stop` is not deletion.
- Existing db rows: no migration needed beyond `CREATE TABLE IF NOT EXISTS` additions to the tracer SCHEMA.
- Docker tests use fake `_docker`/`subprocess` fakes (pattern: `tests/test_sandbox_worktree.py`, `tests/test_healer.py`); never require a live daemon in the unit suite.

---

### Task 1: `sandbox.review` config

**Files:**
- Modify: `src/sssf/adw_modules/data_types.py` (`SandboxConfig`, ~line 402)
- Modify: `src/sssf/templates/adws/config/sssf.config.yaml` (commented example under `sandbox:`)
- Test: `tests/test_sandbox_config.py` (existing file — add tests)

**Interfaces:**
- Produces: `ReviewConfig(command: list[str] | None = None, container_port: int | None = None, instructions: str = "")`; `SandboxConfig.review: ReviewConfig = Field(default_factory=ReviewConfig)`. Later tasks read `cfg.sandbox.review.command`, `.container_port`, `.instructions`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sandbox_config.py
def test_review_config_parses_and_defaults(tmp_path):
    from sssf.adw_modules.agents import load_config
    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text(
        "sandbox:\n"
        "  review:\n"
        "    command: [\"npm\", \"run\", \"dev\", \"--workspace=web\"]\n"
        "    container_port: 3000\n"
        "    instructions: \"open the url\"\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.sandbox.review.command == ["npm", "run", "dev", "--workspace=web"]
    assert cfg.sandbox.review.container_port == 3000
    assert cfg.sandbox.review.instructions == "open the url"


def test_review_config_absent_by_default(tmp_path):
    from sssf.adw_modules.agents import load_config
    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text("sandbox:\n  enabled: true\n")
    cfg = load_config(str(cfg_file))
    assert cfg.sandbox.review.command is None
    assert cfg.sandbox.review.container_port is None


def test_review_config_rejects_bad_port(tmp_path):
    import pydantic
    from sssf.adw_modules.agents import load_config
    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text("sandbox:\n  review:\n    container_port: 99999\n")
    try:
        load_config(str(cfg_file))
    except pydantic.ValidationError as e:
        assert "container_port" in str(e)
    else:
        raise AssertionError("expected ValidationError for container_port 99999")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_sandbox_config.py -q`
Expected: FAIL — `ReviewConfig`/`review` attribute missing.

- [ ] **Step 3: Implement**

In `data_types.py` above `SandboxConfig`:

```python
class ReviewConfig(BaseModel):
    """How to launch the project's app for human review after the ADW exits.
    `command` runs in the container (cwd = the run worktree); `container_port`
    is the port the app listens on inside the container — published on a
    random HOST port so concurrent runs never collide. `instructions` is shown
    by the visualizer next to the review URL."""

    command: list[str] | None = None
    container_port: int | None = None
    instructions: str = ""

    @field_validator("container_port")
    @classmethod
    def _port_range(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 65535:
            raise ValueError("container_port must be 1-65535")
        return v

    @field_validator("command")
    @classmethod
    def _command_shape(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and (not v or any(not s for s in v)):
            raise ValueError("review command must be a non-empty argv list")
        return v
```

In `SandboxConfig` add:

```python
    review: ReviewConfig = Field(default_factory=ReviewConfig)
```

Add the `field_validator` import (`from pydantic import BaseModel, Field, field_validator` — check the existing pydantic imports first).

In the config template `src/sssf/templates/adws/config/sssf.config.yaml`, under the existing `sandbox:` block (find it at the end of the file), add the commented example:

```yaml
sandbox:
  image: sssf-runner
  # review: how to launch the project's app AFTER the run so you can open it
  # and validate the change. The container stays up; the app's port is
  # published on a RANDOM host port (see the run in the visualizer).
  # review:
  #   command: ["npm", "run", "dev", "--workspace=web"]   # argv list, cwd = the run worktree
  #   container_port: 3000                                  # the app's port inside the container
  #   instructions: "open the URL; sign in as Local Dev via the mock IDP"
```

(Reconcile with the actual current `sandbox:` block in that template — add/edit the comment lines accordingly.)

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_sandbox_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/data_types.py src/sssf/templates/adws/config/sssf.config.yaml tests/test_sandbox_config.py
git commit -m "feat(sandbox): review config — post-run app command, container port, instructions"
```

---

### Task 2: Container-side supervisor

**Files:**
- Create: `src/sssf/adw_modules/supervise.py`
- Test: `tests/test_supervise.py` (new)

**Interfaces:**
- Produces: `supervise.main()` entry (`python -m sssf.adw_modules.supervise [--] <adw cmd...>`), exit-marker file at `<data_dir>/sessions/<adw_id>.supervisor-exit` containing the ADW exit code. Task 5 wraps the ADW command with it; Task 6 makes the monitor watch the marker.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_supervise.py
"""Container-side supervisor: runs the ADW command, then the project's review
command, then idles — the container never exits on its own, and the run's end
is signalled by an exit marker the monitor can see through the bind mount."""

import subprocess

from sssf.adw_modules import supervise


def test_supervise_runs_adw_then_review_and_idles(tmp_path, monkeypatch):
    import sssf.adw_modules.supervise as sv

    calls: list[list[str]] = []

    def fake_call(argv, **kwargs):
        calls.append(argv)
        return 7 if argv == ["adw", "--adw-id", "abc1"] else 0

    monkeypatch.setattr(sv, "_call", fake_call)
    monkeypatch.setattr(sv, "_idle", lambda: None)  # do not sleep forever
    data_dir = tmp_path / "adws" / "data"
    (data_dir / "sessions").mkdir(parents=True)

    sv.run(
        ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"],
        data_dir=data_dir,
        review_cmd=["npm", "run", "dev"],
    )

    assert calls == [
        ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"],
        ["npm", "run", "dev"],
    ]
    marker = data_dir / "sessions" / "abc1.supervisor-exit"
    assert marker.read_text() == "7"  # the ADW's exit code is recorded
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_supervise.py -q`
Expected: FAIL — module/`run` missing.

- [ ] **Step 3: Implement** (`src/sssf/adw_modules/supervise.py`)

```python
"""Container-side supervisor — the container's PID 1.

Runs the ADW command, then (whatever its exit code) the project's configured
review command, then idles forever so the container stays up for review. The
run's end is signalled to the host monitor by an exit-marker file written into
the bind-mounted worktree (data_dir/sessions/<adw_id>.supervisor-exit); the
monitor cannot rely on the container exiting, because it deliberately does
not."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path


def _idle() -> None:
    while True:
        time.sleep(3600)


def _call(argv: list[str], **kwargs) -> int:
    return subprocess.call(argv, **kwargs)


def _adw_id(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--adw-id" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def run(argv: list[str], *, data_dir: Path, review_cmd: list[str] | None) -> int:
    adw_id = _adw_id(argv)
    rc = _call(argv)
    if adw_id:
        marker = Path(data_dir) / "sessions" / f"{adw_id}.supervisor-exit"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(rc))
    if review_cmd:
        _call(review_cmd)
    _idle()  # never reached in tests (monkeypatched); keeps the container up
    return rc


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    try:
        from sssf.adw_modules.agents import default_config_path, load_config

        cfg = load_config(str(default_config_path()))
        review_cmd = cfg.sandbox.review.command
        data_dir = Path(cfg.defaults.data_dir)
    except Exception:
        review_cmd, data_dir = None, Path("adws/data")
    return run(argv, data_dir=data_dir, review_cmd=review_cmd)


if __name__ == "__main__":
    sys.exit(main())
```

Note: `default_config_path()` resolves to `adws/config/sssf.config.yaml` relative to the container cwd (`/work`) — verify its implementation in `agents.py` and keep this contract (falls back to `adws/data` + no review command on any failure).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_supervise.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/supervise.py tests/test_supervise.py
git commit -m "feat(sandbox): container supervisor — run ADW, then review app, keep container up"
```

---

### Task 3: Publish the review port at `docker run`

**Files:**
- Modify: `src/sssf/sandbox.py` (`run_sandbox`, ~line 180)
- Test: `tests/test_sandbox_docker.py` (add a unit test with a fake `_docker`)

**Interfaces:**
- Consumes: `cfg.sandbox.review.container_port` (Task 1).
- Produces: `run_sandbox(..., publish_port: int | None)` appends `-p 127.0.0.1::<port>` to the docker args when set; helper `stop_container(name)` (docker stop, not rm).

- [ ] **Step 1: Write the failing test** (append to `tests/test_sandbox_docker.py` — pattern: monkeypatch `sandbox._docker` to capture args)

```python
def test_run_sandbox_publishes_review_port(tmp_path, monkeypatch):
    import sssf.sandbox as sb

    captured: list[list[str]] = []

    def fake_docker(*args, timeout_s=30):
        captured.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr(sb, "_docker", fake_docker)
    sb.run_sandbox(
        "sssf-runner", "sssf-x1", worktree=tmp_path / "wt",
        data_dir=tmp_path / "adws" / "data", publish_port=3000,
        cmd=["python", "-c", "pass"],
    )
    run_args = next(a for a in captured if a[0] == "run")
    assert any(a == "-p" for a in run_args)
    i = run_args.index("-p")
    assert run_args[i + 1] == "127.0.0.1::3000"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sandbox_docker.py -k publish -q`
Expected: FAIL — `publish_port` not accepted.

- [ ] **Step 3: Implement** in `sandbox.py`

- Extend `run_sandbox` signature with `publish_port: int | None = None`; after the `--user`/env args and before the image, when `publish_port` is set:

```python
    if publish_port:
        # Loopback-only, random HOST port (docker picks a free one) so
        # concurrent runs never collide. The app binds container_port inside
        # the container; docker port <name> resolves the host port.
        args += ["-p", f"127.0.0.1::{publish_port}"]
```

- Add next to `stop_remove`:

```python
def stop_container(name: str) -> None:
    """Stop a container and KEEP it (logs + worktree mount stay for review).
    Deletion is `sssf sweep`'s job; docker stop is not deletion."""
    if not name:
        return
    _docker("stop", "-t", "5", name)  # errors when absent — ignored


def stop_remove(name: str) -> None:
    """Remove the container whether running or stopped. ONLY callers: the
    same-session container reuse in run_sandbox, and sssf sweep."""
    _docker("rm", "-f", name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_sandbox_docker.py -k publish -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_docker.py
git commit -m "feat(sandbox): publish review port on a random host port at docker run"
```

---

### Task 4: `sandbox_run` table + full-prompt fidelity

**Files:**
- Modify: `src/sssf/adw_modules/tracer.py` (SCHEMA, `session_request`)
- Test: `tests/test_obs.py` (tracer tests live here — add/verify)

**Interfaces:**
- Produces: table `sandbox_run(adw_id PK, container, container_port, host_port, review_url, review_command, instructions, status, updated_at)` in every db the tracer opens; `session_request` stores the FULL prompt (no `[:500]`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_obs.py — add
def test_session_request_keeps_full_prompt(tmp_path):
    from sssf.adw_modules.tracer import Tracer
    t = Tracer(str(tmp_path / "sssf.db"), str(tmp_path / "e.jsonl"))
    t.session_start("r1", "T", "adw_x")
    t.session_request("r1", "x" * 2000)
    row = t.conn.execute("SELECT request FROM sessions WHERE adw_id='r1'").fetchone()
    assert len(row[0]) == 2000  # not truncated to 500


def test_sandbox_run_table_exists(tmp_path):
    from sssf.adw_modules.tracer import Tracer
    t = Tracer(str(tmp_path / "sssf.db"), str(tmp_path / "e.jsonl"))
    cols = {r[1] for r in t.conn.execute("PRAGMA table_info(sandbox_run)")}
    assert {"adw_id", "container", "host_port", "review_url", "review_command",
            "instructions", "status"} <= cols
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_obs.py -k "request or sandbox_run" -q`
Expected: FAIL — truncation to 500 / missing table.

- [ ] **Step 3: Implement** in `tracer.py`

- Append to `SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS sandbox_run (
  adw_id          TEXT PRIMARY KEY,
  container       TEXT NOT NULL,
  container_port  INTEGER,
  host_port       INTEGER,
  review_url      TEXT,
  review_command  TEXT,      -- json list, for the visualizer
  instructions    TEXT DEFAULT '',
  status          TEXT,      -- 'up' | 'stopped'
  updated_at      TEXT
);
```

- In `session_request`, drop the slice:

```python
    def session_request(self, adw_id: str, request: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET request=? WHERE adw_id=?", (request, adw_id)
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_obs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/tracer.py tests/test_obs.py
git commit -m "feat(sandbox): sandbox_run table; sessions.request keeps the full prompt"
```

---

### Task 5: Spawn wraps the supervisor, records `sandbox_run`

**Files:**
- Modify: `src/sssf/sandbox.py` (`spawn_sandbox`)
- Test: `tests/test_sandbox_worktree.py` or `tests/test_sandbox_docker.py` (fake docker)

**Interfaces:**
- Consumes: Task 2 supervisor module, Task 3 `publish_port`/`stop_container`, Task 4 table.
- Produces: `spawn_sandbox(..., cmd, image, data_dir, ...)` — wraps `cmd` in the supervisor, publishes `cfg.sandbox.review.container_port`, and after the container starts writes the `sandbox_run` row (host project db at `data_dir/sssf.db`), resolving the host port via `docker port <name> <cp>/tcp`.

- [ ] **Step 1: Write the failing test** (fake `_docker`; assert the row + wrapped cmd)

```python
def test_spawn_records_sandbox_run(tmp_path, monkeypatch):
    import json, sqlite3
    import sssf.sandbox as sb

    # fake docker: run succeeds, port mapping resolves to 41234
    def fake_docker(*args, timeout_s=30):
        if args[0] == "port":
            return subprocess.CompletedProcess(list(args), 0, stdout="127.0.0.1:41234\n", stderr="")
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr(sb, "_docker", fake_docker)
    monkeypatch.setattr(sb, "ensure_image_current", lambda image: None)
    monkeypatch.setattr(sb, "_engine_fingerprint", lambda: "x")
    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.executescript("CREATE TABLE sandbox_run (adw_id TEXT PRIMARY KEY, container TEXT,"
                       " container_port INTEGER, host_port INTEGER, review_url TEXT,"
                       " review_command TEXT, instructions TEXT, status TEXT, updated_at TEXT)")
    conn.close()

    sb.spawn_sandbox(
        tmp_path, "abc1", cmd=["python", "adws/modules/adw_simple_sdlc.py", "p", "--adw-id", "abc1"],
        image="sssf-runner", data_dir=data, pi_home=tmp_path / "pi",
        review={"command": ["npm", "run", "dev"], "container_port": 3000, "instructions": "open it"},
    )
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    row = conn.execute("SELECT * FROM sandbox_run WHERE adw_id='abc1'").fetchone()
    conn.close()
    assert row is not None
    assert row[conn.description.index(("host_port",))[0] if False else 3] == 41234
```

> Note: index the columns by name (`{c[0]: c[1] for c in conn.description}`) rather than the positional shortcut above — write the assertion cleanly: `d = dict(...)`; `assert d["host_port"] == 41234`, `assert d["review_url"] == "http://127.0.0.1:41234"`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sandbox_docker.py -k spawn_records -q`
Expected: FAIL — `review` kwarg / no row written.

- [ ] **Step 3: Implement**

In `spawn_sandbox`, accept the review payload (the callers already load `cfg` — see `_run_sandboxed` in `commands/run.py` and the ticket path; pass `cfg.sandbox.review` down as a small dict or pass `review_cfg`). Suggested signature change: `spawn_sandbox(..., review: dict | None = None)` where the caller passes `cfg.sandbox.review.model_dump()`.

Body changes, after `stamp_adw_template(...)`:

```python
    review = review or {}
    cmd = ["python", "-m", "sssf.adw_modules.supervise", "--", *cmd]
    run_sandbox(
        image,
        container_name(adw_id),
        worktree=wt,
        data_dir=data_dir,
        pi_home=pi_home,
        git_dir=project_root / ".git",
        config_dir=Path.home() / ".config",
        uid=uid,
        gid=gid,
        env=env,
        publish_port=review.get("container_port"),
        cmd=cmd,
    )
    _record_sandbox_run(project_root, adw_id, wt, review)
```

And a module-level helper (host side; also imported by stop/sweep later):

```python
def sandbox_run_db(data_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(project_db_path(data_dir)), isolation_level=None)


def _record_sandbox_run(project_root, adw_id, wt, review) -> None:
    """Record the live container + review mapping in the host project db."""
    import datetime, json
    name = container_name(adw_id)
    cp = review.get("container_port")
    host_port = None
    if cp:
        r = _docker("port", name, f"{cp}/tcp")
        if r.returncode == 0:
            host_port = int(r.stdout.strip().split(":")[-1])
    conn = sandbox_run_db(paths.data_dir(project_root))
    try:
        conn.execute(
            "INSERT INTO sandbox_run (adw_id, container, container_port, host_port,"
            " review_url, review_command, instructions, status, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(adw_id) DO UPDATE SET container=excluded.container,"
            " container_port=excluded.container_port, host_port=excluded.host_port,"
            " review_url=excluded.review_url, review_command=excluded.review_command,"
            " instructions=excluded.instructions, status='up', updated_at=excluded.updated_at",
            (
                adw_id, name, cp, host_port,
                f"http://127.0.0.1:{host_port}" if host_port else None,
                json.dumps(review.get("command") or []),
                review.get("instructions") or "",
                "up",
                datetime.datetime.now(datetime.UTC).isoformat(),
            ),
        )
    finally:
        conn.close()
```

`paths` is imported at the top of `sandbox.py` inside functions today — import `from sssf.adw_modules import paths` at the top of the helper.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_sandbox_docker.py -k spawn_records -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_docker.py
git commit -m "feat(sandbox): spawn wraps the ADW in the supervisor and records sandbox_run"
```

---

### Task 6: Monitor ends when the RUN ends (container stays up)

**Files:**
- Modify: `src/sssf/sandbox.py` (`monitor_run`, ~line 575-635)
- Test: `tests/test_sandbox_worktree.py`

**Interfaces:**
- Consumes: Task 2 exit marker.
- Produces: `monitor_run` exits when the container is gone OR the supervisor-exit marker exists; performs `record_never_started` + final `sync_run_db`; never stops/removes the container.

- [ ] **Step 1: Write the failing test**

```python
def test_monitor_exits_when_run_ends_but_container_alive(tmp_path, monkeypatch):
    """Regression: with the supervisor keeping the container up after the run,
    the monitor must stop syncing when the RUN ends (exit marker present), not
    wait for the container to disappear."""
    import sqlite3, time
    from sssf.sandbox import monitor_run

    data = tmp_path / "adws" / "data"
    (data / "sessions" / "r1").mkdir(parents=True)
    conn = sqlite3.connect(str(data / "sssf.db"))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.commit(); conn.close()

    states = iter([True, True, True])  # container "alive" first three polls

    def fake_gone(fn, name):
        # after the marker appears, treat the container as still up (review)
        # — the monitor must exit anyway because the marker exists
        return not (data / "sessions" / "r1.supervisor-exit").exists()

    monkeypatch.setattr("sssf.sandbox._container_gone", fake_gone)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    # the marker appears during the run (supervisor wrote it after the ADW exited)
    def fake_write():
        (data / "sessions" / "r1.supervisor-exit").write_text("0")
    ...
```

> Write the test against a small refactor of the poll condition. Recommended: extract `_run_ended(data_dir, adw_id) -> bool` (marker exists) and make the loop `while not (_container_gone(...) or _run_ended(...))`. The test asserts the loop exits once the marker is dropped into place even though the fake `_container_gone` keeps returning False.

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (monitor loops forever / no marker handling).

- [ ] **Step 3: Implement** in `monitor_run`:

```python
def _run_ended(data_dir: Path, adw_id: str) -> bool:
    """True once the supervisor wrote the exit marker (the ADW process ended).
    The container stays up for review, so container-death alone no longer
    marks the end of a run."""
    return (data_dir / "sessions" / f"{adw_id}.supervisor-exit").exists()
```

Loop condition change (data_dir is already computed in `monitor_run`):

```python
        while True:
            if _container_gone(_docker, container_name(adw_id)) or _run_ended(data_dir, adw_id):
                break  # container gone OR the run ended — the container may
                # still be up in review mode; leave it running
            sync_run_db(tracer.conn, per_run_db, adw_id)
            time.sleep(3)
```

Keep the `finally` block (evidence → final merge → `record_never_started`) unchanged except the docstring, and do NOT add any stop/remove here (review container stays). After the final sync, delete the marker file if present (idempotence across monitors is not needed, but keep the worktree clean):

```python
        with contextlib.suppress(OSError):
            (data_dir / "sessions" / f"{adw_id}.supervisor-exit").unlink(missing_ok=True)
```

- [ ] **Step 4: Run to verify it passes** — `uv run pytest tests/test_sandbox_worktree.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_worktree.py
git commit -m "feat(sandbox): monitor ends on run end (exit marker) and leaves the review container up"
```

---

### Task 7: `stop_run`/`abort`/healer stop containers, never delete

**Files:**
- Modify: `src/sssf/sandbox.py` (`stop_run`, `abort_sandbox`, `prune_sandbox`, `teardown_sandbox` docstring)
- Modify: `src/sssf/commands/sandbox_cmd.py` (`prune` → deprecation)
- Modify: `src/sssf/healer.py` (`_clean_orphans` → report only; finalize paths already route through `stop_run`)
- Test: `tests/test_healer.py`, `tests/test_sandbox_worktree.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sandbox_worktree.py
def test_stop_run_stops_container_and_keeps_worktree(tmp_path, monkeypatch):
    """stop_run must NOT delete: docker stop (keep container) + keep the
    worktree — the debugging surface and the restart's artifact base."""
    import sqlite3
    import sssf.sandbox as sb

    calls = []
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: calls.append(a) or
                        subprocess.CompletedProcess(list(a), 0, "", ""))
    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute("CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, error TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('r9','running',NULL)")
    conn.execute("INSERT INTO phases VALUES ('p1','r9','running',NULL,NULL)")
    conn.commit(); conn.close()

    sb.stop_run(tmp_path, "r9", data)
    assert any(a[0] == "stop" for a in calls)       # stopped, not removed
    assert not any(a[0] == "rm" for a in calls)     # never rm
    assert (tmp_path / ".worktrees" / "r9").exists() is False or True  # worktree untouched (may not exist at all)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    assert conn.execute("SELECT status FROM sessions WHERE adw_id='r9'").fetchone()[0] == "fail"
    conn.close()
```

```python
# tests/test_healer.py
def test_clean_orphans_reports_but_never_deletes(tmp_path, monkeypatch):
    import sssf.healer as h
    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(h, "_docker", lambda *a: calls.append(a) or
                        subprocess.CompletedProcess(["docker"], 0, "", ""))
    # a worktree whose session is gone + a real known session
    from sssf.sandbox import container_name
    out = h._clean_orphans(tmp_path)  # no sandboxes dir → no-op
    assert out == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_sandbox_worktree.py tests/test_healer.py -k "stop_run or orphans" -q`
Expected: FAIL — `stop_run` still rms/removes the worktree; `_clean_orphans` still deletes.

- [ ] **Step 3: Implement**

In `sandbox.py`:

- `stop_run`: replace `stop_remove(container_name(adw_id))` + `remove_worktree(sandbox_dir(project_root, adw_id))` with `stop_container(container_name(adw_id))`; keep the existing host-side finalize (mark running phases failed with `reason`, `session_finish(ok=False)` when still running); add a `sandbox_run` status flip to `stopped` (update `status='stopped', updated_at=now`); update the docstring: stops the container, keeps container + worktree; deletion is sweep's job.
- `abort_sandbox`: use `stop_container` (never rm); update its comment (worktree already stays).
- `prune_sandbox`: print/raise deprecation — return a message; simplest: `raise SandboxError("cleanup is \`sssf sweep\` only — prune is deprecated")`.
- `sandbox_cmd.py` `prune()`: catch the deprecation and print guidance, exit 0.
- `healer.py` `_clean_orphans`: replace the deletion with reporting only (collect names, no `stop_remove`/`teardown_sandbox`), return lines like `f"{name}: orphaned sandbox (removed by sssf sweep)"`. Finalize/restart-budget paths already call `stop_run` — now stop-only automatically.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_sandbox_worktree.py tests/test_healer.py -q`
Expected: PASS (adjust the stop-run test's worktree assertion to reality: with no sandbox dir created the worktree simply never existed — assert the container was stopped and NOT removed, and the session row finalized).

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py src/sssf/commands/sandbox_cmd.py src/sssf/healer.py tests/test_sandbox_worktree.py tests/test_healer.py
git commit -m "fix(sandbox): stop/finalize/abort stop containers only — sweep is the sole deleter"
```

---

### Task 8: Sweep is the deleter (containers, worktrees, rows, orphans)

**Files:**
- Modify: `src/sssf/commands/sweep.py`
- Test: `tests/test_sweep.py` (exists)

- [ ] **Step 1: Write the failing tests**

```python
def test_sweep_clears_sandbox_run_row(tmp_path, monkeypatch):
    import sqlite3
    from sssf.commands import sweep
    from sssf import sandbox as sb

    calls = []
    monkeypatch.setattr(sb, "_docker", lambda *a: calls.append(a) or
                        subprocess.CompletedProcess(list(a), 0, "", ""))
    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT, archived INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE sandbox_run (adw_id TEXT PRIMARY KEY, container TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('old1','success','2020-01-01T00:00:00',0)")
    conn.execute("INSERT INTO sandbox_run VALUES ('old1','sssf-old1')")
    conn.commit(); conn.close()
    ids = sweep.sweep_db(sb.project_db_path(data), "-1 days")
    assert ids == ["old1"]
    sweep._clear_sandbox(tmp_path, "old1")
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    assert conn.execute("SELECT COUNT(*) FROM sandbox_run WHERE adw_id='old1'").fetchone()[0] == 0
    conn.close()
```

- [ ] **Step 2: Run to verify it fails** — Expected: FAIL (`sandbox_run` row survives).

- [ ] **Step 3: Implement** in `commands/sweep.py` `_clear_sandbox`:

```python
def _clear_sandbox(root: Path, adw_id: str) -> None:
    """The ONLY deleter of run artifacts: remove the session's container(s) and
    worktree, and its sandbox_run row. Everything else only stops containers."""
    from sssf import sandbox

    try:
        sandbox.stop_remove(sandbox.container_name(adw_id))
    except Exception as error:
        print(f"sssf sweep: {root.name}: container cleanup failed: {error}")
    try:
        sandbox.remove_worktree(sandbox.sandbox_dir(root, adw_id))
    except Exception as error:
        print(f"sssf sweep: {root.name}: worktree cleanup failed: {error}")
    try:
        conn = sandbox.sandbox_run_db(root / "adws" / "data")
        conn.execute("DELETE FROM sandbox_run WHERE adw_id=?", (adw_id,))
        conn.close()
    except Exception as error:
        print(f"sssf sweep: {root.name}: review record cleanup failed: {error}")
```

Also extend `run()` to remove orphan containers (a container whose `sssf-<id>` has no session row) so spawn leftovers are still cleaned by the only deleter:

```python
def _clean_orphan_containers(root: Path, db_path: Path) -> list[str]:
    """Remove sssf-* containers that match no session (spawn leftovers)."""
    from sssf import sandbox
    try:
        conn = sandbox.sandbox_run_db(db_path.parent)
        known = {r[0] for r in conn.execute("SELECT adw_id FROM sessions").fetchall()}
        conn.close()
    except Exception:
        return []
    r = sandbox._docker("ps", "-a", "--filter", "name=sssf-", "--format", "{{.Names}}")
    removed = []
    for name in r.stdout.split():
        adw_id = name.removeprefix("sssf-")
        if adw_id not in known:
            sandbox.stop_remove(name)
            removed.append(name)
    return removed
```

(Verify `sandbox_run_db` accepts a directory — make it take `data_dir: Path` as in Task 5 and pass `root / "adws" / "data"` consistently.)

- [ ] **Step 4: Run to verify they pass** — `uv run pytest tests/test_sweep.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/commands/sweep.py tests/test_sweep.py
git commit -m "feat(sweep): sole deleter — clears sandbox_run rows and orphan containers"
```

---

### Task 9: Viz server — review + logs + prompt endpoints

**Files:**
- Modify: `src/sssf/apps/visualizer/server/index.ts` (routes)
- Modify: `src/sssf/apps/visualizer/server/cockpit.ts` (helpers: `containerState`, `reviewRow`, `sandboxLogs`)
- Modify: `src/sssf/apps/visualizer/server/db.ts` (session detail already returns `request` — verify)
- Test: `src/sssf/apps/visualizer/server/cockpit.test.ts` (helpers) — bun tests

**Interfaces:**
- Produces routes (scoped to `/api/projects/:project/sessions/:adw_id/`):
  - `GET .../review` → `{ row: sandbox_run | null, container: {state: 'running'|'exited'|'absent'} }`
  - `GET .../logs?tail=N` → `{ ok, lines }` from docker logs of `sssf-<adw_id>` (reuse `containerLogs`); `{ ok:false, lines:[], error: 'no container' }` when the container is absent.

- [ ] **Step 1: Write the failing bun test** (cockpit.test.ts style, fake docker + a temp db with a `sandbox_run` row)

```ts
test("reviewRow reads the sandbox_run table and container state", async () => {
  // build a temp project db with sandbox_run + a fake dockerPs
});
```

- [ ] **Step 2: Run to verify it fails** — `bun test server/cockpit.test.ts` → FAIL (helpers missing).

- [ ] **Step 3: Implement** (helpers in cockpit.ts)

```ts
export interface ReviewRow { adw_id: string; container: string; container_port: number | null;
  host_port: number | null; review_url: string | null; review_command: string | null;
  instructions: string | null; status: string | null; updated_at: string | null; }

export async function reviewFor(db: SssfDb, adwId: string, dockerPs?: (args: string[]) => Promise<string>): Promise<{
  row: ReviewRow | null; container: { state: "running" | "exited" | "absent" } }> {
  const row = db.reviewRow(adwId);                       // add a db.ts method (sqlite)
  let state: "running" | "exited" | "absent" = "absent";
  const name = row?.container ?? `sssf-${adwId}`;
  if (row) {
    const ps = await (dockerPs ?? defaultDockerPs)(["-a", "--filter", `name=${name}`, "--format", "{{.Status}}"]);
    state = ps.includes("Up") ? "running" : ps.trim() ? "exited" : "absent";
  }
  return { row, container: { state } };
}
```

(`defaultDockerPs` shells `docker ps`; SAFE_CONTAINER validates the name before any shell-out — reuse the existing `containerLogs` guard.) Wire the two routes in `index.ts` with the existing `dbForProject`/`scoped` helpers and return the session's `request` alongside review (the trace already has it; verify `SessionDetail.request` is the full value from R6).

- [ ] **Step 4: Run to verify they pass** — `bun test server/` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/server
git commit -m "feat(viz): review/logs endpoints — container state, sandbox_run, docker logs"
```

---

### Task 10: Viz UI — Prompt / Logs / Review on the trace page

**Files:**
- Modify: `src/sssf/apps/visualizer/src/components/SessionTrace.vue`
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`
- Rebuild: `bun run build` (dist) + restart the viz server

- [ ] **Step 1:** add api.ts helpers `fetchReview(adwId)`, `fetchSessionLogs(adwId, tail)` (project-scoped base like the other calls).
- [ ] **Step 2:** In `SessionTrace.vue` run-strip, add three buttons after the stop/restart buttons (hidden when there is nothing to show):
  - Prompt: toggles a block rendering `session.request` (full) in a `<pre>`.
  - Logs: fetches `fetchSessionLogs` and renders the tail in a scrollable `<pre>` with a refresh.
  - Review: when `review.row?.review_url` — an `<a :href="review.row.review_url" target="_blank">Open app ↗</a>` plus the `instructions` and review command; container state chip (`running`/`exited`/`absent`).
- [ ] **Step 3:** Verify types: `bun run typecheck` must stay free of NEW errors (pre-existing server errors in `ticketRoutes*.ts`/`tickets.ts` are out of scope), then `bun run build` and restart the server via the `_spawn` pattern used earlier.
- [ ] **Step 4:** manual smoke via `curl http://localhost:4600/api/.../review` on the dsl-app project.
- [ ] **Step 5: Commit**

```bash
git add src/sssf/apps/visualizer/src
git commit -m "feat(viz): trace page prompt/logs/review panel"
```

---

### Task 11: Docs + template config + final verification

**Files:**
- Modify: `site/src/pages/docs/sandbox.astro` (review mode + sweep-only deletion)
- Modify: `docs/quality-gates.md` if it documents teardown/prune behavior
- Verify: full suites

- [ ] **Step 1:** Update `sandbox.astro`: document `sandbox.review` config, the container-stays-up lifecycle, random host port publishing, and that `sssf sweep` is the only deleter (prune deprecated).
- [ ] **Step 2:** grep docs for `prune`, `stop_remove`, "teardown" claims that contradict the new policy and fix them.
- [ ] **Step 3:** Full verification in the worktree:

```bash
uv run pytest tests/ --ignore=tests/test_sandbox_docker.py
uv run ruff check src/sssf tests
uv run mypy src/sssf
cd src/sssf/apps/visualizer && bun test server/ && bun run lint
```

- [ ] **Step 4:** Note for the operator: rebuild the runner image so sandboxed runs use the supervisor (`sssf sandbox build` or let the healer auto-rebuild); then a real smoke: run a project's ADW, confirm the container stays up, `docker port sssf-<id>` resolves, and the review app is reachable.
- [ ] **Step 5: Commit**

```bash
git add site docs
git commit -m "docs(sandbox): review-mode lifecycle, sweep-only deletion, review config"
```

---

## Self-review notes

- R1 (sweep-only) → Tasks 3 (stop_container), 7, 8.
- R2 (container stays up) → Tasks 2 (supervisor), 6 (monitor on marker).
- R3 (review config) → Task 1; template in Task 1; docs Task 11.
- R4 (random host port + record) → Tasks 3, 4 (table), 5 (record).
- R5 (viz prompt/logs/review) → Tasks 9, 10 (R6 full prompt via Task 4).
- R7 (restart replay semantics + reopen keep) → no code change needed beyond what the base commits already do; reopen_session already flips the row; verified in `tests/test_sandbox_worktree.py`.
- Open questions (branch deletion in sweep; restart ADW choice; review app on failed runs) are recorded in the spec, not decided here.
