#!/usr/bin/env -S uv run
"""ADW Implement — the implement flow chain: triage → build → review.

Usage:
    uv run adws/adw_implement.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> scout(triage) -> builder(build) ->
[quality verify/fix] bounded -> builder(self_review) ->
[reviewer -> builder(revise)] bounded -> git(commit)

The builder self-reviews its own diff (code-review pass) before the reviewer
gate, so obvious defects are caught before the reviewer's time. Code lands only
on a green suite and an approved review. The ticket machine is driven from the
HOST (the tickets table is project-owned): `sssf flow implement` claims the
ticket (ready-for-agent → in-progress) before the run spawns, and the run's
outcome settles it — success → ready-for-signoff, failure → back to
ready-for-agent with the run's feedback attached (fix-forward) — in the
monitor (sandboxed) or in the flow command (--no-sandbox) per #92.
"""

import argparse
import sys

from sssf.adw_modules import agents, gates, session, utils
from sssf.adw_modules.chains import (
    AgentPhase,
    Chain,
    CommitPhase,
    QualityLoop,
    ReviewLoop,
    run_chain,
)
from sssf.adw_modules.data_types import BuildOutput, ScoutOutput

CHAIN = Chain(
    name="implement",
    required_agents=["builder", "reviewer", "scout"],
    phases=[
        AgentPhase(
            "triage",
            "scout",
            ScoutOutput,
            description="Triage the ticket — confirm it is implementable, flag gaps",
            gates=[gates.artifacts_exist],
            user_directive=(
                "You are the triage step of an implement flow. Confirm the ticket is "
                "implementable as written: the ask, the seams, and the acceptance "
                "criteria are clear. Report gaps as findings; change no code."
            ),
        ),
        AgentPhase(
            "build",
            "builder",
            BuildOutput,
            description="Implement the ticket exactly",
            gates=[gates.diff_matches_claims],
        ),
        QualityLoop(name="quality"),
        AgentPhase(
            "self_review",
            "builder",
            BuildOutput,
            description="Builder self-review — catch defects before the reviewer gate",
            gates=[gates.diff_matches_claims],
            user_directive=(
                "Self-review your own diff before the reviewer sees it: re-read every "
                "changed file against the ticket's acceptance criteria, run the "
                "code-review pass, and fix everything you confirm. Report what you "
                "found and fixed. Your diff must be committed work — amend or add a "
                "commit so HEAD carries the reviewed state."
            ),
        ),
        ReviewLoop(name="review"),
        CommitPhase(
            description="Commit the reviewed implementation",
            allow_empty=True,
        ),
    ],
)


def main(prompt: str, config: str | None = None, adw_id: str | None = None) -> int:
    cfg = agents.load_config(config or agents.default_config_path())
    run = session.ensure(cfg, adw_id)
    agents.validate(cfg, CHAIN.required_agents)
    return run_chain(cfg, run, prompt, CHAIN)


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
