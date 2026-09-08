"""Update checks for the sssf tool itself: local source repo vs origin.

sssf is typically installed as an editable uv tool over a git checkout; an
"update available" means the checkout's branch is behind its origin. These
tests drive the real git CLI against local bare-origin fixtures — no mocks,
no network.
"""

import subprocess
from pathlib import Path

from sssf import updates


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Bare origin + local clone with main pushed and tracking."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("one\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "c1"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=root, check=True)
    return root, origin


def _advance_origin(origin: Path, n: int = 1) -> None:
    """Push n new commits to the bare origin's main from a scratch clone."""
    work = origin.parent / "advance-work"
    if work.exists():
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
        subprocess.run(["git", "-C", str(work), "pull", "-q", "--ff-only"], check=True)
    else:
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    for i in range(n):
        (work / "f.txt").write_text(f"remote {i}\n")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", f"remote {i}"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)


def test_check_reports_behind_when_origin_is_ahead(tmp_path):
    root, origin = _make_repo(tmp_path)
    _advance_origin(origin, n=2)

    r = updates.check(root)

    assert r["ok"] is True
    assert r["behind"] == 2
    assert r["ahead"] == 0
    assert r["local_sha"] != r["remote_sha"]
    assert r["command"] == "sssf upgrade"
    assert r["repo"] == str(root)


def test_check_reports_current_when_synced(tmp_path):
    root, origin = _make_repo(tmp_path)
    assert updates.check(root)["behind"] == 0

    _advance_origin(origin, n=1)
    assert updates.check(root)["behind"] == 1  # stale until we pull

    subprocess.run(["git", "-C", str(root), "pull", "-q", "--ff-only"], check=True)
    r = updates.check(root)
    assert r["behind"] == 0
    assert r["local_sha"] == r["remote_sha"]


def test_check_counts_local_ahead_commits(tmp_path):
    """Local commits the origin does not have are ahead, not an update."""
    root, _origin = _make_repo(tmp_path)
    (root / "f.txt").write_text("local only\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "local ahead"], check=True)

    r = updates.check(root)

    assert r["ok"] is True
    assert r["ahead"] == 1
    assert r["behind"] == 0


def test_check_unreachable_origin_is_offline(tmp_path):
    """A fetch failure must never claim an update we cannot verify — the UI
    stays silent on !ok. The origin points at a path that does not exist, so
    git fails fast and locally."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "gone")], cwd=root, check=True)

    r = updates.check(root)

    assert r["ok"] is False
    assert r["reason"] == "unreachable"
    assert "behind" not in r


def test_check_not_a_git_checkout(tmp_path):
    """A packaged (non-editable) install has no repo to compare — report so
    the UI can stay quiet instead of guessing."""
    r = updates.check(tmp_path)

    assert r["ok"] is False
    assert r["reason"] == "not-git"


def test_check_reports_dirty_working_tree(tmp_path):
    root, _origin = _make_repo(tmp_path)
    (root / "f.txt").write_text("modified\n")

    assert updates.check(root)["dirty"] >= 1


def test_check_tracks_feature_branch_upstream(tmp_path):
    """On a feature branch with its own upstream, the comparison targets that
    upstream, not main."""
    root, origin = _make_repo(tmp_path)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "dev"], check=True)
    subprocess.run(["git", "-C", str(root), "push", "-q", "-u", "origin", "dev"], check=True)
    # advance dev on the origin
    work = origin.parent / "advance-work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "dev"], check=True)
    (work / "f.txt").write_text("dev remote\n")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "dev remote"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "dev"], check=True)

    r = updates.check(root)

    assert r["ok"] is True
    assert r["branch"] == "dev"
    assert r["behind"] == 1


def test_upgrade_pulls_when_behind(tmp_path, capsys):
    root, origin = _make_repo(tmp_path)
    _advance_origin(origin, n=1)

    assert updates.upgrade(root) == 0

    assert updates.check(root)["behind"] == 0
    out = capsys.readouterr().out
    assert "Restart" in out  # guidance to restart viz/healer after the pull


def test_upgrade_falls_back_to_uv_tool_for_packaged_install(tmp_path, monkeypatch, capsys):
    """Not a git checkout -> the uv tool upgrade path is the source of truth."""
    calls: list[str] = []

    def fake_call(argv, **kw):
        calls.append(" ".join(argv))
        return 7

    monkeypatch.setattr(updates.subprocess, "call", fake_call)
    rc = updates.upgrade(tmp_path)
    assert rc == 7
    assert calls == ["uv tool upgrade sssf"]
