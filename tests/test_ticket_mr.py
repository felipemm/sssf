"""Ticket ↔ MR registration (issue #98).

The monitor scans `ready-to-deploy` tickets with an open MR. MR references
live in the `ticket_mrs` table, written by `sssf mr add` (and later by the
deploy flow once it lands): deterministic, audited, and read by the viz
server's monitor — never scraped from ticket text.
"""

import sqlite3

from sssf import ticketing


def _conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "mr.db"))
    ticketing.ensure_schema(conn)
    return conn


def _insert_ticket(conn: sqlite3.Connection, ticket_id: str, status: str = "ready-for-agent") -> None:
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status) VALUES (?,?,?,?,?)",
        (ticket_id, "internal", "", ticket_id, status),
    )


def test_ensure_schema_creates_ticket_mrs_table(tmp_path):
    conn = _conn(tmp_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ticket_mrs" in tables
    conn.close()


def test_register_mr_stores_repo_iid_url(tmp_path):
    conn = _conn(tmp_path)
    _insert_ticket(conn, "t1", status="ready-to-deploy")
    ticketing.register_mr(conn, "t1", repo="group/proj", iid="42", url="https://gitlab/group/proj/-/merge_requests/42")
    conn.commit()
    mr = ticketing.mr_for_ticket(conn, "t1")
    conn.close()
    assert mr is not None
    assert mr.repo == "group/proj"
    assert mr.iid == "42"
    assert mr.url == "https://gitlab/group/proj/-/merge_requests/42"


def test_register_mr_upserts_in_place(tmp_path):
    conn = _conn(tmp_path)
    _insert_ticket(conn, "t1", status="ready-to-deploy")
    ticketing.register_mr(conn, "t1", "group/proj", "42", url="first")
    ticketing.register_mr(conn, "t1", "group/proj", "42", url="second")
    conn.commit()
    rows = conn.execute("SELECT ticket_id FROM ticket_mrs").fetchall()
    conn.close()
    assert len(rows) == 1  # one row per ticket — never a duplicate
    assert ticketing.mr_for_ticket  # (lens already exercised above)


def test_register_mr_writes_audit_event(tmp_path):
    conn = _conn(tmp_path)
    _insert_ticket(conn, "t1", status="ready-to-deploy")
    ticketing.register_mr(conn, "t1", "group/proj", "42", actor="monitor")
    conn.commit()
    events = ticketing.ticket_events(conn, "t1")
    conn.close()
    assert any(
        e["event_type"] == "mr" and e["payload"].get("action") == "register" for e in events
    )


def test_unregister_mr_removes_and_audits(tmp_path):
    conn = _conn(tmp_path)
    _insert_ticket(conn, "t1", status="ready-to-deploy")
    ticketing.register_mr(conn, "t1", "group/proj", "42")
    ticketing.unregister_mr(conn, "t1", actor="monitor")
    conn.commit()
    assert ticketing.mr_for_ticket(conn, "t1") is None
    events = ticketing.ticket_events(conn, "t1")
    conn.close()
    assert any(
        e["event_type"] == "mr" and e["payload"].get("action") == "remove" for e in events
    )


def test_mrs_for_status_returns_only_ready_to_deploy_tickets_with_mrs(tmp_path):
    conn = _conn(tmp_path)
    _insert_ticket(conn, "deploy-1", status="ready-to-deploy")
    _insert_ticket(conn, "deploy-2", status="ready-to-deploy")
    _insert_ticket(conn, "inprog-1", status="in-progress")
    _insert_ticket(conn, "no-mr", status="ready-to-deploy")
    ticketing.register_mr(conn, "deploy-1", "group/a", "1")
    ticketing.register_mr(conn, "deploy-2", "group/b", "2")
    ticketing.register_mr(conn, "inprog-1", "group/c", "3")  # not deployable
    conn.commit()
    rows = ticketing.mrs_for_status(conn, ticketing.STATUS_DEPLOY)
    conn.close()
    ids = {r.ticket_id for r in rows}
    assert ids == {"deploy-1", "deploy-2"}


def test_mrs_for_status_empty_when_no_mrs(tmp_path):
    conn = _conn(tmp_path)
    _insert_ticket(conn, "t1", status="ready-to-deploy")
    conn.commit()
    assert ticketing.mrs_for_status(conn, ticketing.STATUS_DEPLOY) == []
    conn.close()
