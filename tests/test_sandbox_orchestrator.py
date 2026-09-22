"""Run lifecycle: spawn, monitor, stop/abort, teardown — the package's
run-lifecycle verbs exercised directly and through the CLI commands.

Mirrors src/sssf/sandbox/orchestrator.py. Docker is faked via the
conftest fake_docker PATH shim and module-attr patches at the seams the
code crosses.
"""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

import sssf.sandbox.orchestrator as orchestrator_mod
from sssf.sandbox import stop_run
from sssf.sandbox.worktree_git import create_worktree


@pytest.fixture
def repo(tmp_path):
    # A bare origin with main pushed — the sandbox contract is origin/main,
    # so the fixture mirrors a real remote.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=root, check=True)
    return root


def _make_repo(tmp_path) -> Path:
    # Bare origin + pushed main: sandbox runs fetch origin/main, so a repo
    # without a remote can no longer spawn fresh worktrees.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=root, check=True)
    return root




def _setup_project(tmp_path, monkeypatch) -> Path:
    from sssf import registry

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    (root / "adws" / "modules").mkdir(parents=True)
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "config" / "sssf.config.yaml").write_text(
        "defaults:\n  coding_agent: pi\n  model: openai/gpt-4o-mini\n"
        "sandbox:\n  enabled: false\n"
        "observability:\n  db: adws/data/sssf.db\n"
    )
    registry.register_project(root, root / "adws" / "data" / "sssf.db", "1.0.0")
    # A legacy ADW left in the project from before the chain-set reduction —
    # restart resolves it from the project file when no installed template exists.
    (root / "adws" / "modules" / "adw_build_review.py").write_text("print('legacy adw')\n")
    monkeypatch.chdir(root)
    return root




def _seed_session(root: Path, adw_id: str, adw_name: str, request: str) -> None:
    """A minimal sessions table with one row, shaped like tracer's."""

    from sssf.sandbox.rundb import project_db_path
    from sssf.sandbox.session_env import sandbox_env

    db = project_db_path(sandbox_env(root)[0])
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT,"
        " status TEXT, engineer TEXT, started_at TEXT, ended_at TEXT,"
        " total_tokens INTEGER DEFAULT 0, total_cost REAL DEFAULT 0,"
        " archived INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,'success','Felipe',"
        " '2026-09-04T11:17:48',NULL,0,0,0)",
        (adw_id, adw_name, request),
    )
    conn.commit()
    conn.close()



def test_spawn_sandbox_creates_worktree_and_records_port(tmp_path, monkeypatch, fake_docker):
    root = _make_repo(tmp_path)
    from sssf.sandbox import sandbox_dir, spawn_sandbox

    monkeypatch.setattr("sssf.sandbox.docker._engine_fingerprint", lambda: "FPFIXED")
    # the orchestration helper under test: spawn_sandbox creates the worktree
    record = spawn_sandbox(
        root,
        "abc123",
        cmd=["true"],
        image="sssf-runner",
        data_dir=root / "adws" / "adw_data",
        pi_home=tmp_path / "pi",
    )
    assert sandbox_dir(root, "abc123").is_dir()
    assert record["worktree"] == str(sandbox_dir(root, "abc123"))
    assert record["name"] == "sssf-abc123"




def test_stop_run_finalizes_stale_session(tmp_path, monkeypatch, fake_docker):
    """A stale run (no container/worktree, session stuck running) becomes
    failed on stop — so it is archivable."""

    from sssf.sandbox.rundb import project_db_path

    root = _make_repo(tmp_path)
    data = root / "adws" / "adw_data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('stale1', 'running', NULL)")
    conn.commit()
    conn.close()
    assert stop_run(root, "stale1", data) == 0
    conn = sqlite3.connect(str(project_db_path(data)))
    status = conn.execute("SELECT status FROM sessions WHERE adw_id='stale1'").fetchone()[0]
    conn.close()
    assert status == "fail"




