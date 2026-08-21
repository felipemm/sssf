#!/usr/bin/env -S uv run
"""ADW Design SDLC — plan, build, impeccable design pass, verify, document.

Usage:
    uv run adws/adw_design_sdlc.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> planner -> builder -> documenter(init) ->
designer(design) -> [code(verify) -> builder(fix)] bounded -> documenter(document)
-> git(commit)

The design pass is agentic and bounded: the designer runs /impeccable audit →
critique → polish → optimize on the design surface, and its work is then
verified by the deterministic quality gates (including the `design` detect
check). PRODUCT.md (via /impeccable init) is the designer's design context;
DESIGN.md (via /impeccable document) ships with the project.
"""

import argparse
import sys

from sssf.adw_modules import agents, chains, gates, session, utils
from sssf.adw_modules.chains import (
    AgentPhase,
    Chain,
    CommitPhase,
    QualityLoop,
)
from sssf.adw_modules.data_types import BuildOutput, DocumentOutput, PlanOutput

DESIGN_SURFACE = "site/"

CHAIN = Chain(
    name="design_sdlc",
    required_agents=["planner", "builder", "designer", "documenter"],
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
        AgentPhase(
            "init",
            "documenter",
            DocumentOutput,
            retries=1,
            description="Run /impeccable init to generate PRODUCT.md — the designer's design context",
            gates=[gates.artifacts_exist, gates.files_non_empty],
            previous="plan",
        ),
        AgentPhase(
            "design",
            "designer",
            BuildOutput,
            retries=1,
            description=f"Impeccable design pass (audit → critique → polish → optimize) on {DESIGN_SURFACE}",
            gates=[gates.diff_matches_claims],
            previous="build",
        ),
        QualityLoop(),
        AgentPhase(
            "document",
            "documenter",
            DocumentOutput,
            retries=1,
            description="Run /impeccable document to generate DESIGN.md from the built project",
            gates=[gates.artifacts_exist, gates.files_non_empty],
            previous="init",
        ),
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
