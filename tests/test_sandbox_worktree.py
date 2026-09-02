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
