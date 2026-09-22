"""Worktrees + integration: create/remove/delete the run's worktree
and branch, and merge successful runs into the integration branch.

Pure git + filesystem — no docker, no db. SandboxError is imported from
the docker module (the package's single sandbox error type).
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from sssf.sandbox.docker import SandboxError
from sssf.sandbox.rundb import _session_status

if TYPE_CHECKING:
    from sssf.adw_modules.data_types import IntegrationConfig




def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _has_remote(root: Path, name: str) -> bool:
    """True if the repo has a git remote under `name` (e.g. origin)."""
    return _run_git(root, "config", "--get", f"remote.{name}.url").returncode == 0


def _ref_exists(root: Path, ref: str) -> bool:
    """True when `ref` resolves to a commit. Never raises — this is a question."""
    return _run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0


def _integration_cfg(root: Path) -> "IntegrationConfig | None":
    """The project's integration config when integration is ON; None when the
    project has no adws config (plain repos, test fixtures) or has disabled it.
    A missing/undecodable config disables integration silently — those repos
    keep the legacy main-only sandbox contract."""
    try:
        from sssf.adw_modules import paths
        from sssf.adw_modules.agents import load_config

        cfg = load_config(str(paths.config_file(root)))
    except Exception:
        return None
    return cfg.integration if cfg.integration.enabled else None


def _integration_branch(root: Path) -> str | None:
    """The integration branch name when integration is enabled (branching and
    merging share this target); None for the legacy main-only flow."""
    cfg = _integration_cfg(root)
    return cfg.branch if cfg else None


def sandbox_dir(project_root: Path, adw_id: str) -> Path:
    """<repo>/.worktrees/<adw_id> — the worktree lives next to the code so
    the operator can inspect or re-run it manually after a run. Ignored via
    .git/info/exclude (never committed); cleanup is `sssf sandbox prune`."""
    return project_root / ".worktrees" / adw_id


def _exclude_worktrees(project_root: Path) -> None:
    """Keep `.worktrees/` out of `git status` — a LOCAL ignore, never committed."""
    exclude = project_root / ".git" / "info" / "exclude"
    try:
        if exclude.exists() and ".worktrees/" in exclude.read_text():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as f:
            f.write("\n.worktrees/\n")
    except OSError:
        pass  # best-effort — a repo without writable .git still runs


def create_worktree(project_root: Path, adw_id: str, attach: bool = False) -> Path:
    """git worktree add -q <dir> -b sssf/<adw_id> — the branch is unique per
    adw_id; worktrees are structurally isolated (a branch lives in one).

    Fresh runs check out origin/main, never local main: the sandbox must run
    what was actually merged, so unpushed local commits and uncommitted edits
    stay out (the snyk-gate incident). origin/main is fetched first so the
    run always sees the latest remote state. Repos with no origin remote
    (local-only) fall back to committed local main.

    attach=True attaches to the EXISTING sssf/<adw_id> branch (a restart reuses
    the previous run's branch — the ADW joins and reaps the old state).

    Fresh runs branch from the INTEGRATION branch (config integration.branch,
    default dev) when integration is enabled and that branch exists — branching
    and merging share the same target. When the branch does not exist yet, the
    run falls back to main and the first successful run creates it."""
    branch = f"sssf/{adw_id}"
    wt = sandbox_dir(project_root, adw_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    _exclude_worktrees(project_root)
    if attach:
        # A restart attaches to the run's EXISTING branch. The checkout may
        # already be there from a previous attempt — a stop/prune race can
        # leave the worktree registered while the container is gone — and `git
        # worktree add` would then fail with 'already exists', killing the
        # restart before the ADW ever starts (session 9701903a, 2026-09-02:
        # the leftover registered worktree from one stopped attempt silently
        # broke every later restart). The branch is the same, so the existing
        # checkout IS the attach target: reuse it.
        if wt.exists() and _worktree_registered(wt):
            return wt
        if wt.exists():  # unregistered leftover dir — clear before git worktree add
            shutil.rmtree(wt)
        args = ["worktree", "add", "-q", str(wt), branch]
    elif _has_remote(project_root, "origin"):
        base = _integration_branch(project_root) or "main"
        r = _run_git(project_root, "fetch", "origin", base)
        if r.returncode != 0 or not _ref_exists(project_root, f"origin/{base}"):
            if base == "main":
                raise SandboxError(f"git fetch origin main failed: {r.stderr.strip()}")
            # The integration branch does not exist upstream yet — the first
            # successful run creates it; branch from origin/main meanwhile.
            base = "main"
            r = _run_git(project_root, "fetch", "origin", "main")
            if r.returncode != 0:
                raise SandboxError(f"git fetch origin main failed: {r.stderr.strip()}")
        args = ["worktree", "add", "-q", str(wt), "-b", branch, f"origin/{base}"]
    else:
        # No remote (a local-only repo): the origin/main source of truth
        # doesn't exist, so the committed local main is the ground truth. With
        # integration on, the committed local integration branch wins when it
        # exists; a missing one falls back to local main (the first successful
        # run creates it).
        base = _integration_branch(project_root) or "main"
        if not _ref_exists(project_root, base):
            base = "main"
        args = ["worktree", "add", "-q", str(wt), "-b", branch, base]
    r = _run_git(project_root, *args)
    if r.returncode != 0:
        raise SandboxError(f"worktree add failed: {r.stderr.strip()}")
    return wt


def _worktree_registered(wt_dir: Path) -> bool:
    """True while git still registers this worktree (its admin dir exists).
    A failed `git worktree remove` (e.g. the container still held the mount)
    leaves the checkout dir behind UNREGISTERED — then it is a plain directory
    and safe to delete directly."""
    gitfile = wt_dir / ".git"
    if not gitfile.is_file():
        return False
    try:
        line = gitfile.read_text().strip()
        if line.startswith("gitdir:"):
            admin = Path(line.split(":", 1)[1].strip())
            return admin.exists()
    except OSError:
        pass
    return True  # unknown — don't delete


def remove_worktree(wt_dir: Path) -> None:
    """Idempotent: remove the worktree (force — the run's work is committed on
    its branch, and teardown must never block on stray files), then prune. If
    git's removal left the checkout behind (a teardown race), delete the dir
    directly once it is no longer registered."""
    if not wt_dir.exists():
        return
    r = subprocess.run(
        ["git", "-C", str(wt_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        root = Path(r.stdout.strip())
        subprocess.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(root), "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
        )
    if wt_dir.exists() and not _worktree_registered(wt_dir):
        import shutil

        shutil.rmtree(wt_dir, ignore_errors=True)


def delete_branch(project_root: Path, adw_id: str) -> None:
    """Idempotent: delete the run's branch ref (call after the engineer
    merged/PR'd, or to discard a failed run)."""
    _run_git(project_root, "branch", "-D", f"sssf/{adw_id}")


# ── integration: successful runs merge into the integration branch ──────────
# Branching and merging share ONE target (config integration.branch): fresh
# runs branch from it (create_worktree) and successful runs merge back into it
# here. Host-side deterministic Python — the container never merges; the merge
# runs where the operator's checkout is, so it never disturbs a dirty tree.


def _outcome(
    outcome: str,
    target: str,
    *,
    commit: str | None = None,
    pushed: bool = False,
    resolved: bool = False,
    reason: str | None = None,
    push_error: str | None = None,
) -> dict:
    """One integration outcome, shaped for the session trace event."""
    d: dict = {"outcome": outcome, "target": target, "commit": commit, "pushed": pushed, "resolved": resolved}
    if reason:
        d["reason"] = reason
    if push_error:
        d["push_error"] = push_error
    return d


def _git_head(root: Path) -> str:
    return _run_git(root, "rev-parse", "HEAD").stdout.strip()


def _push_target(root: Path, target: str) -> tuple[bool, str | None]:
    """Push local <target> to origin/<target>, creating it when missing.
    Returns (ok, error)."""
    r = _run_git(root, "push", "-q", "origin", target)
    if r.returncode == 0:
        return True, None
    return False, r.stderr.strip()[-300:] or None


def _resolve_with_agent(root: Path, adw_id: str, data_dir: Path, cfg: "IntegrationConfig") -> tuple[bool, str]:
    """Ask the coding agent to clear the in-progress merge conflict (MERGE_HEAD
    stays in place while it edits), using the resolving-merge-conflicts skill.
    The agent stages its resolution; the harness verifies and commits. Returns
    (ok, reason)."""
    try:
        from sssf.adw_modules import paths
        from sssf.adw_modules.agent_pi import run as run_agent
        from sssf.adw_modules.agents import load_config
        from sssf.adw_modules.data_types import PiRequest
    except Exception as error:
        return False, f"could not start the resolution agent: {error}"
    try:
        project_cfg = load_config(str(paths.config_file(root)))
    except Exception:
        project_cfg = None
    conflicted = _run_git(root, "diff", "--name-only", "--diff-filter=U").stdout.strip()
    skill = cfg.resolve_skill_path
    if not skill:
        pi_home = Path(os.environ.get("PI_HOME", Path.home() / ".pi" / "agent"))
        candidate = pi_home / "skills" / "resolving-merge-conflicts" / "SKILL.md"
        if candidate.is_file():
            skill = str(candidate)
    prompt = (
        f"A git merge is in progress in the repo at {root}: branch "
        f"sssf/{adw_id} is being merged into the integration branch "
        f"'{cfg.branch}'. The following files have merge conflicts:\n\n"
        f"{conflicted}\n\n"
        "Resolve every conflict following the resolving-merge-conflicts skill, "
        "then stage your resolution with `git add` (deletions included). Do NOT "
        "commit — the harness commits after you. When no unmerged paths remain "
        "(git ls-files -u is empty), you are done."
    )
    model = (project_cfg.defaults.model if project_cfg else None) or "google/gemini-3.6-flash"
    thinking = project_cfg.defaults.thinking if project_cfg else "medium"
    session_dir = Path(data_dir) / "sessions" / adw_id
    session_dir.mkdir(parents=True, exist_ok=True)
    request = PiRequest(
        prompt=prompt,
        system_prompt=(
            "You resolve git merge conflicts. The run's engine is mid-merge; do "
            "not commit, do not push, do not switch branches."
        ),
        model=model,
        thinking=thinking,
        session_id=f"{adw_id}-integration",
        session_dir=str(session_dir),
        raw_output_path=str(session_dir / "integration.jsonl"),
        skill_path=skill,
        cwd=str(root),
    )
    run_agent(request)  # may raise on a hard pi failure
    if _run_git(root, "ls-files", "-u").stdout.strip():
        return False, "agent finished with unmerged paths still present"
    return True, ""


def _finish_merged(
    root: Path, adw_id: str, target: str, cfg: "IntegrationConfig", *, resolved: bool
) -> dict:
    """Complete an in-progress merge (conflicts resolved by the agent): commit
    the staged resolution and push. Returns the merged outcome. When the agent
    already committed its own resolution, nothing is staged — fall through to
    the push unchanged."""
    if resolved and _run_git(root, "status", "--porcelain").stdout.strip():
        r = _run_git(root, "commit", "-q", "-m", f"Merge sssf/{adw_id} into {target}")
        if r.returncode != 0:
            _run_git(root, "merge", "--abort")
            return _outcome(
                "error", target, reason=f"could not commit the resolution: {r.stderr.strip()[-300:]}"
            )
    pushed = False
    push_error = None
    if _has_remote(root, "origin") and cfg.push:
        pushed, push_error = _push_target(root, target)
    return _outcome(
        "merged",
        target,
        commit=_git_head(root),
        pushed=pushed,
        resolved=resolved,
        push_error=push_error,
    )


def _integrate(root: Path, adw_id: str, cfg: "IntegrationConfig", data_dir: Path) -> dict | None:
    """The merge itself, run while holding the integration lock. The operator's
    checkout is moved to the integration branch and always restored."""
    run_ref = f"sssf/{adw_id}"
    target = cfg.branch or "dev"
    has_origin = _has_remote(root, "origin")

    # 1. Never move an operator's dirty/detached checkout.
    probe = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if probe.returncode != 0 or probe.stdout.strip() == "HEAD":
        return _outcome("skipped", target, reason="detached HEAD — nothing to integrate into")
    prev = probe.stdout.strip()
    if _run_git(root, "status", "--porcelain").stdout.strip():
        return _outcome("skipped", target, reason="dirty worktree — refusing to switch branches")
    if not _ref_exists(root, run_ref):
        return _outcome("error", target, reason=f"{run_ref} missing — nothing to merge")

    switched = False
    try:
        # 2. Land on the integration branch, creating / syncing it first.
        if has_origin:
            _run_git(root, "fetch", "origin", target)  # best-effort refresh
            on_origin = _ref_exists(root, f"origin/{target}")
        else:
            on_origin = False
        if _ref_exists(root, target):
            if _run_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != target:
                r = _run_git(root, "checkout", "-q", target)
                if r.returncode != 0:
                    return _outcome("skipped", target, reason=f"cannot check out {target}: {r.stderr.strip()[-200:]}")
                switched = True
            if has_origin and on_origin:
                r = _run_git(root, "merge", "--ff-only", f"origin/{target}")
                if r.returncode != 0:
                    return _outcome(
                        "skipped",
                        target,
                        reason=f"local {target} diverged from origin/{target} — sync it manually first",
                    )
        else:
            base_ref = (
                f"origin/{target}"
                if (has_origin and on_origin)
                else ("origin/main" if has_origin else "main")
            )
            if not _ref_exists(root, base_ref):
                return _outcome("error", target, reason=f"cannot create {target}: {base_ref} missing")
            r = _run_git(root, "checkout", "-q", "-b", target, "--no-track", base_ref)
            if r.returncode != 0:
                return _outcome("error", target, reason=f"cannot create {target}: {r.stderr.strip()[-200:]}")
            switched = True

        # 3. Nothing to do when the run's tip already lives on the target
        # (idempotent restarts, re-runs whose work already landed).
        if _run_git(root, "merge-base", "--is-ancestor", run_ref, "HEAD").returncode == 0:
            return None

        # 4. Merge (explicit --ff: the operator's ambient git config must not
        # decide the shape of integration merges). Conflicts go to the agent
        # when configured; otherwise they abort cleanly and the run's branch
        # survives for a manual merge.
        r = _run_git(root, "merge", "--no-edit", "--ff", run_ref)
        if r.returncode == 0:
            return _finish_merged(root, adw_id, target, cfg, resolved=False)
        if _run_git(root, "ls-files", "-u").stdout.strip():
            if cfg.resolve:
                ok, reason = _resolve_with_agent(root, adw_id, data_dir, cfg)
                if ok:
                    return _finish_merged(root, adw_id, target, cfg, resolved=True)
                _run_git(root, "merge", "--abort")
                return _outcome("conflicted", target, reason=reason or "agent could not resolve the conflicts")
            _run_git(root, "merge", "--abort")
            return _outcome("conflicted", target, reason="merge conflicts — resolve manually")
        _run_git(root, "merge", "--abort")
        return _outcome("error", target, reason=f"merge failed: {r.stderr.strip()[-300:]}")
    finally:
        if switched:
            _run_git(root, "checkout", "-q", prev)  # best-effort restore


def integrate_run(project_root: Path, adw_id: str, data_dir: Path) -> dict | None:
    """Merge the run's branch sssf/<adw_id> into the integration branch
    (config integration.branch, default dev), creating it from main when
    missing and pushing it upstream when the repo has an origin and config
    allows. Returns an outcome dict for the session trace, or None when there
    was nothing to do. Concurrent integrations serialize on a lock file in
    data_dir; the operator's checkout is always restored."""
    cfg = _integration_cfg(project_root)
    if cfg is None:
        return None
    lock_path = Path(data_dir) / "sessions" / "integration.lock"
    try:
        import fcntl

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return _integrate(project_root, adw_id, cfg, data_dir)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        return _integrate(project_root, adw_id, cfg, data_dir)  # no flock support


def integrate_successful_run(project_root: Path, adw_id: str) -> dict | None:
    """The monitor's post-run seam: integrate only SUCCESSFUL sessions of
    projects with integration enabled. Returns the outcome dict (for the trace
    event) or None when nothing was done."""
    from sssf.adw_modules import paths

    data_dir = paths.data_dir(project_root)
    if _session_status(data_dir, adw_id) != "success":
        return None
    if _integration_cfg(project_root) is None:
        return None
    return integrate_run(project_root, adw_id, data_dir)
