import subprocess
from pathlib import Path

import pytest

from sssf.adw_modules import git_helper


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with one commit and clean state, cwd inside it."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "tester"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"], check=True
    )
    (tmp_path / "seed.txt").write_text("seed")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"], check=True)
    return tmp_path


def test_commit_all_raises_on_empty(repo, monkeypatch):
    monkeypatch.chdir(repo)
    with pytest.raises(RuntimeError, match="nothing to commit"):
        git_helper.commit_all("should not land")


def test_commit_all_allow_empty_returns_none(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert git_helper.commit_all("no-op", allow_empty=True) is None
    # still clean — nothing landed
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
        ).stdout
        == ""
    )


def test_commit_all_commits_when_changed(repo, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / "work.txt").write_text("work")
    sha = git_helper.commit_all("real change", allow_empty=True)
    assert sha is not None
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True
    ).stdout.strip()
    assert log == "real change"


def test_diff_files_between(repo, monkeypatch):
    monkeypatch.chdir(repo)
    base = git_helper.rev("HEAD")
    (repo / "a.txt").write_text("a")
    git_helper.commit_all("add a")
    (repo / "b.txt").write_text("b")
    git_helper.commit_all("add b")
    assert git_helper.diff_files_between(base, "HEAD") == ["a.txt", "b.txt"]
    assert git_helper.diff_files_between(base, "HEAD~1") == ["a.txt"]
    assert git_helper.diff_files_between("HEAD", "HEAD") == []
