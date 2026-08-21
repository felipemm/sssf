#!/usr/bin/env -S uv run
"""ADW Document — write up the work that was just done, from the diff.

Usage:
    uv run adws/adw_document.py "<prompt or path/to/prompt.md>" [--base main] [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(changes) -> documenter -> git(commit_docs)

This runs AFTER a build, and the guard is structural rather than advisory: the
change capture is a code phase, and an empty diff raises there — before the
documenter is ever spawned. There is nothing to document until something was
built, and the phase says so instead of paying an agent to discover it.
"""

import argparse
import sys

from sssf.adw_modules import agents, chains, changes, gates, session, utils
from sssf.adw_modules.chains import AgentPhase, Chain, CodePhase, CommitPhase
from sssf.adw_modules.data_types import ChangeCapture, DocumentOutput

REQUIRED_AGENTS = ["documenter"]

DOCUMENT_NOTES = (
    "Read diff_path in full before writing. Document only what the "
    "diff shows, then copy the write-up into adws/kb/ as your task describes."
)


def _capture_changes(base: str):
    def fn(run, phase, previous=None):
        changeset = changes.capture(run, ChangeCapture(base=base))
        phase.log(
            base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
            reason=changeset.base.reason,
            files=len(changeset.files) + len(changeset.untracked),
            lines=f"+{changeset.insertions} -{changeset.deletions}",
            diff=changeset.diff_path,
        )
        if changeset.empty:
            raise RuntimeError(
                f"nothing changed since {changeset.base.label} ({changeset.base.reason}) "
                f"— documenting runs after a build. Build something first, or point "
                f"--base at the ref the work should be measured from."
            )
        return changes.as_envelope(changeset, DOCUMENT_NOTES)

    return fn


def _make_chain(base: str) -> Chain:
    return Chain(
        name="document",
        required_agents=REQUIRED_AGENTS,
        phases=[
            CodePhase(
                "changes",
                "git",
                _capture_changes(base),
                description=f"Diff the working tree against {base} — the change to be written up",
            ),
            AgentPhase(
                "document",
                "documenter",
                DocumentOutput,
                retries=1,
                description="Turn the captured diff into a write-up an engineer can read",
                gates=[gates.artifacts_exist, gates.files_non_empty],
            ),
            CommitPhase(
                name="commit_docs",
                description="Ship the write-up in its own commit, beside the code it describes",
            ),
        ],
    )


def main(
    prompt: str, base: str = "main", config: str | None = None, adw_id: str | None = None
) -> int:
    cfg = agents.load_config(config or agents.default_config_path())
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)
    return chains.run_chain(cfg, run, prompt, _make_chain(base))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--base", default="main", help="ref the change is measured against")
    parser.add_argument(
        "--config",
        default=None,
        help="path to sssf.config.yaml (default: adws/config/sssf.config.yaml)",
    )
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.base, args.config, args.adw_id))