def test_stop_run_marks_inflight_phases(tmp_path, monkeypatch, fake_docker):
    """Stop marks the running/queued phases failed, not just the session."""

    from sssf.sandbox.rundb import project_db_path

    root = _make_repo(tmp_path)
    data = root / "adws" / "adw_data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute(
        "CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('stop1', 'running', NULL)")
    conn.execute("INSERT INTO phases VALUES ('p1', 'stop1', 'success', NULL, NULL)")
    conn.execute("INSERT INTO phases VALUES ('p2', 'stop1', 'running', NULL, NULL)")
    conn.execute("INSERT INTO phases VALUES ('p3', 'stop1', 'queued', NULL, NULL)")
    conn.commit()
    conn.close()
    stop_run(root, "stop1", data)
    conn = sqlite3.connect(str(project_db_path(data)))
    rows = conn.execute("SELECT phase_id, status FROM phases WHERE adw_id='stop1'").fetchall()
    sess = conn.execute("SELECT status FROM sessions WHERE adw_id='stop1'").fetchone()[0]
    conn.close()
    assert dict(rows) == {"p1": "success", "p2": "fail", "p3": "fail"}
    assert sess == "fail"




def test_restart_reruns_the_original_adw(tmp_path, monkeypatch):
    """`sssf sandbox restart` re-runs the ADW that ORIGINALLY ran the session,
    not a hardcoded simple_sdlc. (Field case, session 36bbd3b3: a build_review
    session restarted as simple_sdlc — different roster, different chain — and
    died validating agents the original run never touched.)"""
    from sssf.commands import sandbox_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "adw_build_review", "bound the page to the viewport")

    captured: dict = {}

    def fake_run_sandboxed(root_, adw_file, args, adw_id=None, attach=False):
        captured.update(adw_file=str(adw_file.name), args=args, adw_id=adw_id, attach=attach)
        return 0

    monkeypatch.setattr("sssf.commands.flow._run_sandboxed", fake_run_sandboxed)
    assert sandbox_cmd.restart(None, "abc123") == 0
    assert captured == {
        "adw_file": "adw_build_review.py",
        "args": ["bound the page to the viewport"],
        "adw_id": "abc123",
        "attach": True,
    }




def test_restart_uses_first_name_when_adws_joined(tmp_path, monkeypatch):
    """A session joined by a second ADW records 'original + joiner' — the
    restart must still run the ORIGINAL (first) ADW."""
    from sssf.commands import sandbox_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "adw_build_review + adw_simple_sdlc", "bound the page")

    captured: dict = {}
    monkeypatch.setattr(
        "sssf.commands.flow._run_sandboxed",
        lambda root_, adw_file, args, adw_id=None, attach=False: captured.update(
            adw_file=adw_file.name
        )
        or 0,
    )
    assert sandbox_cmd.restart(None, "abc123") == 0
    assert captured["adw_file"] == "adw_build_review.py"




def test_restart_unprefixed_name_resolves(tmp_path, monkeypatch):
    """Legacy/unprefixed adw names ('build_review') resolve the same way the
    run command normalized them."""
    from sssf.commands import sandbox_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "build_review", "bound the page")

    captured: dict = {}
    monkeypatch.setattr(
        "sssf.commands.flow._run_sandboxed",
        lambda root_, adw_file, args, adw_id=None, attach=False: captured.update(
            adw_file=adw_file.name
        )
        or 0,
    )
    assert sandbox_cmd.restart(None, "abc123") == 0
    assert captured["adw_file"] == "adw_build_review.py"




def test_restart_of_vanished_adw_is_loud(tmp_path, monkeypatch, capsys):
    """No silent fallback: if the original ADW's module is gone, say so."""
    from sssf.commands import sandbox_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "adw_vanished_adw", "bound the page")
    assert sandbox_cmd.restart(None, "abc123") == 1
    assert "adw_vanished_adw" in capsys.readouterr().err




