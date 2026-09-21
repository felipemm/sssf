#!/usr/bin/env -S uv run
"""ADW Plan — the plan flow chain: exploration → grill-with-docs → to-spec → to-tickets.

Usage:
    uv run adws/adw_plan.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4] [--skip-exploration]

Phases: engineer(request) -> scout(explore) -> planner(grill) -> planner(spec)
-> planner(tickets) -> git(commit)

The exploration pass is skippable (`--skip-exploration`) when the problem is
already well understood; the plan steps each run as their own agent phase (the
fresh-session mechanism per step is #91's refinement). The db-level transform
(idea ticket → spec + implementation children) lands with #91 — this chain
produces the plan artifacts end-to-end.
"""

import argparse
import sys

from sssf.adw_modules import agents, chains, gates, session, utils
from sssf.adw_modules.chains import (
    AgentPhase,
    Chain,
    CommitPhase,
)
from sssf.adw_modules.data_types import PlanOutput, ScoutOutput

CHAIN = Chain(
    name="plan",
    required_agents=["planner", "scout"],
    phases=[
        AgentPhase(
            "explore",
            "scout",
            ScoutOutput,
            description="Explore the problem space and land a brief — skippable",
            gates=[gates.artifacts_exist],
            when=lambda run: not getattr(run, "_skip_exploration", False),
        ),
        AgentPhase(
            "grill",
            "planner",
            PlanOutput,
            description="Grill the proposal against the project's ADRs and CONTEXT.md",
            user_directive=(
                "You are the grill step of a plan flow. Read docs/adr/ and CONTEXT.md "
                "in full and stress-test the ask against them: challenge the design, "
                "the state model, and the seam choices. Resolve every open question "
                "in the plan you land; write the plan under adws/specs/ as "
                "adws/specs/<adw_id>_grill-<slug>.md and declare it in artifacts."
            ),
            gates=[gates.artifacts_exist, gates.files_non_empty],
        ),
        AgentPhase(
            "spec",
            "planner",
            PlanOutput,
            description="Turn the grilled proposal into the spec (to-spec)",
            user_directive=(
                "You are the to-spec step of a plan flow. Write the final spec under "
                "adws/specs/ as adws/specs/<adw_id>_spec-<slug>.md (list the directory "
                "first; never overwrite an existing spec — pick a free name). The spec "
                "must be implementable without further questions: goal, scope, seams, "
                "acceptance criteria, and the files it touches. Declare the spec path "
                "in artifacts."
            ),
            gates=[gates.artifacts_exist, gates.files_non_empty],
        ),
        AgentPhase(
            "tickets",
            "planner",
            PlanOutput,
            description="Slice the spec into implementation tickets (to-tickets)",
            user_directive=(
                "You are the to-tickets step of a plan flow. Slice the spec into "
                "implementation tickets — small, independently implementable units, "
                "each with its own acceptance criteria. Write the breakdown under "
                "adws/specs/ as adws/specs/<adw_id>_tickets-<slug>.md and declare it "
                "in artifacts. (The db-level creation of the child tickets is a later "
                "flow step — this chain records the breakdown.)"
            ),
            gates=[gates.artifacts_exist, gates.files_non_empty],
        ),
        CommitPhase(
            description="Commit the plan artifacts (brief / grill / spec / tickets)",
            allow_empty=True,
        ),
    ],
)


def main(prompt: str, config: str | None = None, adw_id: str | None = None,
         skip_exploration: bool = False) -> int:
    cfg = agents.load_config(config or agents.default_config_path())
    run = session.ensure(cfg, adw_id)
    agents.validate(cfg, CHAIN.required_agents)
    run._skip_exploration = skip_exploration
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
    parser.add_argument(
        "--skip-exploration",
        action="store_true",
        help="skip the scout exploration pass — the problem is already well understood",
    )
    args = parser.parse_args()
    sys.exit(
        main(
            utils.resolve_prompt(args.prompt),
            args.config,
            args.adw_id,
            skip_exploration=args.skip_exploration,
        )
    )
