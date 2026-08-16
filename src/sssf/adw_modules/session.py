"""Session lifecycle: pin-or-create an adw_id, build the Run object.

`ensure(cfg, adw_id)` joins the session if it exists or creates it under
exactly that id (pinned ids for repeatable runs); omitted, a fresh id is
minted and printed so the next ADW can pick it up.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .data_types import SSSFConfig
from .runner import Run
from .tracer import Tracer
from .utils import engineer_name, new_id, now_iso


def _finalize_when_killed(run: Run) -> None:
    """A killed run still closes its own trace.

    Python's default SIGTERM handling exits without unwinding, so `just kill`
    (or any `kill <pid>`) would leave the session reading `running` forever and
    its process rows open — the trace would claim work is in flight that is
    already dead. Turning the signal into SystemExit both finalizes here and
    lets the phase context manager record the phase as failed on the way out.
    """
    def handler(signum, _frame):
        run.tracer.session_finish(run.adw_id, ok=False)   # also closes process rows
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def ensure(cfg: SSSFConfig, adw_id: str | None = None) -> Run:
    adw_id = adw_id or new_id(8)
    tracer = Tracer(cfg.observability.db,
                    f"{cfg.defaults.data_dir}/sessions/{adw_id}/events.jsonl")
    _reap_stale_run(tracer, adw_id)
    run = Run(cfg=cfg, adw_id=adw_id, tracer=tracer, engineer=engineer_name())
    tracer.session_start(adw_id, run.engineer, adw_name=Path(sys.argv[0]).stem)
    # This process is the run. Record it before any phase opens, so a run that
    # hangs in its first agent call is still killable by adw_id.
    tracer.process_start(adw_id, "adw", "", os.getpid(),
                         " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]))
    _finalize_when_killed(run)
    _failsafe_on_uncaught(run)
    run.console.session_started(adw_id, run.engineer)
    return run


def _failsafe_on_uncaught(run: Run) -> None:
    """Any uncaught exception in the ADW process must not leave the session
    reading 'running' forever. The phase manager already marks a phase (and the
    session) failed when an exception escapes a phase block; this covers the
    rest — exceptions between phases, in finish(), or anywhere else after the
    session row exists. Idempotent: an already-failed session is just written
    again."""
    def hook(exc_type, exc, tb):
        try:
            run.tracer.session_finish(run.adw_id, ok=False)
        except Exception:
            pass   # the trace must never make the original crash unreportable
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


def _reap_stale_run(tracer: Tracer, adw_id: str) -> None:
    """A re-run owns the adw_id: a previous run still marked in flight is dead
    on arrival. Terminate its recorded processes and mark its open phases and
    session failed, so the trace shows one finished run instead of a zombie
    that reads as still working.

    No-op for a fresh adw_id; the queries are cheap and hit empty tables.
    """
    stale = tracer.conn.execute(
        "SELECT pid, kind, command FROM processes WHERE adw_id=? AND ended_at IS NULL",
        (adw_id,)).fetchall()
    for pid, _kind, command in stale:
        _terminate(pid, command)
    if stale:
        time.sleep(0.5)   # grace for the SIGTERM handler to close its own trace
        for pid, _kind, command in stale:
            _terminate(pid, command, force=True)
    # Mark the prior attempt's open phases + session failed REGARDLESS of
    # stale processes — a previous attempt may have died without recording one
    # (e.g. SIGKILL), leaving phases stuck 'running'.
    tracer.conn.execute(
        "UPDATE phases SET status='fail', error=?, ended_at=? "
        "WHERE adw_id=? AND status IN ('running','queued')",
        ("reaped: superseded by a re-run", now_iso(), adw_id))
    tracer.conn.execute(
        "UPDATE sessions SET status='fail', ended_at=? WHERE adw_id=? AND status='running'",
        (now_iso(), adw_id))
    tracer.processes_end_all(adw_id)


def _terminate(pid: int, recorded_command: str, force: bool = False) -> None:
    """Kill a stale process, only when its pid still belongs to a process whose
    command line matches what the trace recorded — a recycled pid must never
    take down an innocent process."""
    if not _matches_recorded(pid, recorded_command):
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _matches_recorded(pid: int, recorded: str) -> bool:
    """The first token of the recorded command must appear in the pid's current
    command line. Recorded forms: 'adw_simple_sdlc.py …' (ADW), 'pi builder
    model' (agent) — both match against `ps` output."""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    head = (recorded or "").split()[0] if (recorded or "").split() else ""
    return bool(head) and head in out