def test_restart_missing_session_is_loud(tmp_path, monkeypatch, capsys):
    from sssf.commands import sandbox_cmd

    _setup_project(tmp_path, monkeypatch)
    assert sandbox_cmd.restart(None, "nope") == 1
    assert "no session nope" in capsys.readouterr().err


def test_teardown_keeps_container_and_worktree(repo, tmp_path, monkeypatch):
    """After a run NEITHER the container nor the worktree is deleted — both
    are the debugging surface. Cleanup is explicit (sweep / sandbox prune)."""
    import sssf.sandbox as sandbox

    wt = create_worktree(repo, "keep1")
    called = []
    monkeypatch.setattr("sssf.sandbox.docker.stop_remove", lambda name: called.append(name))
    assert sandbox.teardown_sandbox(repo, "keep1") == 0
    assert called == []  # container is KEPT
    assert wt.is_dir()  # worktree survives




def test_abort_keeps_worktree_for_manual_debug(repo, tmp_path, monkeypatch):
    import sssf.sandbox as sandbox

    wt = create_worktree(repo, "abrt1")
    stopped = []
    monkeypatch.setattr("sssf.sandbox.orchestrator.stop_container", lambda name: stopped.append(name))
    sandbox.abort_sandbox(repo, "abrt1")
    assert stopped == ["sssf-abrt1"]  # stopped, never removed
    assert wt.is_dir()  # failed spawns leave the worktree too




def test_monitor_exits_when_run_ends_but_container_alive(tmp_path, monkeypatch):
    """The supervisor keeps the container up after the run (review mode), so
    the monitor must stop when the RUN ends — signalled by the supervisor-exit
    marker — not wait for the container to disappear (session 9701903a lesson:
    the run's end must not depend on container teardown)."""

    from sssf.sandbox.orchestrator import monitor_run
    from sssf.sandbox.worktree_git import sandbox_dir

    root = tmp_path / "proj"
    root.mkdir()
    data = root / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(data / "sssf.db"))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.commit()
    conn.close()
    # the container's worktree — where the per-run db and the supervisor marker live
    wt_data = sandbox_dir(root, "r6") / "adws" / "data"
    (wt_data / "sessions").mkdir(parents=True)

    monkeypatch.setattr("sssf.sandbox.orchestrator._container_gone", lambda fn, name: False)  # container stays up
    monkeypatch.setattr("sssf.sandbox.orchestrator.time.sleep", lambda s: None)  # no real waiting
    monkeypatch.setattr("sssf.sandbox.orchestrator.sync_run_db", lambda *a, **k: None)
    monkeypatch.setattr("sssf.sandbox.orchestrator.record_never_started", lambda *a, **k: None)

    # the supervisor wrote its exit marker (the ADW ended; container idles)
    (wt_data / "sessions" / "r6.supervisor-exit").write_text("0")

    assert monitor_run(root, "r6") == 0
    # cleanup: the marker is consumed; the container/worktree are untouched
    assert not (wt_data / "sessions" / "r6.supervisor-exit").exists()




