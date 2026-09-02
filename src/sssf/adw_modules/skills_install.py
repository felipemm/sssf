"""Project-local interview skills installer (spec-create).

Installs the grilling/brainstorming skills into `<project>/.pi/skills/` —
NEVER the user's global pi home. A version marker (`.sssf-versions.json`)
pins each skill to its source commit so `sssf doctor` can report staleness
and `sssf init --refresh` can update.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# skill -> (repo, path-within-repo)
SOURCES: dict[str, tuple[str, str]] = {
    "brainstorming": ("https://github.com/obra/superpowers.git", "skills/brainstorming"),
    "grilling": ("https://github.com/mattpocock/skills.git", "skills/productivity/grilling"),
    "grill-me": ("https://github.com/mattpocock/skills.git", "skills/productivity/grill-me"),
    "grill-with-docs": ("https://github.com/mattpocock/skills.git", "skills/engineering/grill-with-docs"),
}

MARKER = ".sssf-versions.json"


def skills_dir(root: Path) -> Path:
    return root / ".pi" / "skills"


def marker_path(root: Path) -> Path:
    return skills_dir(root) / MARKER


def _git(*args: str, cwd: Path | None = None, timeout_s: int = 300):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout_s
    )


def install_skills(root: Path, *, refresh: bool = False) -> int:
    """Fetch the four skills into <root>/.pi/skills/ and write the marker.
    Returns 0 on success; prints and returns 1 on fetch failure."""
    target = skills_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    pinned: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="sssf-skills-") as tmp:
        tmp_path = Path(tmp)
        for skill, (repo, rel) in SOURCES.items():
            repo_dir = tmp_path / repo.split("/")[-1].removesuffix(".git")
            if repo not in seen:
                r = _git("clone", "--depth", "1", repo, str(repo_dir))
                if r.returncode != 0:
                    print(f"sssf: could not fetch {repo} ({r.stderr.strip()[:200]})", file=sys.stderr)
                    return 1
                seen.add(repo)
            src = repo_dir / rel
            if not src.is_dir():
                print(f"sssf: skill {skill} not found at {rel} in {repo}", file=sys.stderr)
                return 1
            dst = target / skill
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            head = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()
            pinned[skill] = {"source": repo, "path": rel, "commit": head}
    marker_path(root).write_text(json.dumps(pinned, indent=2))
    print(f"sssf: installed {len(pinned)} interview skills into {target} (project-local)")
    return 0


def check_skills(root: Path) -> dict:
    """{skill: {present, pinned, latest, stale}} — for `sssf doctor`.
    Offline (ls-remote fails) -> latest None, stale False."""
    out: dict = {}
    target = skills_dir(root)
    marker = marker_path(root)
    pinned: dict = {}
    if marker.exists():
        try:
            pinned = json.loads(marker.read_text())
        except json.JSONDecodeError:
            pinned = {}
    for skill, (repo, _rel) in SOURCES.items():
        present = (target / skill / "SKILL.md").is_file()
        p = pinned.get(skill, {}).get("commit")
        latest = None
        stale = False
        r = _git("ls-remote", repo, "HEAD", timeout_s=30)
        if r.returncode == 0 and r.stdout.strip():
            latest = r.stdout.split()[0]
            stale = bool(p) and latest != p
        out[skill] = {"present": present, "pinned": p, "latest": latest, "stale": stale}
    return out
