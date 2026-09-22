"""Project db + per-run sync: forward-only merges of a run's per-run db
into the project db, and the sandbox_run bookkeeping rows.

Mirrors src/sssf/sandbox/rundb.py.
"""

import sqlite3

from sssf.sandbox.rundb import sync_run_db


def test_sync_merges_live_totals_monotonically(tmp_path):
    """Card tokens/cost update in-flight: a mid-run sync carries totals that
    only grow, and a torn copy (fewer tokens than the last sync) never
    regresses the project db."""


    conn = sqlite3.connect(str(tmp_path / "proj.db"))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
        "started_at TEXT, ended_at TEXT, total_tokens INTEGER DEFAULT 0, "
        "total_cost REAL DEFAULT 0)"
    )
    conn.commit()
    per = tmp_path / "per-run" / "adws" / "adw_data"
    per.mkdir(parents=True)
    per_db = per / "sssf.db"
    src = sqlite3.connect(str(per_db))
    src.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
        "started_at TEXT, ended_at TEXT, total_tokens INTEGER DEFAULT 0, "
        "total_cost REAL DEFAULT 0)"
    )
    src.execute("INSERT INTO sessions VALUES ('r1','running','2026-08-16T10:00:00',NULL,100,1.5)")
    src.commit()

    sync_run_db(conn, per_db, "r1")
    assert conn.execute("SELECT total_tokens FROM sessions WHERE adw_id='r1'").fetchone()[0] == 100

    # torn mid-run copy with FEWER tokens — the max-merge must not regress
    src.execute("UPDATE sessions SET total_tokens=40, total_cost=0.5 WHERE adw_id='r1'")
    src.commit()
    sync_run_db(conn, per_db, "r1")
    assert conn.execute("SELECT total_tokens FROM sessions WHERE adw_id='r1'").fetchone()[0] == 100

    # real growth merges forward
    src.execute("UPDATE sessions SET total_tokens=250, total_cost=3.0 WHERE adw_id='r1'")
    src.commit()
    sync_run_db(conn, per_db, "r1")
    row = conn.execute("SELECT total_tokens, total_cost FROM sessions WHERE adw_id='r1'").fetchone()
    assert tuple(row) == (250, 3.0)
    # status stays 'running' — never downgraded by a mid-run copy
    assert conn.execute("SELECT status FROM sessions WHERE adw_id='r1'").fetchone()[0] == "running"
    conn.close()
    src.close()




def test_sync_propagates_request_mid_run(tmp_path):
    """Regression (2026-09-02, session 9701903a): the project row is inserted
    at the FIRST sync — usually BEFORE the sandboxed ADW's request phase logs
    the prompt — and the ended-row forward update only fires at the final
    merge (and never once the healer has finalized the host row first). A
    mid-run copy must therefore carry `request` too, or every `sssf run
    restart` on the host reads an empty request and bails with 'no request to
    re-run' — the healer's restarts of a hung sandboxed run then burn the
    whole budget doing nothing and the run is finalized unrecoverably."""


    schema = (
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT,"
        " status TEXT, engineer TEXT, started_at TEXT, ended_at TEXT,"
        " total_tokens INTEGER DEFAULT 0, total_cost REAL DEFAULT 0,"
        " archived INTEGER DEFAULT 0)"
    )
    conn = sqlite3.connect(str(tmp_path / "proj.db"))
    conn.execute(schema)
    # The first sync already ran: the host row exists, inserted BEFORE the
    # request phase logged (request NULL), and the run is still in flight.
    conn.execute(
        "INSERT INTO sessions VALUES ('r1','adw_sdlc_full',NULL,'running','Felipe',"
        " '2026-09-02T20:48:16',NULL,0,0,0)"
    )
    conn.commit()
    per = tmp_path / "per-run" / "adws" / "data"
    per.mkdir(parents=True)
    per_db = per / "sssf.db"
    src = sqlite3.connect(str(per_db))
    src.execute(schema)
    # ...while the per-run copy HAS the request: the request phase logged it,
    # but the container-side row is still running (ended_at NULL).
    src.execute(
        "INSERT INTO sessions VALUES ('r1','adw_sdlc_full','implement oauth',"
        " 'running','Felipe','2026-09-02T20:48:16',NULL,123,0.5,0)"
    )
    src.commit()

    sync_run_db(conn, per_db, "r1")
    row = conn.execute("SELECT request, status FROM sessions WHERE adw_id='r1'").fetchone()
    assert row[0] == "implement oauth"  # the request phase's value reached the host
    assert row[1] == "running"  # a mid-run copy never downgrades the status

    # The healer finalizes the HOST row (stop_run) while the container-side
    # copy is still running; the request must already be there, so a restart
    # can re-run the session...
    conn.execute(
        "UPDATE sessions SET status='fail', ended_at='2026-09-02T21:30:23' WHERE adw_id='r1'"
    )
    conn.commit()
    sync_run_db(conn, per_db, "r1")  # final merge after the container is killed
    row = conn.execute("SELECT request, status FROM sessions WHERE adw_id='r1'").fetchone()
    assert row[0] == "implement oauth"  # ...and the final merge must not clear it
    assert row[1] == "fail"  # nor downgrade the terminal status
    conn.close()
    src.close()




