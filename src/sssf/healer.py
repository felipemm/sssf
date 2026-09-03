"""Self-healing monitor: scan running sessions and recover the stuck ones.

Deterministic Python — no agents. The daemon loops over every registered
project; for each running session / starting ticket it diagnoses staleness
(dead container+worktree, a monitor crash before teardown, a hung phase with
no progress, spawn failures, orphaned leftovers) and recovers: finalize,
sync-and-teardown, or restart with a budget. Nothing healthy is touched.

State: ~/.sssf/heal-state.json tracks per-session restart counts (the budget).
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from sssf.sandbox import (
    abort_sandbox,
    build_runner_image,
    container_name,
    docker_available,
    image_is_current,
    project_db_path,
    sandbox_dir,
    sandbox_env,
    stop_remove,
    sync_run_db,
    teardown_sandbox,
)

STATE_DIR = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf"))
REGISTRY_PATH = Path(os.environ.get("SSSF_REGISTRY", STATE_DIR / "projects.json"))
NO_PROGRESS_MIN = 10  # a phase with no new events for this long = hung
MAX_RESTARTS = 3  # per-session restart budget before finalizing
DEFAULT_INTERVAL = 30  # daemon loop interval (seconds)
BUILD_COOLDOWN_MIN = 30  # min between image-rebuild attempts after a failure


# ── registry ───────────────────────────────────────────────────────────────


def registry_projects() -> list[tuple[str, Path]]:
    """[(name, root)] from the registry; [] when absent or unreadable."""
    try:
        data = json.loads(REGISTRY_PATH.read_text())
        projects = data.get("projects", data) if isinstance(data, dict) else data
        return [(p.get("name"), Path(p["root"])) for p in projects if p.get("root")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _project_db(root: Path) -> Path:
    data_dir, _pi, _env = sandbox_env(root)
    return project_db_path(data_dir)


# ── diagnosis ──────────────────────────────────────────────────────────────


def _age_minutes(ts: str | None) -> float | None:
    """Minutes since an ISO timestamp (used for ticket updated_at ages).
    Naive timestamps are treated as UTC (the tracer writes UTC)."""
    if not ts:
        return None
    import datetime

    try:
        last = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=datetime.UTC)
        return (datetime.datetime.now(datetime.UTC) - last).total_seconds() / 60
    except ValueError:
        return None


def _last_event_minutes(db: Path, adw_id: str) -> float | None:
    """Minutes since the session's most recent event; None when none exist."""
    try:
        conn = sqlite3.connect(str(db), isolation_level=None, timeout=5)
        row = conn.execute(
            "SELECT MAX(started_at) FROM events WHERE adw_id=?", (adw_id,)
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    ts = row[0] if row else None
    if not ts:
        return None
    import datetime

    try:
        last = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.UTC) - last).total_seconds() / 60
    except ValueError:
        return None


def diagnose(
    session_status: str | None,
    ticket_status: str | None,
    has_container: bool,
    has_worktree: bool,
    per_run_db_exists: bool,
    last_event_min: float | None,
    ticket_age_min: float | None = None,
    linked_session_status: str | None = None,
) -> str | None:
    """Return the recovery action for one run, or None when it is healthy."""
    # A running session with neither container nor worktree: the ADW died
    # silently and nothing can be recovered — finalize it.
    if session_status == "running" and not has_container and not has_worktree:
        return "finalize"
    # A running session whose container is gone but the worktree + per-run db
    # remain: the monitor crashed before its final sync + teardown — recover
    # the terminal state and clean up.
    if session_status == "running" and not has_container and has_worktree and per_run_db_exists:
        return "sync_teardown"
    # A live container whose session has made no progress for a long time:
    # the agent call is hung — restart (subject to the budget).
    if (
        session_status == "running"
        and has_container
        and last_event_min is not None
        and last_event_min > NO_PROGRESS_MIN
    ):
        return "restart"
    # A ticket still 'starting' too long with NO session at all (its spawn
    # never produced one): put the ticket back in the backlog and clean up.
    # The age is the time since the ticket was marked starting (updated_at).
    # A ticket whose session EXISTS is never this case — a stale updated_at
    # (run() bumps it at spawn, but older retries may not) must not classify
    # a live or failed run as a spawn failure.
    if (
        ticket_status == "starting"
        and linked_session_status is None
        and ticket_age_min is not None
        and ticket_age_min > NO_PROGRESS_MIN
    ):
        return "ticket_backlog"
    # A ticket whose RUN FAILED (its session went terminal-fail): back to the
    # backlog so it can be retried. History is preserved — the failed run
    # stays linked (see recover's ticket_backlog branch).
    if ticket_status is not None and ticket_status != "backlog" and linked_session_status == "fail":
        return "ticket_backlog"
    return None


