import json
import sqlite3
from pathlib import Path

import pytest

from sssf import ticketing


def _write(root: Path, text: str) -> Path:
    path = root / ticketing.TICKETING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_missing_config_is_none(tmp_path):
    assert ticketing.load_config(tmp_path) is None


def test_commented_template_is_none(tmp_path):
    _write(tmp_path, "# providers:\n#   - internal\n")
    assert ticketing.load_config(tmp_path) is None


def test_multi_provider_config_parses(tmp_path):
    _write(
        tmp_path,
        (
            "providers:\n  - internal\n  - jira\n"
            "jira:\n  jql: 'project = ACME AND status in (Backlog, \"To Do\")'\n"
            "linear:\n  team: ENG\n  token_env: LINEAR_TOKEN\n  states: [Backlog]\n"
        ),
    )
    cfg = ticketing.load_config(tmp_path)
    assert cfg is not None
    assert cfg.providers == ["internal", "jira"]
    assert cfg.jira["jql"].startswith("project = ACME")


def test_invalid_yaml_raises(tmp_path):
    _write(tmp_path, "providers: [unclosed\n")
    with pytest.raises(RuntimeError, match="invalid"):
        ticketing.load_config(tmp_path)


def _cfg(tmp_path, providers=("jira",), jira=None, linear=None):
    return ticketing.TicketingConfig(
        providers=list(providers),
        jira={"jql": "project = ACME"} if jira is None else jira,
        linear={"team": "ENG", "token_env": "LINEAR_TOKEN", "states": ["Backlog"]}
        if linear is None
        else linear,
    )


def test_fetch_jira_parses_acli_output(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "key": "ACME-7",
                        "self": "https://acme.atlassian.net/rest/api/3/issue/ACME-7",
                        "fields": {
                            "summary": "Add dark mode",
                            "description": "The app needs a dark theme.",
                        },
                    }
                ]
            )
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/acli")
    records = ticketing.fetch_jira(_cfg(tmp_path))
    # acli >= 1.3: `issue list` was removed; work items live under
    # `jira workitem search` with explicit fields.
    assert calls[0] == [
        "acli",
        "jira",
        "workitem",
        "search",
        "--jql",
        "project = ACME",
        "--fields",
        "summary,description",
        "--limit",
        "100",
        "--json",
    ]
    assert records[0].external_id == "ACME-7"
    assert records[0].source_url == "https://acme.atlassian.net/browse/ACME-7"


def test_fetch_jira_internal_host_uses_configured_base_url(tmp_path, monkeypatch):
    """Internal Atlassian `self` hosts (jira-prod-us-*.prod.atl-paas.net) are
    not browsable — the configured base_url wins for source_url when present."""

    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "key": "ACME-7",
                        "self": "https://jira-prod-us-1.prod.atl-paas.net/rest/api/3/issue/ACME-7",
                        "fields": {"summary": "Add dark mode", "description": "x"},
                    }
                ]
            )
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/acli")
    cfg = _cfg(tmp_path)
    cfg.jira["base_url"] = "https://jira.corp.example.com"
    records = ticketing.fetch_jira(cfg)
    assert records[0].source_url == "https://jira.corp.example.com/browse/ACME-7"


def test_adf_to_markdown_renders_rich_document():
    """Jira Cloud API v3 returns descriptions as ADF JSON objects; the kanban
    needs readable markdown, not a Python repr of the dict."""
    adf = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Hello "},
                    {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": " and "},
                    {"type": "text", "text": "italic", "marks": [{"type": "em"}]},
                    {"type": "text", "text": " docs", "marks": [{"type": "link", "attrs": {"href": "https://ifood.atlassian.net/wiki"}}]},
                ],
            },
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "one"}]}]},
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "two"}]}]},
                ],
            },
            {"type": "codeBlock", "attrs": {"language": "python"}, "content": [{"type": "text", "text": "print(1)"}]},
        ],
    }
    md = ticketing.adf_to_markdown(adf)
    assert "# Title" in md
    assert "**bold**" in md
    assert "*italic*" in md
    assert "[ docs](https://ifood.atlassian.net/wiki)" in md
    assert "- one" in md and "- two" in md
    assert "```python" in md and "print(1)" in md


def test_adf_to_markdown_passes_plain_strings_through():
    assert ticketing.adf_to_markdown("plain text") == "plain text"


