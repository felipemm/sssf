"""Shared ADW chain-builder (audit C4).

The 13 ADW templates were near-identical ~100-line scripts: request phase,
agent phases with envelope chaining, the quality verify/fix loop, commit. This
module is the single executor; each chain declares its phases and the ADW file
becomes a short config. Bespoke logic (the review loop, changes capture) lives
in code-phase functions.

Semantics preserved from the original templates:
- agent phases chain envelopes (each agent phase's output is the `previous`
  for the next, unless overridden);
- the quality loop breaks on pass AND on an environment failure (issue #16 —
  never hand an env error to the builder);
- commit happens only when the run is verified;
- `run.finish()` reports accepted/reason exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sssf.adw_modules import gates, git_helper, quality
from sssf.adw_modules.data_types import (
    AgentCall,
    BuildOutput,
    EnvelopeBase,
    PhaseParams,
    QualityResult,
)

MAX_FIX_LOOPS = 3
DESIGN_SURFACE = "site/"


class ChainFailure(RuntimeError):
    """Raise from a code phase to end the chain with a clean failed finish."""


# ── phase specs ───────────────────────────────────────────────────────────────


@dataclass
class AgentPhase:
    """An agent phase: ph.call() with an output type, gates, and optional
    retries. `previous` names the phase whose envelope feeds this call
    (default: the previous agent/code phase's output).

    `user_directive` is appended to the agent's rendered user prompt for THIS
    phase only — how a phase redirects a shared roster agent.
    """

    name: str
    owner: str
    output_type: type[EnvelopeBase]
    description: str = ""

    gates: list = field(default_factory=list)
    retries: int = 0
    previous: str | None = None  # phase name whose envelope feeds this call
    when: Callable[[object], bool] | None = None  # run-only condition
    user_directive: str = ""  # appended to the agent's user prompt this phase


@dataclass
class CodePhase:
    """A deterministic code phase. fn(run, phase, previous) runs inside the
    phase; raise to fail the run."""

    name: str
    owner: str
    fn: Callable
    description: str = ""
    when: Callable[[object], bool] | None = None  # run-only condition


@dataclass
class QualityLoop:
    """The verify/fix loop: run_quality, record, break on pass or on an
    environment failure; otherwise dispatch the builder with the envelope."""

    name: str = "verify"
    description: str = "Run every quality gate — known commands, no rediscovery"


@dataclass
class ReviewLoop:
    """The reviewer/revise loop: review until approved (bounded)."""

    max_loops: int = 3
    description: str = "Confirm the build matches the plan"


@dataclass
class CommitPhase:
    """A git commit of the verified working tree."""

    name: str = "commit"
    description: str = "Commit the tested and quality-verified working tree"


@dataclass
class Chain:
    name: str
    phases: list
    required_agents: list[str] = field(default_factory=list)


# ── shared phase functions ─────────────────────────────────────────────────────


def record_quality(phase, result: QualityResult) -> None:
    passed = sum(1 for check in result.checks if check.passed)
    phase.log(
        passed=result.passed,
        checks=f"{passed}/{len(result.checks)}",
        artifacts=", ".join(result.artifacts),
    )


def run_quality_phase(run, phase) -> QualityResult:
    result = quality.run_quality(run)
    record_quality(phase, result)
    if not result.passed:
        # Visual truth, not a hard stop: the phase RAN (kind=code) but its
        # checks came back red — record it as not_passed in the db + events so
        # the trace never claims a failed gate was a success. The loop still
        # hands the builder the envelope to repair.
        phase.mark_not_passed(
            f"{len(result.failures)} of {len(result.checks)} check(s) failed"
        )
    return result


def run_quality_or_raise(run, phase, previous=None) -> None:
    result = run_quality_phase(run, phase)
    if not result.passed:
        raise RuntimeError("quality failed: " + "; ".join(result.failures))


def commit_all(run, phase, message: str) -> None:
    phase.log(sha=git_helper.commit_all(message), message=message)


# ── the executor ───────────────────────────────────────────────────────────────


def run_chain(cfg, run, prompt: str, chain: Chain) -> int:
    """Execute a declared chain. Returns run.finish()'s code (0 accepted)."""
    try:
        return _run_phases(cfg, run, prompt, chain)
    except ChainFailure as failure:
        return run.finish(accepted=False, reason=str(failure))


def _run_phases(cfg, run, prompt: str, chain: Chain) -> int:
    previous: EnvelopeBase | None = None
    outputs: dict[str, EnvelopeBase] = {}

    with run.phase(
        PhaseParams(
            name="request",
            kind="engineer",
            owner=run.engineer,
            description="Capture the incoming ask",
        )
    ) as ph:
        ph.log(input=prompt)

    for spec in chain.phases:
        if isinstance(spec, AgentPhase):
            if spec.when is not None and not spec.when(run):
                continue
            prev = outputs.get(spec.previous, previous) if spec.previous else previous
            with run.phase(
                PhaseParams(
                    name=spec.name,
                    kind="agent",
                    owner=spec.owner,
                    description=spec.description,
                    retries=spec.retries,
                )
            ) as ph:
                previous = ph.call(
                    AgentCall(
                        output_type=spec.output_type,
                        prompt=prompt,
                        previous=prev,
                        gates=spec.gates,
                        user_directive=spec.user_directive,
                    )
                )
            outputs[spec.name] = previous

        elif isinstance(spec, QualityLoop):
            verified, reason, previous = _quality_loop(run, prompt, chain, previous)
            if not verified:
                return run.finish(accepted=False, reason=reason)

        elif isinstance(spec, ReviewLoop):
            verified, previous = _review_loop(run, prompt, spec.max_loops, previous)
            if not verified:
                return run.finish(
                    accepted=False,
                    reason=f"review was not approved after {spec.max_loops} attempt(s)",
                )

        elif isinstance(spec, CommitPhase):
            if previous is None:
                return run.finish(accepted=False, reason="nothing produced before the commit phase")
            with run.phase(
                PhaseParams(name=spec.name, kind="code", owner="git", description=spec.description)
            ) as ph:
                message = getattr(previous, "commit_message", None) or (
                    f"sssf({run.adw_id}): {getattr(previous, 'summary', 'chain')}"
                )
                commit_all(run, ph, message)

        elif isinstance(spec, CodePhase):
            if spec.when is not None and not spec.when(run):
                continue
            with run.phase(
                PhaseParams(
                    name=spec.name, kind="code", owner=spec.owner, description=spec.description
                )
            ) as ph:
                out = spec.fn(run, ph, previous)
                if out is not None:
                    outputs[spec.name] = out
                    previous = out

        else:
            raise TypeError(f"unknown phase spec: {spec!r}")

    return run.finish()


def _review_loop(run, prompt: str, max_loops: int, previous):
    """review_i / revise_i until approved. Returns (approved, latest_build)."""
    from sssf.adw_modules.data_types import ReviewOutput

    latest = previous
    for i in range(1, max_loops + 1):
        with run.phase(
            PhaseParams(
                name=f"review_{i}",
                kind="agent",
                owner="reviewer",
                description="Confirm the build matches the plan",
            )
        ) as ph:
            review = ph.call(
                AgentCall(
                    output_type=ReviewOutput,
                    prompt=prompt,
                    previous=latest,
                    gates=[gates.artifacts_exist, gates.verdict_consistent],
                )
            )
        run._review_approved = review.approved
        if review.approved or i == max_loops:
            break
        with run.phase(
            PhaseParams(
                name=f"revise_{i}",
                kind="agent",
                owner="builder",
                retries=1,
                description="Close the reviewer's blocking findings",
            )
        ) as ph:
            latest = ph.call(
                AgentCall(
                    output_type=BuildOutput,
                    prompt=prompt,
                    previous=review,
                    gates=[gates.diff_matches_claims],
                )
            )
    return review.approved, latest


def _quality_loop(run, prompt: str, chain: Chain, previous):
    """The verify/fix loop. Returns (verified, reason, latest_build_envelope)."""
    env_reason: str | None = None
    latest = previous
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(
            PhaseParams(
                name=f"verify_{i}",
                kind="code",
                owner="quality",
                description="Run every quality gate — known commands, no rediscovery",
            )
        ) as ph:
            result = run_quality_phase(run, ph)

        run._quality_result = result
        if result.passed:
            return True, "", latest

        env_reason = quality.env_failure(result)
        if env_reason:
            return False, env_reason, latest
        if i == MAX_FIX_LOOPS:
            break

        with run.phase(
            PhaseParams(
                name=f"fix_{i}",
                kind="agent",
                owner="builder",
                retries=1,
                description="Resolve the reported gate failures",
            )
        ) as ph:
            latest = ph.call(
                AgentCall(
                    output_type=BuildOutput,
                    prompt=prompt,
                    previous=quality.as_envelope(result, "quality gates"),
                    gates=[gates.diff_matches_claims],
                )
            )

    return (
        False,
        env_reason or f"quality gates never came back clean after {MAX_FIX_LOOPS} fix attempt(s)",
        latest,
    )
