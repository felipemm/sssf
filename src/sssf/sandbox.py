"""Deterministic sandbox lifecycle: worktrees, docker, auto-teardown.

Every function here is plain Python — no agents, no ad-hoc steps. Creation
and teardown are idempotent so a crash mid-teardown leaves re-runnable
cleanup.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


class SandboxError(RuntimeError):
    """Raised when a sandbox lifecycle step fails deterministically."""


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def sandbox_dir(project_root: Path, adw_id: str) -> Path:
    """~/.sssf/sandboxes/<project-basename>/<adw_id> — the repo tree stays clean."""
    home = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf"))
    return home / "sandboxes" / project_root.name / adw_id


def create_worktree(project_root: Path, adw_id: str, attach: bool = False) -> Path:
    """git worktree add -q <dir> -b sssf/<adw_id>. The branch is unique per
    adw_id; worktrees are structurally isolated (a branch lives in one).

    attach=True attaches to the EXISTING sssf/<adw_id> branch (a restart reuses
    the previous run's branch — the ADW joins and reaps the old state)."""
    branch = f"sssf/{adw_id}"
    wt = sandbox_dir(project_root, adw_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", "-q", str(wt)] + (["-b", branch] if not attach else [branch])
    r = _run_git(project_root, *args)
    if r.returncode != 0:
        raise SandboxError(f"worktree add failed: {r.stderr.strip()}")
    return wt


def remove_worktree(wt_dir: Path) -> None:
    """Idempotent: remove the worktree (force — the run's work is committed on
    its branch, and teardown must never block on stray files), then prune."""
    if not wt_dir.exists():
        return
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
        "-v", f"{pi_home}:/opt/pi-agent-host:ro",
    ]
    if git_dir is not None:
        args += ["-v", f"{git_dir}:{git_dir}:rw"]
    if config_dir is not None:
        # The provider apiKey shell commands resolve ${HOME}/.config/... — with
        # HOME=/tmp in the image, mount the host config read-only at /tmp/.config.
        args += ["-v", f"{config_dir}:/tmp/.config:ro"]
    args += ["--user", f"{uid}:{gid}"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    args += [image, *cmd]
    r = _docker(*args)
    if r.returncode != 0:
        raise SandboxError(f"docker run failed: {r.stderr.strip()[:500]}")


def stop_remove(name: str) -> None:
    """Idempotent: remove the container whether running or stopped."""
    _docker("rm", "-f", name)


def container_name(adw_id: str) -> str:
    return f"sssf-{adw_id}"


def project_db_path(data_dir: Path) -> Path:
    """The shared project db (bind-mounted into the container at
    /work/adws/adw_data/sssf.db)."""
    return data_dir / "sssf.db"


def spawn_sandbox(project_root: Path, adw_id: str, *, cmd: list[str],
                  image: str, data_dir: Path, pi_home: Path,
                  env: dict[str, str] | None = None,
                  uid: int | None = None, gid: int | None = None,
                  attach: bool = False) -> dict:
    """Create the worktree + start the container. Deterministic; returns the
    sandbox record (worktree, name). attach=True reuses the run's existing
    branch (a restart)."""
    wt = create_worktree(project_root, adw_id, attach=attach)
    stamp_adw_template(wt)   # deterministic: the installed template, not a stale init stamp
    uid = uid if uid is not None else os.getuid()
    gid = gid if gid is not None else os.getgid()
    env = {**(env or {}), "SSSF_IN_SANDBOX": "1"}   # tracer uses rollback journal (mount-visible)
    run_sandbox(
        image, container_name(adw_id),
        worktree=wt, data_dir=data_dir, pi_home=pi_home,
        git_dir=project_root / ".git",
        config_dir=Path.home() / ".config",
        uid=uid, gid=gid, env=env, cmd=cmd,
    )
    return {"worktree": str(wt), "name": container_name(adw_id)}


def teardown_sandbox(project_root: Path, adw_id: str) -> int:
    """Remove the container + worktree once the run is done (success or fail).
    The branch sssf/<adw_id> survives as the deliverable — the engineer merges
    or PRs it; prune deletes it afterwards. Idempotent."""
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    return 0


def sync_run_db(conn: sqlite3.Connection, per_run_db: Path, adw_id: str) -> None:
    """Merge a run's per-run db (written by the ADW inside the container) into
    the project db via the given connection. The per-run db is COPIED first —
    a plain file read never takes sqlite locks on the live db, so the ADW's
    own writes are never disturbed (a concurrent sqlite reader through the
    bind mount caused 'disk I/O error' in the ADW). A torn copy (mid-commit)
    is skipped; the next sync catches up. DELETE the run's previous rows then
    INSERT the current ones, per table, so repeated syncs never duplicate."""
    import shutil
    if not per_run_db.exists():
        return
    tmp = per_run_db.with_suffix(".sync-copy.db")
    try:
        shutil.copy2(per_run_db, tmp)
    except OSError:
        return
    try:
        src = sqlite3.connect(str(tmp), isolation_level=None)
        try:
            for table in ("sessions", "phases", "events", "envelopes",
                          "gate_results", "processes", "agent_sessions", "tickets"):
                try:
                    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                    if not cols or "adw_id" not in cols:
                        continue
                    conn.execute(f"DELETE FROM {table} WHERE adw_id=?", (adw_id,))
                    rows = src.execute(f"SELECT * FROM {table} WHERE adw_id=?", (adw_id,)).fetchall()
                    if rows:
                        q = ",".join("?" * len(cols))
                        conn.executemany(
                            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({q})", rows)
                except sqlite3.Error:
                    continue   # a table missing in one of the dbs — skip
        finally:
            src.close()
    except sqlite3.Error:
        pass   # torn copy — the next sync catches up
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def monitor_run(project_root: Path, adw_id: str) -> int:
    """The detached monitor: while the run's container is alive, merge the
    per-run db into the project db (live-ish visibility, ~3s), then a final
    sync and teardown once the ADW exits (success or fail). One project
    connection is reused (the host owns the project db — WAL, host filesystem —
    so concurrent monitors serialize through busy_timeout). Spawned by
    `sssf run`/`ticket run` right after the container starts."""
    import sqlite3
    from sssf.adw_modules.tracer import Tracer
    data_dir, _pi, _env = sandbox_env(project_root)
    project_db = project_db_path(data_dir)
    per_run_db = sandbox_dir(project_root, adw_id) / "adws" / "adw_data" / "sssf.db"
    tracer = Tracer(str(project_db), str(project_db.parent / "sessions" / adw_id / "events.jsonl"))
    try:
        while True:
            try:
                r = _docker("ps", "--filter", f"name={container_name(adw_id)}",
                            "--format", "{{.Status}}", timeout_s=30)
                if not r.stdout.strip():
                    break   # container gone — the run is done
            except Exception:
                break
            sync_run_db(tracer.conn, per_run_db, adw_id)
            time.sleep(3)
    finally:
        sync_run_db(tracer.conn, per_run_db, adw_id)   # final merge
        teardown_sandbox(project_root, adw_id)
    return 0


def spawn_monitor(project_root: Path, adw_id: str) -> None:
    """Launch monitor_run detached (it blocks for the run's whole lifetime)."""
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from sssf.sandbox import monitor_run\n"
        "sys.exit(monitor_run(Path(sys.argv[1]), sys.argv[2]))\n"
    )
    subprocess.Popen(
        [sys.executable, "-c", code, str(project_root), adw_id],
        start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


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
    for var, key in (("GIT_AUTHOR_NAME", "user.name"),
                     ("GIT_AUTHOR_EMAIL", "user.email")):
        if not os.environ.get(var):
            r = subprocess.run(["git", "config", "--global", key],
                               capture_output=True, text=True, check=False)
            if r.returncode == 0 and r.stdout.strip():
                env[var] = r.stdout.strip()
    return data_dir, pi_home, env


def _session_status(data_dir: Path, adw_id: str) -> str | None:
    import sqlite3
    db_path = project_db_path(data_dir)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        row = conn.execute("SELECT status FROM sessions WHERE adw_id=?", (adw_id,)).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def stop_run(project_root: Path, adw_id: str, data_dir: Path) -> int:
    """Terminate a run: kill the container and remove the worktree. If the
    session is still marked running afterwards (a stale run whose ADW died
    without its failsafe — e.g. SIGKILL teardown), finalize it as failed so it
    becomes archivable, and mark every in-flight/queued PHASE failed — the
    trace must show the run stopped cleanly, never a phase stuck 'running'.
    The branch stays for inspection (prune deletes it once resolved)."""
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    status = _session_status(data_dir, adw_id)
    if status is not None and status not in ("success", "fail"):
        import datetime
        from sssf.adw_modules.tracer import Tracer
        tracer = Tracer(str(project_db_path(data_dir)),
                        str(data_dir / "sessions" / adw_id / "events.jsonl"))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tracer.conn.execute(
            "UPDATE phases SET status='fail', error=?, ended_at=? "
            "WHERE adw_id=? AND status IN ('running','queued')",
            ("stopped by the engineer", now, adw_id))
        tracer.session_finish(adw_id, ok=False)   # a cancelled run is failed
    return 0


def stamp_adw_template(wt: Path) -> None:
    """Stamp the CURRENT adw_simple_sdlc.py into the worktree. The worktree's
    copy is the project's committed template (stamped at init, possibly stale
    after an sssf upgrade) — sandboxed runs must use the installed template so
    the run matches the installed sssf exactly."""
    import shutil
    import sssf
    template = Path(sssf.__file__).parent / "templates" / "adws" / "adw_simple_sdlc.py"
    if template.exists():
        dest = wt / "adws" / "adw_simple_sdlc.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template, dest)
