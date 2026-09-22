"""Project-local workflow skills installer: hermetic closure into .pi/skills/,
version marker, never the global pi home; check_skills reports staleness for
doctor. The stamp root is the workflow closure (#88): the twelve workflow
skills plus brainstorming (spec_interviewer references it); every referenced
skill (by frontmatter name) is pulled in transitively, and external setup
references are rewritten to sssf's own commands."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sssf.adw_modules import skills_install
from sssf.adw_modules.skills_install import check_skills, install_skills

# The mattpocock/skills layout the fixture repo mirrors. implement references
# /tdd + /code-review (the transitive edge); wayfinder + code-review carry the
# external setup token that must be rewritten to sssf's own commands.
SKILLS_REPO_FILES = {
    "productivity/grilling": "# Grilling\n",
    "productivity/grill-me": "# Grill me\n",
    "engineering/grill-with-docs": '# Grill with docs\nCall the Skill tool twice, for "grilling" and "domain-modeling".\n',
    "engineering/wayfinder": "# Wayfinder\nIf no tracker is provided, tell the user to run `/setup-matt-pocock-skills`.\n",
    "engineering/to-spec": "# To spec\n",
    "engineering/to-tickets": "# To tickets\n",
    "engineering/triage": "# Triage\n",
    "engineering/implement": "# Implement\nUse /tdd where possible, then /code-review.\n",
    "engineering/code-review": "# Code review\nIf the tracker doc is missing, tell the user to run `/setup-matt-pocock-skills`.\n",
    "engineering/tdd": "# TDD\n",
    "engineering/domain-modeling": "# Domain modeling\n",
    "engineering/codebase-design": "# Codebase design\n",
}

REFERENCE_DOCS = {  # ride along inside the skill directory (in-directory refs)
    "engineering/triage": {
        "AGENT-BRIEF.md": "# Agent briefs\n",
        "OUT-OF-SCOPE.md": "# Out of scope\n",
        "agents/openai.yaml": "name: triage-agent\n",
    }
}

ALL_SKILLS = (
    "brainstorming", "grilling", "grill-me", "grill-with-docs", "wayfinder",
    "to-spec", "to-tickets", "triage", "implement", "code-review", "tdd",
    "domain-modeling", "codebase-design",
)


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
    # skills/skills/<rel> — the twelve workflow skills
    for rel, body in SKILLS_REPO_FILES.items():
        d = repos["skills"] / "skills" / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(body)
        for ref, content in REFERENCE_DOCS.get(rel, {}).items():
            p = d / ref
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    for repo in repos.values():
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

    # point the manifest at the fixture repos (file:// works with git clone)
    monkeypatch.setattr(skills_install, "SOURCES", {
        "brainstorming": (repos["superpowers"].as_uri(), "skills/brainstorming"),
        "grilling": (repos["skills"].as_uri(), "skills/productivity/grilling"),
        "grill-me": (repos["skills"].as_uri(), "skills/productivity/grill-me"),
        "grill-with-docs": (repos["skills"].as_uri(), "skills/engineering/grill-with-docs"),
        "wayfinder": (repos["skills"].as_uri(), "skills/engineering/wayfinder"),
        "to-spec": (repos["skills"].as_uri(), "skills/engineering/to-spec"),
        "to-tickets": (repos["skills"].as_uri(), "skills/engineering/to-tickets"),
        "triage": (repos["skills"].as_uri(), "skills/engineering/triage"),
        "implement": (repos["skills"].as_uri(), "skills/engineering/implement"),
        "code-review": (repos["skills"].as_uri(), "skills/engineering/code-review"),
        "tdd": (repos["skills"].as_uri(), "skills/engineering/tdd"),
        "domain-modeling": (repos["skills"].as_uri(), "skills/engineering/domain-modeling"),
        "codebase-design": (repos["skills"].as_uri(), "skills/engineering/codebase-design"),
    })
    return repos


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_install_stamps_the_workflow_closure(tmp_path, fake_repos):
    root = tmp_path / "proj"
    root.mkdir()
    assert install_skills(root) == 0
    target = root / ".pi" / "skills"
    for skill in ALL_SKILLS:
        assert (target / skill / "SKILL.md").is_file(), f"{skill} not stamped"


def test_closure_pulls_referenced_skills(tmp_path, fake_repos, monkeypatch):
    """The transitive closure: a seed that references other skills pulls them
    in. implement references /tdd and /code-review — with the root shrunk to
    just implement, all three must land and nothing else."""
    monkeypatch.setattr(skills_install, "WORKFLOW_SKILLS", ("implement",))
    root = tmp_path / "proj"
    root.mkdir()
    assert install_skills(root) == 0
    target = root / ".pi" / "skills"
    for skill in ("implement", "tdd", "code-review"):
        assert (target / skill / "SKILL.md").is_file(), skill
    assert not (target / "grilling").exists()  # unreferenced stays out


def test_reference_docs_and_agent_defs_ride_along(tmp_path, fake_repos):
    root = tmp_path / "proj"
    root.mkdir()
    install_skills(root)
    target = root / ".pi" / "skills" / "triage"
    assert (target / "AGENT-BRIEF.md").is_file()
    assert (target / "OUT-OF-SCOPE.md").is_file()
    assert (target / "agents" / "openai.yaml").is_file()


def test_external_setup_references_rewritten(tmp_path, fake_repos):
    """The stamped skills never point agents at another tool's setup — the
    /setup-matt-pocock-skills token becomes sssf's own command."""
    root = tmp_path / "proj"
    root.mkdir()
    install_skills(root)
    for skill in ("wayfinder", "code-review"):
        text = (root / ".pi" / "skills" / skill / "SKILL.md").read_text()
        assert "/setup-matt-pocock-skills" not in text, skill
        assert "sssf init --refresh" in text, skill


def test_marker_pins_the_stamped_closure(tmp_path, fake_repos):
    root = tmp_path / "proj"
    root.mkdir()
    install_skills(root)
    marker = json.loads((root / ".pi" / "skills" / ".sssf-versions.json").read_text())
    assert set(marker) == set(ALL_SKILLS)
    assert marker["grilling"]["commit"] == _head(fake_repos["skills"])
    assert marker["brainstorming"]["commit"] == _head(fake_repos["superpowers"])


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
