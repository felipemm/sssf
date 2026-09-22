"""Run lifecycle: the sandbox decision, spawn (worktree + container +
record), stop/abort, and the no-op teardown. Deterministic plain Python
— no agents. The monitor (detached) and its helpers live here too.
"""

import contextlib
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from sssf.sandbox.docker import (
    SandboxError,
    _docker,
    container_name,
    ensure_image_current,
    run_sandbox,
    stop_container,
)
from sssf.sandbox.rundb import (
    _record_sandbox_run,
    _session_status,
    project_db_path,
    sandbox_run_db,
    sync_run_db,
)
from sssf.sandbox.session_env import sandbox_env
from sssf.sandbox.worktree_git import (
    create_worktree,
    integrate_successful_run,
    sandbox_dir,
)


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
    review: dict | None = None,
) -> dict:
    """Start the container in a (created) worktree. Deterministic; returns the
    sandbox record (worktree, name). attach=True reuses the run's existing
    branch (a restart). `worktree` supplies an ALREADY-created worktree (the
    ticket path creates one first to write the prompt) — never create twice.

    `review` is the project's cfg.sandbox.review as a dict: the ADW command is
    wrapped in the container supervisor (which keeps the container up after the
    run and launches the review app), the review container_port is published on
    a random host port, and the resolved mapping is recorded in sandbox_run.
    """
    ensure_image_current(image)
    wt = worktree or create_worktree(project_root, adw_id, attach=attach)
    stamp_adw_template(wt)  # deterministic: the installed template, not a stale init stamp
    uid = uid if uid is not None else os.getuid()
    gid = gid if gid is not None else os.getgid()
    env = {**(env or {}), "SSSF_IN_SANDBOX": "1"}  # tracer uses rollback journal (mount-visible)
    review = review or {}
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
        publish_port=review.get("container_port"),
        cmd=["python", "-m", "sssf.adw_modules.supervise", "--", *cmd],
    )
    _record_sandbox_run(data_dir, adw_id, review)
    return {"worktree": str(wt), "name": container_name(adw_id)}


def abort_sandbox(project_root: Path, adw_id: str) -> None:
    """Clean up after a FAILED spawn: STOP the (possibly 'Created'-stuck)
    container — it is kept (logs stay readable); `sssf sweep` removes it. The
    worktree stays under .worktrees/ for inspection."""
    stop_container(container_name(adw_id))


def teardown_sandbox(project_root: Path, adw_id: str) -> int:
    """No-op by design: after a run, NEITHER the container nor the worktree
    is deleted — they are the operator's debugging surface (docker logs, the
    .worktrees/<adw_id> checkout, its adws/data/sessions artifacts). Cleanup is
    explicit: `sssf sandbox prune <adw_id>` or `sssf sweep`."""
    return 0


def _container_gone(docker_fn, name: str) -> bool:
    """True only when the container is actually gone. A docker hiccup is NOT
    'gone' — treat it as a retry, never as the run finishing (audit A2)."""
    try:
        r = docker_fn("ps", "--filter", f"name={name}", "--format", "{{.Status}}", timeout_s=30)
        return not r.stdout.strip()
    except Exception as error:  # docker is down/glitchy
        print(f"sssf: teardown poll docker error ({error}) — retrying", file=sys.stderr)
        return False


def _run_ended(wt_data: Path, adw_id: str) -> bool:
    """True once the container-side supervisor wrote its exit marker — the ADW
    process has ended. The container itself stays up (review mode), so its
    death alone no longer marks the end of a run."""
    return (wt_data / "sessions" / f"{adw_id}.supervisor-exit").exists()


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
    from sssf.postmortem import classify_failure

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
                "remediation": classify_failure(log_tail, exit_code),
            },
        )
    )
    tracer.conn.execute(
        "UPDATE tickets SET status='ready-for-agent', updated_at=? WHERE adw_id=?",
        (now, adw_id),
    )
    try:
        from sssf import ticketing

        ticketing.ensure_schema(tracer.conn)  # ticket_events + machine columns
        row = tracer.conn.execute(
            "SELECT id, status FROM tickets WHERE adw_id=?", (adw_id,)
        ).fetchone()
        if row:
            ticketing.add_ticket_event(
                tracer.conn,
                row[0],
                "transition",
                actor="system",
                payload={
                    "from": row[1],
                    "to": "ready-for-agent",
                    "reason": "spawn failure: container exited before the ADW started",
                    "exit_code": exit_code,
                },
            )
    except sqlite3.Error:
        pass  # audit write is best-effort — the status flip above is the truth


