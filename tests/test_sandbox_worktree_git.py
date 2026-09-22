"""Worktree + integration-branch behavior: worktree create/remove/branch,
the origin/main sandbox contract, and the integration merge (auto-created,
optionally pushed, conflicts optionally resolved by the coding agent).

Mirrors src/sssf/sandbox/worktree_git.py.
"""

import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from sssf.adw_modules.data_types import SSSFConfig
from sssf.sandbox.docker import SandboxError
from sssf.sandbox.worktree_git import (
    create_worktree,
    delete_branch,
    integrate_run,
    integrate_successful_run,
    remove_worktree,
    sandbox_dir,
)

BASE = "adws/config/sssf.config.yaml"


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



def _git(root, *args, check=False):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r




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


def _make_repo(tmp_path) -> Path:
    # Bare origin + pushed main: sandbox runs fetch origin/main, so a repo
    # without a remote can no longer spawn fresh worktrees.
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


def test_teardown_keeps_branch(tmp_path):
    root = _make_repo(tmp_path)
    from sssf.sandbox.worktree_git import create_worktree, remove_worktree

    wt = create_worktree(root, "abc123")
    remove_worktree(wt)
    branches = subprocess.run(
        ["git", "branch", "--list", "sssf/abc123"], cwd=root, capture_output=True, text=True
    ).stdout
    assert "sssf/abc123" in branches


