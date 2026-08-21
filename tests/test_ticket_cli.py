import datetime
import sqlite3
from pathlib import Path

from sssf import ticketing
from sssf.commands import ticket


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    (root / "adws" / "prompts").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / "adws" / "config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    conn.execute(ticketing.TICKETS_DDL)
    conn.execute(ticketing.TICKET_RUNS_DDL)
    return conn


CONFIG = "providers:\n  - internal\n  - jira\njira:\n  jql: 'project = ACME'\n"


def test_add_internal_ticket(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    assert ticket.add("Ship dark mode", None) == 0
    conn = _db(root)
    row = conn.execute("SELECT provider, title, status FROM tickets").fetchone()
    conn.close()
    assert row == ("internal", "Ship dark mode", "backlog")
    assert "added" in capsys.readouterr().out


def test_add_requires_internal_provider(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "providers:\n  - jira\njira:\n  jql: 'x'\n")
    assert ticket.add("nope", None) == 1
    assert "internal provider is not enabled" in capsys.readouterr().err


def test_not_configured_is_friendly(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)  # no ticketing.yaml
    assert ticket.sync(None) == 1
    assert "not configured" in capsys.readouterr().err


def test_run_creates_prompt_and_spawns(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:abc', 'internal', '', 'Dark mode', 'Make it dark', 'backlog')"
    )
    conn.commit()
    conn.close()
    (root / "adws" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "adws" / "modules" / "adw_simple_sdlc.py").write_text("print('adw stub')\n")
    spawned = {}

    def fake_popen(argv, **kw):
        spawned["argv"] = argv

        class P:
            pid = 12345

        return P()

    monkeypatch.setattr(ticket.subprocess, "Popen", fake_popen)
    assert ticket.run("internal:abc", None) == 0
    assert spawned["argv"][0] == ticket.sys.executable
    prompt = sorted((root / "adws" / "prompts").glob("*.md"))
    assert len(prompt) == 1
    text = prompt[0].read_text()
    assert "Dark mode" in text and "Make it dark" in text
    assert "--adw-id" in spawned["argv"]
    conn = _db(root)
    row = conn.execute(
        "SELECT status, adw_id, prompt_file FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    conn.close()
    assert (
        row[0] == "starting" and row[1] and row[2]
    )  # spawned — warms up before the session appears


def test_run_rejects_already_running(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status)"
        " VALUES ('internal:abc', 'internal', '', 'X', 'running')"
    )
    conn.commit()
    conn.close()
    assert ticket.run("internal:abc", None) == 1
    assert "already running" in capsys.readouterr().err


def test_run_bumps_updated_at(tmp_path, monkeypatch):
    """run() marks the spawn time on updated_at — the healer's spawn-fail
    age check measures from it. A stale timestamp classifies a healthy retry
    as a failed spawn and kills the live container."""
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, updated_at)"
        " VALUES ('internal:abc', 'internal', '', 'X', 'backlog', '2026-08-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    (root / "adws" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "adws" / "modules" / "adw_simple_sdlc.py").write_text("print('adw stub')\n")

    class P:
        pid = 12345

    monkeypatch.setattr(ticket.subprocess, "Popen", lambda argv, **kw: P())
    assert ticket.run("internal:abc", None) == 0
    conn = _db(root)
    row = conn.execute("SELECT updated_at FROM tickets WHERE id='internal:abc'").fetchone()
    conn.close()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    assert row[0].startswith(today)  # today, not a stale hard-coded date


def test_run_records_run_history(tmp_path, monkeypatch, capsys):
    """Every spawn appends to ticket_runs — a retried ticket accumulates its
    runs; the ticket row's adw_id is the LATEST run."""
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:abc', 'internal', '', 'Dark mode', 'Make it dark', 'backlog')"
    )
    conn.commit()
    conn.close()
    (root / "adws" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "adws" / "modules" / "adw_simple_sdlc.py").write_text("print('adw stub')\n")

    class P:
        pid = 12345

    monkeypatch.setattr(ticket.subprocess, "Popen", lambda argv, **kw: P())
    assert ticket.run("internal:abc", None) == 0
    first_id = conn_adw_id(root)
    assert ticket.run("internal:abc", None) == 0  # retry — a second run
    second_id = conn_adw_id(root)
    assert first_id != second_id

    conn = _db(root)
    rows = conn.execute(
        "SELECT adw_id FROM ticket_runs WHERE ticket_id='internal:abc' ORDER BY created_at"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == [first_id, second_id]


def conn_adw_id(root: Path) -> str:
    conn = _db(root)
    row = conn.execute("SELECT adw_id FROM tickets WHERE id='internal:abc'").fetchone()
    conn.close()
    return row[0]


def _sessions_ddl(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " adw_id TEXT PRIMARY KEY, status TEXT, started_at TEXT,"
        " ended_at TEXT, total_tokens INTEGER, total_cost REAL)"
    )


def test_backlog_keeps_link_and_history(tmp_path, monkeypatch, capsys):
    """Back to backlog keeps the adw_id + ticket_runs so the failed run stays
    in the trace — a retried ticket accumulates history instead of losing it."""
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    _sessions_ddl(conn)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, adw_id)"
        " VALUES ('internal:abc', 'internal', '', 'X', 'starting', 'sess_fail')"
    )
    conn.execute("INSERT INTO sessions (adw_id, status) VALUES ('sess_fail', 'fail')")
    conn.execute(
        "INSERT INTO ticket_runs (ticket_id, adw_id, created_at)"
        " VALUES ('internal:abc', 'sess_fail', '2026-08-16T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    assert ticket.backlog("internal:abc", None) == 0
    conn = _db(root)
    row = conn.execute("SELECT status, adw_id FROM tickets WHERE id='internal:abc'").fetchone()
    runs = conn.execute(
        "SELECT COUNT(*) FROM ticket_runs WHERE ticket_id='internal:abc'"
    ).fetchone()[0]
    conn.close()
    assert row == ("backlog", "sess_fail")  # link preserved
    assert runs == 1  # history preserved


def test_backlog_refuses_running_session(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    _sessions_ddl(conn)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, adw_id)"
        " VALUES ('internal:abc', 'internal', '', 'X', 'starting', 'sess_run')"
    )
    conn.execute("INSERT INTO sessions (adw_id, status) VALUES ('sess_run', 'running')")
    conn.commit()
    conn.close()
    assert ticket.backlog("internal:abc", None) == 1
    assert "running" in capsys.readouterr().err


def test_backlog_missing_ticket(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, CONFIG)
    assert ticket.backlog("internal:nope", None) == 1
    assert "no ticket" in capsys.readouterr().err


def test_next_prompt_name_enumerates(tmp_path):
    root = tmp_path / "proj"
    (root / "adws" / "prompts").mkdir(parents=True)
    for name in ("01-foo.md", "02-bar.md", "08-last.md"):
        (root / "adws" / "prompts" / name).write_text("x")
    assert ticketing.next_prompt_name(root, "baz").name == "09-baz.md"
    assert ticketing.next_prompt_name(root, "baz").exists() is False


def test_ticket_sandbox_failure_is_loud(tmp_path, monkeypatch, capsys):
    """A config error in the sandbox decision must be visible — never silently
    unsandboxed (audit A1)."""
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)   # no sssf.config.yaml
    from sssf.commands import ticket
    assert ticket._sandbox_enabled(root) is False
    assert "sandbox decision failed" in capsys.readouterr().err
