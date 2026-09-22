"""Docker image + container lifecycle: the runner image build, the
staleness fingerprint, container run/stop, and the docker execution
seam (`_docker`).

`_docker` is the seam the fake-docker PATH shim in tests crosses — the
binary is resolved per call so a PATH swap after import takes effect.
SandboxError lives here: docker is the dependency root (leaf module),
so every other module can import it without a cycle.
"""

import shutil
import subprocess
import time
from pathlib import Path


class SandboxError(RuntimeError):
    """Raised when a sandbox lifecycle step fails deterministically."""


def _docker(*args: str, timeout_s: int = 300) -> subprocess.CompletedProcess[str]:
    # Resolve per call (not at import): the fake-docker tests swap PATH after
    # import, and shutil.which at module level would pin the real binary.
    docker = shutil.which("docker") or "docker"
    return subprocess.run(
        [docker, *args], capture_output=True, text=True, check=False, timeout=timeout_s
    )


def docker_available() -> bool:
    r = _docker("info")
    return r.returncode == 0


def build_image(
    image: str,
    dockerfile: Path,
    context: Path | None = None,
    *,
    timeout_s: int = 1800,
    stream: bool = False,
) -> None:
    """docker build the runner image.

    Captured mode (the default) is for daemon callers whose stdout feeds a log
    (the healer). stream=True is for the interactive CLI: docker's own progress
    goes straight to the terminal, so a cache-cold build (it downloads
    pi/bun/snyk/Chrome — many minutes) never reads as a silent freeze, and a
    failure shows the real docker output instead of a 500-char tail.

    -t is mandatory: an untagged build leaves the image dangling and every
    run keeps using the stale sssf-runner:latest.
    """
    cmd = ["build", "-t", image, "-f", str(dockerfile), str(context or dockerfile.parent)]
    if stream:
        try:
            docker = shutil.which("docker") or "docker"
            proc = subprocess.run([docker, *cmd], timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise SandboxError(
                f"docker build timed out after {timeout_s}s — the runner image installs "
                "pi/bun/snyk/Chrome from the network; check connectivity and Docker "
                "resources, then retry"
            ) from None
        if proc.returncode != 0:
            raise SandboxError(
                f"docker build failed (exit {proc.returncode}) — see the build output above"
            )
        return
    try:
        r = _docker(*cmd, timeout_s=timeout_s)
    except subprocess.TimeoutExpired:
        raise SandboxError(
            f"docker build timed out after {timeout_s}s — check Docker/network and retry"
        ) from None
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
    uid: int = 1000,
    gid: int = 1000,
    env: dict[str, str] | None = None,
    publish_port: int | None = None,
    cmd: list[str] | None = None,
) -> None:
    """docker run -d with the worktree + shared data bound, credentials ro.

    git_dir mounts the repo's .git at its HOST path inside the container: a
    worktree's `.git` file references that absolute path, so without the mount
    git inside the container can't resolve the repo (the ADW's commits land in
    the shared object store — that is the point). publish_port publishes the
    review app's container port loopback-only on a RANDOM host port.
    """
    args = [
        "run",
        "-d",
        "--name",
        name,
        "-v",
        f"{worktree}:/work",
        "-w",
        "/work",
        "-v",
        f"{pi_home}:/opt/pi-agent-host:ro",
    ]
    if git_dir is not None:
        args += ["-v", f"{git_dir}:{git_dir}:rw"]
    if config_dir is not None:
        # The provider apiKey shell commands resolve ${HOME}/.config/... — with
        # HOME=/tmp in the image, mount the host config read-only at /tmp/.config.
        args += ["-v", f"{config_dir}:/tmp/.config:ro"]
    args += ["--user", f"{uid}:{gid}"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    if publish_port:
        # Loopback-only, random HOST port (docker picks a free one) so
        # concurrent runs never collide. The app binds container_port inside
        # the container; `docker port <name>` resolves the host port.
        args += ["-p", f"127.0.0.1::{publish_port}"]
    args += [image, *(cmd or [])]
    # Containers are KEPT after a run for debugging, so a retry/restart may
    # find an Exited container with this name — remove it before running.
    _docker("rm", "-f", name)  # no-op when absent (docker prints an error we ignore)
    # Docker Desktop can hiccup under concurrent container creation — retry
    # the run a few times before giving up.
    last: subprocess.CompletedProcess[str] | None = None
    for _attempt in range(3):
        last = _docker(*args)
        if last.returncode == 0:
            return
        time.sleep(2)
    if last is not None:
        raise SandboxError(f"docker run failed: {last.stderr.strip()[:500]}")


def stop_remove(name: str) -> None:
    """Remove the container whether running or stopped. ONLY callers: the
    same-session container reuse in run_sandbox, and `sssf sweep` (the sole
    deleter of run artifacts)."""
    _docker("rm", "-f", name)


def list_container_names(prefix: str = "") -> list[str]:
    """Every container name matching `prefix` (docker ps -a, name filter), one
    per line — the public docker-module verb `sssf sweep` uses to find orphan
    containers (replaces a private `_docker` reach). A docker failure returns
    [] — the sweep caller treats that as nothing to remove."""
    r = _docker("ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}", timeout_s=30)
    return r.stdout.split()


def stop_container(name: str) -> None:
    """Stop a container and KEEP it — its logs and the worktree mount stay
    available for review. Stopping is never deletion; removal is `sssf sweep`'s
    job. Absent/stopped containers are fine (docker errors are ignored)."""
    if not name:
        return
    _docker("stop", "-t", "5", name)


def container_name(adw_id: str) -> str:
    return f"sssf-{adw_id}"


_FINGERPRINT_PATH = "/opt/sssf-fingerprint"
_fingerprint_cache: dict[str, str | None] = {}


def _engine_fingerprint() -> str:
    """Fingerprint of the LOCAL sssf engine source — the CLI side of the
    staleness check. The runner image bakes the same fingerprint at build time
    (docker/sssf-runner.Dockerfile); a mismatch means the image predates local
    engine changes and every sandboxed run would die cryptically."""
    import hashlib

    import sssf

    root = Path(sssf.__file__).resolve().parent
    # Same algorithm as the Dockerfile's marker build: one sha256 per file
    # (in sorted-path order), then sha256 of the newline-joined hex digests.
    # Both sides must match byte-for-byte or the guard reports stale forever.
    # The visualizer is host-side UI — the ADW sandbox never runs it, so
    # frontend-only changes must not stale the runner image.
    _SKIP_DIRS = {"node_modules", ".venv", ".git", "__pycache__", "visualizer"}
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and p.suffix != ".pyc"
        and not _SKIP_DIRS.intersection(p.parts)
    )
    digests = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    return hashlib.sha256(("\n".join(digests) + "\n").encode()).hexdigest()


