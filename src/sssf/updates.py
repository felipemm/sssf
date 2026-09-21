"""Local-source vs origin update checks for the sssf tool itself.

sssf is typically installed as an editable uv tool over a git checkout (the
repo this package is developed in), where `uv tool upgrade` is a no-op that
reinstalls the same local files. These helpers detect that layout, compare the
local checkout with its origin, and upgrade by pulling the source repo.

Contract (every function takes an explicit root so tests can drive real git
against throwaway fixtures; callers that omit it use the tree this module
ships in):

    check(root=None) -> dict    JSON-serialisable update report
    upgrade(root=None) -> int   pull when editable, else uv tool upgrade

Report shape (consumed by `sssf upgrade --check` and the viz banner):
    {"ok": true,  "local_sha", "remote_sha", "ahead", "behind",
                   "branch", "dirty", "command", "repo"}
    {"ok": false, "reason": "not-git" | "unreachable", "command", "repo"}
A behind > 0 report means an update is available; ahead-only or clean are not.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

COMMAND = "sssf upgrade"
FETCH_TIMEOUT_S = 15  # the viz check must never hang on a slow origin


def source_root() -> Path:
    """The sssf source tree this module belongs to: the repo root when the
    tool is installed editable, a site-packages tree for packaged installs."""
    return Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str, timeout: int = 30):
    """Run `git -C <root> ...` with output captured; None on timeout."""
    git = shutil.which("git") or "git"
    try:
        return subprocess.run(
            [git, "-C", str(root), *args], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None


def _is_git_checkout(root: Path) -> bool:
    # .git is a directory in a checkout, a file in a worktree/submodule
    return (root / ".git").exists()


def _rev_short(root: Path, ref: str) -> str | None:
    r = _git(root, "rev-parse", "--short", ref)
    if r is None or r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _branch(root: Path) -> str:
    r = _git(root, "branch", "--show-current")
    if r is None or r.returncode != 0:
        return ""
    return r.stdout.strip()


def check(root: Path | None = None) -> dict:
    """Is the local checkout behind its origin? Never throws and never hangs
    (the fetch is time-boxed). A failed/absent fetch reports !ok so the caller
    can stay quiet instead of inventing an update state."""
    root = Path(root) if root else source_root()
    base = {"command": COMMAND, "repo": str(root)}
    if not _is_git_checkout(root):
        return {"ok": False, "reason": "not-git", **base}
    branch = _branch(root)
    fetch = _git(root, "fetch", "origin", timeout=FETCH_TIMEOUT_S)
    if fetch is None or fetch.returncode != 0:
        return {"ok": False, "reason": "unreachable", **base}
    # Compare against the branch's own upstream when it tracks one (a feature
    # branch should not be judged against main), else origin/<branch>.
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream is not None and upstream.returncode == 0 and upstream.stdout.strip():
        upstream_ref = upstream.stdout.strip()
    else:
        upstream_ref = f"origin/{branch}" if branch else "origin/main"
    counts = _git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream_ref}")
    ahead = behind = 0
    if counts is not None and counts.returncode == 0 and counts.stdout.strip():
        left, _, right = counts.stdout.strip().partition("\t")
        ahead = int(left) if left.isdigit() else 0
        behind = int(right) if right.isdigit() else 0
    status = _git(root, "status", "--porcelain")
    dirty = (
        len([line for line in status.stdout.splitlines() if line.strip()])
        if status is not None and status.returncode == 0
        else 0
    )
    return {
        "ok": True,
        "local_sha": _rev_short(root, "HEAD"),
        "remote_sha": _rev_short(root, upstream_ref),
        "ahead": ahead,
        "behind": behind,
        "branch": branch or "detached",
        "dirty": dirty,
        **base,
    }


def upgrade(root: Path | None = None) -> int:
    """Update the running tool. For an editable git install: git pull on the
    source branch, then point the operator at the restart step. For a packaged
    install: the uv tool upgrade path. Prints the git output + guidance."""
    root = Path(root) if root else source_root()
    if not _is_git_checkout(root):
        return subprocess.call(["uv", "tool", "upgrade", "sssf"])
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream is None or upstream.returncode != 0 or not upstream.stdout.strip():
        branch = _branch(root)
        print(
            f"sssf: {root} is on branch '{branch or '(detached)'}' with no upstream to pull — "
            "switch to a tracking branch (e.g. main) and retry",
            file=sys.stderr,
        )
        return 1
    status = _git(root, "status", "--porcelain")
    if status is not None and status.returncode == 0 and status.stdout.strip():
        print(
            "sssf: working tree has uncommitted changes — the pull may refuse "
            "if it would clobber them (that is fine; git says so)",
            file=sys.stderr,
        )
    git = shutil.which("git") or "git"
    rc = subprocess.call([git, "-C", str(root), "pull", "--ff-only"])
    if rc == 0:
        print(
            "\nsssf updated. Restart the running services to load the new code:\n"
            "  sssf viz stop && sssf viz\n"
            "  sssf heal stop && sssf heal start"
        )
    return rc
