"""Integration-branch behavior: fresh runs branch from the integration target,
and successful runs merge back into it (auto-created, optionally pushed, and
conflicts optionally resolved by the coding agent).

The sandbox contract stays for repos without an adws config (or with
integration disabled): branch from origin/main / local main, never merge.
"""

import sqlite3
import subprocess

import pytest
import yaml

from sssf.adw_modules.data_types import SSSFConfig
from sssf.sandbox import (
    create_worktree,
    integrate_run,
    integrate_successful_run,
)

BASE = "adws/config/sssf.config.yaml"


def _git(root, *args, check=False):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


@pytest.fixture
def repo(tmp_path):
    """A bare origin with main pushed; local clone checked out on main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    _git(root, "add", "-A", check=True)
    _git(root, "commit", "-qm", "base", check=True)
    _git(root, "remote", "add", "origin", str(origin), check=True)
    _git(root, "push", "-q", "-u", "origin", "main", check=True)
    return root


@pytest.fixture
def local_repo(tmp_path):
    """A local-only repo (no origin) on main — the dsl-app shape."""
    root = tmp_path / "local"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n")
    _git(root, "add", "-A", check=True)
    _git(root, "commit", "-qm", "base", check=True)
    return root


@pytest.fixture(autouse=True)
def sssf_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_HOME", str(tmp_path / "sssf-home"))


def _config(root, *, enabled=True, branch="dev", push=True, resolve=True, resolve_skill_path=None):
    cfg = root / BASE
    cfg.parent.mkdir(parents=True, exist_ok=True)
    block = {"integration": {"enabled": enabled, "branch": branch, "push": push, "resolve": resolve}}
    if resolve_skill_path:
        block["integration"]["resolve_skill_path"] = resolve_skill_path
    cfg.write_text(yaml.safe_dump(block))
    # Real projects commit the config and ignore the adws/data runtime; mirror
    # that so the integration dirty-check sees a clean tree.
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text("adws/data/\n")
    _git(root, "add", "-A", check=True)
    _git(root, "commit", "-qm", "integration config", check=True)
    return cfg


def _branch_from(root, branch):
    """Worktree for sssf/<adw_id> created by the engine, then one commit."""
    wt = create_worktree(root, branch)
    return wt


def _run_commit(wt, path="run.txt", content="run work\n", message="run work"):
    (wt / path).write_text(content)
    _git(wt, "add", "-A", check=True)
    _git(wt, "commit", "-qm", message, check=True)


def _push_dev(root):
    """Push a local dev branch (at the current main sha) to origin."""
    _git(root, "checkout", "-q", "-b", "dev", check=True)
    _git(root, "push", "-q", "-u", "origin", "dev", check=True)
    _git(root, "checkout", "-q", "main", check=True)


def _origin_has(root, ref):
    r = _git(root, "rev-parse", "--verify", "--quiet", f"origin/{ref}^{{commit}}")
    return r.returncode == 0


def test_defaults_integration_on_dev_branch():
    cfg = SSSFConfig()
    assert cfg.integration.enabled is True
    assert cfg.integration.branch == "dev"
    assert cfg.integration.push is True
    assert cfg.integration.resolve is True


def test_config_parses_and_disables(repo):
    _config(repo, enabled=False)
    from sssf.adw_modules.agents import load_config

    cfg = load_config(str(repo / BASE))
    assert cfg.integration.enabled is False
    assert cfg.integration.branch == "dev"  # branch name survives disable


# ── fresh-run base (create_worktree) ───────────────────────────────────────


def test_worktree_branches_from_origin_dev_when_configured(repo):
    _config(repo)
    _push_dev(repo)
    dev_tip = _git(repo, "rev-parse", "origin/dev").stdout.strip()
    wt = _branch_from(repo, "wtdev1")
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == dev_tip


def test_worktree_falls_back_to_main_when_dev_missing_remotely(repo):
    _config(repo)
    main_tip = _git(repo, "rev-parse", "origin/main").stdout.strip()
    wt = _branch_from(repo, "wtfb1")
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == main_tip


def test_worktree_disabled_keeps_main_base(repo):
    _config(repo, enabled=False)
    main_tip = _git(repo, "rev-parse", "origin/main").stdout.strip()
    wt = _branch_from(repo, "wtleg1")
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == main_tip


def test_worktree_branches_from_local_dev_without_remote(local_repo):
    _config(local_repo)
    _git(local_repo, "branch", "dev", check=True)
    dev_tip = _git(local_repo, "rev-parse", "dev").stdout.strip()
    wt = _branch_from(local_repo, "wtld1")
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == dev_tip


def test_worktree_local_falls_back_to_main_when_dev_missing(local_repo):
    _config(local_repo)
    main_tip = _git(local_repo, "rev-parse", "main").stdout.strip()
    wt = _branch_from(local_repo, "wtlf1")
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == main_tip


# ── integrate_run: merge + push + restore ──────────────────────────────────


def test_integrate_merges_run_into_dev_and_pushes(repo):
    _config(repo)
    _push_dev(repo)
    wt = _branch_from(repo, "abcd1234")
    _run_commit(wt)
    run_tip = _git(repo, "rev-parse", "sssf/abcd1234").stdout.strip()

    outcome = integrate_run(repo, "abcd1234", repo / "adws" / "data")

    assert outcome["outcome"] == "merged"
    assert outcome["target"] == "dev"
    assert outcome["commit"] == run_tip  # fast-forwarded — tip is the run's commit
    assert outcome["pushed"] is True
    # the merge landed locally on dev AND upstream
    assert _git(repo, "rev-parse", "dev").stdout.strip() == run_tip
    assert _origin_has(repo, "dev")
    # the operator's checkout was restored
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_integrate_creates_dev_when_missing_and_pushes(repo):
    _config(repo)
    wt = _branch_from(repo, "beef0001")  # dev absent -> branched from origin/main
    _run_commit(wt)
    run_tip = _git(repo, "rev-parse", "sssf/beef0001").stdout.strip()

    outcome = integrate_run(repo, "beef0001", repo / "adws" / "data")

    assert outcome["outcome"] == "merged"
    assert _git(repo, "rev-parse", "dev").stdout.strip() == run_tip
    assert _git(repo, "rev-parse", "origin/dev").stdout.strip() == run_tip
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_integrate_pushes_nothing_when_push_disabled(repo):
    _config(repo, push=False)
    _push_dev(repo)
    origin_before = _git(repo, "rev-parse", "origin/dev").stdout.strip()
    wt = _branch_from(repo, "cafe0002")
    _run_commit(wt)
    run_tip = _git(repo, "rev-parse", "sssf/cafe0002").stdout.strip()
    assert run_tip != origin_before

    outcome = integrate_run(repo, "cafe0002", repo / "adws" / "data")

    assert outcome["outcome"] == "merged"
    assert outcome["pushed"] is False
    assert _git(repo, "rev-parse", "dev").stdout.strip() == run_tip
    assert _git(repo, "rev-parse", "origin/dev").stdout.strip() == origin_before  # local only


def test_integrate_skips_when_already_merged(repo):
    _config(repo)
    _push_dev(repo)
    wt = _branch_from(repo, "dead0003")
    _run_commit(wt)
    assert integrate_run(repo, "dead0003", repo / "adws" / "data")["outcome"] == "merged"

    outcome = integrate_run(repo, "dead0003", repo / "adws" / "data")

    assert outcome is None  # the run's tip is already on dev — nothing to do
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_integrate_skips_when_tree_dirty(repo):
    _config(repo)
    _push_dev(repo)
    dev_before = _git(repo, "rev-parse", "origin/dev").stdout.strip()
    wt = _branch_from(repo, "d1rt0004")
    _run_commit(wt)
    run_tip = _git(repo, "rev-parse", "sssf/d1rt0004").stdout.strip()
    assert run_tip != dev_before
    (repo / "f.txt").write_text("dirty\n")  # operator's uncommitted edit

    outcome = integrate_run(repo, "d1rt0004", repo / "adws" / "data")

    assert outcome["outcome"] == "skipped"
    assert "dirty" in outcome["reason"]
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    # nothing was merged or pushed
    assert _git(repo, "rev-parse", "origin/dev").stdout.strip() == dev_before
    (repo / "f.txt").write_text("x\n")  # restore for the fixture teardown


def test_integrate_merges_local_only_repo(local_repo):
    _config(local_repo)
    _git(local_repo, "branch", "dev", check=True)
    wt = _branch_from(local_repo, "10ca1005")
    _run_commit(wt)
    run_tip = _git(local_repo, "rev-parse", "sssf/10ca1005").stdout.strip()

    outcome = integrate_run(local_repo, "10ca1005", local_repo / "adws" / "data")

    assert outcome["outcome"] == "merged"
    assert outcome["pushed"] is False  # no origin — nothing to push
    assert _git(local_repo, "rev-parse", "dev").stdout.strip() == run_tip
    assert _git(local_repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


# ── conflicts ──────────────────────────────────────────────────────────────


def _conflicting_run(repo):
    """dev advances on f.txt; the run (from the older dev) edits f.txt too."""
    _push_dev(repo)
    wt = _branch_from(repo, "c0nf1ict")
    _run_commit(wt, path="f.txt", content="run line\n", message="run edits f")
    # dev moves forward on the same line, unpushed
    _git(repo, "checkout", "-q", "dev", check=True)
    (repo / "f.txt").write_text("dev line\n")
    _git(repo, "add", "-A", check=True)
    _git(repo, "commit", "-qm", "dev edits f", check=True)
    _git(repo, "checkout", "-q", "main", check=True)
    return wt


def test_integrate_conflict_aborts_without_resolve(repo):
    _config(repo, resolve=False)
    _conflicting_run(repo)

    outcome = integrate_run(repo, "c0nf1ict", repo / "adws" / "data")

    assert outcome["outcome"] == "conflicted"
    assert outcome["resolved"] is False
    # merge aborted cleanly: no MERGE_HEAD, operator restored to main
    assert _git(repo, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode != 0
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    # the run's branch survives for a manual merge
    assert _git(repo, "rev-parse", "--verify", "--quiet", "sssf/c0nf1ict^{commit}").returncode == 0
    # nothing was pushed
    assert not _origin_has(repo, "dev") or _git(repo, "rev-parse", "origin/dev").stdout.strip() != _git(
        repo, "rev-parse", "dev"
    ).stdout.strip()


def test_integrate_conflict_resolved_by_agent(repo, monkeypatch):
    _config(repo, resolve=True, resolve_skill_path="/skills/resolving-merge-conflicts/SKILL.md")
    _conflicting_run(repo)

    calls = {}

    def fake_run(request):
        calls["prompt"] = request.prompt
        calls["skill"] = request.skill_path
        calls["cwd"] = request.cwd
        # resolve the conflict the way the skill would: pick one side and stage
        (repo / "f.txt").write_text("run line\nresolved\n")
        _git(request.cwd, "add", "-A", check=True)

    import sssf.adw_modules.agent_pi as agent_pi

    monkeypatch.setattr(agent_pi, "run", fake_run)

    outcome = integrate_run(repo, "c0nf1ict", repo / "adws" / "data")

    assert outcome["outcome"] == "merged"
    assert outcome["resolved"] is True
    assert "resolving-merge-conflicts" in calls["prompt"]
    assert calls["skill"] == "/skills/resolving-merge-conflicts/SKILL.md"
    assert str(repo) == calls["cwd"]  # agent works in the repo root, mid-merge
    # the resolution landed on dev (the root checkout was restored to main)
    assert "resolved" in _git(repo, "show", "dev:f.txt").stdout
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_integrate_conflict_agent_leaves_conflicts(repo, monkeypatch):
    _config(repo, resolve=True)
    _conflicting_run(repo)

    import sssf.adw_modules.agent_pi as agent_pi

    monkeypatch.setattr(agent_pi, "run", lambda request: None)  # agent does nothing

    outcome = integrate_run(repo, "c0nf1ict", repo / "adws" / "data")

    assert outcome["outcome"] == "conflicted"
    assert outcome["resolved"] is False
    assert "agent" in outcome["reason"]
    assert _git(repo, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode != 0
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


# ── integrate_successful_run: the monitor seam ─────────────────────────────


def _session_db(root, adw_id, status="success"):
    db = root / "adws" / "data" / "sssf.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (adw_id TEXT PRIMARY KEY, status TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO sessions (adw_id, status) VALUES (?, ?)", (adw_id, status)
    )
    conn.commit()
    conn.close()
    return db


def test_integrate_successful_run_merges_only_success(local_repo):
    _config(local_repo)
    _git(local_repo, "branch", "dev", check=True)
    for status, adw in (("running", "run1"), ("fail", "fail1"), ("success", "okay1")):
        wt = _branch_from(local_repo, adw)
        _run_commit(wt, path=f"{adw}.txt")
        _session_db(local_repo, adw, status=status)

    assert integrate_successful_run(local_repo, "run1") is None
    assert integrate_successful_run(local_repo, "fail1") is None
    outcome = integrate_successful_run(local_repo, "okay1")
    assert outcome is not None
    assert outcome["outcome"] == "merged"
    assert _git(local_repo, "rev-parse", "dev").stdout.strip() == _git(
        local_repo, "rev-parse", "sssf/okay1"
    ).stdout.strip()


def test_integrate_successful_run_disabled_config(local_repo):
    _config(local_repo, enabled=False)
    wt = _branch_from(local_repo, "off001")
    _run_commit(wt)
    _session_db(local_repo, "off001")
    assert integrate_successful_run(local_repo, "off001") is None
    # dev never appeared — pure-main behavior preserved
    assert _git(local_repo, "rev-parse", "--verify", "--quiet", "dev^{commit}").returncode != 0
