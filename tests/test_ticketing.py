import json
import sqlite3
import subprocess
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


# ── finish_implement_run (#92): the implement flow's terminal machine step ──


def _impl_db(tmp_path, *, adw_name="adw_implement", session_status="success",
             ticket_status="in-progress"):
    """A db shaped like a finished implement run: one ticket claimed by the
    flow (adw_id linked, in-progress) and its session row."""
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "t1", status=ticket_status, adw_id="run1")
    conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES ('run1', ?, ?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
        (adw_name, session_status),
    )
    conn.commit()
    return conn


def test_finish_implement_success_moves_to_signoff(tmp_path):
    conn = _impl_db(tmp_path, session_status="success")
    outcome = ticketing.finish_implement_run(conn, "run1")
    assert outcome == "signoff"
    row = conn.execute("SELECT status, adw_id FROM tickets WHERE id='t1'").fetchone()
    assert row == ("ready-for-signoff", "run1")  # link preserved for the trace
    # the transition is audited like any machine write
    events = ticketing.ticket_events(conn, "t1")
    assert events[-1]["event_type"] == "transition"
    assert events[-1]["payload"]["from"] == "in-progress"
    assert events[-1]["payload"]["to"] == "ready-for-signoff"
    conn.close()


def test_finish_implement_failure_requeues_with_reviewer_feedback(tmp_path):
    conn = _impl_db(tmp_path, session_status="fail")
    conn.execute(
        "INSERT INTO envelopes (envelope_id, adw_id, agent, output_type, payload_json,"
        " valid, attempt, created_at) VALUES"
        " ('env1', 'run1', 'reviewer', 'ReviewOutput',"
        " '{\"approved\": false, \"blocking\": [\"fix the login redirect\", \"drop the debug print\"]}',"
        " 1, 1, '2026-09-01T01:00:00+00:00')"
    )
    conn.commit()
    outcome = ticketing.finish_implement_run(conn, "run1")
    assert outcome == "requeued"
    row = conn.execute("SELECT status, rejection_feedback FROM tickets WHERE id='t1'").fetchone()
    assert row[0] == "ready-for-agent"
    assert "fix the login redirect" in row[1]
    assert "drop the debug print" in row[1]
    # the requeue is audited with the feedback in the payload
    events = ticketing.ticket_events(conn, "t1")
    assert events[-1]["payload"]["to"] == "ready-for-agent"
    assert "fix the login redirect" in events[-1]["payload"]["feedback"]
    conn.close()


def test_finish_implement_failure_feedback_falls_back_to_error_event(tmp_path):
    conn = _impl_db(tmp_path, session_status="fail")
    conn.execute(
        "INSERT INTO events (event_id, adw_id, type, name, payload_json, started_at)"
        " VALUES ('e1', 'run1', 'error', 'not_accepted',"
        " '{\"reason\": \"quality gates never came back clean after 3 fix attempt(s)\"}',"
        " '2026-09-01T01:00:00+00:00')"
    )
    conn.commit()
    assert ticketing.finish_implement_run(conn, "run1") == "requeued"
    row = conn.execute("SELECT rejection_feedback FROM tickets WHERE id='t1'").fetchone()
    assert "quality gates never came back clean" in row[0]
    conn.close()


def test_finish_implement_missing_session_counts_as_failure(tmp_path):
    """A run that never wrote a session row is a failed run — same rule as
    record_never_started — so its ticket requeues instead of rotting in
    in-progress."""
    conn = _impl_db(tmp_path, session_status="success")
    conn.execute("DELETE FROM sessions WHERE adw_id='run1'")
    conn.commit()
    assert ticketing.finish_implement_run(conn, "run1") == "requeued"
    row = conn.execute("SELECT status FROM tickets WHERE id='t1'").fetchone()
    assert row == ("ready-for-agent",)
    conn.close()


