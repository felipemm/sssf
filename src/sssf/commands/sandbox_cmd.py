"""`sssf sandbox build|list|prune` — deterministic sandbox lifecycle commands."""
from __future__ import annotations

import subprocess
from pathlib import Path

from sssf import registry
from sssf.project import find_project


def _root(explicit: str | None) -> Path | None:
    return find_project(Path.cwd(), explicit)


def build(explicit: str | None) -> int:
    from sssf.sandbox import SandboxError, build_image, docker_available
    root = _root(explicit)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    if not docker_available():
        print("sssf: docker is not available — install/start Docker Desktop first.",
              file=sys.stderr)
        return 1
    dockerfile = root / ".." / ".." / "docker" / "sssf-runner.Dockerfile"
    # The Dockerfile lives in the sssf source tree; fall back to the package's
    # installed copy if the repo isn't next to the project.
    candidates = [
        dockerfile.resolve(),
        Path(__file__).resolve().parents[2] / "docker" / "sssf-runner.Dockerfile",
    ]
    df = next((c for c in candidates if c.exists()), None)
    if df is None:
        print("sssf: docker/sssf-runner.Dockerfile not found.", file=sys.stderr)
        return 1
    try:
        from sssf.adw_modules.agents import load_config
        cfg = load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml"))
        build_image(cfg.sandbox.image, df)
    except SandboxError as e:
        print(f"sssf: image build failed: {e}", file=sys.stderr)
        return 1
    print(f"sssf: image built ({cfg.sandbox.image})")
    return 0


def list_(explicit: str | None) -> int:
    root = _root(explicit)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    from sssf.sandbox import container_name, sandbox_dir
    import os
    base = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / root.name
    rows = []
    if base.is_dir():
        for wt_dir in sorted(base.iterdir()):
            if not wt_dir.is_dir():
                continue
            adw_id = wt_dir.name
            branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--list", f"sssf/{adw_id}"],
                capture_output=True, text=True).stdout.strip()
            status = "sandboxed"
            rows.append((adw_id, status, branch or f"sssf/{adw_id}", container_name(adw_id), str(wt_dir)))
    if not rows:
        print("no sandboxes")
        return 0
    print(f"{'adw_id':<10} {'status':<10} {'branch':<22} {'container':<16} worktree")
    for adw_id, status, branch, name, wt in rows:
        print(f"{adw_id:<10} {status:<10} {branch:<22} {name:<16} {wt}")
    return 0


def prune(explicit: str | None, adw_id: str | None, all_: bool) -> int:
    root = _root(explicit)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    from sssf.sandbox import prune_sandbox, sandbox_dir
    import os
    base = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / root.name
    if all_:
        if not base.is_dir():
            print("no sandboxes to prune")
            return 0
        ids = sorted(d.name for d in base.iterdir() if d.is_dir())
    elif adw_id:
        ids = [adw_id]
    else:
        print("sssf: usage: sssf sandbox prune <adw_id> | --all", file=sys.stderr)
        return 1
    for _id in ids:
        prune_sandbox(root, _id)
        print(f"pruned {_id}")
    return 0
