"""Project-local skills installer: fetch into .pi/skills/, version marker,
never the global pi home; check_skills reports staleness for doctor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sssf.adw_modules import skills_install
from sssf.adw_modules.skills_install import check_skills, install_skills


@pytest.fixture
def fake_repos(tmp_path, monkeypatch):
    """Two fixture git repos that act as the skill sources, plus a git shim
    that answers clone/ls-remote deterministically."""
    repos = {}
    for name in ("superpowers", "skills"):
        repo = tmp_path / f"remote-{name}"
        repo.mkdir()
        (repo / "skills").mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        repos[name] = repo
    # superpowers/skills/brainstorming
    bs = repos["superpowers"] / "skills" / "brainstorming"
    bs.mkdir(parents=True)
    (bs / "SKILL.md").write_text("# Brainstorming\n")
    # skills/skills/productivity/grilling + grill-me, skills/engineering/grill-with-docs
    for rel in ("productivity/grilling", "productivity/grill-me", "engineering/grill-with-docs"):
        d = repos["skills"] / "skills" / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {rel.split('/')[-1]}\n")
    for repo in repos.values():
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

    # point SOURCES at the fixture repos (file:// works with git clone)
    monkeypatch.setattr(skills_install, "SOURCES", {
        "brainstorming": (repos["superpowers"].as_uri(), "skills/brainstorming"),
        "grilling": (repos["skills"].as_uri(), "skills/productivity/grilling"),
        "grill-me": (repos["skills"].as_uri(), "skills/productivity/grill-me"),
        "grill-with-docs": (repos["skills"].as_uri(), "skills/engineering/grill-with-docs"),
    })
    return repos


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_install_writes_skills_and_marker(tmp_path, fake_repos):
    root = tmp_path / "proj"
    root.mkdir()
    assert install_skills(root) == 0
    target = root / ".pi" / "skills"
    for skill in ("brainstorming", "grilling", "grill-me", "grill-with-docs"):
        assert (target / skill / "SKILL.md").is_file(), skill
    marker = json.loads((target / ".sssf-versions.json").read_text())
    assert set(marker) == {"brainstorming", "grilling", "grill-me", "grill-with-docs"}
    assert marker["grilling"]["commit"] == _head(fake_repos["skills"])


def test_install_never_writes_global(tmp_path, fake_repos, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    # a fake global pi home — install must not touch it
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    assert install_skills(root) == 0
    assert not (fake_home / ".pi" / "agent" / "skills").exists()
    assert not (Path.home() / ".pi" / "agent" / "skills" / "grilling").exists()


def test_check_reports_stale_and_fresh(tmp_path, fake_repos):
    root = tmp_path / "proj"
    root.mkdir()
    install_skills(root)
    # fresh — marker matches the fixture HEAD
    state = check_skills(root)
    assert all(s["present"] for s in state.values())
    assert all(not s["stale"] for s in state.values())
    # make one stale by pinning an old commit in the marker
    marker = root / ".pi" / "skills" / ".sssf-versions.json"
    data = json.loads(marker.read_text())
    data["grilling"]["commit"] = "0" * 40
    marker.write_text(json.dumps(data))
    state = check_skills(root)
    assert state["grilling"]["stale"] is True
    assert state["brainstorming"]["stale"] is False


def test_missing_skills_reported(tmp_path, fake_repos):
    root = tmp_path / "proj"
    root.mkdir()
    state = check_skills(root)   # nothing installed
    assert all(not s["present"] for s in state.values())
