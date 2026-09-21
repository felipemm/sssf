"""`sssf sandbox build|list|prune|stop|restart` — sandbox lifecycle commands.

stop/restart are the run-control operations that moved here when ad-hoc
`sssf run` was removed (#89) — the viz trace page shells them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sssf.project import find_project


def _root(explicit: str | None) -> Path | None:
    return find_project(Path.cwd(), explicit)


def build(explicit: str | None) -> int:
    from sssf.sandbox import SandboxError, build_runner_image, docker_available

    root = _root(explicit)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    from sssf.adw_modules import paths

    paths.warn_if_legacy(root, command="sandbox")
    if not docker_available():
        print(
            "sssf: docker is not available — install/start Docker Desktop first.", file=sys.stderr
        )
        return 1
    try:
        from sssf.adw_modules import paths
        from sssf.adw_modules.agents import load_config

        try:
            cfg = load_config(str(paths.config_file(root)))
        except Exception as error:
            print(
                f"sssf: cannot read {paths.config_file(root)} ({error}) — "
                "if this project predates the v2 layout, run `sssf init --refresh`",
                file=sys.stderr,
            )
            return 1
        print(
            f"building {cfg.sandbox.image} — cache-cold builds download "
            "pi/bun/snyk/Chrome from the network and can take many minutes "
            "(docker progress streams below)",
            file=sys.stderr,
        )
        build_runner_image(cfg.sandbox.image, stream=True)
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
    import os

    from sssf.sandbox import container_name

    base = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / root.name
    rows = []
    if base.is_dir():
        for wt_dir in sorted(base.iterdir()):
            if not wt_dir.is_dir():
                continue
            adw_id = wt_dir.name
            branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--list", f"sssf/{adw_id}"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            status = "sandboxed"
            rows.append(
                (adw_id, status, branch or f"sssf/{adw_id}", container_name(adw_id), str(wt_dir))
            )
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
    import os

    from sssf.sandbox import SandboxError, prune_sandbox

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
        try:
            prune_sandbox(root, _id)
        except SandboxError as e:
            print(f"sssf: {e}", file=sys.stderr)
            return 0
        print(f"pruned {_id}")
    return 0


def stop(explicit: str | None, adw_id: str) -> int:
    """`sssf sandbox stop <adw_id>` — stop a live run's container and session."""
    from sssf.sandbox import sandbox_env, stop_run

    root = _root(explicit)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    data_dir, _pi, _env = sandbox_env(root)
    return stop_run(root, adw_id, data_dir)


def restart(explicit: str | None, adw_id: str) -> int:
    """`sssf sandbox restart <adw_id>` — re-run a session, reusing its adw_id
    (the ADW joins and reaps the old state) with the original request as the
    prompt. The sandbox attaches to the existing sssf/<adw_id> branch. The ADW
    that ORIGINALLY ran the session is re-run — never a hardcoded default."""
    import sqlite3

    from sssf.sandbox import _session_status, project_db_path, reopen_session, sandbox_env

    root = _root(explicit)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    data_dir, _pi, _env = sandbox_env(root)
    if _session_status(data_dir, adw_id) is None:
        print(f"sssf: no session {adw_id}", file=sys.stderr)
        return 1
    db_path = project_db_path(data_dir)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    row = conn.execute(
        "SELECT request, adw_name FROM sessions WHERE adw_id=?", (adw_id,)
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        print(f"sssf: session {adw_id} has no request to re-run", file=sys.stderr)
        return 1
    # Re-open the host session row: a restart attaches to the existing branch,
    # and the monitor's forward-merge never flips a TERMINAL host row back to
    # running — without this the UI keeps showing the previous run's fail/end
    # state and the restarted run's own outcome is never recorded either.
    reopen_session(data_dir, adw_id)
    # Re-run the ADW that ORIGINALLY ran the session, never a hardcoded
    # default. sessions.adw_name records every ADW that joined, newest
    # appended; the FIRST is the original run.
    original = (row[1] or "").split(" + ", 1)[0].strip()
    if original and not original.startswith("adw_"):
        original = f"adw_{original}"
    adw_file = _adw_file(root, original) if original else None
    if adw_file is None:
        print(
            f"sssf: session {adw_id} ran '{row[1] or '?'}' — no adw module or "
            "installed template by that name to re-run",
            file=sys.stderr,
        )
        return 1
    return _run_sandboxed(root, adw_file, [row[0]], adw_id=adw_id, attach=True)


def _adw_file(root: Path, name: str) -> Path | None:
    """Prefer the INSTALLED template for standard ADWs; custom ADWs fall back
    to the project's file. (Shared with sssf.commands.flow.)"""
    from sssf.commands.flow import _adw_file as flow_adw_file

    return flow_adw_file(root, name)


def _run_sandboxed(
    root: Path, adw_file: Path, args: list[str], adw_id: str | None = None, attach: bool = False
) -> int:
    """Run an ADW inside the per-run sandbox. (Shared with sssf.commands.flow.)"""
    from sssf.commands.flow import _run_sandboxed as flow_run_sandboxed

    return flow_run_sandboxed(root, adw_file, args, adw_id=adw_id, attach=attach)
