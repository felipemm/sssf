#!/usr/bin/env -S uv run

"""ADW Plan Build Test Quality + Design — full agent chain with the
impeccable design pass and deterministic quality gates.

Usage:
    uv run adws/adw_design_sdlc.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> planner -> builder -> documenter(init) ->
designer(design) -> [code(verify) -> builder(fix)] bounded -> documenter(document)
-> git(commit)

The design pass is agentic and bounded: the designer runs /impeccable audit →
critique → polish → optimize on the design surface, and its work is then
verified by the deterministic quality gates (including the `design` detect
check). PRODUCT.md (via /impeccable init) is the designer's design context;
DESIGN.md (via /impeccable document) ships with the project. A failing gate
does not fail its phase — the failure becomes an envelope and flows back into
the builder, and only an exhausted repair loop fails the run.
"""

import argparse
import sys
from pathlib import Path

from sssf.adw_modules import agents, gates, git_helper, quality, session, utils
from sssf.adw_modules.data_types import (
    AgentCall,
    BuildOutput,
    DocumentOutput,
    PhaseParams,
    PlanOutput,
)

REQUIRED_AGENTS = ["planner", "builder", "designer", "documenter"]
MAX_FIX_LOOPS = 3

DESIGN_SURFACE = "site/"  # the designer edits this; the `design` gate checks site/dist


def main(prompt: str, config: str | None = None, adw_id: str | None = None) -> int:
    from sssf.adw_modules import paths

    cfg = agents.load_config(config or str(paths.config_file(Path.cwd())))
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    with run.phase(
        PhaseParams(
            name="request",
            kind="engineer",
            owner=run.engineer,
            description="Capture the incoming ask",
        )
    ) as ph:
        ph.log(input=prompt)

    with run.phase(
        PhaseParams(
            name="plan",
            kind="agent",
            owner="planner",
            description="Turn the request into an implementable plan",
        )
    ) as ph:
        plan = ph.call(
            AgentCall(
                output_type=PlanOutput,
                prompt=prompt,
                gates=[gates.artifacts_exist, gates.files_non_empty],
            )
        )

    with run.phase(
        PhaseParams(
            name="build", kind="agent", owner="builder", description="Implement the plan exactly"
        )
    ) as ph:
        build_out = ph.call(
            AgentCall(
                output_type=BuildOutput,
                prompt=prompt,
                previous=plan,
                gates=[gates.diff_matches_claims],
            )
        )

    with run.phase(
        PhaseParams(
            name="init",
            kind="agent",
            owner="documenter",
            retries=1,
            description="Run /impeccable init to generate PRODUCT.md — the designer's design context",
        )
    ) as ph:
        init_out = ph.call(
            AgentCall(
                output_type=DocumentOutput,
                prompt=prompt,
                previous=plan,
                gates=[gates.artifacts_exist, gates.files_non_empty],
            )
        )

    with run.phase(
        PhaseParams(
            name="design",
            kind="agent",
            owner="designer",
            retries=1,
            description=f"Impeccable design pass (audit → critique → polish → optimize) on {DESIGN_SURFACE}",
        )
    ) as ph:
        ph.call(
            AgentCall(
                output_type=BuildOutput,
                prompt=prompt,
                previous=build_out,
                gates=[gates.diff_matches_claims],
            )
        )

    def record(ph, result) -> None:
        passed = sum(1 for check in result.checks if check.passed)
        ph.log(
            passed=result.passed,
            checks=f"{passed}/{len(result.checks)}",
            artifacts=", ".join(result.artifacts),
        )

    quality_result = None
    env_reason = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(
            PhaseParams(
                name=f"verify_{i}",
                kind="code",
                owner="quality",
                description="Run every quality gate — tests, typecheck, build, design, snyk",
            )
        ) as ph:
            quality_result = quality.run_quality(run)
            record(ph, quality_result)

        if quality_result.passed:
            break
        if i == MAX_FIX_LOOPS:
            break
        env_reason = quality.env_failure(quality_result)
        if env_reason:
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
            build_out = ph.call(
                AgentCall(
                    output_type=BuildOutput,
                    prompt=prompt,
                    previous=quality.as_envelope(quality_result, "quality gates"),
                    gates=[gates.diff_matches_claims],
                )
            )

    verified = quality_result is not None and quality_result.passed
    if verified:
        with run.phase(
            PhaseParams(
                name="document",
                kind="agent",
                owner="documenter",
                retries=1,
                description="Run /impeccable document to generate DESIGN.md from the built project",
            )
        ) as ph:
            document = ph.call(
                AgentCall(
                    output_type=DocumentOutput,
                    prompt=prompt,
                    previous=init_out,
                    gates=[gates.artifacts_exist, gates.files_non_empty],
                )
            )

        with run.phase(
            PhaseParams(
                name="commit",
                kind="code",
                owner="git",
                description="Commit the designed, tested, and quality-verified working tree",
            )
        ) as ph:
            message = document.commit_message or f"sssf({run.adw_id}): {build_out.summary}"
            ph.log(sha=git_helper.commit_all(message), message=message)

    return run.finish(
        accepted=verified,
        reason=env_reason
        or f"quality gates never came back clean after {MAX_FIX_LOOPS} fix attempt(s)",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument(
        "--config",
        default=None,
        help="path to sssf.config.yaml (default: adws/config/sssf.config.yaml)",
    )
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))