def test_stop_run_stops_container_keeps_worktree_and_marks_stopped(tmp_path, monkeypatch):
    """stop_run must NOT delete: docker stop (container kept for logs/review)
    and the worktree kept (the restart's artifact base — session 9701903a's
    builder work was destroyed because stop/finalize removed the worktree).
    sandbox_run flips to stopped; session + in-flight phases finalize failed."""

    import sssf.sandbox as sb
    from sssf.sandbox.rundb import project_db_path

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "sssf.sandbox.docker._docker",
        lambda *a, timeout_s=30: calls.append(list(a))
        or subprocess.CompletedProcess(list(a), 0, "", ""),
    )
    root = tmp_path / "proj"
    data = root / "adws" / "data"
    data.mkdir(parents=True)
    wt = sb.sandbox_dir(root, "r9")
    (wt / "adws" / "data" / "sessions").mkdir(parents=True)
    conn = sqlite3.connect(str(project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute(
        "CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT,"
        " status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE sandbox_run (adw_id TEXT PRIMARY KEY, container TEXT,"
        " status TEXT, updated_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('r9','running',NULL)")
    conn.execute("INSERT INTO phases VALUES ('p1','r9','running',NULL,NULL)")
    conn.execute("INSERT INTO sandbox_run VALUES ('r9','sssf-r9','up','x')")
    conn.commit()
    conn.close()

    sb.stop_run(root, "r9", data)
    assert ["stop", "-t", "5", "sssf-r9"] in calls
    assert not any(a[0] == "rm" for a in calls)  # never deletes
    assert wt.exists()  # worktree kept — restart can reuse the artifacts
    conn = sqlite3.connect(str(project_db_path(data)))
    assert conn.execute("SELECT status FROM sessions WHERE adw_id='r9'").fetchone()[0] == "fail"
    err = conn.execute("SELECT error FROM phases WHERE adw_id='r9'").fetchone()[0]
    assert "stopped by the engineer" in err
    status = conn.execute("SELECT status FROM sandbox_run WHERE adw_id='r9'").fetchone()[0]
    conn.close()
    assert status == "stopped"




def test_abort_sandbox_stops_not_removes(tmp_path, monkeypatch):
    """A failed spawn leaves the (stuck) container stopped, never removed —
    sweep cleans it up."""
    import sssf.sandbox as sb

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "sssf.sandbox.docker._docker",
        lambda *a, timeout_s=30: calls.append(list(a))
        or subprocess.CompletedProcess(list(a), 0, "", ""),
    )
    sb.abort_sandbox(tmp_path, "abc9")
    assert calls == [["stop", "-t", "5", "sssf-abc9"]]




def test_record_never_started_leaves_evidence(monkeypatch, tmp_path):
    """A spawn-death (container exits before the ADW ever writes a session)
    records a failed session + the container log tail and flips the linked
    ticket — the monitor no longer erases the only evidence."""
    from sssf.adw_modules.tracer import Tracer

    db = tmp_path / "proj" / "adws" / "data" / "sssf.db"
    tracer = Tracer(
        db, tmp_path / "proj" / "adws" / "data" / "sessions" / "abc123" / "events.jsonl"
    )
    tracer.conn.execute(
        "INSERT INTO tickets (id, provider, title, status, adw_id) VALUES (?,?,?,?,?)",
        ("internal:x", "internal", "boom", "starting", "abc123"),
    )

    def fake_docker(*args, **kwargs):
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, stdout="1\n", stderr="")
        if args[0] == "logs":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "python: can't open file 'adws/modules/adw_simple_sdlc.py':"
                    " [Errno 2] No such file or directory\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("sssf.sandbox.orchestrator._docker", fake_docker)
    per_run = tmp_path / "proj" / ".worktrees" / "abc123" / "adws" / "data" / "sssf.db"
    orchestrator_mod.record_never_started(tmp_path / "proj", "abc123", tracer, per_run)

    row = tracer.conn.execute(
        "SELECT status, adw_name FROM sessions WHERE adw_id='abc123'"
    ).fetchone()
    assert row == ("fail", "adw_simple_sdlc (never started)")
    ev = tracer.conn.execute(
        "SELECT name, payload_json FROM events WHERE adw_id='abc123'"
    ).fetchone()
    assert ev[0] == "sandbox spawn failure"
    assert "1" in ev[1]  # exit code captured
    assert "No such file or directory" in ev[1]  # log tail captured

    payload = json.loads(ev[1])
    assert "remediation" in payload
    assert "not in the worktree" in payload["remediation"]
    status = tracer.conn.execute("SELECT status FROM tickets WHERE id='internal:x'").fetchone()[0]
    # a spawn failure requeues the ticket (fix-forward, retryable)
    assert status == "ready-for-agent"




