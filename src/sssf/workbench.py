"""The deploy flow's QA workbench (issue #96, ADR-0004).

The runner sandbox executes work; the WORKBENCH is the interactive QA surface:
a container from the `dev` branch (the batch snapshot) with mocks and a
published port, brought up by `sssf flow deploy` and torn down by the human
(`sssf flow deploy --down`, or automatically when a batch is rejected at
signoff — "the workbench is rebuilt" after the fix). It replaces the fused
`review.command` machinery: the runner container no longer idles to host an
app; the workbench is its own disposable container.

Deterministic plain Python — no agents. The workbench worktree is a DETACHED
checkout at the dev tip (read-only QA surface — the human never commits
there); the release-train worktree where the deploy chain lands its bump is
the chain's own checkout at the `dev` branch.
"""

from __future__ import annotations

import os
import shlex
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from sssf import db_schema
from sssf.sandbox.docker import _docker
from sssf.sandbox.worktree_git import _exclude_worktrees, remove_worktree

DEPLOY_CONFIG = "adws/config/deploy.yaml"


class WorkbenchError(RuntimeError):
    """Raised when a workbench lifecycle step fails deterministically."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The schema contract: workbench_runs comes from the db_schema models."""
    db_schema.apply_schema(conn)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def workbench_config(root: Path) -> dict:
    """The project's workbench block from adws/config/deploy.yaml:
    `{command: [...], container_port: N}`. Raises WorkbenchError when missing —
    the workbench is a required part of the deploy flow, never silently
    skipped."""
    path = root / DEPLOY_CONFIG
    if not path.exists():
        raise WorkbenchError(f"no {DEPLOY_CONFIG} — the workbench needs a `workbench:` block")
    data = yaml.safe_load(path.read_text()) or {}
    wb = data.get("workbench") or {}
    command = wb.get("command")
    port = wb.get("container_port")
    if not command or not port:
        raise WorkbenchError(
            f"{DEPLOY_CONFIG} needs a `workbench:` block with `command` (argv list)"
            " and `container_port` (the app's port inside the container)"
        )
    return {"command": list(command), "container_port": int(port)}


def dev_ref(root: Path) -> str | None:
    """The dev integration branch ref (`dev` or `origin/dev`), or None when
    the project has no release train yet. The single source for "where the
    release train lives" (the workbench QA's it, deploy ships it)."""
    for ref in ("dev", "origin/dev"):
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return ref
    return None


def workbench_wt(root: Path, adw_id: str) -> Path:
    """<root>/.worktrees/wb-<adw_id> — the workbench's detached dev checkout."""
    return root / ".worktrees" / f"wb-{adw_id}"


def workbench_name(adw_id: str) -> str:
    return f"sssf-wb-{adw_id}"


def _create_worktree_at_dev(root: Path, adw_id: str, ref: str) -> Path:
    """A detached checkout at the dev tip — the batch snapshot the human QA's.
    Detached by design: the QA surface is read-only; the release train's bump
    lands on the `dev` branch proper, in the deploy chain's own worktree."""
    wt = workbench_wt(root, adw_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    _exclude_worktrees(root)
    r = subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "--detach", "-q", str(wt), ref],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise WorkbenchError(f"workbench worktree add failed: {r.stderr.strip()[:300]}")
    return wt


def _host_port(name: str, container_port: int) -> int | None:
    """Resolve the random host port docker published for the app's container
    port (`docker port <name> <port>/tcp`); None when the mapping is not
    resolvable yet."""
    r = _docker("port", name, f"{container_port}/tcp", timeout_s=30)
    if r.returncode == 0 and r.stdout.strip():
        try:
            return int(r.stdout.strip().split(":")[-1])
        except ValueError:
            return None
    return None