def test_finish_implement_never_touches_legacy_runs(tmp_path):
    """Legacy adw_simple_sdlc runs settle by hand (`sssf ticket backlog`) —
    the flow finish must leave them alone."""
    conn = _impl_db(tmp_path, adw_name="adw_simple_sdlc", session_status="success")
    assert ticketing.finish_implement_run(conn, "run1") is None
    row = conn.execute("SELECT status FROM tickets WHERE id='t1'").fetchone()
    assert row == ("in-progress",)  # untouched
    conn.close()


def test_finish_implement_never_yanks_a_ticket_it_does_not_own(tmp_path):
    """Only a ticket whose adw_id links THIS run and whose machine status is
    in-progress may be settled — a ticket the operator already requeued or
    signed off is never yanked."""
    # operator requeued it while the run was still finishing
    conn = _impl_db(tmp_path, session_status="success")
    conn.execute("UPDATE tickets SET status='ready-for-agent' WHERE id='t1'")
    conn.commit()
    assert ticketing.finish_implement_run(conn, "run1") is None
    # a different ticket's run must not move this ticket
    other = tmp_path / "other"
    other.mkdir()
    conn2 = _impl_db(other, session_status="success")
    conn2.execute("UPDATE tickets SET adw_id='other-run' WHERE id='t1'")
    conn2.commit()
    assert ticketing.finish_implement_run(conn2, "run1") is None
    assert conn2.execute(
        "SELECT status FROM tickets WHERE id='t1'"
    ).fetchone() == ("in-progress",)
    conn.close()
    conn2.close()


def test_finish_implement_unknown_run_is_a_noop(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    assert ticketing.finish_implement_run(conn, "no-such-run") is None


# ── plan flow: guard + breakdown parser + the settle (issue #91) ────────────


def _plan_conn(root: Path) -> sqlite3.Connection:
    db = root / "adws" / "data" / "sssf.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return _machine_conn(db)


def _plan_session(
    conn: sqlite3.Connection, adw_id: str, status: str = "success", adw_name: str = "adw_plan"
) -> None:
    conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES (?,?,?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
        (adw_id, adw_name, status),
    )


def _plan_artifacts(root: Path, adw_id: str, *, spec: str = "# Dark mode\n\nA spec.\n", tickets: str = "") -> None:
    """Write the plan run's artifacts exactly where the ADW lands them."""
    specs = root / "adws" / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / f"{adw_id}_spec-dark-mode.md").write_text(spec)
    (specs / f"{adw_id}_tickets-dark-mode.md").write_text(
        tickets
        or (
            "# Dark mode tickets\n\n"
            "## Toggle component\n\nA `## `-heading slice.\n\n- bullet\n\n"
            "## Persist the choice\n\nSecond slice, with a nested `### Detail` heading kept in the body.\n"
        )
    )


