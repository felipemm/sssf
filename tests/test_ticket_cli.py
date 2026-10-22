import sqlite3
import subprocess
from pathlib import Path

from sssf import ticketing, writeback
from sssf.commands import ticket


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / ".adws" / "config").mkdir(parents=True)
    (root / ".adws" / "data").mkdir(parents=True)
    (root / ".adws" / "prompts").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / ".adws" / "config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / ".adws" / "data" / "sssf.db")
    ticketing.ensure_schema(conn)
    return conn


CONFIG = "providers:\n  - internal\n  - jira\njira:\n  jql: 'project = ACME'\n"


def test_not_configured_is_friendly(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)  # no ticketing.yaml
    assert ticket.sync(None) == 1
    assert "not configured" in capsys.readouterr().err


def test_context_set_get_roundtrip(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:abc', 'internal', '', 'Dark mode', 'Make it dark', 'ready-for-agent')"
    )
    conn.commit()
    conn.close()
    assert ticket.ticket_context("internal:abc", None, set_text="focus on the OAuth flow") == 0
    capsys.readouterr()  # consume the "context saved" line
    conn = _db(root)
    saved = conn.execute("SELECT context FROM tickets WHERE id='internal:abc'").fetchone()[0]
    conn.close()
    assert saved == "focus on the OAuth flow"
    assert ticket.ticket_context("internal:abc", None) == 0
    assert capsys.readouterr().out.strip() == "focus on the OAuth flow"


def test_schema_migration_adds_context_column(tmp_path):
    """Pre-context databases (no column) get it added, not re-created."""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
        " title TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'backlog')"
    )
    conn.execute("INSERT INTO tickets (id, provider, title) VALUES ('a', 'internal', 't')")
    conn.commit()
    ticketing.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
    assert "context" in cols
    row = conn.execute("SELECT id, context FROM tickets WHERE id='a'").fetchone()
    assert row == ("a", "")  # existing row backfilled to ''
    conn.close()


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
        " VALUES ('internal:abc', 'internal', '', 'X', 'in-progress', 'sess_fail')"
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
    assert row == ("ready-for-agent", "sess_fail")  # link preserved
    assert runs == 1  # history preserved


def test_backlog_refuses_running_session(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    _sessions_ddl(conn)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, adw_id)"
        " VALUES ('internal:abc', 'internal', '', 'X', 'in-progress', 'sess_run')"
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
    (root / ".adws" / "prompts").mkdir(parents=True)
    for name in ("01-foo.md", "02-bar.md", "08-last.md"):
        (root / ".adws" / "prompts" / name).write_text("x")
    assert ticketing.next_prompt_name(root, "baz").name == "09-baz.md"
    assert ticketing.next_prompt_name(root, "baz").exists() is False


def test_ticket_new_creates_idea_ticket(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    assert ticket.new("Ship dark mode", None) == 0
    out = capsys.readouterr().out
    assert "idea ticket" in out and "needs-triage" in out
    conn = _db(root)
    row = conn.execute(
        "SELECT title, description, status, kind, tracked, origin, spec"
        " FROM tickets WHERE provider='internal'"
    ).fetchone()
    events = conn.execute("SELECT event_type, actor FROM ticket_events").fetchall()
    conn.close()
    assert row == ("Ship dark mode", "", "needs-triage", "idea", 1, "internal", "")
    import getpass

    assert ("created", getpass.getuser()) in events  # operator recorded as the actor


def test_ticket_new_requires_internal_provider(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "providers:\n  - jira\njira:\n  jql: 'x'\n")
    assert ticket.new("nope", None) == 1
    assert "internal provider is not enabled" in capsys.readouterr().err


def test_ticket_list_backlog_returns_only_ready_for_agent(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    ticket.new("Idea first", None)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:ready1', 'internal', '', 'Build feature', '', 'ready-for-agent')"
    )
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:blocked1', 'internal', '', 'Blocked thing', '', 'blocked')"
    )
    conn.commit()
    conn.close()
    capsys.readouterr()  # consume prior output
    assert ticket.list_tickets(None, backlog_only=True) == 0
    out = capsys.readouterr().out
    assert "Build feature" in out  # ready-for-agent -> in the backlog
    assert "Idea first" not in out  # needs-triage idea -> not in the backlog
    assert "Blocked thing" not in out  # blocked -> not in the backlog


