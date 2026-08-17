"""Self-healing monitor: diagnosis + recovery."""

import json
import sqlite3

from sssf.healer import NO_PROGRESS_MIN, diagnose


def test_healthy_running_session_is_left_alone():
    assert diagnose("running", None, True, True, True, 1.0) is None  # container up, recent progress
    assert (
        diagnose("running", None, True, True, True, None) is None
    )  # no events yet — still warming up


def test_dead_run_finalizes():
    # running session, no container, no worktree — the ADW died silently
    assert diagnose("running", None, False, False, False, 99.0) == "finalize"


def test_monitor_crash_sync_and_teardown():
    # container gone but the worktree + per-run db remain — the monitor crashed
    assert diagnose("running", None, False, True, True, 50.0) == "sync_teardown"


def test_hung_phase_restarts():
    assert diagnose("running", None, True, True, True, NO_PROGRESS_MIN + 1) == "restart"


def test_failed_spawn_returns_ticket():
    assert (
        diagnose(None, "starting", False, True, False, None, NO_PROGRESS_MIN + 1)
        == "ticket_backlog"
    )
    assert diagnose(None, "starting", True, True, False, 1.0, 1.0) is None  # still warming up


def test_failed_session_ticket_returns_ticket():
    """A ticket whose RUN FAILED goes back to the backlog (history preserved)
    — the new linked_session_status branch, distinct from spawn failures."""
    assert diagnose(None, "starting", False, True, False, None, 1.0, "fail") == "ticket_backlog"
    assert diagnose(None, "starting", True, True, False, 1.0, 1.0, "running") is None
    assert diagnose(None, "starting", True, True, False, 1.0, 1.0, "success") is None
    assert diagnose(None, "backlog", True, True, False, 1.0, 1.0, "fail") is None  # already backlog


def test_starting_ticket_with_live_session_is_never_spawn_fail():
    """Regression: a retried ticket carries a STALE updated_at (run() never
    bumped it), so an old age must not classify a run that HAS a session as a
    failed spawn — that killed a healthy run (abort_sandbox on a live
    container). Spawn-fail is only for tickets with NO session at all."""
    assert (
        diagnose(None, "starting", True, True, False, 1.0, NO_PROGRESS_MIN + 99, "running") is None
    )
    assert (
        diagnose(None, "starting", True, True, False, 1.0, NO_PROGRESS_MIN + 99, "fail")
        == "ticket_backlog"
    )
    assert (
        diagnose(None, "starting", True, True, False, 1.0, NO_PROGRESS_MIN + 99, None)
        == "ticket_backlog"
    )


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
    data = root / "adws" / "data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute(
        "CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('stuck1', 'running', NULL)")
    conn.execute("INSERT INTO phases VALUES ('p1', 'stuck1', 'running', NULL, NULL)")
    conn.commit()
    conn.close()
    from sssf.healer import recover

    actions = recover(root, "stuck1", "running", None, "finalize", {"restarts": {}})
    assert "finalized" in actions
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    assert conn.execute("SELECT status FROM sessions WHERE adw_id='stuck1'").fetchone()[0] == "fail"
    # The healer's finalize records WHAT IT DID, not the engineer's stop.
    err = conn.execute("SELECT error FROM phases WHERE adw_id='stuck1'").fetchone()[0]
    assert "finalized by the healer" in err and "dead run" in err
    assert "engineer" not in err
    conn.close()


def test_recover_ticket_backlog_keeps_history(tmp_path, monkeypatch):
    """The healer moves a failed-session ticket back to backlog WITHOUT clearing
    the adw_id — the failed run stays linked for history and the retry color."""
    import subprocess

    import sssf.sandbox as sb

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    data = root / "adws" / "data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, provider TEXT, external_id TEXT,"
        " title TEXT, description TEXT, status TEXT, prompt_file TEXT, adw_id TEXT,"
        " source_url TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE ticket_runs (ticket_id TEXT, adw_id TEXT, created_at TEXT,"
        " PRIMARY KEY(ticket_id, adw_id))"
    )
    conn.execute("INSERT INTO sessions VALUES ('dead1', 'fail', '2026-08-16T00:00:00+00:00')")
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, adw_id)"
        " VALUES ('internal:retry', 'internal', '', 'X', 'starting', 'dead1')"
    )
    conn.execute(
        "INSERT INTO ticket_runs VALUES ('internal:retry', 'dead1', '2026-08-16T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    from sssf.healer import recover

    actions = recover(root, "dead1", None, "starting", "ticket_backlog", {"restarts": {}})
    assert "backlog" in actions
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    row = conn.execute("SELECT status, adw_id FROM tickets WHERE id='internal:retry'").fetchone()
    runs = conn.execute(
        "SELECT COUNT(*) FROM ticket_runs WHERE ticket_id='internal:retry'"
    ).fetchone()[0]
    conn.close()
    assert row == ("backlog", "dead1")  # link preserved — history intact
    assert runs == 1


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
    data = root / "adws" / "data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute(
        "CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('hung1', 'running', NULL)")
    conn.execute("INSERT INTO phases VALUES ('hp1', 'hung1', 'running', NULL, NULL)")
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
    err = conn.execute("SELECT error FROM phases WHERE adw_id='hung1'").fetchone()[0]
    assert "finalized by the healer" in err and "budget" in err
    assert "engineer" not in err
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
    (tmp_path / "heal.pid").write_text(str(os.getpid()))  # we are alive
    s = h.heal_summary()
    assert s["running"] is True and s["pid"] == os.getpid()


def test_healed_total_counts_last_7_days_from_state(tmp_path, monkeypatch):
    """The healed metric counts timestamped state records within 7 days."""
    import datetime

    import sssf.healer as h

    monkeypatch.setattr(h, "STATE_DIR", tmp_path)
    now = datetime.datetime.now(datetime.UTC)

    def days_ago(n):
        return (now - datetime.timedelta(days=n)).isoformat()

    (tmp_path / "heal-state.json").write_text(
        json.dumps(
            {
                "restarts": {},
                "healed": [
                    {"adw_id": "abc123", "ts": days_ago(1)},
                    {"adw_id": "def456", "ts": days_ago(6)},
                    {"adw_id": "old99", "ts": days_ago(10)},  # outside the window
                ],
            }
        )
    )
    s = h.heal_summary()
    assert s["healed7d"] == 2
    assert h.healed_total() == 2
    assert h.healed_total(days=14) == 3


def test_recover_records_and_prunes_healed_state(tmp_path, monkeypatch):
    """recover() appends a timestamped record and prunes to 7 days."""
    import datetime
    import subprocess

    import sssf.healer as h
    import sssf.sandbox as sb

    monkeypatch.setattr(h, "STATE_DIR", tmp_path)  # never touch the real state file
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    data = root / "adws" / "data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sb.project_db_path(data)))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('stuck1', 'running', NULL)")
    conn.commit()
    conn.close()
    state = {"restarts": {}}
    h.recover(root, "stuck1", "running", None, "finalize", state)
    healed = state["healed"]
    assert len(healed) == 1 and healed[0]["adw_id"] == "stuck1"
    # a 10-day-old record is pruned on the next append
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=10)).isoformat()
    state["healed"].append({"adw_id": "ancient", "ts": old})
    h.recover(root, "stuck2", "running", None, "finalize", state)
    assert {x["adw_id"] for x in state["healed"]} == {"stuck1", "stuck2"}