def _record(
    root: Path,
    adw_id: str,
    name: str,
    wt: Path,
    container_port: int | None,
    host_port: int | None,
    url: str,
    status: str,
) -> None:
    """Record/refresh one workbench row in the project db. Best-effort: a
    write hiccup must never kill the flow — the container is the truth."""
    try:
        from sssf.adw_modules import paths

        conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
        ensure_schema(conn)
        now = _now()
        conn.execute(
            "INSERT INTO workbench_runs (adw_id, container, worktree, container_port,"
            " host_port, url, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(adw_id) DO UPDATE SET"
            " container=excluded.container, worktree=excluded.worktree,"
            " container_port=excluded.container_port, host_port=excluded.host_port,"
            " url=excluded.url, status=excluded.status, updated_at=excluded.updated_at",
            (adw_id, name, str(wt), container_port, host_port, url, status, now, now),
        )
        conn.commit()
        conn.close()
    except (sqlite3.Error, OSError):
        pass


def bring_up(root: Path, adw_id: str, image: str) -> dict:
    """Start the QA workbench: a detached worktree at the dev tip plus a
    container running the workbench command with the container port published
    loopback-only on a random host port. Returns the workbench record
    (adw_id, container, worktree, url, host_port)."""
    cfg = workbench_config(root)
    ref = dev_ref(root)
    if ref is None:
        raise WorkbenchError(
            "no dev integration branch — the workbench QA's the dev snapshot,"
            " and there is nothing to QA yet"
        )
    import shutil

    if shutil.which("docker") is None:
        raise WorkbenchError(
            "docker is not available — the workbench is a container from the dev"
            " branch; install/start docker and retry"
        )
    wt = _create_worktree_at_dev(root, adw_id, ref)
    name = workbench_name(adw_id)
    command = ["bash", "-lc", " ".join(shlex.quote(c) for c in cfg["command"])]
    args = [
        "run",
        "-d",
        "--name",
        name,
        "-v",
        f"{wt}:/work",
        "-w",
        "/work",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-p",
        f"127.0.0.1::{cfg['container_port']}",
        image,
        *command,
    ]
    r = _docker(*args, timeout_s=300)
    if r.returncode != 0:
        _docker("rm", "-f", name)  # no-op when the container never started
        remove_worktree(wt)
        raise WorkbenchError(f"workbench container failed to start: {r.stderr.strip()[:300]}")
    host_port = _host_port(name, cfg["container_port"])
    url = f"http://127.0.0.1:{host_port}" if host_port else ""
    _record(root, adw_id, name, wt, cfg["container_port"], host_port, url, status="up")
    return {
        "adw_id": adw_id,
        "container": name,
        "worktree": str(wt),
        "url": url,
        "host_port": host_port,
    }


def live_workbenches(root: Path) -> list[dict]:
    """The project's recorded workbenches still up, newest first. Best-effort
    read — a torn-down ('down') record is history, not a live surface."""
    try:
        from sssf.adw_modules import paths

        conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT adw_id, container, worktree, container_port, host_port, url, status"
            " FROM workbench_runs WHERE status != 'down' ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return [
            {
                "adw_id": r[0],
                "container": r[1],
                "worktree": r[2],
                "container_port": r[3],
                "host_port": r[4],
                "url": r[5],
                "status": r[6],
            }
            for r in rows
        ]
    except (sqlite3.Error, OSError):
        return []


def tear_down(root: Path, adw_id: str | None = None) -> bool:
    """Tear the workbench down (the human's `--down`, or a rejected batch):
    remove the container and the detached worktree, mark the record 'down'.
    Returns True when something was actually removed; False when there is no
    workbench to tear down."""
    wbs = live_workbenches(root)
    if adw_id:
        target = next((w for w in wbs if w["adw_id"] == adw_id), None)
    else:
        target = wbs[0] if wbs else None
    if target is None:
        return False
    _docker("rm", "-f", target["container"], timeout_s=60)  # absent container is fine
    try:
        wt = Path(target["worktree"])
        if wt.exists():
            remove_worktree(wt)
    except OSError:
        pass
    _record(
        root,
        target["adw_id"],
        target["container"],
        Path(target["worktree"]),
        target["container_port"],
        target["host_port"],
        target["url"] or "",
        status="down",
    )
    return True
