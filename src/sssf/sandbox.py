"""Deterministic sandbox lifecycle: worktrees, docker, auto-teardown.

Every function here is plain Python — no agents, no ad-hoc steps. Creation
and teardown are idempotent so a crash mid-teardown leaves re-runnable
cleanup.
"""

import contextlib
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
        capture_output=True,
        text=True,
        check=False,
    )


def _has_remote(root: Path, name: str) -> bool:
    """True if the repo has a git remote under `name` (e.g. origin)."""
    return _run_git(root, "config", "--get", f"remote.{name}.url").returncode == 0


def sandbox_dir(project_root: Path, adw_id: str) -> Path:
    """<repo>/.worktrees/<adw_id> — the worktree lives next to the code so
    the operator can inspect or re-run it manually after a run. Ignored via
    .git/info/exclude (never committed); cleanup is `sssf sandbox prune`."""
    return project_root / ".worktrees" / adw_id


def _exclude_worktrees(project_root: Path) -> None:
    """Keep `.worktrees/` out of `git status` — a LOCAL ignore, never committed."""
    exclude = project_root / ".git" / "info" / "exclude"
    try:
        if exclude.exists() and ".worktrees/" in exclude.read_text():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as f:
            f.write("\n.worktrees/\n")
    except OSError:
        pass  # best-effort — a repo without writable .git still runs


