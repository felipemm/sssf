"""Self-healing monitor: diagnosis + recovery."""
import sqlite3

from sssf.healer import NO_PROGRESS_MIN, diagnose


def test_healthy_running_session_is_left_alone():
    assert diagnose("running", None, True, True, True, 1.0) is None   # container up, recent progress
    assert diagnose("running", None, True, True, True, None) is None  # no events yet — still warming up


def test_dead_run_finalizes():
    # running session, no container, no worktree — the ADW died silently
    assert diagnose("running", None, False, False, False, 99.0) == "finalize"


def test_monitor_crash_sync_and_teardown():
    # container gone but the worktree + per-run db remain — the monitor crashed
    assert diagnose("running", None, False, True, True, 50.0) == "sync_teardown"


def test_hung_phase_restarts():
    assert diagnose("running", None, True, True, True, NO_PROGRESS_MIN + 1) == "restart"


def test_failed_spawn_returns_ticket():
    assert diagnose(None, "starting", False, True, False, None, NO_PROGRESS_MIN + 1) == "ticket_backlog"
    assert diagnose(None, "starting", True, True, False, 1.0, 1.0) is None   # still warming up


def test_recover_finalize_marks_failed(tmp_path, monkeypatch):
    """A dead run gets finalized (session + in-flight phases failed)."""
    import subprocess
    import sssf.sandbox as sb
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    data = root / "adws" / "adw_data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute("CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, error TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('stuck1', 'running', NULL)")
    conn.execute("INSERT INTO phases VALUES ('p1', 'stuck1', 'running', NULL, NULL)")
    conn.commit()
    conn.close()
    from sssf.healer import recover
    actions = recover(root, "stuck1", "running", None, "finalize", {"restarts": {}})
    assert "finalized" in actions
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    assert conn.execute("SELECT status FROM sessions WHERE adw_id='stuck1'").fetchone()[0] == "fail"
    assert conn.execute("SELECT status FROM phases WHERE adw_id='stuck1'").fetchone()[0] == "fail"
    conn.close()


def test_restart_budget_exhausts_then_finalizes(tmp_path, monkeypatch):
    """Restart bumps the budget; at the cap the session is finalized instead."""
    import subprocess
    import sssf.sandbox as sb
    root = tmp_path / "proj2"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    data = root / "adws" / "adw_data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('hung1', 'running', NULL)")
    conn.commit()
    conn.close()
    from sssf.healer import MAX_RESTARTS, recover
    state = {"restarts": {}}
    for i in range(MAX_RESTARTS):
        out = recover(root, "hung1", "running", None, "restart", state)
        assert f"restarted ({i + 1}/{MAX_RESTARTS})" in out
    out = recover(root, "hung1", "running", None, "restart", state)
    assert "budget exhausted" in out
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    assert conn.execute("SELECT status FROM sessions WHERE adw_id='hung1'").fetchone()[0] == "fail"
    conn.close()


def test_heal_summary_accessors(tmp_path, monkeypatch):
    """heal_summary() exposes restarts + log tail; running only when the pid is alive."""
    import sssf.healer as h
    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    (tmp_path / "heal-state.json").write_text('{"restarts": {"a1": 2, "b2": 1}}')
    (tmp_path / "heal.log").write_text("line1\nline2\nline3\n")
    s = h.heal_summary()
    assert s["restarts"] == {"a1": 2, "b2": 1}
    assert s["logTail"] == ["line1", "line2", "line3"]
    assert s["running"] is False and s["pid"] is None


def test_heal_summary_missing_files(tmp_path, monkeypatch):
    import sssf.healer as h
    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    s = h.heal_summary()
    assert s["restarts"] == {} and s["logTail"] == [] and s["running"] is False


def test_heal_summary_running_when_pid_alive(tmp_path, monkeypatch):
    import os
    import sssf.healer as h
    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    (tmp_path / "heal.pid").write_text(str(os.getpid()))   # we are alive
    s = h.heal_summary()
    assert s["running"] is True and s["pid"] == os.getpid()
