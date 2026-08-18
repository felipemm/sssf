import subprocess
from pathlib import Path

import sssf.cli as cli  # noqa: F401
from sssf.commands import misc  # noqa: F401  (CLI registration smoke)


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
    (root / "adws" / "config" / "sssf.config.yaml").write_text(
        "sandbox:\n  image: sssf-runner\n")
    monkeypatch.chdir(root)
    from sssf.commands import sandbox_cmd
    assert sandbox_cmd.build(None) == 0
    # a v1-only project is refused (legacy banner)
    (root / "adws" / "config").rename(root / "adws" / "adw_sssf_config")
    monkeypatch.chdir(root)
    assert sandbox_cmd.build(None) == 0
