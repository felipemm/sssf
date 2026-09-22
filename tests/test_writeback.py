import sqlite3
from pathlib import Path

from sssf import ticketing, writeback


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "sssf.db")
    ticketing.ensure_schema(conn)
    return conn


def _insert(conn: sqlite3.Connection, ticket_id: str, origin: str, external_id: str) -> None:
    now = "2026-09-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ticket_id, origin, external_id, "T", "ready-for-agent", "idea", 0, origin, now, now),
    )
    conn.commit()


def _run_mock(monkeypatch, calls: list, *, returncode: int = 0, stderr: str = ""):
    rc = returncode
    err = stderr

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = rc
            stdout = ""
            stderr = err

        return R()

    monkeypatch.setattr(writeback.subprocess, "run", fake_run)


def _events(conn: sqlite3.Connection, ticket_id: str) -> list[dict]:
    return ticketing.ticket_events(conn, ticket_id)


# ── github writer argv shapes ───────────────────────────────────────────────


def test_github_state_writeback_argv(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "github:owner/repo#12", "github", "owner/repo#12")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_state(conn, "github:owner/repo#12", "closed")
    assert calls == [["gh", "issue", "edit", "12", "--repo", "owner/repo", "--state", "closed"]]
    assert _events(conn, "github:owner/repo#12") == []
    conn.close()


def test_github_comment_writeback_argv(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "github:owner/repo#12", "github", "owner/repo#12")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_comment(conn, "github:owner/repo#12", "rejected: nope")
    assert calls == [["gh", "issue", "comment", "12", "--repo", "owner/repo", "--body", "rejected: nope"]]
    conn.close()


def test_github_label_add_remove_argv(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "github:owner/repo#12", "github", "owner/repo#12")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_label(conn, "github:owner/repo#12", "bug", add=True)
    writeback.writeback_label(conn, "github:owner/repo#12", "bug", add=False)
    assert calls == [
        ["gh", "issue", "edit", "12", "--repo", "owner/repo", "--add-label", "bug"],
        ["gh", "issue", "edit", "12", "--repo", "owner/repo", "--remove-label", "bug"],
    ]
    conn.close()


# ── gitlab writer argv shapes (canonical open → opened) ────────────────────


def test_gitlab_state_maps_open_to_opened(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "gitlab:group/proj#5", "gitlab", "group/proj#5")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_state(conn, "gitlab:group/proj#5", "open")
    assert calls == [["glab", "issue", "update", "5", "--repo", "group/proj", "--state", "opened"]]
    conn.close()


def test_gitlab_comment_and_label_argv(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "gitlab:group/proj#5", "gitlab", "group/proj#5")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_comment(conn, "gitlab:group/proj#5", "see MR !3")
    writeback.writeback_label(conn, "gitlab:group/proj#5", "p1", add=True)
    writeback.writeback_label(conn, "gitlab:group/proj#5", "p1", add=False)
    assert calls == [
        ["glab", "issue", "note", "5", "--repo", "group/proj", "-m", "see MR !3"],
        ["glab", "issue", "update", "5", "--repo", "group/proj", "--label", "p1"],
        ["glab", "issue", "update", "5", "--repo", "group/proj", "--unlabel", "p1"],
    ]
    conn.close()


# ── best-effort guarantees ──────────────────────────────────────────────────


def test_internal_ticket_is_noop(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "internal:abc", "internal", "")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_state(conn, "internal:abc", "closed")
    writeback.writeback_comment(conn, "internal:abc", "x")
    writeback.writeback_label(conn, "internal:abc", "bug")
    assert calls == []
    assert _events(conn, "internal:abc") == []
    conn.close()


def test_failure_recorded_as_event_never_raises(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "github:owner/repo#12", "github", "owner/repo#12")
    calls = []
    _run_mock(monkeypatch, calls, returncode=1, stderr="gh: not authenticated")
    writeback.writeback_state(conn, "github:owner/repo#12", "closed")  # must not raise
    events = _events(conn, "github:owner/repo#12")
    (event,) = events
    assert event["event_type"] == "writeback_failed"
    assert event["payload"]["origin"] == "github"
    assert event["payload"]["operation"] == "state"
    assert "gh failed" in event["payload"]["error"]
    conn.close()


def test_missing_binary_recorded_as_event(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "github:owner/repo#12", "github", "owner/repo#12")

    def fake_run(args, capture_output, text, timeout):
        raise FileNotFoundError("gh: No such file or directory")

    monkeypatch.setattr(writeback.subprocess, "run", fake_run)
    writeback.writeback_label(conn, "github:owner/repo#12", "bug")  # must not raise
    events = _events(conn, "github:owner/repo#12")
    assert events[0]["event_type"] == "writeback_failed"
    conn.close()


def test_jira_and_linear_record_skipped(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "jira:ACME-1", "jira", "ACME-1")
    _insert(conn, "linear:ENG-3", "linear", "ENG-3")
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_state(conn, "jira:ACME-1", "open")
    writeback.writeback_comment(conn, "linear:ENG-3", "x")
    assert calls == []  # no wired writer — nothing shelled
    jira_events = _events(conn, "jira:ACME-1")
    assert jira_events[0]["event_type"] == "writeback_skipped"
    assert jira_events[0]["payload"] == {"origin": "jira", "operation": "state"}
    linear_events = _events(conn, "linear:ENG-3")
    assert linear_events[0]["event_type"] == "writeback_skipped"
    conn.close()


def test_unknown_ticket_recorded_failed(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_state(conn, "github:owner/repo#99", "closed")
    assert calls == []
    (event,) = _events(conn, "github:owner/repo#99")
    assert event["event_type"] == "writeback_failed"
    assert "no ticket" in event["payload"]["error"]
    conn.close()


def test_unparseable_external_id_recorded_failed(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _insert(conn, "github:odd", "github", "odd")  # no `#` in external_id
    calls = []
    _run_mock(monkeypatch, calls)
    writeback.writeback_state(conn, "github:odd", "closed")
    assert calls == []
    (event,) = _events(conn, "github:odd")
    assert event["event_type"] == "writeback_failed"
    assert "cannot parse" in event["payload"]["error"]
    conn.close()
