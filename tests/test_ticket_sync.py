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
        {
            "github": {
                "state": writer("state"),
                "comment": writer("comment"),
                "label": writer("label"),
            }
        },
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
        {
            "github": {
                "state": lambda *args: None,
                "comment": lambda *args: None,
                "label": label_writer,
            }
        },
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


def test_new_machine_transition_allows_a_new_feedback_comment(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    conn.execute(
        "UPDATE tickets SET status='in-progress' WHERE id='github:owner/repo#1'"
    )
    ticketing.transition_ticket(
        conn,
        "github:owner/repo#1",
        ticketing.STATUS_READY,
        feedback="First rejection.",
    )
    calls = _recording_writers(monkeypatch)
    assert writeback.mirror_tickets(conn, providers={"github"}) == {}

    ticketing.transition_ticket(
        conn, "github:owner/repo#1", ticketing.STATUS_IN_PROGRESS
    )
    ticketing.transition_ticket(
        conn,
        "github:owner/repo#1",
        ticketing.STATUS_READY,
        feedback="Second rejection.",
    )
    assert writeback.mirror_tickets(conn, providers={"github"}) == {}

    assert [call[3] for call in calls if call[0] == "comment"] == [
        "First rejection.",
        "Second rejection.",
    ]
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


def test_successful_close_is_not_replayed_after_comment_failure(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    conn.execute("UPDATE tickets SET status='done' WHERE id='github:owner/repo#1'")
    conn.commit()
    calls = []
    comment_attempts = 0

    def close(repo, number, state):
        calls.append(("state", repo, number, state))

    def comment(repo, number, text):
        nonlocal comment_attempts
        comment_attempts += 1
        calls.append(("comment", repo, number, text))
        if comment_attempts == 1:
            raise RuntimeError("tracker unavailable")

    monkeypatch.setattr(
        writeback,
        "ORIGIN_WRITERS",
        {"github": {"state": close, "comment": comment, "label": lambda *args: None}},
    )

    assert writeback.mirror_tickets(conn, providers={"github"}) == {"github": 1}
    assert conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0] is None
    conn.close()
    conn = sqlite3.connect(tmp_path / "sssf.db")
    ticketing.ensure_schema(conn)
    assert writeback.mirror_tickets(conn, providers={"github"}) == {}

    assert calls.count(("state", "owner/repo", "1", "closed")) == 1
    assert calls.count(("comment", "owner/repo", "1", writeback.CLOSING_COMMENT)) == 2
    assert conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0]
    conn.close()


def test_successful_closing_comment_is_not_replayed_after_close_failure(tmp_path, monkeypatch):
    conn = _connection(tmp_path)
    conn.execute("UPDATE tickets SET status='done' WHERE id='github:owner/repo#1'")
    conn.commit()
    calls = []
    close_attempts = 0

    def close(repo, number, state):
        nonlocal close_attempts
        close_attempts += 1
        calls.append(("state", repo, number, state))
        if close_attempts == 1:
            raise RuntimeError("tracker unavailable")

    def comment(repo, number, text):
        calls.append(("comment", repo, number, text))

    monkeypatch.setattr(
        writeback,
        "ORIGIN_WRITERS",
        {"github": {"state": close, "comment": comment, "label": lambda *args: None}},
    )

    assert writeback.mirror_tickets(conn, providers={"github"}) == {"github": 1}
    assert writeback.mirror_tickets(conn, providers={"github"}) == {}

    assert calls.count(("state", "owner/repo", "1", "closed")) == 2
    assert calls.count(("comment", "owner/repo", "1", writeback.CLOSING_COMMENT)) == 1
    assert conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0]
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
        {
            "github": {
                "state": succeeds("state"),
                "comment": fail_comment,
                "label": succeeds("label"),
            }
        },
    )

    failures = writeback.mirror_tickets(conn, providers={"github"})

    assert failures == {"github": 1}
    assert conn.execute(
        "SELECT mirrored_at FROM tickets WHERE id='github:owner/repo#1'"
    ).fetchone()[0] is None
    assert ticketing.ticket_events(conn, "github:owner/repo#1")[-1]["event_type"] == "writeback_failed"
    assert ("state", "owner/repo", "1", "closed") in calls
    conn.close()


def test_migration_8_adds_partial_mirror_checkpoint_table_to_schema_v7_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "schema-v7.db")
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT, "
        "title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'needs-triage', "
        "prompt_file TEXT, adw_id TEXT, source_url TEXT, context TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL DEFAULT 'implementation', tracked INTEGER NOT NULL DEFAULT 1, "
        "origin TEXT NOT NULL DEFAULT 'internal', parent_id TEXT, spec TEXT NOT NULL DEFAULT '', "
        "rejection_feedback TEXT NOT NULL DEFAULT '', created_at TEXT, updated_at TEXT, mirrored_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO tickets (id, provider, external_id, title, status, tracked, origin, mirrored_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            ("github:owner/repo#done", "github", "owner/repo#done", "Done", "done", 0, "github", "2026-09-01"),
            ("github:owner/repo#ready", "github", "owner/repo#ready", "Ready", "ready-for-agent", 0, "github", "2026-09-01"),
        ],
    )
    conn.execute("PRAGMA user_version = 7")

    db_schema.apply_schema(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ticket_mirror_operations'"
    ).fetchone() == ("ticket_mirror_operations",)
    assert conn.execute(
        "SELECT ticket_id, operation_key FROM ticket_mirror_operations ORDER BY ticket_id, operation_key"
    ).fetchall() == [
        ("github:owner/repo#done", "done:close"),
        ("github:owner/repo#done", "done:comment"),
        ("github:owner/repo#ready", "feedback:comment"),
    ]
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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    conn.close()