def create_worktree(project_root: Path, adw_id: str, attach: bool = False) -> Path:
    """git worktree add -q <dir> -b sssf/<adw_id> — the branch is unique per
    adw_id; worktrees are structurally isolated (a branch lives in one).

    Fresh runs check out origin/main, never local main: the sandbox must run
    what was actually merged, so unpushed local commits and uncommitted edits
    stay out (the snyk-gate incident). origin/main is fetched first so the
    run always sees the latest remote state. Repos with no origin remote
    (local-only) fall back to committed local main.

    attach=True attaches to the EXISTING sssf/<adw_id> branch (a restart reuses
    the previous run's branch — the ADW joins and reaps the old state)."""
    branch = f"sssf/{adw_id}"
    wt = sandbox_dir(project_root, adw_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    _exclude_worktrees(project_root)
    if attach:
        # A restart attaches to the run's EXISTING branch. The checkout may
        # already be there from a previous attempt — a stop/prune race can
        # leave the worktree registered while the container is gone — and `git
        # worktree add` would then fail with 'already exists', killing the
        # restart before the ADW ever starts (session 9701903a, 2026-09-02:
        # the leftover registered worktree from one stopped attempt silently
        # broke every later restart). The branch is the same, so the existing
        # checkout IS the attach target: reuse it.
        if wt.exists() and _worktree_registered(wt):
            return wt
        if wt.exists():  # unregistered leftover dir — clear before git worktree add
            shutil.rmtree(wt)
        args = ["worktree", "add", "-q", str(wt), branch]
    elif _has_remote(project_root, "origin"):
        r = _run_git(project_root, "fetch", "origin", "main")
        if r.returncode != 0:
            raise SandboxError(f"git fetch origin main failed: {r.stderr.strip()}")
        args = ["worktree", "add", "-q", str(wt), "-b", branch, "origin/main"]
    else:
        # No remote (a local-only repo): the origin/main source of truth
        # doesn't exist, so the committed local main is the ground truth.
        args = ["worktree", "add", "-q", str(wt), "-b", branch, "main"]
    r = _run_git(project_root, *args)
    if r.returncode != 0:
        raise SandboxError(f"worktree add failed: {r.stderr.strip()}")
    return wt


def _worktree_registered(wt_dir: Path) -> bool:
    """True while git still registers this worktree (its admin dir exists).
    A failed `git worktree remove` (e.g. the container still held the mount)
    leaves the checkout dir behind UNREGISTERED — then it is a plain directory
    and safe to delete directly."""
    gitfile = wt_dir / ".git"
    if not gitfile.is_file():
        return False
    try:
        line = gitfile.read_text().strip()
        if line.startswith("gitdir:"):
            admin = Path(line.split(":", 1)[1].strip())
            return admin.exists()
    except OSError:
        pass
    return True  # unknown — don't delete


def remove_worktree(wt_dir: Path) -> None:
    """Idempotent: remove the worktree (force — the run's work is committed on
    its branch, and teardown must never block on stray files), then prune. If
    git's removal left the checkout behind (a teardown race), delete the dir
    directly once it is no longer registered."""
    if not wt_dir.exists():
        return
    r = subprocess.run(
        ["git", "-C", str(wt_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        root = Path(r.stdout.strip())
        subprocess.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(root), "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
        )
    if wt_dir.exists() and not _worktree_registered(wt_dir):
        import shutil

        shutil.rmtree(wt_dir, ignore_errors=True)


def delete_branch(project_root: Path, adw_id: str) -> None:
    """Idempotent: delete the run's branch ref (call after the engineer
    merged/PR'd, or to discard a failed run)."""
    _run_git(project_root, "branch", "-D", f"sssf/{adw_id}")


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


def build_image(image: str, dockerfile: Path, context: Path | None = None, *, timeout_s: int = 1800) -> None:
    # -t is mandatory: an untagged build leaves the image dangling and every
    # run keeps using the stale sssf-runner:latest.
    r = _docker("build", "-t", image, "-f", str(dockerfile), str(context or dockerfile.parent), timeout_s=timeout_s)
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
    """Idempotent: remove the container whether running or stopped."""
    _docker("rm", "-f", name)


def container_name(adw_id: str) -> str:
    return f"sssf-{adw_id}"


def project_db_path(data_dir: Path) -> Path:
    """The shared project db (bind-mounted into the container at
    /work/adws/data/sssf.db)."""
    return data_dir / "sssf.db"


_FINGERPRINT_PATH = "/opt/sssf-fingerprint"
_fingerprint_cache: dict[str, str | None] = {}


def enabled(root: Path, *, command: str) -> bool:
    """The single sandbox decision (audit A1, C2). NEVER silently degrades to a
    local run: a missing config or a bug here is printed, not swallowed."""
    try:
        from sssf.adw_modules import paths
        from sssf.adw_modules.agents import load_config

        cfg = load_config(str(paths.config_file(root)))
        return cfg.sandbox.enabled
    except Exception as error:
        print(
            f"sssf: sandbox decision failed for {command} ({error}) — running unsandboxed",
            file=sys.stderr,
        )
        return False


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
    the package layout, whatever the current directory is)."""
    return Path(__file__).resolve().parents[2]


def runner_dockerfile() -> Path | None:
    """The sssf-runner Dockerfile in the sssf source tree; None when missing."""
    df = runner_source_root() / "docker" / "sssf-runner.Dockerfile"
    return df if df.exists() else None


def image_is_current(image: str) -> bool:
    """True when the runner image exists and its baked engine fingerprint
    matches the local engine — exactly what ensure_image_current() enforces,
    without raising (the healer's rebuild probe)."""
    return image_engine_fingerprint(image) == _engine_fingerprint()


def build_runner_image(image: str) -> None:
    """Build (or rebuild) the runner image with the current engine baked in.

    Uses a generous timeout (a full build installs pi/bun/snyk/impeccable) and
    clears the in-process fingerprint cache afterwards: the cache would
    otherwise keep reporting the OLD marker, and the next guard would still
    refuse the freshly rebuilt image.
    """
    df = runner_dockerfile()
    if df is None:
        raise SandboxError("docker/sssf-runner.Dockerfile not found")
    src = runner_source_root()
    context = src if (src / "pyproject.toml").exists() else df.parent
    build_image(image, df, context)
    _fingerprint_cache.pop(image, None)


def spawn_sandbox(
    project_root: Path,
    adw_id: str,
    *,
    cmd: list[str],
    image: str,
    data_dir: Path,
    pi_home: Path,
    env: dict[str, str] | None = None,
    uid: int | None = None,
    gid: int | None = None,
    attach: bool = False,
    worktree: Path | None = None,
) -> dict:
    """Start the container in a (created) worktree. Deterministic; returns the
    sandbox record (worktree, name). attach=True reuses the run's existing
    branch (a restart). `worktree` supplies an ALREADY-created worktree (the
    ticket path creates one first to write the prompt) — never create twice."""
    ensure_image_current(image)
    wt = worktree or create_worktree(project_root, adw_id, attach=attach)
    stamp_adw_template(wt)  # deterministic: the installed template, not a stale init stamp
    uid = uid if uid is not None else os.getuid()
    gid = gid if gid is not None else os.getgid()
    env = {**(env or {}), "SSSF_IN_SANDBOX": "1"}  # tracer uses rollback journal (mount-visible)
    run_sandbox(
        image,
        container_name(adw_id),
        worktree=wt,
        data_dir=data_dir,
        pi_home=pi_home,
        git_dir=project_root / ".git",
        config_dir=Path.home() / ".config",
        uid=uid,
        gid=gid,
        env=env,
        cmd=cmd,
    )
    return {"worktree": str(wt), "name": container_name(adw_id)}


def abort_sandbox(project_root: Path, adw_id: str) -> None:
    """Clean up after a FAILED spawn: remove the (possibly 'Created'-stuck)
    container. The worktree stays under .worktrees/ for inspection."""
    stop_remove(container_name(adw_id))


def teardown_sandbox(project_root: Path, adw_id: str) -> int:
    """No-op by design: after a run, NEITHER the container nor the worktree
    is deleted — they are the operator's debugging surface (docker logs, the
    .worktrees/<adw_id> checkout, its adws/data/sessions artifacts). Cleanup is
    explicit: `sssf sandbox prune <adw_id>` or `sssf sweep`."""
    return 0


def _forward_merge(
    conn: sqlite3.Connection, src: sqlite3.Connection, table: str, adw_id: str
) -> None:
    """Merge one row-table (sessions/phases) forward-only: INSERT rows the
    project lacks, and UPDATE a row's status only while the project row is
    still un-ended (ended_at IS NULL). A stale mid-run copy can never
    downgrade a terminal status."""
    try:
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        if not cols or "adw_id" not in cols:
            return
        pk = "adw_id" if table == "sessions" else "phase_id"
        if pk not in cols:
            return
        rows = src.execute(f"SELECT * FROM {table} WHERE adw_id=?", (adw_id,)).fetchall()
        if not rows:
            return
        q = ",".join("?" * len(cols))
        # insert missing rows (by PK)
        existing = {
            r[cols.index(pk)]
            for r in conn.execute(f"SELECT {pk} FROM {table} WHERE adw_id=?", (adw_id,)).fetchall()
        }
        insert_cols = ",".join(cols)
        for row in rows:
            if row[cols.index(pk)] not in existing:
                conn.execute(f"INSERT INTO {table} ({insert_cols}) VALUES ({q})", row)
        # forward update: fill un-ended rows with the source's terminal state
        if "ended_at" in cols and "status" in cols:
            for row in rows:
                pk_val = row[cols.index(pk)]
                if row[cols.index("ended_at")] is not None:
                    sets = [f"{c}=?" for c in cols if c not in (pk, "adw_id")]
                    conn.execute(
                        f"UPDATE {table} SET {','.join(sets)} WHERE {pk}=? AND ended_at IS NULL",
                        [row[cols.index(c)] for c in cols if c not in (pk, "adw_id")] + [pk_val],
                    )
        # live totals: tokens/cost only accumulate, so a max-merge on every
        # sync never regresses — a torn mid-run copy carries fewer tokens than
        # the previous sync, and MAX is safe in both directions. This is what
        # makes card tokens/costs update in-flight instead of only at teardown.
        for col in ("total_tokens", "total_cost"):
            if col in cols:
                for row in rows:
                    pk_val = row[cols.index(pk)]
                    conn.execute(
                        f"UPDATE {table} SET {col}=MAX(COALESCE({col},0), COALESCE(?,0)) "
                        f"WHERE {pk}=?",
                        (row[cols.index(col)], pk_val),
                    )
        # request (sessions only): the request phase writes it ONCE and it is
        # immutable for the run, but the project row is inserted at the FIRST
        # sync — usually BEFORE the request phase logs — and the ended-row
        # forward update above only fires at the final merge (never once the
        # healer has finalized the host row first). A mid-run copy must carry
        # the request too, or `sssf run restart` on the host reads an empty
        # request and bails ('no request to re-run'): the healer's restarts of
        # a hung sandboxed run then burn the whole budget doing nothing and
        # the run is finalized unrecoverably. Copy only into an empty host
        # slot — a torn source copy (NULL request) never regresses one the
        # host already merged.
        if table == "sessions" and "request" in cols:
            for row in rows:
                req = row[cols.index("request")]
                if req:
                    conn.execute(
                        f"UPDATE {table} SET request=? WHERE {pk}=? "
                        "AND (request IS NULL OR request='')",
                        (req, row[cols.index(pk)]),
                    )
    except sqlite3.Error:
        pass


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
            # tickets is PROJECT-owned (the host's ticket commands write it; the
            # per-run db never contains tickets) — syncing it would DELETE the
            # run's ticket row and insert nothing.
            # sessions/phases merge FORWARD-ONLY: INSERT missing rows, and
            # update a status only when the project row is still un-ended. A
            # torn mid-run copy (status 'running') can therefore never
            # downgrade a terminal state the project already recorded.
            _forward_merge(conn, src, "sessions", adw_id)
            _forward_merge(conn, src, "phases", adw_id)
            for table in ("events", "envelopes", "gate_results", "processes", "agent_sessions"):
                try:
                    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                    if not cols or "adw_id" not in cols:
                        continue
                    conn.execute(f"DELETE FROM {table} WHERE adw_id=?", (adw_id,))
                    rows = src.execute(
                        f"SELECT * FROM {table} WHERE adw_id=?", (adw_id,)
                    ).fetchall()
                    if rows:
                        q = ",".join("?" * len(cols))
                        conn.executemany(
                            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({q})", rows
                        )
                except sqlite3.Error:
                    continue  # a table missing in one of the dbs — skip
        finally:
            src.close()
    except sqlite3.Error:
        pass  # torn copy — the next sync catches up
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _container_gone(docker_fn, name: str) -> bool:
    """True only when the container is actually gone. A docker hiccup is NOT
    'gone' — treat it as a retry, never as the run finishing (audit A2)."""
    try:
        r = docker_fn("ps", "--filter", f"name={name}", "--format", "{{.Status}}", timeout_s=30)
        return not r.stdout.strip()
    except Exception as error:  # docker is down/glitchy
        print(f"sssf: teardown poll docker error ({error}) — retrying", file=sys.stderr)
        return False


def record_never_started(project_root: Path, adw_id: str, tracer, per_run_db: Path) -> None:
    """A run whose container exited before the ADW ever wrote a session row
    (spawn-death: missing entry file, stale/broken image, import error). The
    monitor used to erase the only evidence — container and worktree — so a
    dead spawn looked like it 'never started' (issue #21; the 2026-08-18
    ticket stuck at 'starting' with no session row). Record the failure:
    exit code + container log tail become a failed session with an 'error'
    event, and any ticket linked to this run flips to failed. Best-effort:
    teardown must still run even when docker/sqlite hiccups."""
    # The ADW did start if EITHER db has a session row (a previous sync may
    # already have merged the per-run copy into the project db).
    for db in (per_run_db, tracer.conn):
        try:
            if isinstance(db, Path):
                if not db.exists():
                    continue
                conn = sqlite3.connect(str(db), isolation_level=None)
                row = conn.execute("SELECT 1 FROM sessions WHERE adw_id=?", (adw_id,)).fetchone()
                conn.close()
            else:
                row = db.execute("SELECT 1 FROM sessions WHERE adw_id=?", (adw_id,)).fetchone()
            if row:
                return
        except sqlite3.Error:
            continue
    name = container_name(adw_id)
    exit_code, log_tail = "", ""
    try:
        r = _docker("inspect", "--format", "{{.State.ExitCode}}", name, timeout_s=15)
        exit_code = r.stdout.strip()
    except Exception:
        pass
    try:
        r = _docker("logs", "--tail", "40", name, timeout_s=15)
        log_tail = (r.stdout + r.stderr).strip()
    except Exception:
        pass
    from sssf.adw_modules.data_types import EventRecord
    from sssf.adw_modules.utils import now_iso

    now = now_iso()
    tracer.conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, request, status, engineer,"
        " started_at, ended_at) VALUES (?,?,?,?,?,?,?)",
        (
            adw_id,
            "adw_simple_sdlc (never started)",
            "sandboxed run died before the ADW started — see the error event for container output",
            "fail",
            "sssf",
            now,
            now,
        ),
    )
    tracer.event(
        EventRecord(
            adw_id=adw_id,
            type="error",
            name="sandbox spawn failure",
            payload={
                "exit_code": exit_code,
                "container_log_tail": log_tail[-2000:],
            },
        )
    )
    tracer.conn.execute(
        "UPDATE tickets SET status='failed', updated_at=? WHERE adw_id=?",
        (now, adw_id),
    )


def monitor_run(project_root: Path, adw_id: str) -> int:
    """The detached monitor: while the run's container is alive, merge the
    per-run db into the project db (live-ish visibility, ~3s), then a final
    sync and teardown once the ADW exits (success or fail). One project
    connection is reused (the host owns the project db — WAL, host filesystem —
    so concurrent monitors serialize through busy_timeout). Spawned by
    `sssf run`/`ticket run` right after the container starts."""
    from sssf.adw_modules.tracer import Tracer

    data_dir, _pi, _env = sandbox_env(project_root)
    project_db = project_db_path(data_dir)
    per_run_db = sandbox_dir(project_root, adw_id) / "adws" / "data" / "sssf.db"
    tracer = Tracer(str(project_db), str(project_db.parent / "sessions" / adw_id / "events.jsonl"))
    try:
        while True:
            if _container_gone(_docker, container_name(adw_id)):
                break  # container gone — the run is done
            sync_run_db(tracer.conn, per_run_db, adw_id)
            time.sleep(3)
    finally:
        # Evidence first (logs are read while the container still exists),
        # then the final merge, then teardown. A spawn-death must leave a
        # visible failed session — never look like it 'never started'.
        try:
            record_never_started(project_root, adw_id, tracer, per_run_db)
        except Exception as error:  # evidence is best-effort; teardown must still run
            print(f"sssf: could not record spawn failure ({error})", file=sys.stderr)
        sync_run_db(tracer.conn, per_run_db, adw_id)  # final merge
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prune_sandbox(project_root: Path, adw_id: str) -> int:
    """Remove a run's leftovers AND its branch — the engineer runs this once
    the PR is merged (or to discard a failed run). Idempotent."""
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    delete_branch(project_root, adw_id)
    return 0


def _git_identity(project_root: Path) -> tuple[str, str]:
    """The operator's git identity, resolved from the project root so both
    repo-local and global config work. Empty strings when unset — the
    container then has no identity and git fails loudly instead of silently
    attributing the commit."""

    def get(key: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(project_root), "config", key],
            capture_output=True,
            text=True,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    return get("user.name"), get("user.email")


def sandbox_env(project_root: Path) -> tuple[Path, Path, dict[str, str]]:
    """The per-run data dir (shared, bind-mounted rw), the pi home (read-only
    mount), and the env passed to the container: credentials + git identity
    only — never project files."""
    from sssf.adw_modules import paths

    data_dir = paths.data_dir(project_root)
    pi_home = Path(os.environ.get("PI_HOME", Path.home() / ".pi" / "agent"))
    env: dict[str, str] = {}
    if os.environ.get("OPENAI_API_KEY"):
        # The standard OpenAI env vars — litellm/pi read these natively for
        # OpenAI-compatible endpoints (e.g. GenPlat); the container gets them
        # the same way it gets SNYK_TOKEN. No GENPLAT_TOKEN: it is not a
        # standard var and no tooling in the container reads it.
        env["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL"):
        env["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("SNYK_TOKEN"):
        # SNYK_TOKEN (service accounts / CI) is forwarded when set. OAuth-authed
        # CLIs need nothing here: spawn_sandbox mounts the operator's ~/.config
        # (where snyk keeps its OAuth session) read-only at /tmp/.config. A stale
        # SNYK_TOKEN overrides that session (snyk env precedence) — the gate then
        # fails SNYK-0005 even though `snyk whoami` succeeds with the var unset.
        env["SNYK_TOKEN"] = os.environ["SNYK_TOKEN"]
    name, email = _git_identity(project_root)
    if name and email:
        # Author AND committer — git needs both pairs inside the container, or
        # `git commit` dies with "Committer identity unknown". ENGINEER_NAME is
        # how engineer_name() resolves the run's engineer label in the sandbox.
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
                "ENGINEER_NAME": name,
            }
        )
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


def reopen_session(data_dir: Path, adw_id: str) -> None:
    """A restart (attach) re-opens the project row of a TERMINAL session so the
    UI reflects the new run. Without this the host row keeps the first run's
    terminal state forever: the monitor's forward-merge only updates un-ended
    rows (ended_at IS NULL), so a restarted run — however long it lives or
    however it ends — never flips the row back to running and never records its
    own outcome (session 9701903a: status stayed 'fail / ended 21:30' while a
    restarted run was live).

    The previous run's events and phase rows are cleared too: the restarted run
    reuses the SAME phase_ids, and the phases merge is forward-only (an ended
    host phase row is never overwritten), so without the reset the trace's
    waterfall would keep the old run's statuses (04_build fail 'finalized by
    the healer') on top of the new run's events. Events are already replaced
    wholesale by every sync — the trace is "the current run" by design.
    Best-effort: the run proceeds even if a write fails."""
    import datetime
    import sqlite3

    db_path = project_db_path(data_dir)
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        # events first — events.phase_id references phases
        for table in ("events", "phases"):
            conn.execute(f"DELETE FROM {table} WHERE adw_id=?", (adw_id,))
        conn.execute(
            "UPDATE sessions SET status='running', started_at=?, ended_at=NULL WHERE adw_id=?",
            (datetime.datetime.now(datetime.UTC).isoformat(), adw_id),
        )
        conn.close()
    except sqlite3.Error:
        pass


def stop_run(
    project_root: Path, adw_id: str, data_dir: Path, reason: str = "stopped by the engineer"
) -> int:
    """Terminate a run: kill the container and remove the worktree. If the
    session is still marked running afterwards (a stale run whose ADW died
    without its failsafe — e.g. SIGKILL teardown), finalize it as failed so it
    becomes archivable, and mark every in-flight/queued PHASE failed — the
    trace must show the run stopped cleanly, never a phase stuck 'running'.
    `reason` is what the trace records as the phase error — the healer says
    what IT did; only the engineer's own stop says 'stopped by the engineer'.
    The branch stays for inspection (prune deletes it once resolved)."""
    stop_remove(container_name(adw_id))
    remove_worktree(sandbox_dir(project_root, adw_id))
    status = _session_status(data_dir, adw_id)
    if status is not None and status not in ("success", "fail"):
        import datetime

        from sssf.adw_modules.tracer import Tracer

        tracer = Tracer(
            str(project_db_path(data_dir)), str(data_dir / "sessions" / adw_id / "events.jsonl")
        )
        now = datetime.datetime.now(datetime.UTC).isoformat()
        tracer.conn.execute(
            "UPDATE phases SET status='fail', error=?, ended_at=? "
            "WHERE adw_id=? AND status IN ('running','queued')",
            (reason, now, adw_id),
        )
        tracer.session_finish(adw_id, ok=False)  # a cancelled run is failed
    return 0


def stamp_adw_template(wt: Path) -> None:
    """Stamp the CURRENT installed ADW modules into the worktree (v2 layout:
    adws/modules/adw_*.py). The worktree's copies are the project's committed
    templates (stamped at init, possibly stale after an sssf upgrade — e.g.
    the chain-builder migration and the review-gate removal) — sandboxed runs
    must run the installed ADWs so the run matches the installed sssf exactly.

    Only files the installed templates ship are refreshed: a project's CUSTOM
    adw_*.py (no installed twin) stays as committed. Prompt_engineering is NOT
    stamped — those files are the documented per-project customization surface
    and the sandbox contract runs the committed project state for them."""
    import shutil

    import sssf

    templates = Path(sssf.__file__).parent / "templates"
    src_modules = templates / "adws" / "modules"
    if not src_modules.is_dir():
        return
    dest = wt / "adws" / "modules"
    dest.mkdir(parents=True, exist_ok=True)
    for adw in src_modules.glob("adw_*.py"):
        shutil.copy(adw, dest / adw.name)
