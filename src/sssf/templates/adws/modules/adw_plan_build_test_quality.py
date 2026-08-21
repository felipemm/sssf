#!/usr/bin/env -S uv run
"""ADW Plan Build Test Quality — full agent chain plus deterministic quality.

Usage:
    uv run adws/adw_plan_build_test_quality.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> planner -> builder -> [code(verify) -> code(test) -> builder(fix)] bounded -> git(commit)"""

import argparse
import sys

from sssf.adw_modules import agents, chains, gates, session, utils
from sssf.adw_modules.chains import (
    AgentPhase,
    Chain,
    CommitPhase,
    QualityLoop,
)
from sssf.adw_modules.data_types import BuildOutput, PlanOutput

CHAIN = Chain(
    name="plan_build_test_quality",
    required_agents=["planner", "builder"],
    phases=[
        AgentPhase(
            "plan",
            "planner",
            PlanOutput,
            description="Turn the request into an implementable plan",
            gates=[gates.artifacts_exist, gates.files_non_empty],
        ),
        AgentPhase(
            "build",
            "builder",
            BuildOutput,
            description="Implement the plan exactly",
            gates=[gates.diff_matches_claims],
        ),
        QualityLoop(),
        CommitPhase(),
    ],
)


def main(prompt: str, config: str | None = None, adw_id: str | None = None) -> int:
    cfg = agents.load_config(config or agents.default_config_path())
    agents.validate(cfg, CHAIN.required_agents)
    run = session.ensure(cfg, adw_id)
    return chains.run_chain(cfg, run, prompt, CHAIN)


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
