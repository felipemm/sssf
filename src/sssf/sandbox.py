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


def build_image(image: str, dockerfile: Path, context: Path | None = None) -> None:
    r = _docker("build", "-f", str(dockerfile), str(context or dockerfile.parent))
    if r.returncode != 0:
        raise SandboxError(f"docker build failed: {r.stderr.strip()[:500]}")


def run_sandbox(
    image: str,
    name: str,
    *,
    worktree: Path,
    data_dir: Path,
    pi_home: Path,
    git_dir: Path | None = None,
    config_dir: Path | None = None,
    host_port: int = 0,
    container_port: int = 3000,
    uid: int = 1000,
    gid: int = 1000,
    env: dict[str, str] | None = None,
    cmd: list[str] | None = None,
) -> None:
    """docker run -d with the worktree + shared data bound, credentials ro.

    git_dir mounts the repo's .git at its HOST path inside the container: a
    worktree's `.git` file references that absolute path, so without the mount
    git inside the container can't resolve the repo (the ADW's commits land in
    the shared object store — that is the point).
    """
    args = [
        "run", "-d", "--name", name,
        "-v", f"{worktree}:/work",
        "-w", "/work",
        "-v", f"{data_dir}:/work/adws/adw_data",
        "-v", f"{pi_home}:/opt/pi-agent-host:ro",
    ]
    if git_dir is not None:
        args += ["-v", f"{git_dir}:{git_dir}:rw"]
    if config_dir is not None:
        # The provider apiKey shell commands resolve ${HOME}/.config/... — with
        # HOME=/tmp in the image, mount the host config read-only at /tmp/.config.
        args += ["-v", f"{config_dir}:/tmp/.config:ro"]
    args += [
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

def review_db_path(data_dir: Path) -> Path:
    """The shared db lives in the project's data dir (bind-mounted into the
    container at /work/adws/adw_data)."""
    return data_dir / "sssf.db"


def container_name(adw_id: str) -> str:
    return f"sssf-{adw_id}"


def spawn_sandbox(project_root: Path, adw_id: str, *, cmd: list[str],
                  port: int, image: str, data_dir: Path, pi_home: Path,
                  container_port: int = 3000, env: dict[str, str] | None = None,
                  uid: int | None = None, gid: int | None = None) -> dict:
    """Create the worktree + start the container. Deterministic; returns the
    sandbox record (worktree, name, host_port)."""
    wt = create_worktree(project_root, adw_id)
    stamp_adw_template(wt)   # deterministic: the installed template, not a stale init stamp
    uid = uid if uid is not None else os.getuid()
    gid = gid if gid is not None else os.getgid()
    run_sandbox(
        image, container_name(adw_id),
        worktree=wt, data_dir=data_dir, pi_home=pi_home,
        git_dir=project_root / ".git",
        config_dir=Path.home() / ".config",
        host_port=port, container_port=container_port,
        uid=uid, gid=gid, env=env or {}, cmd=cmd,
    )
    return {"worktree": str(wt), "name": container_name(adw_id), "host_port": port}


def decide_and_teardown(project_root: Path, adw_id: str, status: str,
                        data_dir: Path) -> int:
    """Mark the decision, wake the ADW (SIGUSR1 approve / SIGUSR2 reject — the
    phase ends immediately), wait for it to exit, then tear the container +
    worktree down. The branch sssf/<adw_id> survives (prune deletes it once the
    engineer resolved the run)."""
    # The CLI spells it "approve"/"reject"; the db record + signals use the
    # past participle. Normalize so approve never falls into the reject branch.
    status = {"approve": "approved", "reject": "rejected"}.get(status, status)
    from sssf.adw_modules.tracer import Tracer
    db_path = review_db_path(data_dir)
    tracer = Tracer(str(db_path), str(data_dir / "sessions" / adw_id / "events.jsonl"))
    tracer.review_decide(adw_id, status)
    signal_name = "USR1" if status == "approved" else "USR2"
    _docker("kill", "-s", signal_name, container_name(adw_id))
    wait_exit(container_name(adw_id), timeout_s=30)
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    return 0


def prune_sandbox(project_root: Path, adw_id: str) -> int:
    """Remove a run's leftovers AND its branch — the engineer runs this once
    the PR is merged (or to discard a failed run). Idempotent."""
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    delete_branch(project_root, adw_id)
    return 0


def sandbox_env(project_root: Path) -> tuple[Path, Path, dict[str, str]]:
    """The per-run data dir (shared, bind-mounted rw), the pi home (read-only
    mount), and the env passed to the container: credentials + git identity
    only — never project files."""
    data_dir = project_root / "adws" / "adw_data"
    pi_home = Path(os.environ.get("PI_HOME", Path.home() / ".pi" / "agent"))
    env: dict[str, str] = {}
    if os.environ.get("GENPLAT_TOKEN"):
        env["GENPLAT_TOKEN"] = os.environ["GENPLAT_TOKEN"]
    # git identity for the container's commits (read the host's git config).
    for var, key in (("GIT_AUTHOR_NAME", "user.name"),
                     ("GIT_AUTHOR_EMAIL", "user.email")):
        if not os.environ.get(var):
            r = subprocess.run(["git", "config", "--global", key],
                               capture_output=True, text=True, check=False)
            if r.returncode == 0 and r.stdout.strip():
                env[var] = r.stdout.strip()
    return data_dir, pi_home, env


def stop_run(project_root: Path, adw_id: str, data_dir: Path) -> int:
    """Terminate a run: kill the container (the ADW's kill-failsafe marks the
    session failed), clear a pending review, remove the worktree. The branch
    stays for inspection (prune deletes it once resolved)."""
    from sssf.adw_modules.tracer import Tracer
    db_path = review_db_path(data_dir)
    tracer = Tracer(str(db_path), str(data_dir / "sessions" / adw_id / "events.jsonl"))
    if tracer.review_status(adw_id) == "pending":
        tracer.review_decide(adw_id, "rejected")   # a stopped run is not approved
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    return 0


def stamp_adw_template(wt: Path) -> None:
    """Stamp the CURRENT adw_simple_sdlc.py into the worktree. The worktree's
    copy is the project's committed template (stamped at init, possibly stale
    after an sssf upgrade) — sandboxed runs must use the installed template so
    the review stage matches the installed sssf exactly."""
    import shutil
    import sssf
    template = Path(sssf.__file__).parent / "templates" / "adws" / "adw_simple_sdlc.py"
    if template.exists():
        dest = wt / "adws" / "adw_simple_sdlc.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template, dest)