def test_record_never_started_zero_evidence_has_null_remediation(monkeypatch, tmp_path):
    """Evidence capture comes up empty (container already gone) — the
    failure still records, and remediation is null. The classifier's
    pass-through branches guarantee a hint whenever evidence EXISTS, so
    null means zero evidence, not 'unmatched signature'."""
    from sssf.adw_modules.tracer import Tracer

    db = tmp_path / "proj" / "adws" / "data" / "sssf.db"
    tracer = Tracer(db, tmp_path / "proj" / "adws" / "data" / "sessions" / "abc123" / "events.jsonl")

    def fake_docker(*args, **kwargs):
        # container already gone: both evidence captures come back empty
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("sssf.sandbox.orchestrator._docker", fake_docker)
    per_run = tmp_path / "proj" / ".worktrees" / "abc123" / "adws" / "data" / "sssf.db"
    orchestrator_mod.record_never_started(tmp_path / "proj", "abc123", tracer, per_run)

    ev = tracer.conn.execute(
        "SELECT payload_json FROM events WHERE adw_id='abc123'"
    ).fetchone()
    payload = json.loads(ev[0])
    assert payload["remediation"] is None




def test_record_never_started_skips_when_adw_started(monkeypatch, tmp_path):
    """A run that DID write a session row is left to the normal sync path —
    no synthetic failure row, even when the session exists only in the
    per-run db (not yet merged)."""
    from sssf.adw_modules.tracer import Tracer

    db = tmp_path / "proj" / "adws" / "data" / "sssf.db"
    tracer = Tracer(
        db, tmp_path / "proj" / "adws" / "data" / "sessions" / "abc123" / "events.jsonl"
    )
    tracer.conn.execute("INSERT INTO sessions (adw_id, status) VALUES ('abc123', 'running')")

    def fake_docker(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("sssf.sandbox.orchestrator._docker", fake_docker)
    per_run = tmp_path / "proj" / ".worktrees" / "abc123" / "adws" / "data" / "sssf.db"
    orchestrator_mod.record_never_started(tmp_path / "proj", "abc123", tracer, per_run)

    rows = tracer.conn.execute("SELECT count(*) FROM sessions WHERE adw_id='abc123'").fetchone()[0]
    assert rows == 1  # still just the ADW's own row




def test_teardown_poll_treats_docker_error_as_retry_not_gone(monkeypatch, capsys):
    """A docker hiccup during the teardown poll must not be read as
    'container gone' — that tears the run down prematurely (audit A2)."""
    from sssf.sandbox.orchestrator import _container_gone

    def flaky(*a, **k):
        raise RuntimeError("docker hiccup")

    assert _container_gone(flaky, "sssf-x") is False
    assert "retrying" in capsys.readouterr().err




def test_teardown_poll_gone_only_on_empty_output(monkeypatch):
    from sssf.sandbox.orchestrator import _container_gone

    def gone(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

    assert _container_gone(gone, "sssf-x") is True

    def up(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="Up 2 minutes", stderr="")

    assert _container_gone(up, "sssf-x") is False


# ── runner image upkeep helpers (auto-rebuild path) ────────────────────────




def test_spawn_wraps_supervisor_and_records_sandbox_run(tmp_path, monkeypatch):
    """spawn_sandbox wraps the ADW cmd in the supervisor, publishes the review
    port, and records the resolved host port + url in the host db."""
    import json

    import sssf.sandbox as sb

    wt = tmp_path / "wt"
    wt.mkdir()
    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(data / "sssf.db"))
    conn.execute(
        "CREATE TABLE sandbox_run (adw_id TEXT PRIMARY KEY, container TEXT,"
        " container_port INTEGER, host_port INTEGER, review_url TEXT,"
        " review_command TEXT, instructions TEXT, status TEXT, updated_at TEXT)"
    )
    conn.close()

    def fake_docker(*a, timeout_s=30):
        if a[0] == "port":
            return subprocess.CompletedProcess(list(a), 0, "127.0.0.1:41234\n", "")
        return subprocess.CompletedProcess(list(a), 0, "", "")

    monkeypatch.setattr("sssf.sandbox.rundb._docker", fake_docker)
    monkeypatch.setattr("sssf.sandbox.orchestrator.ensure_image_current", lambda image: None)
    monkeypatch.setattr("sssf.sandbox.orchestrator.stamp_adw_template", lambda wt_dir: None)

    captured: dict = {}

    def fake_run_sandbox(image, name, **kw):
        captured["name"] = name
        captured["cmd"] = kw["cmd"]
        captured["publish_port"] = kw.get("publish_port")

    monkeypatch.setattr("sssf.sandbox.orchestrator.run_sandbox", fake_run_sandbox)

    review = {"command": ["npm", "run", "dev"], "container_port": 3000, "instructions": "open it"}
    sb.spawn_sandbox(
        tmp_path, "abc1",
        cmd=["python", "adws/modules/adw_x.py", "p", "--adw-id", "abc1"],
        image="sssf-runner", data_dir=data, pi_home=tmp_path / "pi",
        worktree=wt, review=review,
    )
    assert captured["name"] == "sssf-abc1"
    assert captured["cmd"][:5] == ["python", "-m", "sssf.adw_modules.supervise", "--", "python"]
    assert captured["publish_port"] == 3000

    conn = sqlite3.connect(str(data / "sssf.db"))
    cur = conn.execute("SELECT * FROM sandbox_run WHERE adw_id='abc1'")
    row = cur.fetchone()
    d = {cname: v for cname, v in zip([d[0] for d in cur.description], row, strict=True)} if row else {}
    conn.close()
    assert d["host_port"] == 41234
    assert d["review_url"] == "http://127.0.0.1:41234"
    assert json.loads(d["review_command"]) == ["npm", "run", "dev"]
    assert d["status"] == "up"




def test_spawn_records_row_without_review_config(tmp_path, monkeypatch):
    """No review config → still record the container (logs button), ports NULL."""

    import sssf.sandbox as sb

    wt = tmp_path / "wt"
    wt.mkdir()
    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(data / "sssf.db"))
    conn.execute(
        "CREATE TABLE sandbox_run (adw_id TEXT PRIMARY KEY, container TEXT,"
        " container_port INTEGER, host_port INTEGER, review_url TEXT,"
        " review_command TEXT, instructions TEXT, status TEXT, updated_at TEXT)"
    )
    conn.close()
    monkeypatch.setattr("sssf.sandbox.rundb._docker", lambda *a, timeout_s=30: subprocess.CompletedProcess(list(a), 0, "", ""))
    monkeypatch.setattr("sssf.sandbox.orchestrator.ensure_image_current", lambda image: None)
    monkeypatch.setattr("sssf.sandbox.orchestrator.stamp_adw_template", lambda wt_dir: None)
    monkeypatch.setattr("sssf.sandbox.orchestrator.run_sandbox", lambda image, name, **kw: None)

    sb.spawn_sandbox(
        tmp_path, "abc2", cmd=["python", "-c", "pass"], image="sssf-runner",
        data_dir=data, pi_home=tmp_path / "pi", worktree=wt,
    )
    conn = sqlite3.connect(str(data / "sssf.db"))
    row = conn.execute("SELECT container, host_port, status FROM sandbox_run WHERE adw_id='abc2'").fetchone()
    conn.close()
    assert row == ("sssf-abc2", None, "up")