def image_engine_fingerprint(image: str) -> str | None:
    """The fingerprint baked into the image at build time; None when the image
    is missing or unreadable (docker failure, no marker file)."""
    if image in _fingerprint_cache:
        return _fingerprint_cache[image]
    r = _docker("run", "--rm", "--entrypoint", "cat", image, _FINGERPRINT_PATH)
    value = (r.stdout.strip() if r.returncode == 0 else "") or None
    _fingerprint_cache[image] = value
    return value


def ensure_image_current(image: str) -> None:
    """Refuse to spawn on a stale/missing runner image — the failure mode where
    the image's baked engine predates local changes and every sandboxed run
    dies instantly with an ImportError that auto-teardown erases (issue #21)."""
    want = _engine_fingerprint()
    have = image_engine_fingerprint(image)
    if have is None:
        raise SandboxError(
            f"runner image '{image}' is missing or unreadable — "
            f"run `sssf sandbox build` to build it"
        )
    if have != want:
        raise SandboxError(
            f"runner image '{image}' is stale (image fingerprint {have[:12]} "
            f"≠ CLI {want[:12]}) — run `sssf sandbox build` to rebuild it"
        )


# ── runner image upkeep (auto-rebuild path) ────────────────────────────────


def runner_source_root() -> Path:
    """The sssf source tree that owns docker/sssf-runner.Dockerfile — resolved
    from the installed package, not the cwd (the Dockerfile's COPY lines expect
    the package layout, whatever the current directory is). parents[3]: the
    Dockerfile lives next to pyproject.toml, two levels above the package
    (sandbox/ added one level vs the old flat sandbox.py).
    """
    return Path(__file__).resolve().parents[3]


def runner_dockerfile() -> Path | None:
    """The sssf-runner Dockerfile in the sssf source tree; None when missing."""
    df = runner_source_root() / "docker" / "sssf-runner.Dockerfile"
    return df if df.exists() else None


def image_is_current(image: str) -> bool:
    """True when the runner image exists and its baked engine fingerprint
    matches the local engine — exactly what ensure_image_current() enforces,
    without raising (the healer's rebuild probe)."""
    return image_engine_fingerprint(image) == _engine_fingerprint()


def build_runner_image(image: str, *, stream: bool = False) -> None:
    """Build (or rebuild) the runner image with the current engine baked in.

    Uses a generous timeout (a full build installs pi/bun/snyk/impeccable) and
    clears the in-process fingerprint cache afterwards: the cache would
    otherwise keep reporting the OLD marker, and the next guard would still
    refuse the freshly rebuilt image. stream=True forwards docker's progress to
    the terminal (interactive `sssf sandbox build`); daemon callers (healer)
    leave it captured.
    """
    df = runner_dockerfile()
    if df is None:
        raise SandboxError("docker/sssf-runner.Dockerfile not found")
    src = runner_source_root()
    context = src if (src / "pyproject.toml").exists() else df.parent
    build_image(image, df, context, stream=stream)
    _fingerprint_cache.pop(image, None)
