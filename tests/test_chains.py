"""Shared chain-builder (chains.py): the executor mechanics — envelope
chaining, the quality loop's break-on-pass and break-on-env-failure, commit.
The ADW templates declare chains; this pins the executor (audit C4, B5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sssf.adw_modules import chains
from sssf.adw_modules.chains import AgentPhase, Chain, CodePhase, CommitPhase, QualityLoop
from sssf.adw_modules.data_types import (
    EnvelopeBase,
    GenericOutput,
    PhaseParams,
    QualityCheckSpec,
    QualityConfig,
    SSSFConfig,
)
from sssf.adw_modules.tracer import Tracer


class _Console:
    def note(self, *a, **k):
        pass


class _FakePhase:
    def __init__(self, params: PhaseParams):
        self.params = params
        self.output: EnvelopeBase | None = None
        self.seq = 0
        self.phase_id = f"ph_{params.name}"
        self.adw_id = "adw_chain"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def log(self, **kw):
        pass

    def mark_not_passed(self, reason: str = ""):
        self.status = "not_passed"
        self.error = reason

    def call(self, call):
        env = GenericOutput(status="success", summary=f"{self.params.name} done")
        self.output = env
        return env


class _ChainRun:
    """Minimal run surface chains.py touches, with fake agent calls."""

    def __init__(self, repo_root: Path, cfg: SSSFConfig):
        self.cfg = cfg
        self.adw_id = "adw_chain"
        self.repo_root = repo_root
        self.engineer = "Tester"
        self.tracer = Tracer(repo_root / "sssf.db", repo_root / "events.jsonl")
        self.console = _Console()
        self.context_handoff_dir = repo_root / "context_handoff"
        self.phases: list = []
        self.accepted = None
        self.reason = None

    def phase(self, params: PhaseParams):
        ph = _FakePhase(params)
        ph.seq = len(self.phases) + 1
        self.phases.append(ph)
        return ph

    def finish(self, *, accepted: bool = True, reason: str = "") -> int:
        self.accepted = accepted
        self.reason = reason
        return 0 if accepted else 1


def _make_run(tmp: Path, checks=None) -> _ChainRun:
    cfg = SSSFConfig(quality=QualityConfig(checks=checks or []))
    return _ChainRun(tmp, cfg)


def test_agent_phases_chain_envelopes(tmp_path):
    run = _make_run(
        tmp_path,
        checks=[QualityCheckSpec(name="test", area="backend", operation="build", argv=["true"])],
    )
    calls: list[str] = []

    def record_calls(name: str):
        def fn(run_, phase, previous):
            calls.append((name, previous.summary if previous else None))

        return fn

    chain = Chain(
        name="plan_build",
        required_agents=["planner", "builder"],
        phases=[
            AgentPhase("plan", "planner", GenericOutput, description="p"),
            AgentPhase("build", "builder", GenericOutput, description="b"),
            CodePhase("note", "quality", record_calls("note"), description="n"),
            QualityLoop(),
            CommitPhase(),
        ],
    )
    import sssf.adw_modules.git_helper as gh

    gh_orig = gh.commit_all
    gh.commit_all = lambda message: "abc123"  # type: ignore[assignment]
    try:
        assert chains.run_chain(run.cfg, run, "do it", chain) == 0
    finally:
        gh.commit_all = gh_orig  # type: ignore[assignment]
    # the note code phase received the LAST produced envelope (build)
    assert calls == [("note", "build done")]
    assert run.accepted is True
    assert [p.params.name for p in run.phases[:4]] == ["request", "plan", "build", "note"]


@pytest.fixture
def fake_commit(monkeypatch):
    import sssf.adw_modules.git_helper as gh

    monkeypatch.setattr(gh, "commit_all", lambda message: "abc123")


def test_quality_loop_breaks_on_pass(tmp_path, fake_commit):
    run = _make_run(
        tmp_path,
        checks=[QualityCheckSpec(name="test", area="backend", operation="build", argv=["true"])],
    )
    chain = Chain(
        name="bt",
        phases=[
            AgentPhase("build", "builder", GenericOutput, description="b"),
            QualityLoop(),
            CommitPhase(),
        ],
    )
    assert chains.run_chain(run.cfg, run, "x", chain) == 0
    # only verify_1 ran — no fix phase, no env failure
    names = [p.params.name for p in run.phases]
    assert "verify_1" in names and not any(n.startswith("fix_") for n in names)
    assert run.accepted is True
    # all checks passed — no not_passed flag anywhere
    assert not any(getattr(p, "status", None) == "not_passed" for p in run.phases)


def test_quality_loop_breaks_on_env_failure(tmp_path, fake_commit):
    """An env failure never reaches the builder (audit B5 in the executor)."""
    run = _make_run(
        tmp_path,
        checks=[
            QualityCheckSpec(
                name="test", area="backend", operation="build", argv=["definitely-not-a-binary"]
            )
        ],
    )
    chain = Chain(
        name="bt",
        phases=[
            AgentPhase("build", "builder", GenericOutput, description="b"),
            QualityLoop(),
        ],
    )
    assert chains.run_chain(run.cfg, run, "x", chain) == 1
    names = [p.params.name for p in run.phases]
    assert "verify_1" in names
    assert not any(n.startswith("fix_") for n in names)
    assert run.accepted is False
    assert "environment error" in run.reason


def test_quality_loop_exhausts_then_fails(tmp_path, fake_commit):
    """A plain code failure burns the loop, then fails with the standard reason."""
    run = _make_run(
        tmp_path,
        checks=[
            QualityCheckSpec(
                name="test",
                area="backend",
                operation="build",
                argv=["python3", "-c", "import sys; sys.exit(3)"],
            )
        ],
    )
    chain = Chain(
        name="bt",
        phases=[
            AgentPhase("build", "builder", GenericOutput, description="b"),
            QualityLoop(),
        ],
    )
    assert chains.run_chain(run.cfg, run, "x", chain) == 1
    names = [p.params.name for p in run.phases]
    # MAX_FIX_LOOPS verifies, but the loop breaks before the final fix
    assert sum(1 for n in names if n.startswith("fix_")) == chains.MAX_FIX_LOOPS - 1
    assert sum(1 for n in names if n.startswith("verify_")) == chains.MAX_FIX_LOOPS
    assert run.accepted is False
    assert "never came back clean" in run.reason
    # every red verify phase is marked not_passed — the trace never claims success
    not_passed = [p for p in run.phases if getattr(p, "status", None) == "not_passed"]
    assert len(not_passed) == chains.MAX_FIX_LOOPS