def test_spawn_monitor_detaches_monitor_subprocess(tmp_path, monkeypatch):
    """spawn_monitor launches a detached interpreter running
    orchestrator.monitor_run on the project root + adw_id — the monitor's
    block-and-merge loop belongs to the orchestrator module, not the package
    surface."""
    import sys

    import sssf.sandbox.orchestrator as orch

    captured: dict = {}

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return object()

    monkeypatch.setattr(orch.subprocess, "Popen", fake_popen)
    orch.spawn_monitor(tmp_path / "proj", "abc1")
    assert captured["argv"][0] == sys.executable
    assert "from sssf.sandbox.orchestrator import monitor_run" in captured["argv"][2]
    assert captured["argv"][3:] == [str(tmp_path / "proj"), "abc1"]
    assert captured["kw"]["start_new_session"] is True
    assert captured["kw"]["stdin"] is subprocess.DEVNULL


def test_run_lifecycle_spawn_monitor_teardown(tmp_path, monkeypatch, fake_docker):
    """The full run lifecycle through the package's run-lifecycle verbs with
    only the fake-docker PATH shim: spawn records the container + sandbox_run
    row, the monitor merges the ADW's per-run db and consumes the
    supervisor-exit marker, and teardown is a deliberate no-op (the debugging
    surface — container + worktree — is kept for review/restart)."""
    import sqlite3

    from sssf import db_schema
    from sssf.sandbox import spawn_sandbox, teardown_sandbox
    from sssf.sandbox.orchestrator import monitor_run
    from sssf.sandbox.rundb import project_db_path

    monkeypatch.setattr("sssf.sandbox.docker._engine_fingerprint", lambda: "FPFIXED")
    root = tmp_path / "proj"
    data = root / "adws" / "data"
    data.mkdir(parents=True)
    wt = root / ".worktrees" / "abc1"
    (wt / "adws" / "data" / "sessions").mkdir(parents=True)

    record = spawn_sandbox(
        root,
        "abc1",
        cmd=["python", "adws/modules/adw_x.py", "p", "--adw-id", "abc1"],
        image="sssf-runner",
        data_dir=data,
        pi_home=tmp_path / "pi",
        worktree=wt,
        review={},
    )
    assert record == {"worktree": str(wt), "name": "sssf-abc1"}

    # The ADW runs inside the (faked) container: writes its per-run db, then
    # the supervisor writes its exit marker (the container idles in review
    # mode — the monitor must end on the marker, not on container death).
    per_run = wt / "adws" / "data" / "sssf.db"
    conn = sqlite3.connect(str(per_run), isolation_level=None)
    db_schema.apply_schema(conn)
    conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, request, status, engineer,"
        " started_at, ended_at, total_tokens, total_cost)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("abc1", "adw_implement", "build it", "success", "Felipe",
         "2026-09-04T10:00:00", "2026-09-04T10:05:00", 10, 0.01),
    )
    conn.close()
    marker = wt / "adws" / "data" / "sessions" / "abc1.supervisor-exit"
    marker.write_text("0")

    assert monitor_run(root, "abc1") == 0

    # The run's rows landed in the project db; the sandbox_run record is up.
    conn = sqlite3.connect(str(project_db_path(data)))
    status = conn.execute("SELECT status FROM sessions WHERE adw_id='abc1'").fetchone()[0]
    rec = conn.execute(
        "SELECT container, status FROM sandbox_run WHERE adw_id='abc1'"
    ).fetchone()
    conn.close()
    assert status == "success"
    assert rec == ("sssf-abc1", "up")

    # Teardown is a no-op by design — nothing is deleted.
    assert teardown_sandbox(root, "abc1") == 0
    assert not marker.exists()  # the monitor consumed the exit marker
    assert wt.exists()


def test_sandbox_build_reads_v2_config(tmp_path, monkeypatch, fake_docker):
    """sandbox build must load the config from adws/config (v2) — the v1 path
    crash (audit B1, PR #28)."""
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "config" / "sssf.config.yaml").write_text("sandbox:\n  image: sssf-runner\n")
    monkeypatch.chdir(root)
    from sssf.commands import sandbox_cmd

    assert sandbox_cmd.build(None) == 0
    # a v1-only project fails loudly (legacy banner + readable message),
    # never with a raw traceback (audit B1)
    (root / "adws" / "config").rename(root / "adws" / "adw_sssf_config")
    monkeypatch.chdir(root)
    assert sandbox_cmd.build(None) == 1


# ── run control: sandbox stop / restart (moved from `sssf run`, #89) ────────


