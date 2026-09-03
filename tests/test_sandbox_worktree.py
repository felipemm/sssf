import subprocess

import pytest

from sssf.sandbox import (
    SandboxError,
    create_worktree,
    delete_branch,
    remove_worktree,
    sandbox_dir,
)


@pytest.fixture
def repo(tmp_path):
    # A bare origin with main pushed — the sandbox contract is origin/main,
    # so the fixture mirrors a real remote.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=root, check=True)
    return root


@pytest.fixture(autouse=True)
def sssf_home(tmp_path, monkeypatch):
    """Point sandbox_dir at a per-test temp home so the suite is hermetic
    (the brief's default ~/.sssf pollutes real state across runs)."""
    monkeypatch.setenv("SSSF_HOME", str(tmp_path / "sssf-home"))


def test_sandbox_dir_location(repo, tmp_path):
    d = sandbox_dir(repo, "abc123")
    assert d.name == "abc123"
    assert "proj" in d.parts
    assert d.is_absolute()


def test_sandbox_dir_is_repo_worktrees(repo, tmp_path):
    d = sandbox_dir(repo, "abc123")
    assert d == repo / ".worktrees" / "abc123"


def test_worktree_created_inside_repo_and_excluded(repo, tmp_path):
    wt = create_worktree(repo, "wtloc1")
    assert wt == repo / ".worktrees" / "wtloc1"
    assert wt.is_dir()
    # the main tree must not show .worktrees/ as untracked noise
    status = subprocess.run(
        ["git", "status", "--short"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status.strip() == ""


def test_teardown_keeps_container_and_worktree(repo, tmp_path, monkeypatch):
    """After a run NEITHER the container nor the worktree is deleted — both
    are the debugging surface. Cleanup is explicit (sweep / sandbox prune)."""
    import sssf.sandbox as sandbox

    wt = create_worktree(repo, "keep1")
    called = []
    monkeypatch.setattr(sandbox, "stop_remove", lambda name: called.append(name))
    assert sandbox.teardown_sandbox(repo, "keep1") == 0
    assert called == []  # container is KEPT
    assert wt.is_dir()  # worktree survives


def test_abort_keeps_worktree_for_manual_debug(repo, tmp_path, monkeypatch):
    import sssf.sandbox as sandbox

    wt = create_worktree(repo, "abrt1")
    removed = []
    monkeypatch.setattr(sandbox, "stop_remove", lambda name: removed.append(name))
    sandbox.abort_sandbox(repo, "abrt1")
    assert removed == ["sssf-abrt1"]
    assert wt.is_dir()  # failed spawns leave the worktree too


def test_create_remove_branch_survives(repo, tmp_path):
    wt = create_worktree(repo, "abc123")
    assert wt.is_dir()
    assert wt.name == "abc123"
    # the run commits in its worktree
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=wt, check=True)
    (wt / "f.txt").write_text("x\nrun work\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "run"], cwd=wt, check=True)
    # the main checkout is untouched
    main_log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "run" not in main_log
    # remove the worktree — branch survives as a ref
    remove_worktree(wt)
    assert not wt.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", "sssf/abc123"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "sssf/abc123" in branches
    # cwd still on main
    cur = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert cur == "main"


def test_remove_is_idempotent(repo, tmp_path):
    wt = create_worktree(repo, "def456")
    remove_worktree(wt)
    remove_worktree(wt)  # already gone — no error


def test_delete_branch_idempotent(repo, tmp_path):
    wt = create_worktree(repo, "ghi789")
    remove_worktree(wt)  # frees the branch — git refuses -D on a checked-out branch
    delete_branch(repo, "ghi789")
    delete_branch(repo, "ghi789")  # not found — no error
    branches = subprocess.run(
        ["git", "branch", "--list", "sssf/ghi789"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert branches.strip() == ""


def test_worktree_runs_from_origin_main_not_dirty_local(repo, tmp_path):
    """The sandbox contract: fresh runs check out origin/main — never local
    main, which may carry commits that were never pushed."""
    (repo / "f.txt").write_text("x\nlocal dirty\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "dirty local"], cwd=repo, check=True)
    wt = create_worktree(repo, "orig1")
    assert (wt / "f.txt").read_text() == "x\n"  # origin/main state, not local


def test_worktree_ignores_uncommitted_local_edits(repo, tmp_path):
    (repo / "f.txt").write_text("x\nuncommitted\n")
    wt = create_worktree(repo, "orig2")
    assert (wt / "f.txt").read_text() == "x\n"


def test_worktree_fetches_latest_origin_main(repo, tmp_path):
    """A commit pushed to origin AFTER the local clone must be picked up by
    the fresh run — create_worktree fetches origin/main, so the sandbox sees
    the remote state even when local main has moved on with unpushed work."""
    (repo / "f.txt").write_text("x\nremote state\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "remote update"], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
    # local main now diverges with an unpushed commit
    (repo / "f.txt").write_text("x\nlocal only\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "local only"], cwd=repo, check=True)
    wt = create_worktree(repo, "orig3")
    assert (wt / "f.txt").read_text() == "x\nremote state\n"


def test_worktree_without_origin_falls_back_to_local_main(tmp_path):
    """A repo with NO remote (no origin) must not crash on `git fetch origin`
    — fall back to local main. Uncommitted edits stay out either way."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\nlocal\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    wt = create_worktree(root, "noorigin1")
    assert (wt / "f.txt").read_text() == "x\nlocal\n"

    # uncommitted local edits must not leak into the sandbox even with no remote
    (root / "f.txt").write_text("x\nlocal\nuncommitted\n")
    wt2 = create_worktree(root, "noorigin2")
    assert (wt2 / "f.txt").read_text() == "x\nlocal\n"


def test_create_duplicate_raises(repo, tmp_path):
    create_worktree(repo, "dup1")
    with pytest.raises(SandboxError):
        create_worktree(repo, "dup1")  # branch already checked out


def test_sync_merges_live_totals_monotonically(tmp_path):
    """Card tokens/cost update in-flight: a mid-run sync carries totals that
    only grow, and a torn copy (fewer tokens than the last sync) never
    regresses the project db."""
    import sqlite3

    from sssf.sandbox import sync_run_db

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
    import sqlite3

    from sssf.sandbox import sync_run_db

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
    import sqlite3

    from sssf.sandbox import sync_run_db

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


def test_attach_reuses_existing_worktree(repo):
    """A restart attaches to the run's existing branch. When the checkout
    already exists (a stopped/pruned attempt left it registered while the
    container is gone), `git worktree add` would collide with 'already exists'
    and kill the restart before the ADW ever starts (session 9701903a,
    2026-09-02: the leftover registered worktree from one stopped attempt
    silently broke every later restart). Attach must reuse the checkout — the
    branch is the same, so it IS the attach target."""
    wt1 = create_worktree(repo, "att1")  # fresh run — creates sssf/att1
    assert wt1.exists()
    wt2 = create_worktree(repo, "att1", attach=True)  # restart — reuse, no error
    assert wt2 == wt1
    wt3 = create_worktree(repo, "att1", attach=True)  # ...repeatably
    assert wt3 == wt1


def test_attach_clears_unregistered_leftover(repo, tmp_path):
    """A leftover UNREGISTERED checkout dir (a failed `git worktree remove`
    left the dir behind) must not block attach either — clear it and add."""
    from pathlib import Path

    wt1 = create_worktree(repo, "att2")
    # simulate the teardown race: git unregisters but the dir survives
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt1)],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=True)
    assert not Path(wt1).exists()  # prune removed it — recreate the stale dir
    (tmp_path / "proj" / ".worktrees" / "att2").mkdir(parents=True)
    stale = sandbox_dir(repo, "att2")
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "stray.txt").write_text("x")
    wt2 = create_worktree(repo, "att2", attach=True)  # must not raise
    assert wt2 == sandbox_dir(repo, "att2")


def test_reopen_session_flips_terminal_row_to_running(tmp_path):
    """A restart re-opens the host row of a terminal session (status running,
    ended_at cleared). Without it the UI keeps the previous run's fail state
    and the restarted run's own outcome is never recorded either — the
    monitor's forward-merge only updates rows whose ended_at IS NULL. The
    previous run's phases/events are cleared so the restarted run (which reuses
    the same phase_ids) is authoritative in the trace."""
    import sqlite3

    from sssf.sandbox import project_db_path, reopen_session

    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(project_db_path(data)))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT,"
        " started_at TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT,"
        " status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, adw_id TEXT,"
        " phase_id TEXT, type TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('r9','fail','2026-09-02T20:48:16',"
        " '2026-09-02T21:30:23')"
    )
    conn.execute(
        "INSERT INTO phases VALUES ('r9_04_build','r9','fail',"
        " 'finalized by the healer: restart budget exhausted','2026-09-02T21:30:23')"
    )
    conn.execute("INSERT INTO events VALUES ('e1','r9','r9_01_request','phase_start')")
    conn.commit()
    conn.close()

    reopen_session(data, "r9")
    conn = sqlite3.connect(str(project_db_path(data)))
    status, started, ended = conn.execute(
        "SELECT status, started_at, ended_at FROM sessions WHERE adw_id='r9'"
    ).fetchone()
    phases = conn.execute("SELECT COUNT(*) FROM phases WHERE adw_id='r9'").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM events WHERE adw_id='r9'").fetchone()[0]
    conn.close()
    assert status == "running"
    assert ended is None
    assert started is not None and started > "2026-09-02T21:30:23"
    assert phases == 0 and events == 0  # the new run is authoritative


def test_monitor_exits_when_run_ends_but_container_alive(tmp_path, monkeypatch):
    """The supervisor keeps the container up after the run (review mode), so
    the monitor must stop when the RUN ends — signalled by the supervisor-exit
    marker — not wait for the container to disappear (session 9701903a lesson:
    the run's end must not depend on container teardown)."""
    import sqlite3

    import sssf.sandbox as sb
    from sssf.sandbox import monitor_run, sandbox_dir

    root = tmp_path / "proj"
    root.mkdir()
    data = root / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(data / "sssf.db"))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    conn.commit()
    conn.close()
    # the container's worktree — where the per-run db and the supervisor marker live
    wt_data = sandbox_dir(root, "r6") / "adws" / "data"
    (wt_data / "sessions").mkdir(parents=True)

    monkeypatch.setattr(sb, "_container_gone", lambda fn, name: False)  # container stays up
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)  # no real waiting
    monkeypatch.setattr(sb, "sync_run_db", lambda *a, **k: None)
    monkeypatch.setattr(sb, "record_never_started", lambda *a, **k: None)

    # the supervisor wrote its exit marker (the ADW ended; container idles)
    (wt_data / "sessions" / "r6.supervisor-exit").write_text("0")

    assert monitor_run(root, "r6") == 0
    # cleanup: the marker is consumed; the container/worktree are untouched
    assert not (wt_data / "sessions" / "r6.supervisor-exit").exists()
