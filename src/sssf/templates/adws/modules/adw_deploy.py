#!/usr/bin/env -S uv run
"""ADW Deploy — the deterministic release train: bump → MR → e2e → release.

Usage:
    uv run adws/adw_deploy.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: code(bump) -> code(mr) -> [e2e quality] -> code(release)

The batch-level signoff and the QA workbench live in the HOST flow (#96) —
the operator's terminal verdict cannot be answered from a detached runner
container, and the workbench (a container from the `dev` branch with a
published port) is the deploy flow's own, brought up and torn down by
`sssf flow deploy` / `--down`. The flow runs THIS chain in a release worktree
checked out at `dev`, so the bump lands on dev, the MR dev→main carries the
clean snapshot, and the per-run db is merged back into the project db. The
canary/promote/close-by-commits release mechanics land with #97 — this chain
computes the release candidate only.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from sssf.adw_modules import agents, git_helper, session, utils
from sssf.adw_modules.chains import (
    Chain,
    ChainFailure,
    CodePhase,
    QualityLoop,
    run_chain,
)

DEPLOY_CONFIG = "adws/config/deploy.yaml"
VERSION_PATTERNS = (
    (re.compile(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"'), 'version = "{v}"'),
    (re.compile(r'"version"\s*:\s*"(\d+)\.(\d+)\.(\d+)"'), '"version": "{v}"'),
    (re.compile(r"^(\d+)\.(\d+)\.(\d+)\s*$", re.MULTILINE), "{v}"),
)


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip()


def _bump_patch(text: str) -> str | None:
    """Bump the first recognizable x.y.z version in text by one patch; None when
    the file has no version to bump."""
    for pattern, fmt in VERSION_PATTERNS:
        m = pattern.search(text)
        if m:
            next_version = f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3)) + 1}"
            return text.replace(m.group(0), fmt.format(v=next_version), 1)
    return None


def _snapshot_summary(run) -> str:
    """Commits on the dev branch not in main — the batch this deploy ships."""
    root = Path(run.repo_root)
    base = "origin/main" if _git(root, "rev-parse", "--verify", "-q", "origin/main") else "main"
    head = "dev" if _git(root, "rev-parse", "--verify", "-q", "dev") else "origin/dev"
    if not head or not _git(root, "rev-parse", "--verify", "-q", head):
        return ""
    return _git(root, "log", "--oneline", f"{base}..{head}")


def _deploy_bump(run, ph, previous):
    """Advance the patch version in the version files listed in
    adws/config/deploy.yaml (defaults: pyproject.toml, package.json)."""
    root = Path(run.repo_root)
    files = ["pyproject.toml", "package.json"]
    cfg = root / DEPLOY_CONFIG
    if cfg.exists():
        import yaml

        data = yaml.safe_load(cfg.read_text()) or {}
        files = data.get("version_files") or files
    bumped: list[str] = []
    for rel in files:
        target = root / rel
        if not target.exists():
            continue
        updated = _bump_patch(target.read_text())
        if updated is None or updated == target.read_text():
            continue
        target.write_text(updated)
        bumped.append(rel)
    if not bumped:
        raise ChainFailure(
            f"no version file to bump (looked at {', '.join(files)} in {root}) — "
            "list your version files in adws/config/deploy.yaml"
        )
    message = f"chore: bump version ({', '.join(bumped)})"
    git_helper.commit_all(message, allow_empty=False)
    ph.log(files=bumped, message=message)
    return None


def _deploy_mr(run, ph, previous):
    """Open the dev→main MR carrying the clean snapshot. When the glab CLI is
    available the MR is opened and the mapping (title/url/iid) is recorded as
    `<adw_id>-mr.json` for the host flow's settle (#96); otherwise the payload
    is recorded under adws/data/deploy/ for the operator (no MR to register)."""
    from shutil import which

    root = Path(run.repo_root)
    summary = _snapshot_summary(run)
    commits = summary.splitlines() or []
    tickets = sorted({t for t in re.findall(r"#(\d+)", summary) if t})
    title = (
        f"release: {len(commits)} commit(s) from dev — "
        f"{', '.join(f'#{t}' for t in tickets[:5]) or 'batch'}"
    )
    body = f"Batch dev → main ({len(commits)} commit(s)).\n\nCloses: {', '.join(f'#{t}' for t in tickets) or '—'}"
    record: dict = {"title": title, "url": "", "iid": ""}
    glab = which("glab")
    if glab:
        r = subprocess.run(
            [
                glab,
                "mr",
                "create",
                "--source",
                "dev",
                "--target",
                "main",
                "--title",
                title,
                "--description",
                body,
                "--yes",
            ],
            capture_output=True,
            text=True,
            cwd=root,
        )
        if r.returncode != 0:
            raise ChainFailure(f"glab mr create failed: {r.stderr.strip()[:400]}")
        url = r.stdout.strip()
        record = {
            "title": title,
            "url": url,
            "iid": url.rstrip("/").split("/")[-1] if url else "",
        }
        ph.log(mr=url, commits=len(commits))
    else:
        out = root / "adws" / "data" / "deploy" / "mr_payload.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# {title}\n\n{body}\n")
        git_helper.commit_all(
            f"chore: record MR payload ({len(commits)} commit(s))", allow_empty=True
        )
        ph.log(
            note="glab not available — MR payload recorded",
            payload=str(out.relative_to(root)),
            commits=len(commits),
        )
    rec = root / "adws" / "data" / "deploy" / f"{run.adw_id}-mr.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps(record))
    return None


def _deploy_release(run, ph, previous):
    """Parse the release candidate: commits since the last tag, the ticket ids
    they reference, and the next version anchor. The tag creation, canary
    step, promote, and close-by-commits ticket closure land with #97."""
    root = Path(run.repo_root)
    last_tag = _git(root, "describe", "--tags", "--abbrev=0") or ""
    since = f"{last_tag}..HEAD" if last_tag else "HEAD"
    commits = _git(root, "log", "--oneline", since).splitlines()
    tickets = sorted({t for t in re.findall(r"#(\d+)", "\n".join(commits)) if t})
    ph.log(
        anchor=last_tag or "(first release — no prior tag)",
        commits=len(commits),
        tickets=", ".join(f"#{t}" for t in tickets) or "—",
        note="close-by-commits, canary, and promote land with #97",
    )
    return None


CHAIN = Chain(
    name="deploy",
    required_agents=[],
    phases=[
        CodePhase(
            "bump",
            "deploy",
            _deploy_bump,
            description="Advance the patch version in the project's version files",
        ),
        CodePhase(
            "mr",
            "deploy",
            _deploy_mr,
            description="Open (or record) the dev→main merge request carrying the clean snapshot",
        ),
        QualityLoop(name="e2e"),
        CodePhase(
            "release",
            "deploy",
            _deploy_release,
            description="Compute the release candidate from commits since the last tag",
        ),
    ],
)


def main(prompt: str, config: str | None = None, adw_id: str | None = None) -> int:
    cfg = agents.load_config(config or agents.default_config_path())
    run = session.ensure(cfg, adw_id)
    return run_chain(cfg, run, prompt, CHAIN)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt", nargs="?", default="", help="inline text or a path to a prompt file"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="path to sssf.config.yaml (default: adws/config/sssf.config.yaml)",
    )
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))
