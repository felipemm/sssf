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


def test_session_request_keeps_full_prompt(tmp_path):
    """Regression: sessions.request was truncated to 500 chars, so a restart
    re-ran only the first 500 chars of the original ask. It now stores the
    full prompt."""
    from sssf.adw_modules import tracer as tracer_mod

    db = tmp_path / "sssf.db"
    t = tracer_mod.Tracer(db_path=db, events_jsonl=tmp_path / "e.jsonl")
    t.session_start("r1", "tester", "adw_x")
    t.session_request("r1", "x" * 2000)
    row = t.conn.execute("SELECT request FROM sessions WHERE adw_id='r1'").fetchone()
    t.conn.close()
    assert len(row[0]) == 2000  # not truncated to 500


def test_sandbox_run_table_exists(tmp_path):
    from sssf.adw_modules import tracer as tracer_mod

    db = tmp_path / "sssf.db"
    t = tracer_mod.Tracer(db_path=db, events_jsonl=tmp_path / "e.jsonl")
    cols = {r[1] for r in t.conn.execute("PRAGMA table_info(sandbox_run)")}
    t.conn.close()
    assert {
        "adw_id", "container", "container_port", "host_port", "review_url",
        "review_command", "instructions", "status", "updated_at",
    } <= cols