def test_fetch_jira_converts_adf_description(tmp_path, monkeypatch):
    """acli --json returns description as an ADF dict; fetch_jira must store
    readable markdown, not str(dict) — the gibberish seen in synced tickets."""
    adf = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Source"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Written by Alf"}]},
        ],
    }

    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "key": "ACME-8",
                        "self": "https://acme.atlassian.net/rest/api/3/issue/ACME-8",
                        "fields": {"summary": "ADF ticket", "description": adf},
                    }
                ]
            )
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/acli")
    records = ticketing.fetch_jira(_cfg(tmp_path))
    assert records[0].description == "## Source\n\nWritten by Alf"


def test_fetch_jira_missing_acli(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="acli"):
        ticketing.fetch_jira(_cfg(tmp_path))


def test_fetch_linear_parses_graphql(tmp_path, monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout):
        sent["body"] = json.loads(request.data)
        sent["auth"] = request.headers.get("Authorization")

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(
                    {
                        "data": {
                            "issues": {
                                "nodes": [
                                    {
                                        "id": "lin1",
                                        "identifier": "ENG-3",
                                        "title": "Linear ticket",
                                        "description": "Do the thing",
                                        "url": "https://linear.app/acme/issue/ENG-3",
                                        "state": {"name": "Backlog"},
                                    },
                                    {
                                        "id": "lin2",
                                        "identifier": "ENG-4",
                                        "title": "Done one",
                                        "description": "",
                                        "url": "https://linear.app/acme/issue/ENG-4",
                                        "state": {"name": "Done"},
                                    },
                                ]
                            }
                        }
                    }
                ).encode()

        return R()

    monkeypatch.setattr(ticketing.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ticketing.os, "environ", {"LINEAR_TOKEN": "tok"}, raising=False)
    records = ticketing.fetch_linear(_cfg(tmp_path))
    assert sent["auth"] == "Bearer tok"
    assert "team" in sent["body"]["query"] and "ENG" in sent["body"]["query"]
    assert [r.external_id for r in records] == ["ENG-3"]


def test_sync_upserts_without_duplicates(tmp_path):
    from sssf.adw_modules import tracer as tracer_mod

    db = tmp_path / "adws" / "adw_data" / "sssf.db"
    db.parent.mkdir(parents=True)
    tracer_mod.Tracer(db_path=db, events_jsonl=db.with_suffix(".jsonl")).conn.close()
    records = [ticketing.TicketRecord("jira", "ACME-1", "One", "d", "u")]
    assert ticketing.upsert_tickets(db, records) == 1
    assert ticketing.upsert_tickets(db, records) == 1  # same id -> update, no new row
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    assert n == 1
    conn.close()


# ── ticket machine core + idea tickets (issue #87) ─────────────────────────


def _machine_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    ticketing.ensure_schema(conn)
    return conn