def test_backlog_retry_requeues_with_feedback(tmp_path, monkeypatch, capsys):
    """The manual retry control moves a ticket back to ready-for-agent via the
    machine and attaches the human's feedback to the audit trail."""
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:abc', 'internal', '', 'X', '', 'in-progress')"
    )
    conn.commit()
    conn.close()
    assert ticket.backlog("internal:abc", None, feedback="needs tests") == 0
    conn = _db(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    ev = conn.execute("SELECT payload FROM ticket_events WHERE ticket_id='internal:abc'").fetchone()
    conn.close()
    assert row == ("ready-for-agent", "needs tests")
    assert '"needs tests"' in ev[0]


def test_backlog_retry_rejects_illegal_from_status(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES ('internal:abc', 'internal', '', 'X', '', 'done')"
    )
    conn.commit()
    conn.close()
    assert ticket.backlog("internal:abc", None) == 1
    assert "done" in capsys.readouterr().err


# ── multi-source sync CLI (issue #90) ───────────────────────────────────────


def test_sync_provider_subset_passes_through(tmp_path, monkeypatch, capsys):
    _project(
        tmp_path,
        monkeypatch,
        "providers:\n  - internal\n  - github\n  - gitlab\n"
        "github:\n  repo: owner/repo\ngitlab:\n  repo: group/proj\n",
    )
    captured = {}

    def fake_sync_tickets(root, cfg, providers=None):
        captured["providers"] = providers
        return [ticketing.ProviderSyncResult("github", tickets=1)]

    monkeypatch.setattr(ticketing, "sync_tickets", fake_sync_tickets)
    assert ticket.sync(None, provider="github") == 0
    assert captured["providers"] == ["github"]
    assert "github: 1 ticket(s) synced" in capsys.readouterr().out


def test_sync_pulls_before_pushing_successful_providers(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "providers:\n  - github\ngithub:\n  repo: owner/repo\n")
    calls = []

    def fake_pull(root, cfg, providers=None):
        calls.append(("pull", providers))
        return [ticketing.ProviderSyncResult("github", tickets=1)]

    def fake_push(conn, *, providers, actor):
        calls.append(("push", providers, actor))
        return {"github": 1}

    monkeypatch.setattr(ticketing, "sync_tickets", fake_pull)
    monkeypatch.setattr(writeback, "mirror_tickets", fake_push)

    assert ticket.sync(None, provider="github") == 0
    assert calls[0] == ("pull", ["github"])
    assert calls[1][0:2] == ("push", {"github"})
    assert "1 tracker write(s) failed" in capsys.readouterr().out


def test_sync_does_not_push_a_provider_that_failed_to_pull(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, CONFIG)
    monkeypatch.setattr(
        ticketing,
        "sync_tickets",
        lambda root, cfg, providers=None: [
            ticketing.ProviderSyncResult("jira", error="jira unavailable")
        ],
    )
    monkeypatch.setattr(
        writeback,
        "mirror_tickets",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not push")),
    )

    assert ticket.sync(None) == 0
    assert "jira: jira unavailable" in capsys.readouterr().out


def test_sync_all_when_no_provider_flag(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, CONFIG)
    captured = {}

    def fake_sync_tickets(root, cfg, providers=None):
        captured["providers"] = providers
        return [ticketing.ProviderSyncResult("jira", tickets=2)]

    monkeypatch.setattr(ticketing, "sync_tickets", fake_sync_tickets)
    assert ticket.sync(None) == 0
    assert captured["providers"] is None  # no --provider → every enabled provider
    assert "jira: 2 ticket(s) synced" in capsys.readouterr().out


def test_sync_prints_skip_warning_distinctly(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "providers:\n  - gitlab\n")
    monkeypatch.setattr(
        ticketing,
        "sync_tickets",
        lambda root, cfg, providers=None: [
            ticketing.ProviderSyncResult("gitlab", warning="gitlab configured but origin is …")
        ],
    )
    assert ticket.sync(None) == 0  # a skipped provider is not a failure
    out = capsys.readouterr().out
    assert "skipped" in out and "gitlab configured but origin" in out


def test_backlog_does_not_write_to_origin_outside_sync(tmp_path, monkeypatch, capsys):
    """Requeue changes the local machine only; `ticket sync` owns tracker writes."""
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin, created_at, updated_at)"
        " VALUES ('github:owner/repo#12','github','owner/repo#12','T','needs-triage','idea',0,'github','2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.close()
    calls = []
    rc = 1
    err = "gh: not authenticated"

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = rc
            stdout = ""
            stderr = err

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ticket.backlog("github:owner/repo#12", None) == 0
    assert calls == []
    conn = _db(root)
    row = conn.execute("SELECT status FROM tickets WHERE id='github:owner/repo#12'").fetchone()
    conn.close()
    assert row[0] == "ready-for-agent"


def test_backlog_internal_ticket_no_writeback(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin, created_at, updated_at)"
        " VALUES ('internal:abc','internal','','T','needs-triage','idea',0,'internal','2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.close()
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ticket.backlog("internal:abc", None) == 0
    assert calls == []  # internal tickets have no tracker to mirror to
