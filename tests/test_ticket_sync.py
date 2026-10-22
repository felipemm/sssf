import sqlite3

from sssf import db_schema, ticketing, writeback


def _connection(tmp_path):
    conn = sqlite3.connect(tmp_path / "sssf.db")
    ticketing.ensure_schema(conn)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin) "
        "VALUES ('github:owner/repo#1', 'github', 'owner/repo#1', 'Ticket', 'needs-triage', "
        "'idea', 0, 'github')"
    )
    conn.commit()
    return conn


def _recording_writers(monkeypatch):
    calls = []

    def writer(operation):
        return lambda repo, number, *args: calls.append((operation, repo, number, *args))

    monkeypatch.setattr(
        writeback,
        "ORIGIN_WRITERS",
        {"github": (writer("state"), writer("comment"), writer("label"))},
    )
    return calls


def test_sync_ensures_only_control_labels_and_preserves_foreign_labels(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    calls = []
    tracker_labels = {"needs-triage", "ready-for-agent", "blocked", "customer:urgent"}

    def label_writer(repo, number, label, add):
        calls.append((repo, number, label, add))
        if add:
            tracker_labels.add(label)
        else:
            tracker_labels.discard(label)

    monkeypatch.setattr(
        writeback,
        "ORIGIN_WRITERS",
        {"github": (lambda *args: None, lambda *args: None, label_writer)},
    )

    assert writeback.mirror_tickets(conn, providers={"github"}) == {}
    first_mirror = set(tracker_labels)
    assert writeback.mirror_tickets(conn, providers={"github"}) == {}

    assert first_mirror == {"needs-triage", "customer:urgent"}
    assert tracker_labels == first_mirror
    assert calls == [
        ("owner/repo", "1", "ready-for-agent", False),
        ("owner/repo", "1", "blocked", False),
        ("owner/repo", "1", "needs-triage", True),
    ] * 2
    assert "customer:urgent" not in {call[2] for call in calls}
    mirrored_at = conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0]
    assert mirrored_at
    conn.close()


def test_rejection_feedback_is_pushed_once_after_a_new_transition(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    conn.execute(
        "UPDATE tickets SET status='in-progress', mirrored_at='2026-09-01T00:00:00+00:00' "
        "WHERE id='github:owner/repo#1'"
    )
    ticketing.transition_ticket(
        conn,
        "github:owner/repo#1",
        ticketing.STATUS_READY,
        feedback="The test is missing the retry case.",
    )
    calls = _recording_writers(monkeypatch)

    failures = writeback.mirror_tickets(conn, providers={"github"})

    assert failures == {}
    assert ("comment", "owner/repo", "1", "The test is missing the retry case.") in calls
    assert conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0] != "2026-09-01T00:00:00+00:00"
    writeback.mirror_tickets(conn, providers={"github"})
    assert sum(call[0] == "comment" for call in calls) == 1
    conn.close()


def test_done_ticket_closes_and_comments_once_behind_the_watermark(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    conn.execute(
        "UPDATE tickets SET status='ready-to-deploy', mirrored_at='2026-09-01T00:00:00+00:00' "
        "WHERE id='github:owner/repo#1'"
    )
    ticketing.transition_ticket(conn, "github:owner/repo#1", ticketing.STATUS_DONE)
    calls = _recording_writers(monkeypatch)

    assert writeback.mirror_tickets(conn, providers={"github"}) == {}
    assert writeback.mirror_tickets(conn, providers={"github"}) == {}

    assert calls.count(("state", "owner/repo", "1", "closed")) == 1
    assert calls.count(("comment", "owner/repo", "1", writeback.CLOSING_COMMENT)) == 1
    conn.close()


def test_failed_comment_is_recorded_and_does_not_advance_the_watermark(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    conn.execute("UPDATE tickets SET status='done' WHERE id='github:owner/repo#1'")
    conn.commit()
    calls = []

    def succeeds(operation):
        return lambda repo, number, *args: calls.append((operation, repo, number, *args))

    def fail_comment(repo, number, text):
        calls.append(("comment", repo, number, text))
        raise RuntimeError("tracker unavailable")

    monkeypatch.setattr(
        writeback,
        "ORIGIN_WRITERS",
        {"github": (succeeds("state"), fail_comment, succeeds("label"))},
    )

    failures = writeback.mirror_tickets(conn, providers={"github"})

    assert failures == {"github": 1}
    assert conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0] is None
    assert ticketing.ticket_events(conn, "github:owner/repo#1")[-1]["event_type"] == "writeback_failed"
    assert ("state", "owner/repo", "1", "closed") in calls
    conn.close()


def test_migration_7_adds_mirrored_at_to_schema_v6_ticket_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "schema-v6.db")
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT, "
        "title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'needs-triage', "
        "prompt_file TEXT, adw_id TEXT, source_url TEXT, context TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL DEFAULT 'implementation', tracked INTEGER NOT NULL DEFAULT 1, "
        "origin TEXT NOT NULL DEFAULT 'internal', parent_id TEXT, spec TEXT NOT NULL DEFAULT '', "
        "rejection_feedback TEXT NOT NULL DEFAULT '', created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, tracked, origin) "
        "VALUES ('jira:ACME-1', 'jira', 'ACME-1', 'Ticket', 'ready-for-agent', 0, 'jira')"
    )
    conn.execute("PRAGMA user_version = 6")

    db_schema.apply_schema(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "mirrored_at" in columns
    assert conn.execute("SELECT mirrored_at FROM tickets WHERE id='jira:ACME-1'").fetchone() == (None,)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
    conn.close()
