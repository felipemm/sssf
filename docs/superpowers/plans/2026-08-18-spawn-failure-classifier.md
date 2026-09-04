# Spawn-Failure Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Annotate sandboxed spawn-deaths (runs whose container exits before the ADW writes a session) with a deterministic remediation hint, and surface recent ones in `sssf doctor`.

**Architecture:** A pure classifier module (`sssf/postmortem.py`) maps a dead container's log tail + exit code to a one-line hint. `record_never_started` (sandbox.py) appends the hint to the existing `sandbox spawn failure` error event payload. `sssf doctor` (misc.py) lists recent spawn-failure sessions with their hints. Annotate-only: nothing auto-acts on the hint.

**Tech Stack:** Python 3.11+, stdlib only (re, json, sqlite3), rich console (existing).

**Spec:** `docs/superpowers/specs/2026-08-18-spawn-failure-classifier-design.md` — the plan argues from the spec; executors read both.

## Global Constraints

- Working tree: `sssf/.worktrees/spawn-failure-classifier` (branch `feat/spawn-failure-classifier`). All commands run from there.
- Tests: `.venv/bin/pytest` (venv created with `uv venv && uv pip install -e . pytest ruff mypy`).
- Code style: ruff + mypy clean (repo CI enforces both). Run `.venv/bin/ruff check src tests && .venv/bin/mypy src/sssf/postmortem.py src/sssf/sandbox.py src/sssf/commands/misc.py` before any commit.
- The classifier is PURE: no docker, no git, no io. Only `classify_failure(log_tail: str, exit_code: str = "") -> str | None`.
- Hint strings are single-line, ≤ 300 chars.
- No behavior change for runs that start normally (the `record_never_started` guard is untouched).

---

### Task 1: Pure classifier module

**Files:**
- Create: `src/sssf/postmortem.py`
- Test: `tests/test_postmortem.py`

**Interfaces:**
- Produces: `classify_failure(log_tail: str, exit_code: str = "") -> str | None` — the one public function Tasks 2 and 3 consume.

- [ ] **Step 1: Write the failing test**

Create `tests/test_postmortem.py`:

```python
"""Table tests for the spawn-death classifier."""

from sssf.postmortem import classify_failure


def test_missing_entry_file_hints_layout():
    tail = ("python: can't open file '/work/adws/modules/adw_simple_sdlc.py':"
            " [Errno 2] No such file or directory")
    hint = classify_failure(tail, "2")
    assert "not in the worktree" in hint
    assert "git add -A && git commit" in hint
    assert "/work/adws/modules/adw_simple_sdlc.py" in hint


def test_no_such_file_naming_adws_hints_layout():
    tail = "python: can't open file '/work/adws/config/sssf.config.yaml': No such file or directory"
    assert "not in the worktree" in classify_failure(tail, "2")


def test_import_error_hints_stale_image():
    tail = ("ImportError: cannot import name 'paths' from 'sssf.adw_modules'"
            " (/usr/local/lib/python3.11/site-packages/sssf/adw_modules/__init__.py)")
    assert "sssf sandbox build" in classify_failure(tail, "1")


def test_import_error_is_case_insensitive():
    tail = "modulenotfounderror: no module named 'sssf.adw_modules.paths'"
    assert "sssf sandbox build" in classify_failure(tail, "1")


def test_exit_127_hints_missing_binary():
    tail = "bun: command not found"
    hint = classify_failure(tail, "127")
    assert "missing from the runner image" in hint


def test_executable_not_found_hints_missing_binary():
    tail = 'exec: "snyk": executable file not found in $PATH'
    assert "missing from the runner image" in classify_failure(tail, "1")


def test_127_with_specific_signature_prefers_signature():
    # The entry-file error exits 2, but if a tail BOTH names adws/ and
    # carries a 127, the layout signature wins — specific before generic.
    tail = "can't open file '/work/adws/modules/x.py': No such file or directory"
    assert "not in the worktree" in classify_failure(tail, "127")


def test_unknown_tail_passes_through():
    tail = "some mysterious failure line"
    assert classify_failure(tail, "1") == tail


def test_unknown_tail_is_trimmed_to_300():
    tail = "x" * 500
    assert len(classify_failure(tail, "1")) <= 300


def test_no_evidence_returns_none():
    assert classify_failure("", "") is None
    assert classify_failure("   ", "   ") is None


def test_empty_tail_with_exit_code():
    hint = classify_failure("", "137")
    assert "137" in hint
    assert "no output" in hint
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_postmortem.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sssf.postmortem'`

- [ ] **Step 3: Write minimal implementation**

Create `src/sssf/postmortem.py`:

```python
"""Spawn-death classification: turn a dead container's evidence into a
remediation hint. Pure functions only — no docker, no git, no io."""

from __future__ import annotations

import re


def classify_failure(log_tail: str, exit_code: str = "") -> str | None:
    """A remediation hint for a spawn-death, or None when there is zero
    evidence. Specific signatures first; unknown evidence passes through as
    its own hint (the tail IS the message)."""
    tail = (log_tail or "").strip()
    code = (exit_code or "").strip()
    if not tail and not code:
        return None
    low = tail.lower()
    if "can't open file" in low and "adws/" in low:
        return _layout_hint(_quoted_path(tail))
    if "no such file or directory" in low and "adws/" in low:
        return _layout_hint(_quoted_path(tail))
    if ("importerror" in low or "modulenotfounderror" in low) and "sssf.adw_modules" in low:
        return "runner image is stale or broken — rebuild it: `sssf sandbox build`"
    if "executable file not found" in low:
        return "a required binary is missing from the runner image — rebuild it or fix docker/sssf-runner.Dockerfile"
    if code == "127" and tail:
        return "a required binary is missing from the runner image (exit 127) — rebuild it or fix docker/sssf-runner.Dockerfile"
    if tail:
        return tail[:300]
    return f"container exited (exit {code}) with no output — inspect the image entrypoint (docker/entrypoint.sh) and the spawned command"


def _quoted_path(tail: str) -> str:
    m = re.search(r"'([^']+)'", tail)
    return m.group(1) if m else "the entry file"


def _layout_hint(path: str) -> str:
    return (
        f"{path} is not in the worktree — the project layout is not committed;"
        " commit it (`git add -A && git commit`) or re-run `sssf init`"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_postmortem.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/sssf/postmortem.py tests/test_postmortem.py
git commit -m "feat(postmortem): pure spawn-death classifier — evidence to remediation hint"
```

---

### Task 2: Annotate the recorded spawn-death event

**Files:**
- Modify: `src/sssf/sandbox.py` (`record_never_started`, the `tracer.event(...)` block around line 507)
- Test: `tests/test_sandbox_docker.py` (extend `test_record_never_started_leaves_evidence`; add one null-remediation case)

