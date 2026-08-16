import sqlite3
from pathlib import Path

from sssf import ticketing
from sssf.commands import ticket


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "adw_sssf_config").mkdir(parents=True)
    (root / "adws" / "adw_data").mkdir(parents=True)
    (root / "adws" / "prompts").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / "adws" / "adw_sssf_config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "adw_data" / "sssf.db")
    conn.execute(ticketing.TICKETS_DDL)
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
    root = _project(tmp_path, monkeypatch, "providers:\n  - jira\njira:\n  jql: 'x'\n")
    assert ticket.add("nope", None) == 1
    assert "internal provider is not enabled" in capsys.readouterr().err


def test_not_configured_is_friendly(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)   # no ticketing.yaml
    assert ticket.sync(None) == 1
    assert "not configured" in capsys.readouterr().err


def test_run_creates_prompt_and_spawns(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, description, status)"
                 " VALUES ('internal:abc', 'internal', '', 'Dark mode', 'Make it dark', 'backlog')")
    conn.commit()
    conn.close()
    (root / "adws" / "adw_simple_sdlc.py").write_text("print('adw stub')\n")
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
    row = conn.execute("SELECT status, adw_id, prompt_file FROM tickets WHERE id='internal:abc'").fetchone()
    conn.close()
    assert row[0] == "starting" and row[1] and row[2]   # spawned — warms up before the session appears


def test_run_rejects_already_running(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, status)"
                 " VALUES ('internal:abc', 'internal', '', 'X', 'running')")
    conn.commit()
    conn.close()
    assert ticket.run("internal:abc", None) == 1
    assert "already running" in capsys.readouterr().err


def test_next_prompt_name_enumerates(tmp_path):
    root = tmp_path / "proj"
    (root / "adws" / "prompts").mkdir(parents=True)
    for name in ("01-foo.md", "02-bar.md", "08-last.md"):
        (root / "adws" / "prompts" / name).write_text("x")
    assert ticketing.next_prompt_name(root, "baz").name == "09-baz.md"
    assert ticketing.next_prompt_name(root, "baz").exists() is False