def monitor_run(project_root: Path, adw_id: str) -> int:
    """The detached monitor: while the run is live, merge the per-run db into
    the project db (live-ish visibility, ~3s), then a final sync once the run
    has ENDED — the supervisor-exit marker (the container is deliberately left
    up in review mode) or the container dying. One project connection is reused
    (the host owns the project db — WAL, host filesystem — so concurrent
    monitors serialize through busy_timeout). Spawned by `sssf flow`/`ticket
    run` right after the container starts."""
    from sssf.adw_modules.tracer import Tracer

    data_dir, _pi, _env = sandbox_env(project_root)
    project_db = project_db_path(data_dir)
    wt_data = sandbox_dir(project_root, adw_id) / "adws" / "data"
    per_run_db = wt_data / "sssf.db"
    tracer = Tracer(str(project_db), str(project_db.parent / "sessions" / adw_id / "events.jsonl"))
    try:
        while True:
            if _container_gone(_docker, container_name(adw_id)) or _run_ended(wt_data, adw_id):
                break  # container gone OR the run ended — the container may
                # still be up in review mode; leave it running for the engineer
            sync_run_db(tracer.conn, per_run_db, adw_id)
            time.sleep(3)
    finally:
        # Evidence first (logs are read while the container still exists),
        # then the final merge. The container and worktree are KEPT — review
        # and restart surfaces; only `sssf sweep` deletes. A spawn-death must
        # leave a visible failed session — never look like it 'never started'.
        try:
            record_never_started(project_root, adw_id, tracer, per_run_db)
        except Exception as error:  # evidence is best-effort
            print(f"sssf: could not record spawn failure ({error})", file=sys.stderr)
        sync_run_db(tracer.conn, per_run_db, adw_id)  # final merge
        # Post-success integration: a successful run merges back into the
        # integration branch (config integration.branch) instead of leaving the
        # operator to merge by hand. Best-effort: the monitor must never crash
        # over a merge hiccup — the outcome lands as a trace event either way.
        try:
            outcome = integrate_successful_run(project_root, adw_id)
            if outcome:
                from sssf.adw_modules.data_types import EventRecord

                name = outcome.get("reason") or (
                    f"{outcome['outcome']} into {outcome['target']}"
                )
                # Anchor the event to the run's last phase so the trace shows
                # it (phase-less events are not rendered by the visualizer).
                phase_id = ""
                try:
                    row = tracer.conn.execute(
                        "SELECT phase_id FROM phases WHERE adw_id=? AND seq IS NOT NULL "
                        "ORDER BY seq DESC LIMIT 1",
                        (adw_id,),
                    ).fetchone()
                    if row:
                        phase_id = row[0]
                except sqlite3.Error:
                    phase_id = ""
                tracer.event(
                    EventRecord(
                        adw_id=adw_id,
                        phase_id=phase_id,
                        type="integration",
                        name=name,
                        payload=outcome,
                    )
                )
        except Exception as error:
            print(f"sssf: post-run integration failed ({error})", file=sys.stderr)
        with contextlib.suppress(OSError):
            (wt_data / "sessions" / f"{adw_id}.supervisor-exit").unlink(missing_ok=True)
    return 0


def spawn_monitor(project_root: Path, adw_id: str) -> None:
    """Launch monitor_run detached (it blocks for the run's whole lifetime)."""
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from sssf.sandbox.orchestrator import monitor_run\n"
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
    """DEPRECATED — only `sssf sweep` deletes run artifacts now. Raises so the
    command can point the engineer at sweep."""
    raise SandboxError(
        "cleanup is `sssf sweep` only — `sssf sandbox prune` is deprecated "
        "(containers, worktrees and branches are never deleted otherwise)"
    )


def stop_run(
    project_root: Path, adw_id: str, data_dir: Path, reason: str = "stopped by the engineer"
) -> int:
    """Stop a run: STOP the container (it is kept — logs + worktree mount stay
    for review and restart; only `sssf sweep` deletes) and finalize the session.
    If the session is still marked running afterwards (a stale run whose ADW
    died without its failsafe — e.g. SIGKILL teardown), finalize it as failed so
    it becomes archivable, and mark every in-flight/queued PHASE failed — the
    trace must show the run stopped cleanly, never a phase stuck 'running'.
    `reason` is what the trace records as the phase error — the healer says
    what IT did; only the engineer's own stop says 'stopped by the engineer'.
    The worktree (uncommitted agent work, per-run db) and the branch survive
    for a restart; the sandbox_run record flips to 'stopped'."""
    stop_container(container_name(adw_id))
    _flip_sandbox_run_stopped(data_dir, adw_id)
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


def _flip_sandbox_run_stopped(data_dir: Path, adw_id: str) -> None:
    """The container is stopped (kept) — record it so the viz shows the review
    URL is no longer reachable. Best-effort."""
    import datetime

    try:
        conn = sandbox_run_db(data_dir)
        conn.execute(
            "UPDATE sandbox_run SET status='stopped', updated_at=? WHERE adw_id=?",
            (datetime.datetime.now(datetime.UTC).isoformat(), adw_id),
        )
        conn.close()
    except sqlite3.Error:
        pass


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
