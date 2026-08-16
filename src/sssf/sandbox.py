"""Deterministic sandbox lifecycle: ports, worktrees, docker, review records.

Every function here is plain Python — no agents, no ad-hoc steps. Creation
and teardown are idempotent so a crash mid-teardown leaves re-runnable
cleanup.
"""
import itertools
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot fulfil a request (no free port, etc.)."""


def allocate_port(base: int, used: set[int] | None = None) -> int:
    """First free host port >= base. Bind-tests 127.0.0.1 so parallel runs of
    the same project don't collide; `used` skips ports already handed out
    this session. Raises SandboxError once the scan passes 65535."""
    used = used or set()
    for port in itertools.count(base):
        if port > 65535:
            raise SandboxError(f"no free host port from {base}")
        if port in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def sandbox_dir(project_root: Path, adw_id: str) -> Path:
    """~/.sssf/sandboxes/<project-basename>/<adw_id> — the repo tree stays clean."""
    home = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf"))
    return home / "sandboxes" / project_root.name / adw_id


def create_worktree(project_root: Path, adw_id: str) -> Path:
    """git worktree add -q <dir> -b sssf/<adw_id>. The branch is unique per
    adw_id; worktrees are structurally isolated (a branch lives in one)."""
    branch = f"sssf/{adw_id}"
    wt = sandbox_dir(project_root, adw_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    r = _run_git(project_root, "worktree", "add", "-q", str(wt), "-b", branch)
    if r.returncode != 0:
        raise SandboxError(f"worktree add failed: {r.stderr.strip()}")
    return wt


def remove_worktree(wt_dir: Path) -> None:
    """Idempotent: remove the worktree (force — the run's work is committed on
    its branch, and teardown must never block on stray files), then prune."""
    if not wt_dir.exists():
        return
    # The worktree is registered in the repo that owns it — resolve the repo
    # from the worktree's own gitdir metadata via `git -C <wt> rev-parse`.
    r = subprocess.run(
        ["git", "-C", str(wt_dir), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 0:
        root = Path(r.stdout.strip())
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(wt_dir)],
                       capture_output=True, text=True, check=False)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"],
                       capture_output=True, text=True, check=False)


def delete_branch(project_root: Path, adw_id: str) -> None:
    """Idempotent: delete the run's branch ref (call after the engineer
    merged/PR'd, or to discard a failed run)."""
    _run_git(project_root, "branch", "-D", f"sssf/{adw_id}")


def _docker(*args: str, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    # Resolve per call (not at import): the fake-docker tests swap PATH after
    # import, and shutil.which at module level would pin the real binary.
    docker = shutil.which("docker") or "docker"
    return subprocess.run([docker, *args], capture_output=True, text=True, check=False, timeout=timeout_s)


def docker_available() -> bool:
    r = _docker("info")
    return r.returncode == 0


def build_image(image: str, dockerfile: Path) -> None:
    r = _docker("build", "-f", str(dockerfile), str(dockerfile.parent))
    if r.returncode != 0:
        raise SandboxError(f"docker build failed: {r.stderr.strip()[:500]}")


def run_sandbox(
    image: str,
    name: str,
    *,
    worktree: Path,
    data_dir: Path,
    pi_home: Path,
    host_port: int,
    container_port: int,
    uid: int,
    gid: int,
    env: dict[str, str],
    cmd: list[str],
) -> None:
    """docker run -d with the worktree + shared data bound, credentials ro."""
    args = [
        "run", "-d", "--name", name,
        "-v", f"{worktree}:/work",
        "-w", "/work",
        "-v", f"{data_dir}:/work/adws/adw_data",
        "-v", f"{pi_home}:/home/agent/.pi/agent:ro",
        "-p", f"{host_port}:{container_port}",
        "--user", f"{uid}:{gid}",
    ]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    args += [image, *cmd]
    r = _docker(*args)
    if r.returncode != 0:
        raise SandboxError(f"docker run failed: {r.stderr.strip()[:500]}")


def wait_exit(name: str, timeout_s: int) -> int:
    """docker wait, but bound: the ADW decides within poll_seconds of the
    review row changing; anything past the bound is treated as 0 (the row
    already records the decision)."""
    try:
        r = _docker("wait", name, timeout_s=timeout_s)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except subprocess.TimeoutExpired:
        pass
    return 0


def stop_remove(name: str) -> None:
    """Idempotent: remove the container whether running or stopped."""
    _docker("rm", "-f", name)
