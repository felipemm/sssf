"""`sssf mr` CLI — attach an MR reference to a ticket (issue #98)."""

import sqlite3
from pathlib import Path

from sssf import ticketing
from sssf.commands import mr


def _project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    ticketing.ensure_schema(conn)
    return conn


def _insert_ticket(conn: sqlite3.Connection, ticket_id: str, status: str = "ready-to-deploy") -> None:
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status) VALUES (?,?,?,?,?)",
        (ticket_id, "internal", "", ticket_id, status),
    )
    conn.commit()


def test_mr_add_registers_and_confirms(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    conn = _db(root)
    _insert_ticket(conn, "t1")
    conn.close()
    assert mr.add("t1", "group/proj", "42", url="https://gitlab/group/proj/-/merge_requests/42") == 0
    conn = _db(root)
    registered = ticketing.mr_for_ticket(conn, "t1")
    conn.close()
    assert registered is not None and registered.repo == "group/proj" and registered.iid == "42"
    assert "registered" in capsys.readouterr().out


def test_mr_add_requires_existing_ticket(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    _db(root).close()
    assert mr.add("ghost", "group/proj", "1") == 1
    assert "no ticket ghost" in capsys.readouterr().err


def test_mr_list_shows_registered(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    conn = _db(root)
    _insert_ticket(conn, "t1")
    _insert_ticket(conn, "t2")
    ticketing.register_mr(conn, "t1", "group/a", "1")
    ticketing.register_mr(conn, "t2", "group/b", "2", url="https://gitlab/group/b/-/merge_requests/2")
    conn.commit()
    conn.close()
    assert mr.list_mrs() == 0
    out = capsys.readouterr().out
    assert "t1" in out and "group/a" in out
    assert "t2" in out and "group/b" in out


def test_mr_rm_removes_and_confirms(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    conn = _db(root)
    _insert_ticket(conn, "t1")
    ticketing.register_mr(conn, "t1", "group/proj", "42")
    conn.commit()
    conn.close()
    assert mr.rm("t1") == 0
    conn = _db(root)
    assert ticketing.mr_for_ticket(conn, "t1") is None
    conn.close()
    assert "removed" in capsys.readouterr().out