def _insert_ticket(conn: sqlite3.Connection, ticket_id: str, title: str = "T", **cols) -> None:
    fields = {
        "provider": "internal",
        "external_id": "",
        "description": "",
        "status": "ready-for-agent",
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    fields.update(cols)
    keys = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    conn.execute(
        f"INSERT INTO tickets (id, title, {keys}) VALUES (?,?,{marks})",
        (ticket_id, title, *fields.values()),
    )


def test_ensure_schema_creates_machine_columns_and_events_table(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
    for col in ("kind", "tracked", "origin", "parent_id", "spec", "rejection_feedback"):
        assert col in cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ticket_events" in tables
    conn.close()


def test_migration_preserves_rows_and_maps_legacy_statuses(tmp_path):
    """Existing rows survive; legacy status values map onto the machine
    vocabulary (backlog -> ready-for-agent, starting/running -> in-progress,
    failed -> ready-for-agent, done stays done)."""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, provider TEXT NOT NULL,"
        " external_id TEXT, title TEXT NOT NULL, description TEXT,"
        " status TEXT NOT NULL DEFAULT 'backlog', prompt_file TEXT, adw_id TEXT,"
        " source_url TEXT, created_at TEXT, updated_at TEXT)"
    )
    legacy = [
        ("t-backlog", "internal", "backlog"),
        ("t-jira", "jira", "backlog"),
        ("t-starting", "internal", "starting"),
        ("t-running", "internal", "running"),
        ("t-failed", "internal", "failed"),
        ("t-done", "internal", "done"),
    ]
    for tid, prov, status in legacy:
        conn.execute(
            "INSERT INTO tickets (id, provider, external_id, title, status) VALUES (?,?,?,?,?)",
            (tid, prov, "", "T", status),
        )
    conn.commit()
    ticketing.ensure_schema(conn)
    rows = {
        r[0]: r
        for r in conn.execute(
            "SELECT id, status, kind, tracked, origin, parent_id, spec, rejection_feedback"
            " FROM tickets"
        )
    }
    conn.close()
    assert len(rows) == len(legacy)  # no rows lost
    assert rows["t-backlog"][1] == "ready-for-agent"
    assert rows["t-starting"][1] == "in-progress"
    assert rows["t-running"][1] == "in-progress"
    assert rows["t-failed"][1] == "ready-for-agent"
    assert rows["t-done"][1] == "done"
    # legacy rows are implementation-kind, tracked only when internal-born
    assert rows["t-backlog"][2] == "implementation" and rows["t-backlog"][3] == 1
    assert rows["t-jira"][2] == "implementation" and rows["t-jira"][3] == 0
    assert rows["t-backlog"][4] == "internal"
    assert rows["t-jira"][4] == "jira"
    # new columns default cleanly
    assert rows["t-backlog"][5] is None  # parent_id
    assert rows["t-backlog"][6] == ""  # spec
    assert rows["t-backlog"][7] == ""  # rejection_feedback


def test_migration_is_idempotent(tmp_path):
    """A second ensure_schema over an already-migrated db is a no-op: rows
    survive, the version stays current (legacy-status remap now runs once per
    version via migrations — nothing writes legacy statuses any more)."""
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1", status="ready-for-agent")
    conn.commit()
    ticketing.ensure_schema(conn)  # second pass over an already-migrated db
    row = conn.execute("SELECT status FROM tickets WHERE id='t1'").fetchone()
    assert row == ("ready-for-agent",)
    conn.close()


def test_transition_legal_edges_allowed(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    legal = [
        ("needs-triage", "ready-for-agent"),
        ("ready-for-agent", "in-progress"),
        ("in-progress", "ready-for-signoff"),
        ("in-progress", "ready-for-agent"),  # reviewer rejection, fix-forward
        ("ready-for-signoff", "ready-to-deploy"),
        ("ready-for-signoff", "ready-for-agent"),  # signoff rejection, fix-forward
        ("ready-to-deploy", "done"),
        ("ready-to-deploy", "blocked"),  # canary failure
        ("blocked", "ready-for-agent"),  # human unblocks -> rework
    ]
    for frm, to in legal:
        assert ticketing.can_transition(frm, to), f"{frm} -> {to} must be legal"
    conn.close()


def test_transition_rejects_every_illegal_edge(tmp_path):
    """The legal edges are exactly TRANSITIONS: every pair inside the table is
    allowed, every pair outside it (self-loops, jumps, from done) is rejected."""
    conn = _machine_conn(tmp_path / "m.db")
    for frm in ticketing.MACHINE_STATUSES:
        for to in ticketing.MACHINE_STATUSES:
            assert ticketing.can_transition(frm, to) == (to in ticketing.TRANSITIONS[frm]), (
                f"{frm} -> {to} must be {'' if to in ticketing.TRANSITIONS[frm] else 'il'}legal"
            )
    conn.close()


def test_full_machine_path_end_to_end(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1", status="needs-triage")
    conn.commit()
    path = [
        ("needs-triage", "ready-for-agent"),
        ("ready-for-agent", "in-progress"),
        ("in-progress", "ready-for-signoff"),
        ("ready-for-signoff", "ready-to-deploy"),
        ("ready-to-deploy", "done"),
    ]
    for _frm, to in path:
        ticketing.transition_ticket(conn, "t1", to, actor="alice")
    row = conn.execute("SELECT status FROM tickets WHERE id='t1'").fetchone()
    assert row == ("done",)
    conn.close()


def test_transition_rejects_illegal_edge_and_changes_nothing(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1", status="needs-triage")
    conn.commit()
    with pytest.raises(ValueError, match="illegal transition"):
        ticketing.transition_ticket(conn, "t1", "done", actor="alice")
    row = conn.execute("SELECT status FROM tickets WHERE id='t1'").fetchone()
    assert row == ("needs-triage",)  # unchanged
    conn.close()


def test_transition_unknown_ticket_raises(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    with pytest.raises(KeyError):
        ticketing.transition_ticket(conn, "nope", "ready-for-agent", actor="alice")
    conn.close()


def test_transition_writes_audit_event_with_actor_and_timestamp(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1", status="needs-triage")
    conn.commit()
    ticketing.transition_ticket(conn, "t1", "ready-for-agent", actor="alice")
    events = ticketing.ticket_events(conn, "t1")
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "transition"
    assert ev["actor"] == "alice"
    assert ev["payload"] == {"from": "needs-triage", "to": "ready-for-agent"}
    assert ev["created_at"]  # timestamp present
    conn.close()


def test_transition_with_feedback_stores_rejection_feedback_and_clears_on_done(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1", status="in-progress")
    conn.commit()
    ticketing.transition_ticket(
        conn, "t1", "ready-for-agent", actor="reviewer", feedback="needs tests"
    )
    row = conn.execute("SELECT rejection_feedback FROM tickets WHERE id='t1'").fetchone()
    assert row == ("needs tests",)
    # move it all the way to done: the stale feedback is cleared at release
    for to in ("in-progress", "ready-for-signoff", "ready-to-deploy", "done"):
        ticketing.transition_ticket(conn, "t1", to, actor="alice")
    row = conn.execute("SELECT rejection_feedback FROM tickets WHERE id='t1'").fetchone()
    assert row == ("",)
    conn.close()


def test_comment_and_label_write_events(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1")
    conn.commit()
    ticketing.comment_ticket(conn, "t1", "please rebase", actor="alice")
    ticketing.label_ticket(conn, "t1", "good-first-issue", actor="bob")
    ticketing.label_ticket(conn, "t1", "good-first-issue", add=False, actor="bob")
    events = ticketing.ticket_events(conn, "t1")
    assert [e["event_type"] for e in events] == ["comment", "label", "label"]
    assert events[0]["payload"] == {"text": "please rebase"}
    assert events[1]["payload"] == {"label": "good-first-issue", "action": "add"}
    assert events[2]["payload"] == {"label": "good-first-issue", "action": "remove"}
    assert events[0]["actor"] == "alice"
    conn.close()


def test_backlog_returns_exactly_ready_for_agent(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "ready1", status="ready-for-agent", created_at="2026-09-01T00:00:00+00:00")
    _insert_ticket(conn, "idea1", status="needs-triage", created_at="2026-09-02T00:00:00+00:00")
    _insert_ticket(conn, "ready2", status="ready-for-agent", created_at="2026-09-03T00:00:00+00:00")
    _insert_ticket(conn, "blocked1", status="blocked", created_at="2026-09-04T00:00:00+00:00")
    conn.commit()
    ids = [r[0] for r in ticketing.backlog_tickets(conn)]
    assert ids == ["ready1", "ready2"]  # exactly the ready-for-agent queue, oldest first
    conn.close()


def test_create_idea_ticket_shell(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    ticket_id = ticketing.create_idea_ticket(conn, "Ship dark mode", actor="alice")
    assert ticket_id.startswith("internal:")
    row = conn.execute(
        "SELECT title, description, status, kind, tracked, origin, spec, parent_id"
        " FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    assert row == ("Ship dark mode", "", "needs-triage", "idea", 1, "internal", "", None)
    events = ticketing.ticket_events(conn, ticket_id)
    assert events[0]["event_type"] == "created" and events[0]["actor"] == "alice"
    conn.close()


def test_upsert_sync_rows_untracked_needs_triage_and_permanent(tmp_path):
    """Synced tickets land needs-triage + untracked; a re-sync updates
    title/description but NEVER rewrites kind/tracked/origin/status."""
    db = tmp_path / "adws" / "adw_data" / "sssf.db"
    db.parent.mkdir(parents=True)
    rec = ticketing.TicketRecord("jira", "ACME-9", "Old title", "d", "u")
    ticketing.upsert_tickets(db, [rec])
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT status, kind, tracked, origin FROM tickets WHERE id='jira:ACME-9'"
    ).fetchone()
    conn.close()
    assert row == ("needs-triage", "idea", 0, "jira")

    rec2 = ticketing.TicketRecord("jira", "ACME-9", "New title", "d2", "u")
    ticketing.upsert_tickets(db, [rec2])
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT title, status, kind, tracked, origin FROM tickets WHERE id='jira:ACME-9'"
    ).fetchone()
    conn.close()
    assert row[0] == "New title"  # content updated
    assert row[1:] == ("needs-triage", "idea", 0, "jira")  # machine fields permanent
