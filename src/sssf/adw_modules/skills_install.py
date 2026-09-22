"""Project-local workflow skills installer (hermetic closure, #88).

Stamps the transitive closure of the workflow skills into
`<project>/.pi/skills/` — NEVER the user's global pi home. A version marker
(`.sssf-versions.json`) pins each stamped skill to its source commit so
`sssf doctor` can report staleness and `sssf init --refresh` can update.

The stamp root is the twelve workflow skills the factory's flows reference
(`WORKFLOW_SKILLS`) plus `brainstorming` (the spec_interviewer prompts invoke
it). `closure()` expands the root: every included skill's files are scanned
for references to OTHER manifest skills (by frontmatter name) and those are
included too, recursively, to a fixpoint. External setup references inside
the skills' own prose (`/setup-matt-pocock-skills`) are rewritten to sssf's
own stamped-config commands, so a stamped project never directs its agents at
another tool's setup flow.
"""

from __future__ import annotations

import json
import re
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
    "wayfinder": ("https://github.com/mattpocock/skills.git", "skills/engineering/wayfinder"),
    "to-spec": ("https://github.com/mattpocock/skills.git", "skills/engineering/to-spec"),
    "to-tickets": ("https://github.com/mattpocock/skills.git", "skills/engineering/to-tickets"),
    "triage": ("https://github.com/mattpocock/skills.git", "skills/engineering/triage"),
    "implement": ("https://github.com/mattpocock/skills.git", "skills/engineering/implement"),
    "code-review": ("https://github.com/mattpocock/skills.git", "skills/engineering/code-review"),
    "tdd": ("https://github.com/mattpocock/skills.git", "skills/engineering/tdd"),
    "domain-modeling": ("https://github.com/mattpocock/skills.git", "skills/engineering/domain-modeling"),
    "codebase-design": ("https://github.com/mattpocock/skills.git", "skills/engineering/codebase-design"),
}

# The closure root (#88): the workflow skills the plan/implement flows reference
# by name. All live in mattpocock/skills; a new workflow skill starts working
# in the same change that adds it here (and to the roster's per-agent subsets).
WORKFLOW_SKILLS: tuple[str, ...] = (
    "grill-me",
    "wayfinder",
    "grill-with-docs",
    "to-spec",
    "to-tickets",
    "triage",
    "implement",
    "code-review",
    "tdd",
    "grilling",
    "domain-modeling",
    "codebase-design",
)

# External setup tooling referenced inside the skills' own prose — rewritten to
# sssf's own stamped-config commands. Ordered (find, replace) pairs: the
# backticked form first so the replacement never nests inside backticks.
REWRITES: tuple[tuple[str, str], ...] = (
    ("`/setup-matt-pocock-skills`", "`sssf init --refresh`"),
    ("/setup-matt-pocock-skills", "sssf init --refresh"),
)

MARKER = ".sssf-versions.json"


def skills_dir(root: Path) -> Path:
    return root / ".pi" / "skills"


def marker_path(root: Path) -> Path:
    return skills_dir(root) / MARKER


def _git(*args: str, cwd: Path | None = None, timeout_s: int = 300):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout_s
    )


def _stamp_root() -> tuple[str, ...]:
    """The closure seed: the workflow skills plus brainstorming (the
    spec_interviewer prompts invoke it). Reads the module constants at call
    time so tests can shrink the root."""
    return (*WORKFLOW_SKILLS, "brainstorming")


def _referenced_skills(skill_dir: Path) -> set[str]:
    """Names of other manifest skills this skill's files mention — the
    transitive edges. Scans every text file in the directory (SKILL.md,
    reference docs, agent defs) for each manifest name as a whole word."""
    found: set[str] = set()
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in SOURCES) + r")\b"
    )
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for match in pattern.findall(text):
            found.add(match)
    return found


def closure(fetched: dict[str, Path]) -> set[str]:
    """The transitive closure of the stamp root over the fetched manifest.

    `fetched` maps skill name -> its freshly cloned directory. Start from the
    root, include every referenced skill, to a fixpoint. A superset is safe —
    hermetic loading only ever reads what the roster names.
    """
    included = set(_stamp_root())
    changed = True
    while changed:
        changed = False
        for name in list(included):
            for ref in _referenced_skills(fetched[name]):
                if ref not in included:
                    included.add(ref)
                    changed = True
    return included


def _rewrite_external_setup(target: Path) -> None:
    """Rewrite external setup references in every stamped .md file in place."""
    for path in target.rglob("*.md"):
        text = path.read_text()
        updated = text
        for old, new in REWRITES:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated)


def install_skills(root: Path, *, refresh: bool = False) -> int:
    """Fetch the manifest, stamp the workflow closure into <root>/.pi/skills/,
    rewrite external setup references, and write the marker. Returns 0 on
    success; prints and returns 1 on fetch failure."""
    target = skills_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, Path] = {}
    heads: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="sssf-skills-") as tmp:
        tmp_path = Path(tmp)
        for skill, (repo, rel) in SOURCES.items():
            repo_dir = tmp_path / repo.split("/")[-1].removesuffix(".git")
            if repo not in heads:
                r = _git("clone", "--depth", "1", repo, str(repo_dir))
                if r.returncode != 0:
                    print(f"sssf: could not fetch {repo} ({r.stderr.strip()[:200]})", file=sys.stderr)
                    return 1
                heads[repo] = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()
            src = repo_dir / rel
            if not src.is_dir():
                print(f"sssf: skill {skill} not found at {rel} in {repo}", file=sys.stderr)
                return 1
            fetched[skill] = src
        stamped = closure(fetched)
        pinned: dict[str, dict[str, str]] = {}
        for skill in sorted(stamped):
            src = fetched[skill]
            repo = SOURCES[skill][0]
            dst = target / skill
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            _rewrite_external_setup(dst)
            pinned[skill] = {"source": repo, "path": SOURCES[skill][1], "commit": heads[repo]}
    marker_path(root).write_text(json.dumps(pinned, indent=2))
    print(f"sssf: installed {len(pinned)} workflow skills into {target} (project-local)")
    return 0


def check_skills(root: Path) -> dict:
    """{skill: {present, pinned, latest, stale}} — for `sssf doctor`.
    Offline (ls-remote fails) -> latest None, stale False. One ls-remote per
    source repo — the manifest shares repos across skills."""
    out: dict = {}
    target = skills_dir(root)
    marker = marker_path(root)
    pinned: dict = {}
    if marker.exists():
        try:
            pinned = json.loads(marker.read_text())
        except json.JSONDecodeError:
            pinned = {}
    heads: dict[str, str | None] = {}
    for skill, (repo, _rel) in SOURCES.items():
        present = (target / skill / "SKILL.md").is_file()
        p = pinned.get(skill, {}).get("commit")
        if repo not in heads:
            r = _git("ls-remote", repo, "HEAD", timeout_s=30)
            heads[repo] = r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else None
        latest = heads[repo]
        stale = bool(p) and latest is not None and latest != p
        out[skill] = {"present": present, "pinned": p, "latest": latest, "stale": stale}
    return out
