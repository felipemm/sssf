#!/usr/bin/env -S uv run
"""ADW Plan — one-shot planning workflow.

Usage:
    uv run adws/adw_plan.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> planner
"""

import argparse
import sys
from pathlib import Path

from sssf.adw_modules import agents, gates, session, utils
from sssf.adw_modules.data_types import AgentCall, PhaseParams, PlanOutput

REQUIRED_AGENTS = ["planner"]


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
        ph.call(
            AgentCall(
                output_type=PlanOutput,
                prompt=prompt,
                gates=[gates.artifacts_exist, gates.files_non_empty],
            )
        )

    return run.finish()


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
