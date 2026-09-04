import subprocess
import sys
from pathlib import Path

from sssf.adw_modules import session
from sssf.adw_modules import tracer as tracer_mod
from sssf.adw_modules.data_types import Phase, PhaseParams, SSSFConfig


def _make_cfg(tmp_path: Path) -> SSSFConfig:
    return SSSFConfig(
        defaults={"model": "litellm/gemini-2.5-flash", "data_dir": str(tmp_path / "data")},
        observability={"db": str(tmp_path / "data" / "sssf.db")},
    )


def _open_phase(t: tracer_mod.Tracer, adw_id: str) -> None:
    t.phase_upsert(
        Phase(
            phase_id=f"{adw_id}_01_build",
            adw_id=adw_id,
            seq=1,
            params=PhaseParams(name="build", kind="agent", owner="builder", description="d"),
            status="running",
        )
    )


# ── stale-run reaping (re-run kills + marks failed) ──────────────────────────


def test_reap_kills_stale_process_and_marks_phases_failed(tmp_path):
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    t.session_start("abc", "tester", "adw_x")
    _open_phase(t, "abc")
    proc = subprocess.Popen(["sleep", "100"])
    t.process_start("abc", "agent", "builder", proc.pid, "sleep 100")

    session._reap_stale_run(t, "abc")

    assert proc.poll() is not None, "stale process was not killed"
    row = t.conn.execute("SELECT status, error FROM phases WHERE adw_id='abc'").fetchone()
    assert row[0] == "fail" and "reaped" in row[1]
    ended = t.conn.execute(
        "SELECT ended_at FROM processes WHERE adw_id='abc' AND pid=?", (proc.pid,)
    ).fetchone()
    assert ended[0] is not None
    t.conn.close()


def test_reap_spares_unrelated_process_with_mismatched_command(tmp_path):
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    t.session_start("abc", "tester", "adw_x")
    _open_phase(t, "abc")
    proc = subprocess.Popen(["sleep", "100"])
    # recorded command does not match what `ps` shows — a recycled pid must not
    # take down an innocent process
    t.process_start("abc", "agent", "builder", proc.pid, "totally-different-tool")

    session._reap_stale_run(t, "abc")

    assert proc.poll() is None, "unrelated process was killed"
    proc.kill()
    proc.wait()
    t.conn.close()


def test_reap_is_noop_for_fresh_adw_id(tmp_path):
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    session._reap_stale_run(t, "brand-new")  # must not raise
    assert t.conn.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 0
    t.conn.close()


# ── failsafe: any uncaught exception marks the session failed ────────────────


def test_uncaught_exception_marks_session_failed(tmp_path):
    cfg = _make_cfg(tmp_path)
    run = session.ensure(cfg, "abc")
    try:
        sys.excepthook(RuntimeError, RuntimeError("boom"), None)
    finally:
        sys.excepthook = sys.__excepthook__
    row = run.tracer.conn.execute("SELECT status FROM sessions WHERE adw_id='abc'").fetchone()
    assert row[0] == "fail"
    run.tracer.conn.close()


# ── re-join: a re-run is a NEW generation of the session ─────────────────────


def test_session_start_on_rerun_clears_previous_terminal_state(tmp_path):
    """A re-run of the same adw_id must not inherit the previous attempt's
    terminal state. Regression (session 2e3d7693): session_start flipped
    status to 'running' but left the old ended_at set — a contradictory
    'running + ended_at' row the monitor's forward-merge froze at the old
    failure, so the kanban card sat in Blocked while attempt 2 ran."""
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    t.session_start("abc", "tester", "adw_simple_sdlc")
    t.session_finish("abc", ok=False)  # attempt 1 fails
    before = t.conn.execute(
        "SELECT status, ended_at FROM sessions WHERE adw_id='abc'"
    ).fetchone()
    assert before == ("fail", before[1]) and before[1] is not None

    t.session_start("abc", "tester", "adw_simple_sdlc")  # attempt 2 joins
    row = t.conn.execute(
        "SELECT status, ended_at FROM sessions WHERE adw_id='abc'"
    ).fetchone()
    assert row[0] == "running"
    assert row[1] is None, "re-run must clear the previous attempt's ended_at"
    names = t.conn.execute(
        "SELECT adw_name FROM sessions WHERE adw_id='abc'"
    ).fetchone()
    assert names[0] == "adw_simple_sdlc"  # same ADW re-joining is not chained
    t.conn.close()
