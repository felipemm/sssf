import subprocess
from pathlib import Path

import sssf.cli as cli  # noqa: F401
from sssf.commands import misc  # noqa: F401  (CLI registration smoke)


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


def test_spawn_sandbox_creates_worktree_and_records_port(tmp_path, monkeypatch, fake_docker):
    root = _make_repo(tmp_path)
    import sssf.sandbox as sandbox
    from sssf.sandbox import sandbox_dir, spawn_sandbox

    monkeypatch.setattr(sandbox, "_engine_fingerprint", lambda: "FPFIXED")
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


def test_teardown_keeps_branch(tmp_path):
    root = _make_repo(tmp_path)
    from sssf.sandbox import create_worktree, remove_worktree

    wt = create_worktree(root, "abc123")
    remove_worktree(wt)
    branches = subprocess.run(
        ["git", "branch", "--list", "sssf/abc123"], cwd=root, capture_output=True, text=True
    ).stdout
    assert "sssf/abc123" in branches


def test_stop_run_finalizes_stale_session(tmp_path, monkeypatch, fake_docker):
    """A stale run (no container/worktree, session stuck running) becomes
    failed on stop — so it is archivable."""
    import sqlite3

    from sssf.sandbox import project_db_path, stop_run

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
    import sqlite3

    from sssf.sandbox import project_db_path, stop_run

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
    import sqlite3

    from sssf import sandbox
    from sssf.sandbox import project_db_path

    db = project_db_path(sandbox.sandbox_env(root)[0])
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