# ── restart budget ─────────────────────────────────────────────────────────


def state() -> dict:
    """Parsed heal-state.json (restart budgets); {} on unreadable — read-only."""
    try:
        return json.loads((STATE_DIR / "heal-state.json").read_text())
    except (OSError, ValueError):
        return {"restarts": {}}


def _save_state(state: dict) -> None:
    with contextlib.suppress(OSError):
        (STATE_DIR / "heal-state.json").write_text(json.dumps(state, indent=2))


def _restart_count(state: dict, adw_id: str) -> int:
    return int(state.get("restarts", {}).get(adw_id, 0))


# ── recovery ───────────────────────────────────────────────────────────────


def recover(
    root: Path,
    adw_id: str,
    session_status: str | None,
    ticket_status: str | None,
    action: str,
    state: dict,
) -> str:
    """Perform one recovery action; returns a human summary line.

    Every recovery is recorded in the state file with a UTC timestamp (the
    'healed' list, pruned to the last 7 days) — that record is the source of
    the cockpit's 'sessions healed (7d)' metric, so a daemon restart never
    loses or double-counts a heal.
    """
    project_db = _project_db(root)
    import datetime

    now = datetime.datetime.now(datetime.UTC)
    state.setdefault("healed", []).append({"adw_id": adw_id, "ts": now.isoformat()})
    cutoff = (now - datetime.timedelta(days=7)).isoformat()
    state["healed"] = [h for h in state["healed"] if h.get("ts", "") >= cutoff][-1000:]
    wt = sandbox_dir(root, adw_id)
    per_run_db = wt / "adws" / "data" / "sssf.db"

    if action == "finalize":
        from sssf.sandbox import stop_run

        stop_run(
            root,
            adw_id,
            project_db.parent,
            reason="finalized by the healer: dead run — no container or "
            "worktree left, nothing could be recovered",
        )
        return f"{adw_id}: finalized (dead run)"

    if action == "sync_teardown":
        try:
            conn = sqlite3.connect(str(project_db), isolation_level=None, timeout=10)
            sync_run_db(conn, per_run_db, adw_id)
            conn.close()
        except sqlite3.Error:
            pass
        teardown_sandbox(root, adw_id)
        return f"{adw_id}: recovered terminal state + tore down"

    if action == "restart":
        count = _restart_count(state, adw_id)
        if count >= MAX_RESTARTS:
            from sssf.sandbox import stop_run

            stop_run(
                root,
                adw_id,
                project_db.parent,
                reason=f"finalized by the healer: restart budget exhausted "
                f"({MAX_RESTARTS} attempts)",
            )
            state.setdefault("restarts", {}).pop(adw_id, None)
            return f"{adw_id}: restart budget exhausted — finalized"
        state.setdefault("restarts", {})[adw_id] = count + 1
        _save_state(state)
        r = subprocess.run(
            ["sssf", "run", "restart", adw_id, "--project", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            # The heal log is the ONLY place a failed restart is visible: a
            # no-op restart (e.g. 'no request to re-run') leaves the session
            # exactly as hung as before, and claiming 'restarted' here made 3
            # phantom recoveries burn the whole budget in a minute before the
            # session was finalized (session 9701903a, 2026-09-02). Say what
            # the CLI said; the budget still counts so an endlessly-failing
            # restart stays bounded.
            detail = (r.stderr or r.stdout or f"exit {r.returncode}").strip()
            return f"{adw_id}: restart FAILED ({count + 1}/{MAX_RESTARTS}) — {detail}"
        return f"{adw_id}: restarted ({count + 1}/{MAX_RESTARTS})"

    if action == "ticket_backlog":
        try:
            conn = sqlite3.connect(str(project_db), isolation_level=None, timeout=5)
            # History is preserved: the adw_id link stays, so the failed run
            # remains in the trace and in the ticket's run list.
            conn.execute(
                "UPDATE tickets SET status='backlog', updated_at=? WHERE adw_id=?",
                (datetime.datetime.now(datetime.UTC).isoformat(), adw_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.Error:
            pass
        abort_sandbox(root, adw_id)
        return f"{adw_id}: ticket back to backlog (history kept)"

    return f"{adw_id}: unknown action {action}"


# ── one pass ───────────────────────────────────────────────────────────────


def heal_once(initial: dict | None = None) -> list[str]:
    """Scan every registered project, recover what is stuck; return the actions."""
    # NB: the working dict is named 'st', never 'state' — 'state' is the
    # module-level reader; a shadowed name silently kills every pass.
    st = initial if initial is not None else state()
    actions: list[str] = []
    for _name, root in registry_projects():
        from sssf.adw_modules import paths

        paths.warn_if_legacy(root, command="healer")
        project_db = _project_db(root)
        if not project_db.exists():
            continue
        try:
            conn = sqlite3.connect(str(project_db), isolation_level=None, timeout=10)
            rows = conn.execute(
                "SELECT adw_id, status FROM sessions WHERE status='running'"
            ).fetchall()
            tickets = conn.execute(
                "SELECT t.adw_id, t.status, t.updated_at, s.status"
                " FROM tickets t LEFT JOIN sessions s ON s.adw_id = t.adw_id"
                " WHERE t.status != 'backlog' AND t.adw_id IS NOT NULL"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        for adw_id, status in rows:
            wt = sandbox_dir(root, adw_id)
            has_wt = wt.exists()
            has_ct = _container_exists(adw_id)
            per_run = wt / "adws" / "data" / "sssf.db"
            action = diagnose(
                status,
                None,
                has_ct,
                has_wt,
                per_run.exists(),
                _last_event_minutes(project_db, adw_id),
            )
            if action:
                actions.append(recover(root, adw_id, status, None, action, st))
        for adw_id, ticket_status, updated_at, linked_status in tickets:
            wt = sandbox_dir(root, adw_id)
            has_ct = _container_exists(adw_id)
            action = diagnose(
                None,
                ticket_status,
                has_ct,
                wt.exists(),
                False,
                _last_event_minutes(project_db, adw_id),
                _age_minutes(updated_at),
                linked_status,
            )
            if action:
                actions.append(recover(root, adw_id, None, ticket_status, action, st))
        # orphaned containers/worktrees whose session is gone
        actions.extend(_clean_orphans(root))
    # preventive upkeep: a stale/missing runner image would break the NEXT
    # sandboxed run — rebuild it now (deduped, cooldown-guarded).
    actions.extend(_heal_runner_images(st))
    _save_state(st)
    return actions


def _container_exists(adw_id: str) -> bool:
    r = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name={container_name(adw_id)}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(r.stdout.strip())


def _clean_orphans(root: Path) -> list[str]:
    """Remove sandbox worktrees/containers that no longer match any session."""
    cleaned: list[str] = []
    base = STATE_DIR / "sandboxes" / root.name
    if not base.is_dir():
        return cleaned
    try:
        conn = sqlite3.connect(str(_project_db(root)), isolation_level=None, timeout=5)
        known = {r[0] for r in conn.execute("SELECT adw_id FROM sessions").fetchall()}
        conn.close()
    except sqlite3.Error:
        return cleaned
    for wt in base.iterdir():
        if wt.is_dir() and wt.name not in known:
            stop_remove(container_name(wt.name))
            teardown_sandbox(root, wt.name)
            cleaned.append(f"{wt.name}: orphaned sandbox removed")
    return cleaned


def healed_total(days: int = 7) -> int:
    """Count recovery actions taken in the LAST N days (default 7).

    Sources the timestamped 'healed' list in heal-state.json, written by
    recover() — the daemon log has no timestamps and appends across restarts,
    so it cannot answer a sliding-window question.
    """
    import datetime

    cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
    return sum(1 for h in state().get("healed", []) if h.get("ts", "") >= cutoff)


# ── runner image upkeep ─────────────────────────────────────────────────────


def _build_attempt_recent(st: dict, image: str) -> bool:
    """A rebuild was attempted for this image within BUILD_COOLDOWN_MIN — skip
    it so a failing build (docker down, no network for the base image) doesn't
    hammer docker on every pass. A successful build clears the record, so the
    cooldown only ever covers failures."""
    import datetime

    ts = st.get("builds", {}).get(image)
    if not ts:
        return False
    try:
        last = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=datetime.UTC)
    except ValueError:
        return False
    return (datetime.datetime.now(datetime.UTC) - last).total_seconds() / 60 < BUILD_COOLDOWN_MIN


def _heal_runner_images(st: dict) -> list[str]:
    """Rebuild the runner image when it is stale or missing — the failure mode
    where every sandboxed run dies on spawn (issue #21) until someone runs
    `sssf sandbox build` by hand.

    Runs once per pass, after session/ticket healing: images are collected from
    every registered project's config (deduped by name, skipped when the
    project has sandbox disabled), gated on docker being available, and each
    stale image is rebuilt. A failed build is remembered in state for
    BUILD_COOLDOWN_MIN so the daemon doesn't hammer docker; a successful
    rebuild clears the record — the fingerprint then matches and this step is
    a no-op until the next engine change.
    """
    import datetime

    if not docker_available():
        return []
    actions: list[str] = []
    seen: set[str] = set()
    for _name, root in registry_projects():
        try:
            from sssf.adw_modules import paths
            from sssf.adw_modules.agents import load_config

            cfg = load_config(str(paths.config_file(root)))
        except Exception:
            continue  # unreadable config — session/ticket healing covers the rest
        image = cfg.sandbox.image
        if not cfg.sandbox.enabled or image in seen:
            continue
        seen.add(image)
        if image_is_current(image):
            continue  # up to date — nothing to do
        if _build_attempt_recent(st, image):
            continue  # a recent attempt failed; back off until the cooldown lapses
        try:
            build_runner_image(image)
        except Exception as error:  # never die — arm the cooldown and retry later
            st.setdefault("builds", {})[image] = datetime.datetime.now(datetime.UTC).isoformat()
            actions.append(
                f"{image}: image rebuild failed ({error}) — retrying in {BUILD_COOLDOWN_MIN} min"
            )
        else:
            st.setdefault("builds", {}).pop(image, None)
            actions.append(f"{image}: image rebuilt (runner image is current)")
    return actions


def log_tail(n: int = 5) -> list[str]:
    """Last n non-empty lines of the daemon log; [] when unreadable."""
    try:
        lines = [line for line in _log_file().read_text().splitlines() if line.strip()]
        return lines[-n:]
    except OSError:
        return []


def heal_summary() -> dict:
    """Read-only snapshot for the cockpit: running state, log tail, restart
    budgets, recent image-rebuild attempts."""
    pid = running_pid()
    return {
        "running": pid is not None,
        "pid": pid,
        "logTail": log_tail(),
        "restarts": state().get("restarts", {}),
        "builds": state().get("builds", {}),
        "healed7d": healed_total(),
    }


# ── daemon loop ────────────────────────────────────────────────────────────


def run_loop(interval: int = DEFAULT_INTERVAL) -> int:
    """The daemon: heal every interval until killed. stdout feeds heal.log."""
    import datetime

    def stamp():
        return datetime.datetime.now(datetime.UTC).isoformat()

    print(f"{stamp()} sssf heal: daemon started — interval {interval}s", flush=True)
    while True:
        try:
            actions = heal_once()
            for a in actions:
                print(f"{stamp()} sssf heal: {a}", flush=True)
        except Exception as e:  # the daemon must never die
            print(f"sssf heal: pass error: {e}", flush=True)
        time.sleep(interval)
    return 0


# ── service control (mirrors viz) ──────────────────────────────────────────


def _pid_file() -> Path:
    return STATE_DIR / "heal.pid"


def _log_file() -> Path:
    return STATE_DIR / "heal.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def running_pid() -> int | None:
    pid_file = _pid_file()
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return None
    return pid if _pid_alive(pid) else None


def start() -> int:
    if running_pid():
        print(f"sssf heal: already running (pid {running_pid()})")
        return 0
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from sssf.healer import run_loop\n"
        "sys.exit(run_loop())\n"
    )
    with open(_log_file(), "a") as log:
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
    _pid_file().write_text(str(proc.pid))
    print(f"sssf heal: started (pid {proc.pid}) — log at {_log_file()}")
    return 0


def stop() -> int:
    pid = running_pid()
    if pid is None:
        print("sssf heal: not running")
        return 0
    try:
        os.kill(pid, 15)
        time.sleep(0.5)
    except ProcessLookupError:
        pass
    print(f"sssf heal: stopped (pid {pid})")
    return 0


def status() -> int:
    pid = running_pid()
    print(f"sssf heal: {'running (pid ' + str(pid) + ')' if pid else 'not running'}")
    if _log_file().exists():
        tail = _log_file().read_text().strip().splitlines()[-5:]
        for line in tail:
            print(f"  {line}")
    return 0
