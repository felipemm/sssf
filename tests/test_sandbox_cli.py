
import os
import stat
import subprocess
from pathlib import Path

from sssf.commands import misc  # noqa: F401  (CLI registration smoke)

import sssf.cli as cli  # noqa: F401

def _make_repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root

def test_spawn_sandbox_creates_worktree_and_records_port(tmp_path, monkeypatch, fake_docker):
    root = _make_repo(tmp_path)
    from sssf.sandbox import allocate_port, sandbox_dir, spawn_sandbox
    port = allocate_port(31200)
    assert port >= 31200
    # the orchestration helper under test: spawn_sandbox creates the worktree
    record = spawn_sandbox(root, "abc123", cmd=["true"], port=port, image="sssf-runner",
                           data_dir=root / "adws" / "adw_data", pi_home=tmp_path / "pi")
    assert sandbox_dir(root, "abc123").is_dir()
    assert record["worktree"] == str(sandbox_dir(root, "abc123"))
    assert record["host_port"] == port
    assert record["name"] == "sssf-abc123"

def test_teardown_keeps_branch(tmp_path):
    root = _make_repo(tmp_path)
    from sssf.sandbox import create_worktree, remove_worktree
    wt = create_worktree(root, "abc123")
    remove_worktree(wt)
    branches = subprocess.run(["git", "branch", "--list", "sssf/abc123"], cwd=root,
                              capture_output=True, text=True).stdout
    assert "sssf/abc123" in branches