def test_sync_never_overwrites_an_existing_request(tmp_path):
    """The request is set once by the request phase and identical on a joined
    re-run — but a TORN source copy (request still NULL, mid-INSERT) must
    never clear a request the host already merged."""


    schema = (
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, request TEXT, status TEXT,"
        " ended_at TEXT, total_tokens INTEGER DEFAULT 0, total_cost REAL DEFAULT 0)"
    )
    conn = sqlite3.connect(str(tmp_path / "proj.db"))
    conn.execute(schema)
    conn.execute("INSERT INTO sessions VALUES ('r2','implement oauth','running',NULL,10,0.1)")
    conn.commit()
    per = tmp_path / "per-run2" / "adws" / "data"
    per.mkdir(parents=True)
    per_db = per / "sssf.db"
    src = sqlite3.connect(str(per_db))
    src.execute(schema)
    src.execute("INSERT INTO sessions VALUES ('r2',NULL,'running',NULL,5,0.05)")  # torn copy
    src.commit()

    sync_run_db(conn, per_db, "r2")
    row = conn.execute("SELECT request, status FROM sessions WHERE adw_id='r2'").fetchone()
    assert row[0] == "implement oauth"  # never regressed by a torn mid-run copy
    conn.close()
    src.close()




def test_sync_newer_ended_source_supersedes_frozen_host_row(tmp_path):
    """Regression (f9e445e9 restarts): a restart route that misses
    reopen_session leaves the HOST row ended at the previous run's terminal
    state — the old ended-only merge could then never record the new run's
    outcome, so the UI showed the old failure forever. A strictly NEWER ended
    source row must supersede an ended host row; an older (or NULL) copy can
    still never downgrade a newer terminal state."""


    conn = sqlite3.connect(str(tmp_path / "proj.db"))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
        "started_at TEXT, ended_at TEXT)"
    )
    # host row frozen at run B's failure (restart missed reopen_session)
    conn.execute(
        "INSERT INTO sessions VALUES ('r1','fail','2026-09-03T00:00:00','2026-09-03T00:29:50')"
    )
    conn.commit()
    per = tmp_path / "per-run" / "adws" / "adw_data"
    per.mkdir(parents=True)
    per_db = per / "sssf.db"
    src = sqlite3.connect(str(per_db))
    src.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
        "started_at TEXT, ended_at TEXT)"
    )
    src.execute(
        "INSERT INTO sessions VALUES ('r1','success','2026-09-03T00:45:00','2026-09-03T01:11:35')"
    )
    src.commit()

    sync_run_db(conn, per_db, "r1")
    row = conn.execute("SELECT status, ended_at FROM sessions WHERE adw_id='r1'").fetchone()
    assert row == ("success", "2026-09-03T01:11:35")  # superseded by the newer run

    # an OLDER ended source copy must never downgrade the newer terminal state
    src.execute(
        "UPDATE sessions SET status='fail', started_at='2026-09-03T00:10:00', "
        "ended_at='2026-09-03T00:20:00' WHERE adw_id='r1'"
    )
    src.commit()
    sync_run_db(conn, per_db, "r1")
    row = conn.execute("SELECT status, ended_at FROM sessions WHERE adw_id='r1'").fetchone()
    assert row == ("success", "2026-09-03T01:11:35")
    conn.close()
    src.close()




def test_sync_older_generation_never_reverts_reopened_host_row(tmp_path):
    """Regression (2e3d7693): a kanban/CLI restart calls reopen_session (host
    row -> running, started_at=now), but the re-run's monitor can sync BEFORE
    the new ADW writes its own session_start — the per-run db still holds the
    PREVIOUS attempt's terminal row. That stale copy (older started_at) must
    never revert the reopened host row, or the kanban card sits in Blocked
    for the whole re-run while the new attempt actually progresses."""


    conn = sqlite3.connect(str(tmp_path / "proj.db"))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
        "started_at TEXT, ended_at TEXT)"
    )
    # reopen_session stamped the host row: running, fresh started_at, no end
    conn.execute(
        "INSERT INTO sessions VALUES ('r1','running','2026-09-04T14:33:06',NULL)"
    )
    conn.commit()
    per = tmp_path / "per-run" / "adws" / "adw_data"
    per.mkdir(parents=True)
    per_db = per / "sssf.db"
    src = sqlite3.connect(str(per_db))
    src.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
        "started_at TEXT, ended_at TEXT)"
    )
    # per-run still carries attempt 1's terminal row (started BEFORE the reopen)
    src.execute(
        "INSERT INTO sessions VALUES "
        "('r1','fail','2026-09-04T14:19:39','2026-09-04T14:24:24')"
    )
    src.commit()

    sync_run_db(conn, per_db, "r1")
    row = conn.execute("SELECT status, started_at, ended_at FROM sessions WHERE adw_id='r1'").fetchone()
    assert row == ("running", "2026-09-04T14:33:06", None), (
        "an older-generation terminal copy reverted the reopened host row"
    )

    # once the re-run's own row exists (newer generation), its terminal state
    # supersedes normally — the reopened host row is not frozen forever
    src.execute(
        "UPDATE sessions SET status='success', started_at='2026-09-04T14:33:08', "
        "ended_at='2026-09-04T14:55:00' WHERE adw_id='r1'"
    )
    src.commit()
    sync_run_db(conn, per_db, "r1")
    row = conn.execute("SELECT status, ended_at FROM sessions WHERE adw_id='r1'").fetchone()
    assert row == ("success", "2026-09-04T14:55:00")
    conn.close()
    src.close()