**Interfaces:**
- Consumes: `from sssf.postmortem import classify_failure` (Task 1)
- Produces: the `sandbox spawn failure` event payload gains a `remediation` key (`str | None`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_sandbox_docker.py`, after the existing `assert "No such file or directory" in ev[1]` line inside `test_record_never_started_leaves_evidence`, add:

```python
    import json

    payload = json.loads(ev[1])
    assert "remediation" in payload
    assert "not in the worktree" in payload["remediation"]
```

Then add a new test after `test_record_never_started_leaves_evidence`:

```python
def test_record_never_started_unmatched_evidence_has_null_remediation(monkeypatch, tmp_path):
    """Evidence with no known signature still records the failure — the
    remediation key is present and null (the tail itself is the message)."""
    import json

    from sssf.adw_modules.tracer import Tracer

    db = tmp_path / "proj" / "adws" / "data" / "sssf.db"
    tracer = Tracer(db, tmp_path / "proj" / "adws" / "data" / "sessions" / "abc123" / "events.jsonl")

    def fake_docker(*args, **kwargs):
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, stdout="3\n", stderr="")
        if args[0] == "logs":
            return subprocess.CompletedProcess(args, 0, stdout="mystery\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox, "_docker", fake_docker)
    per_run = tmp_path / "proj" / ".worktrees" / "abc123" / "adws" / "data" / "sssf.db"
    sandbox.record_never_started(tmp_path / "proj", "abc123", tracer, per_run)

    ev = tracer.conn.execute(
        "SELECT payload_json FROM events WHERE adw_id='abc123'"
    ).fetchone()
    payload = json.loads(ev[0])
    assert payload["remediation"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sandbox_docker.py -q`
Expected: FAIL — `KeyError: 'remediation'` (payload has no such key yet)

- [ ] **Step 3: Write minimal implementation**

In `src/sssf/sandbox.py`, `record_never_started` — add the import next to the other local imports (inside the function, after the docker-evidence capture):

```python
    from sssf.postmortem import classify_failure
```

and change the event payload to:

```python
            payload={
                "exit_code": exit_code,
                "container_log_tail": log_tail[-2000:],
                "remediation": classify_failure(log_tail, exit_code),
            },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sandbox_docker.py -q`
Expected: 14 passed (the two existing recorder tests + the new one, plus the rest)

- [ ] **Step 5: Commit**

```bash
git add src/sssf/sandbox.py tests/test_sandbox_docker.py
git commit -m "feat(sandbox): annotate spawn-death evidence with a remediation hint"
```

---

### Task 3: Surface recent spawn failures in `sssf doctor`

**Files:**
- Modify: `src/sssf/commands/misc.py` (add `_recent_spawn_failures` helper; extend `doctor()`)
- Test: `tests/test_misc.py`

**Interfaces:**
- Consumes: `classify_failure`'s output as stored in the event payload (Task 2); `paths.data_dir` from `sssf.adw_modules.paths`.
- Produces: `_recent_spawn_failures(limit: int = 5) -> list[tuple[str, str]]` — `(adw_id, hint)` pairs, `hint` falling back to the log-tail excerpt (last 120 chars) when no hint was classified.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_misc.py`:

```python
def test_doctor_lists_recent_spawn_failures(tmp_path, monkeypatch, capsys):
    """A recorded spawn-death surfaces its remediation hint in doctor."""
    from sssf.adw_modules.tracer import Tracer

    project = tmp_path / "proj"
    (project / "adws" / "data").mkdir(parents=True)
    tracer = Tracer(
        project / "adws" / "data" / "sssf.db",
        project / "adws" / "data" / "sessions" / "abc1" / "events.jsonl",
    )
    tracer.conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES ('abc1', 'adw_simple_sdlc (never started)', 'fail',"
        " '2026-08-18T00:00:00+00:00', '2026-08-18T00:00:01+00:00')"
    )
    tracer.conn.execute(
        "INSERT INTO events (event_id, adw_id, type, name, payload_json, started_at)"
        " VALUES ('evt1', 'abc1', 'error', 'sandbox spawn failure',"
        " '{\"exit_code\": \"2\", \"remediation\": \"commit the layout\"}',"
        " '2026-08-18T00:00:00+00:00')"
    )
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.chdir(project)
    assert misc.doctor() == 0
    out = capsys.readouterr().out
    assert "recent spawn failures" in out
    assert "abc1" in out
    assert "commit the layout" in out


def test_doctor_no_spawn_failures_is_quiet(tmp_path, monkeypatch, capsys):
    """No 'recent spawn failures' section when there is nothing to report."""
    project = tmp_path / "proj"
    (project / "adws" / "data").mkdir(parents=True)
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.chdir(project)
    assert misc.doctor() == 0
    assert "recent spawn failures" not in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_misc.py -q`
Expected: `test_doctor_lists_recent_spawn_failures` FAILS (no section printed); `test_doctor_no_spawn_failures_is_quiet` PASSES (nothing new printed).

- [ ] **Step 3: Write minimal implementation**

In `src/sssf/commands/misc.py`, add before `doctor()`:

```python
def _recent_spawn_failures(limit: int = 5) -> list[tuple[str, str]]:
    """[(adw_id, hint)] from the current project's db — read-only."""
    import json
    import sqlite3

    from sssf.adw_modules import paths

    root = Path.cwd()
    db = paths.data_dir(root) / "sssf.db"
    if not (root / "adws").exists() or not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        rows = conn.execute(
            "SELECT adw_id FROM sessions"
            " WHERE adw_name='adw_simple_sdlc (never started)'"
            " ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[tuple[str, str]] = []
        for (adw_id,) in rows:
            ev = conn.execute(
                "SELECT payload_json FROM events WHERE adw_id=?"
                " AND name='sandbox spawn failure'"
                " ORDER BY started_at DESC LIMIT 1",
                (adw_id,),
            ).fetchone()
            hint = ""
            if ev:
                payload = json.loads(ev[0])
                hint = payload.get("remediation") or (
                    payload.get("container_log_tail") or ""
                )[-120:]
            out.append((adw_id, hint))
        conn.close()
        return out
    except (sqlite3.Error, ValueError):
        return []
```

In `doctor()`, after the `~/.local/bin on PATH` check and before `return 0 if ok else 1`:

```python
    failures = _recent_spawn_failures()
    if failures:
        console.print("\n[yellow]recent spawn failures[/yellow]")
        for adw_id, hint in failures:
            console.print(f"  {adw_id}  {hint or '(no hint classified)'}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_misc.py tests/test_postmortem.py tests/test_sandbox_docker.py -q`
Expected: all pass. Then run the full suite + lint + typecheck:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src/sssf/postmortem.py src/sssf/sandbox.py src/sssf/commands/misc.py
```

- [ ] **Step 5: Commit**

```bash
git add src/sssf/commands/misc.py tests/test_misc.py
git commit -m "feat(doctor): list recent spawn failures with remediation hints"
```

---

## Self-Review Notes

- **Spec coverage:** §1 (pure classifier) → Task 1; §2 (wiring) → Task 2; §3 (doctor) → Task 3; testing section → the test steps in each task; success criteria → covered by the assertions in Tasks 1–3.
- **Placeholder scan:** every step carries real code; no TBD/TODO.
- **Type consistency:** `classify_failure(log_tail: str, exit_code: str = "") -> str | None` is defined once (Task 1) and consumed identically in Tasks 2 and 3; `_recent_spawn_failures` returns `list[tuple[str, str]]` matching its use in `doctor()`.
- **Out of scope preserved:** no healer changes, no auto-remediation, no quality-gate changes — consistent with the spec.
