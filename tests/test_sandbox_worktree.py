import os
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
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
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
    main_log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True).stdout
    assert "run" not in main_log
    # remove the worktree — branch survives as a ref
    remove_worktree(wt)
    assert not wt.exists()
    branches = subprocess.run(["git", "branch", "--list", "sssf/abc123"], cwd=repo,
                              capture_output=True, text=True).stdout
    assert "sssf/abc123" in branches
    # cwd still on main
    cur = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    assert cur == "main"


def test_remove_is_idempotent(repo, tmp_path):
    wt = create_worktree(repo, "def456")
    remove_worktree(wt)
    remove_worktree(wt)   # already gone — no error


def test_delete_branch_idempotent(repo, tmp_path):
    wt = create_worktree(repo, "ghi789")
    remove_worktree(wt)   # frees the branch — git refuses -D on a checked-out branch
    delete_branch(repo, "ghi789")
    delete_branch(repo, "ghi789")   # not found — no error
    branches = subprocess.run(["git", "branch", "--list", "sssf/ghi789"], cwd=repo,
                              capture_output=True, text=True).stdout
    assert branches.strip() == ""


def test_create_duplicate_raises(repo, tmp_path):
    create_worktree(repo, "dup1")
    with pytest.raises(SandboxError):
        create_worktree(repo, "dup1")   # branch already checked out


def test_sync_merges_live_totals_monotonically(tmp_path):
    """Card tokens/cost update in-flight: a mid-run sync carries totals that
    only grow, and a torn copy (fewer tokens than the last sync) never
    regresses the project db."""
    import sqlite3
    from sssf.sandbox import sync_run_db

    conn = sqlite3.connect(str(tmp_path / "proj.db"))
    conn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
                 "started_at TEXT, ended_at TEXT, total_tokens INTEGER DEFAULT 0, "
                 "total_cost REAL DEFAULT 0)")
    conn.commit()
    per = tmp_path / "per-run" / "adws" / "adw_data"
    per.mkdir(parents=True)
    per_db = per / "sssf.db"
    src = sqlite3.connect(str(per_db))
    src.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, "
                "started_at TEXT, ended_at TEXT, total_tokens INTEGER DEFAULT 0, "
                "total_cost REAL DEFAULT 0)")
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
