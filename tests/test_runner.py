"""Phase lifecycle + run acceptance (runner.py).

The not_passed status is the visual truth for a block that RAN but whose
result was red (quality checks). It must never be a hard stop: the repair loop
hands the builder the envelope, a later iteration may go green, and an
accepted run with a not_passed phase still finishes 0.
"""

from __future__ import annotations

from pathlib import Path

from sssf.adw_modules import tracer as tracer_mod
from sssf.adw_modules.data_types import Phase, PhaseParams, SSSFConfig
from sssf.adw_modules.runner import Run


def _run(tmp_path: Path, adw_id: str) -> tuple[Run, tracer_mod.Tracer]:
    cfg = SSSFConfig(
        defaults={"model": "litellm/x", "data_dir": str(tmp_path / "data")},
        observability={"db": str(tmp_path / "data" / "sssf.db")},
    )
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    return Run(cfg, adw_id, t, "Tester"), t


def test_phase_mark_not_passed_sets_status_and_event(tmp_path):
    run, t = _run(tmp_path, "adw_np")
    with run.phase(
        PhaseParams(name="verify", kind="code", owner="quality", description="run gates")
    ) as ph:
        ph.mark_not_passed("2 of 4 checks failed")
    assert run.phases[-1].status == "not_passed"
    payload = t.conn.execute(
        "SELECT payload_json FROM events WHERE adw_id='adw_np' AND type='phase_end'"
    ).fetchone()[0]
    assert '"not_passed"' in payload
    assert "2 of 4 checks failed" in payload


def test_phase_without_mark_ends_success(tmp_path):
    run, _t = _run(tmp_path, "adw_ok")
    with run.phase(PhaseParams(name="verify", kind="code", owner="quality", description="d")):
        pass
    assert run.phases[-1].status == "success"


def test_finish_accepts_runs_with_not_passed_phases(tmp_path):
    """A not_passed phase is visual only — an accepted run whose earlier
    check-failures were fixed in the repair loop still finishes 0."""
    run, t = _run(tmp_path, "adw_acc")
    t.session_start("adw_acc", "Tester", "adw_simple_sdlc")
    run.phases = [
        Phase(
            phase_id="adw_acc_01_verify",
            adw_id="adw_acc",
            seq=1,
            params=PhaseParams(name="verify", kind="code", owner="quality", description="d"),
            status="not_passed",
            error="1 of 2 checks failed",
        )
    ]
    assert run.finish(accepted=True) == 0
    status = t.conn.execute("SELECT status FROM sessions WHERE adw_id='adw_acc'").fetchone()[0]
    assert status == "success"


def test_finish_rejects_real_failure(tmp_path):
    """A genuinely failed phase still fails the run — not_passed is not a
    smoke-screen for real errors."""
    run, _t = _run(tmp_path, "adw_rej")
    run.phases = [
        Phase(
            phase_id="adw_rej_01_build",
            adw_id="adw_rej",
            seq=1,
            params=PhaseParams(name="build", kind="agent", owner="builder", description="d"),
            status="fail",
        )
    ]
    assert run.finish(accepted=True) == 1