def test_plan_guard_accepts_a_fresh_idea_ticket(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    ticket_id = ticketing.create_idea_ticket(conn, "Dark mode")
    assert ticketing.plan_guard(conn, ticket_id) is None
    conn.close()


def test_plan_guard_refuses_missing_ticket(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    assert "no ticket internal:nope" in ticketing.plan_guard(conn, "internal:nope")
    conn.close()


def test_plan_guard_refuses_implementation_tickets(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _insert_ticket(conn, "internal:impl", kind="implementation", status="ready-for-agent")
    err = ticketing.plan_guard(conn, "internal:impl")
    assert "implementation ticket" in err
    assert ticketing.plan_guard(conn, "internal:impl", revise=True) == err  # terminal
    conn.close()


def test_plan_guard_plan_once_refuses_planned_idea_without_revise(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    parent = ticketing.create_idea_ticket(conn, "Dark mode")
    _insert_ticket(conn, "internal:child", kind="implementation", parent_id=parent)
    err = ticketing.plan_guard(conn, parent)
    assert "already planned" in err and "--revise" in err
    assert ticketing.plan_guard(conn, parent, revise=True) is None
    conn.close()


def test_plan_guard_plan_once_also_sees_a_spec(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    parent = ticketing.create_idea_ticket(conn, "Dark mode")
    conn.execute("UPDATE tickets SET spec='adws/specs/x.md' WHERE id=?", (parent,))
    conn.commit()
    assert "already planned" in ticketing.plan_guard(conn, parent)
    conn.close()


def test_parse_ticket_breakdown_splits_h2_slices():
    text = (
        "# Dark mode tickets\n\n"
        "intro line (dropped)\n\n"
        "## Toggle component\n\nA slice.\n\n- bullet\n\n"
        "## Persist the choice\n\nSecond slice, with a `### Detail` heading kept.\n"
    )
    slices = ticketing.parse_ticket_breakdown(text)
    assert [t for t, _ in slices] == ["Toggle component", "Persist the choice"]
    assert "A slice." in slices[0][1] and "- bullet" in slices[0][1]
    assert "### Detail" in slices[1][1]  # nested headings stay in the body
    assert "intro line" not in slices[0][1] and "intro line" not in slices[1][1]


def test_parse_ticket_breakdown_empty_without_h2():
    assert ticketing.parse_ticket_breakdown("no slices here\n") == []


def test_spec_title_extracts_first_h1():
    assert ticketing._spec_title("# Dark mode\n\nbody\n") == "Dark mode"
    assert ticketing._spec_title("## Not an h1\n") == ""
    assert ticketing._spec_title("") == ""


def test_finish_plan_run_ignores_non_plan_sessions(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _plan_session(conn, "r1", adw_name="adw_implement")
    assert ticketing.finish_plan_run(tmp_path, conn, "r1") is None
    conn.close()


def test_finish_plan_run_ignores_failed_plan_runs(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    parent = ticketing.create_idea_ticket(conn, "Dark mode")
    _plan_session(conn, "r1", status="fail")
    conn.execute("UPDATE tickets SET adw_id='r1' WHERE id=?", (parent,))
    conn.commit()
    assert ticketing.finish_plan_run(tmp_path, conn, "r1") is None
    row = conn.execute("SELECT spec FROM tickets WHERE id=?", (parent,)).fetchone()
    assert row == ("",)  # untouched
    conn.close()


def test_finish_plan_run_ignores_runs_with_no_session(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    assert ticketing.finish_plan_run(tmp_path, conn, "ghost") is None
    conn.close()


def test_finish_plan_run_transforms_linked_idea_ticket(tmp_path):
    root = tmp_path / "proj"
    conn = _plan_conn(root)
    parent = ticketing.create_idea_ticket(conn, "Dark mode")
    _plan_session(conn, "r1")
    conn.execute("UPDATE tickets SET adw_id='r1' WHERE id=?", (parent,))
    conn.commit()
    _plan_artifacts(root, "r1")

    landed = ticketing.finish_plan_run(root, conn, "r1", actor="alice")
    assert landed == parent
    row = conn.execute(
        "SELECT spec, status, adw_id FROM tickets WHERE id=?", (parent,)
    ).fetchone()
    assert row[0] == "adws/specs/r1_spec-dark-mode.md"  # committed relative path
    assert row[1] == "needs-triage"  # the parent stays out of the backlog
    assert row[2] == "r1"
    children = conn.execute(
        "SELECT id, title, status, kind, parent_id, spec FROM tickets WHERE parent_id=?",
        (parent,),
    ).fetchall()
    assert [c[1] for c in children] == ["Toggle component", "Persist the choice"]
    assert all(c[2] == "ready-for-agent" for c in children)
    assert all(c[3] == "implementation" for c in children)
    assert all(c[4] == parent for c in children)
    assert all(c[5] == "adws/specs/r1_spec-dark-mode.md" for c in children)
    events = ticketing.ticket_events(conn, parent)
    planned = [e for e in events if e["event_type"] == "planned"]
    assert len(planned) == 1
    assert planned[0]["actor"] == "alice"
    assert planned[0]["payload"]["children"] == [c[0] for c in children]
    conn.close()


def test_finish_plan_run_no_args_creates_the_idea_ticket(tmp_path):
    """No linked ticket → the no-args mode: the idea ticket is created from
    the spec's title and the transform continues in the same pass."""
    root = tmp_path / "proj"
    conn = _plan_conn(root)
    _plan_session(conn, "r1")
    _plan_artifacts(root, "r1")

    landed = ticketing.finish_plan_run(root, conn, "r1")
    row = conn.execute(
        "SELECT title, kind, status, spec, adw_id FROM tickets WHERE id=?", (landed,)
    ).fetchone()
    assert row == (
        "Dark mode", "idea", "needs-triage", "adws/specs/r1_spec-dark-mode.md", "r1",
    )
    children = conn.execute(
        "SELECT title FROM tickets WHERE parent_id=?", (landed,)
    ).fetchall()
    assert [c[0] for c in children] == ["Toggle component", "Persist the choice"]
    conn.close()


def test_finish_plan_run_raises_when_breakdown_has_no_h2(tmp_path):
    root = tmp_path / "proj"
    conn = _plan_conn(root)
    _plan_session(conn, "r1")
    _plan_artifacts(root, "r1", tickets="# Dark mode tickets\n\nNo slices here.\n")
    with pytest.raises(ValueError, match="no `## ` headings"):
        ticketing.finish_plan_run(root, conn, "r1")
    conn.close()


def test_finish_plan_run_raises_when_artifacts_missing(tmp_path):
    conn = _machine_conn(tmp_path / "m.db")
    _plan_session(conn, "r1")
    with pytest.raises(ValueError, match="no spec/tickets"):
        ticketing.finish_plan_run(tmp_path, conn, "r1")
    conn.close()


def test_finish_plan_run_revise_stacks_new_children_and_supersedes_stale(tmp_path):
    """--revise relaxes the dispatch guard; the settle stacks the new slices,
    comments stale unclaimed children, and never touches claimed ones."""
    root = tmp_path / "proj"
    conn = _plan_conn(root)
    parent = ticketing.create_idea_ticket(conn, "Dark mode")
    _insert_ticket(conn, "internal:old1", title="old1", kind="implementation",
                   parent_id=parent, status="ready-for-agent")
    _insert_ticket(conn, "internal:old2", title="old2", kind="implementation",
                   parent_id=parent, status="in-progress")
    _plan_session(conn, "r2")
    conn.execute("UPDATE tickets SET adw_id='r2' WHERE id=?", (parent,))
    conn.commit()
    _plan_artifacts(root, "r2")

    landed = ticketing.finish_plan_run(root, conn, "r2")
    assert landed == parent
    children = conn.execute(
        "SELECT id, title FROM tickets WHERE parent_id=? ORDER BY rowid", (parent,)
    ).fetchall()
    assert [c[1] for c in children] == [
        "old1", "old2", "Toggle component", "Persist the choice",
    ]
    # the stale unclaimed child is commented (never deleted, never parked)
    old1_events = ticketing.ticket_events(conn, "internal:old1")
    assert any("superseded by re-plan" in e["payload"].get("text", "") for e in old1_events)
    # the claimed child is untouched
    old2_events = ticketing.ticket_events(conn, "internal:old2")
    assert not any("superseded" in e["payload"].get("text", "") for e in old2_events)
    conn.close()


def test_finish_plan_run_reads_artifacts_from_the_sandbox_worktree(tmp_path):
    """A sandboxed run's artifacts live in the per-run worktree, not the
    project tree — the settle must read them from .worktrees/<adw_id>."""
    root = tmp_path / "proj"
    conn = _plan_conn(root)
    conn.execute(
        "INSERT INTO sandbox_run (adw_id, container) VALUES ('r1', 'sssf-r1')"
    )
    parent = ticketing.create_idea_ticket(conn, "Dark mode")
    _plan_session(conn, "r1")
    conn.execute("UPDATE tickets SET adw_id='r1' WHERE id=?", (parent,))
    conn.commit()
    _plan_artifacts(root / ".worktrees" / "r1", "r1")

    landed = ticketing.finish_plan_run(root, conn, "r1")
    assert landed == parent
    children = conn.execute(
        "SELECT title FROM tickets WHERE parent_id=?", (parent,)
    ).fetchall()
    assert [c[0] for c in children] == ["Toggle component", "Persist the choice"]
    conn.close()


# ── multi-source sync: origin resolution (issue #90) ───────────────────────


def _git_repo(root: Path, origin_url: str | None = None) -> Path:
    """A tmp git repo with an optional origin remote (sync tests shell git)."""
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if origin_url is not None:
        subprocess.run(["git", "remote", "add", "origin", origin_url], cwd=repo, check=True)
    return repo


def test_detect_origin_parses_ssh_form(tmp_path):
    repo = _git_repo(tmp_path, "git@github.com:owner/repo.git")
    assert ticketing.detect_origin(repo) == ("github.com", "owner/repo")


def test_detect_origin_parses_https_form(tmp_path):
    repo = _git_repo(tmp_path, "https://github.com/owner/repo.git")
    assert ticketing.detect_origin(repo) == ("github.com", "owner/repo")


def test_detect_origin_parses_ssh_url_form(tmp_path):
    repo = _git_repo(tmp_path, "ssh://git@gitlab.com/group/project.git")
    assert ticketing.detect_origin(repo) == ("gitlab.com", "group/project")


def test_detect_origin_missing_is_none(tmp_path):
    repo = _git_repo(tmp_path)  # no origin remote
    assert ticketing.detect_origin(repo) is None


def test_origin_repo_override_wins_without_host_matching(tmp_path):
    cfg = _cfg(tmp_path, providers=("github",))
    cfg.github = {"repo": "acme/override"}
    repo, warning = ticketing.github_repo(cfg, ("gitlab.com", "other/repo"))
    assert repo == "acme/override"
    assert warning is None


def test_origin_repo_cloud_host_matches(tmp_path):
    cfg = _cfg(tmp_path, providers=("github",))
    repo, warning = ticketing.github_repo(cfg, ("github.com", "acme/app"))
    assert repo == "acme/app"
    assert warning is None


def test_origin_repo_cloud_mismatch_warns_self_hosted(tmp_path):
    cfg = _cfg(tmp_path, providers=("gitlab",))
    repo, warning = ticketing.gitlab_repo(cfg, ("git.ifoodcorp.com.br", "acme/app"))
    assert repo is None
    assert warning is not None and "self_hosted" in warning and "custom_url" in warning


def test_origin_repo_self_hosted_custom_url_match(tmp_path):
    cfg = _cfg(tmp_path, providers=("github",))
    cfg.github = {"self_hosted": True, "custom_url": "https://github.company.com"}
    repo, warning = ticketing.github_repo(cfg, ("github.company.com", "acme/app"))
    assert repo == "acme/app"
    assert warning is None


def test_origin_repo_self_hosted_custom_url_mismatch_warns(tmp_path):
    cfg = _cfg(tmp_path, providers=("github",))
    cfg.github = {"self_hosted": True, "custom_url": "https://github.company.com"}
    repo, warning = ticketing.github_repo(cfg, ("github.com", "acme/app"))
    assert repo is None
    assert warning is not None and "fix custom_url" in warning


def test_origin_repo_self_hosted_without_custom_url_accepts_any_host(tmp_path):
    cfg = _cfg(tmp_path, providers=("github",))
    cfg.github = {"self_hosted": True}
    repo, warning = ticketing.github_repo(cfg, ("forge.example.net", "acme/app"))
    assert repo == "acme/app"
    assert warning is None


def test_origin_repo_no_origin_warns_repo_override(tmp_path):
    cfg = _cfg(tmp_path, providers=("github",))
    repo, warning = ticketing.github_repo(cfg, None)
    assert repo is None
    assert warning is not None and "repo:" in warning


def test_fetch_github_parses_gh_output(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "number": 12,
                        "title": "Dark mode",
                        "body": "The app needs a dark theme.",
                        "url": "https://github.com/owner/repo/issues/12",
                        "state": "open",
                        "labels": [{"name": "bug"}],
                    }
                ]
            )
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")
    records = ticketing.fetch_github(_cfg(tmp_path, providers=("github",)), "owner/repo")
    assert calls[0] == [
        "gh",
        "issue",
        "list",
        "--repo",
        "owner/repo",
        "--state",
        "open",
        "--json",
        "number,title,body,url,state,labels",
        "--limit",
        "100",
    ]
    assert records[0].external_id == "owner/repo#12"
    assert records[0].provider == "github"
    assert records[0].source_url == "https://github.com/owner/repo/issues/12"


def test_fetch_github_repeats_label_flags(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")
    cfg = _cfg(tmp_path, providers=("github",))
    cfg.github = {"labels": ["bug", "p1"]}
    ticketing.fetch_github(cfg, "owner/repo")
    assert calls[0] == [
        "gh",
        "issue",
        "list",
        "--repo",
        "owner/repo",
        "--state",
        "open",
        "--label",
        "bug",
        "--label",
        "p1",
        "--json",
        "number,title,body,url,state,labels",
        "--limit",
        "100",
    ]


def test_fetch_github_missing_gh_raises_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="install gh"):
        ticketing.fetch_github(_cfg(tmp_path, providers=("github",)), "owner/repo")


def test_fetch_github_nonzero_exit_raises(tmp_path, monkeypatch):
    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 1
            stdout = ""
            stderr = "gh: not authenticated"

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/gh")
    with pytest.raises(RuntimeError, match="gh failed"):
        ticketing.fetch_github(_cfg(tmp_path, providers=("github",)), "owner/repo")


def test_fetch_gitlab_parses_glab_output(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "iid": 5,
                        "title": "Dark mode",
                        "description": "The app needs a dark theme.",
                        "web_url": "https://gitlab.com/group/proj/-/issues/5",
                        "labels": ["bug"],
                    }
                ]
            )
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/glab")
    records = ticketing.fetch_gitlab(_cfg(tmp_path, providers=("gitlab",)), "group/proj")
    assert calls[0] == [
        "glab",
        "issue",
        "list",
        "--repo",
        "group/proj",
        "--state",
        "opened",
        "--output",
        "json",
    ]
    assert records[0].external_id == "group/proj#5"
    assert records[0].provider == "gitlab"
    assert records[0].source_url == "https://gitlab.com/group/proj/-/issues/5"


def test_fetch_gitlab_label_flags_and_missing_binary(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/glab")
    cfg = _cfg(tmp_path, providers=("gitlab",))
    cfg.gitlab = {"labels": ["bug"]}
    ticketing.fetch_gitlab(cfg, "group/proj")
    assert calls[0] == [
        "glab",
        "issue",
        "list",
        "--repo",
        "group/proj",
        "--state",
        "opened",
        "--label",
        "bug",
        "--output",
        "json",
    ]

    monkeypatch.setattr(ticketing.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="install glab"):
        ticketing.fetch_gitlab(cfg, "group/proj")


def _sync_cfg(tmp_path, providers, github=None, gitlab=None):
    cfg = _cfg(tmp_path, providers=providers)
    cfg.github = github or {}
    cfg.gitlab = gitlab or {}
    return cfg


def test_config_parses_github_gitlab_blocks(tmp_path):
    _write(
        tmp_path,
        (
            "providers:\n  - internal\n  - github\n  - gitlab\n"
            "github:\n  repo: acme/override\n  labels: [bug]\n"
            "gitlab:\n  self_hosted: true\n  custom_url: https://git.ifoodcorp.com.br\n"
        ),
    )
    cfg = ticketing.load_config(tmp_path)
    assert cfg is not None
    assert cfg.github["repo"] == "acme/override"
    assert cfg.gitlab["self_hosted"] is True
    assert cfg.gitlab["custom_url"] == "https://git.ifoodcorp.com.br"


def test_sync_tickets_upserts_all_four_origins(tmp_path, monkeypatch):
    root = _git_repo(tmp_path, "git@github.com:owner/repo.git")
    cfg = _sync_cfg(tmp_path, ("internal", "jira", "github", "gitlab"))
    cfg.github = {"repo": "owner/repo"}  # override — no host matching
    cfg.gitlab = {"repo": "group/proj"}

    def fake_run(args, capture_output, text, timeout):
        binary = args[0]

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if binary == "gh":
            R.stdout = json.dumps(
                [{"number": 12, "title": "Dark mode", "body": "b", "url": "https://github.com/owner/repo/issues/12", "state": "open", "labels": []}]
            )
        elif binary == "glab":
            R.stdout = json.dumps(
                [{"iid": 5, "title": "Light mode", "description": "d", "web_url": "https://gitlab.com/group/proj/-/issues/5"}]
            )
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    results = ticketing.sync_tickets(root, cfg)
    by_provider = {r.provider: r for r in results}
    assert by_provider["github"].tickets == 1
    assert by_provider["gitlab"].tickets == 1
    assert "internal" not in by_provider  # nothing to fetch
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    rows = conn.execute(
        "SELECT id, status, kind, tracked, origin FROM tickets"
        " WHERE origin IN ('github','gitlab') ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [
        ("github:owner/repo#12", "needs-triage", "idea", 0, "github"),
        ("gitlab:group/proj#5", "needs-triage", "idea", 0, "gitlab"),
    ]


def test_sync_tickets_origin_mismatch_skips_with_warning(tmp_path, monkeypatch):
    """A cloud gitlab provider with a non-standard origin host is skipped
    with a warning — never fetched against the wrong forge."""
    root = _git_repo(tmp_path, "git@git.ifoodcorp.com.br:acme/app.git")
    cfg = _sync_cfg(tmp_path, ("gitlab",))
    shells = []

    def fake_run(args, capture_output, text, timeout):
        if args[0] == "git":
            # origin resolution runs real git — hand it the configured origin
            class G:
                returncode = 0
                stdout = "git@git.ifoodcorp.com.br:acme/app.git"
                stderr = ""

            return G()
        shells.append(args[0])
        raise AssertionError(f"must not shell {args[0]} when the origin mismatches")

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    results = ticketing.sync_tickets(root, cfg)
    (result,) = results
    assert result.provider == "gitlab"
    assert result.tickets == 0
    assert result.error is None
    assert result.warning is not None and "self_hosted" in result.warning
    assert shells == []


def test_sync_tickets_provider_subset_fetches_only_that_provider(tmp_path, monkeypatch):
    root = _git_repo(tmp_path, "git@github.com:owner/repo.git")
    cfg = _sync_cfg(tmp_path, ("github", "gitlab"))
    cfg.github = {"repo": "owner/repo"}
    cfg.gitlab = {"repo": "group/proj"}
    seen = []

    def fake_run(args, capture_output, text, timeout):
        seen.append(args[0])

        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    results = ticketing.sync_tickets(root, cfg, providers=["github"])
    assert [r.provider for r in results] == ["github"]
    assert seen == ["git", "gh"]  # origin resolution shells git, then gh only


def test_sync_tickets_failing_provider_does_not_stop_others(tmp_path, monkeypatch):
    root = _git_repo(tmp_path, "git@github.com:owner/repo.git")
    cfg = _sync_cfg(tmp_path, ("jira", "github"))
    cfg.github = {"repo": "owner/repo"}

    def fake_run(args, capture_output, text, timeout):
        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        if args[0] == "gh":
            R.returncode = 1
            R.stderr = "gh: not authenticated"
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    # jira syncs fine while github errors — the failure never blocks the others.
    results = ticketing.sync_tickets(root, cfg)
    by_provider = {r.provider: r for r in results}
    assert by_provider["jira"].tickets == 0 and by_provider["jira"].error is None
    assert by_provider["github"].error is not None and "gh failed" in by_provider["github"].error
