from pathlib import Path

from sssf.adw_modules import tracer as tracer_mod
from sssf.commands import obs_cmds


def _make_db(path: Path) -> None:
    t = tracer_mod.Tracer(db_path=path, events_jsonl=path.with_suffix(".jsonl"))
    t.session_start("abc123", "tester", "adw_prompt")
    t.session_request("abc123", "hello")
    t.session_finish("abc123", True)
    t.session_add_usage("abc123", 100, 0.001)
    t.conn.close()


def test_sessions_lists_runs(tmp_path, capsys):
    db = tmp_path / "sssf.db"
    _make_db(db)
    assert obs_cmds.sessions(db) == 0
    out = capsys.readouterr().out
    assert "abc123" in out and "adw_prompt" in out


def test_phases_empty_ok(tmp_path, capsys):
    db = tmp_path / "sssf.db"
    _make_db(db)
    assert obs_cmds.phases(db, "nope") == 0


def test_missing_db_is_friendly(tmp_path, capsys):
    assert obs_cmds.sessions(tmp_path / "missing.db") == 0
    out = capsys.readouterr().out
    assert "no trace db" in out
