import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sssf import registry
from sssf.commands import sweep


def _make_db(path: Path) -> None:
    """A sessions table with one old and one recent session, plus a running one."""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT,"
        " archived INTEGER DEFAULT 0, request TEXT);")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="milliseconds")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="milliseconds")
    conn.execute(
        "INSERT INTO sessions (adw_id, status, ended_at, archived) VALUES"
        " ('old-success', 'success', ?, 0), ('old-fail', 'fail', ?, 0),"
        " ('recent', 'success', ?, 0), ('running', 'running', NULL, 0)",
        (old, old, recent))
    conn.commit()
    conn.close()


def _register(tmp_path: Path, monkeypatch, root: Path | None = None) -> None:
    monkeypatch.setattr(registry, "registry_path",
                        lambda: tmp_path / ".sssf" / "projects.json")
    if root is not None:
        registry.register_project(root, root / "adws" / "adw_data" / "sssf.db", "0.1.0")


def test_sweep_archives_old_finished_only(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/adw_data").mkdir(parents=True)
    db = root / "adws" / "adw_data" / "sssf.db"
    _make_db(db)
    _register(tmp_path, monkeypatch, root)

    assert sweep.run(None) == 0
    out = capsys.readouterr().out
    assert "archived 2 session(s)" in out

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT adw_id, archived FROM sessions").fetchall())
    conn.close()
    assert rows["old-success"] == 1
    assert rows["old-fail"] == 1
    assert rows["recent"] == 0
    assert rows["running"] == 0


def test_sweep_project_flag(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/adw_data").mkdir(parents=True)
    db = root / "adws" / "adw_data" / "sssf.db"
    _make_db(db)
    # not registered — --project sweeps it directly
    assert sweep.run(str(root)) == 0
    assert "archived 2 session(s)" in capsys.readouterr().out


def test_sweep_empty_registry_is_friendly(tmp_path, monkeypatch, capsys):
    _register(tmp_path, monkeypatch)
    assert sweep.run(None) == 0
    assert "no projects registered" in capsys.readouterr().out


def test_sweep_days_flag(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/adw_data").mkdir(parents=True)
    db = root / "adws" / "adw_data" / "sssf.db"
    _make_db(db)
    _register(tmp_path, monkeypatch, root)
    # 1-day window: only the 40-day-old sessions qualify
    assert sweep.run(None, days=1) == 0
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE archived = 1").fetchone()[0]
    conn.close()
    assert n == 2
