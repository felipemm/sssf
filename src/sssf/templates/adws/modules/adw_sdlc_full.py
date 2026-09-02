#!/usr/bin/env -S uv run
"""ADW Full SDLC — the simple SDLC chain with the impeccable design pass
inserted: plan, build, design, test, review, document, committing as it goes.

Usage:
    uv run adws/adw_sdlc_full.py "<prompt or path/to/prompt.md>" [--config adws/config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> planner -> git(commit_plan) -> builder ->
[documenter(init, PRODUCT.md — once per project)] -> designer(design: the
impeccable audit -> critique -> polish -> optimize pass on the declared design
surface) -> [code(verify) -> builder(fix)] bounded -> [reviewer ->
builder(revise)] bounded -> code(retest) -> git(commit_build) -> code(changes)
-> documenter(document_design, DESIGN.md — when the design world changed) ->
documenter(document) -> git(commit_docs)

Identical to simple_sdlc except: the designer mid-chain edits the design
surface, its work is verified by the quality gates (including the per-project
`design` detect check when configured), and the impeccable design-context
files (PRODUCT.md once, DESIGN.md when the world changed) are produced by the
documenter. A no-op re-run still walks the doc chain.
"""

import argparse
import sys
from pathlib import Path

from sssf.adw_modules import agents, changes, gates, git_helper, quality, session, utils
from sssf.adw_modules.chains import (
    AgentPhase,
    Chain,
    ChainFailure,
    CodePhase,
    CommitPhase,
    QualityLoop,
    ReviewLoop,
    run_chain,
)
from sssf.adw_modules.data_types import (
    BuildOutput,
    ChangeCapture,
    DocumentOutput,
    PlanOutput,
)

REQUIRED_AGENTS = ["planner", "builder", "designer", "reviewer", "documenter"]

DOCUMENT_NOTES = (
    "Read diff_path in full before writing. Document only what the diff shows, "
    "then copy the write-up into adws/kb/ as your task describes."
)

# The impeccable design pass + design-context phases redirect the shared
# roster agents through user_directive (appended to the rendered user prompt):
# the documenter defaults to a kb write-up, and these phases are NOT that.

INIT_DIRECTIVE = (
    "This phase is NOT the kb write-up. Run the impeccable skill's `init` "
    "command (/impeccable init) to capture durable product context: answer its "
    "questions from the request/plan context you were handed (surface type "
    "inferred from the work; buildPath: code — this factory has no image "
    "generation) and write PRODUCT.md at the repo root. It is generated once "
    "per project; the designer reads it as design context."
)

DESIGN_DIRECTIVE = (
    "This phase runs the impeccable design pass on the project's DESIGN "
    "SURFACE. Find the surface in adws/config/sssf.config.yaml: the `design` "
    "quality check's `surface` (or its detect target). If PRODUCT.md exists, "
    "read it first (it is your design context). Run the impeccable skill's "
    "audit -> critique -> polish -> optimize commands against that surface, "
    "and apply every actionable fix each command reports. Edit ONLY the "
    "design surface; never commit — the factory commits. Report every file "
    "you changed in your envelope."
)

DESIGN_DOC_DIRECTIVE = (
    "This phase is NOT the kb write-up. The design world changed or DESIGN.md "
    "is missing: run the impeccable skill's `document` command "
    "(/impeccable document) to write DESIGN.md at the repo root from the "
    "project's design state, and declare it as document_path/artifacts."
)


def _product_md(run) -> Path:
    return run.repo_root / "PRODUCT.md"


def _product_md_missing(run) -> bool:
    return not _product_md(run).exists()


def _design_md(run) -> Path:
    return run.repo_root / "DESIGN.md"


def _design_surface(run) -> str | None:
    """The declared design surface: the design check's `surface` (or its
    detect argv target) from the project config — None when the design gate
    is not configured."""
    cfg = getattr(run, "cfg", None)
    checks = getattr(getattr(cfg, "quality", None), "checks", None) or []
    for check in checks:
        if check.name == "design":
            return check.surface or (check.argv[-1] if check.argv else None)
    return None


def _design_world_changed(run) -> bool:
    """DESIGN.md must exist when the design world changed — first run always
    establishes it; later runs only when the run's committed diff touched
    files under the design surface."""
    if not _design_md(run).exists():
        return True
    surface = _design_surface(run)
    if not surface:
        return False
    prefix = surface.rstrip("/")
    changed = git_helper.diff_files_between(run._sdlc_baseline)
    return any(f == prefix or f.startswith(prefix + "/") for f in changed)


def _commit(run, ph, envelope, *, allow_empty=False) -> bool:
    """Commit what the preceding phase produced, in that agent's own words."""
    message = envelope.commit_message or f"sssf({run.adw_id}): {envelope.summary}"
    sha = git_helper.commit_all(message, allow_empty=allow_empty)
    if sha is None:
        return False
    ph.log(sha=sha, message=message)
    return True


def _record(ph, result) -> None:
    passed = sum(1 for check in result.checks if check.passed)
    ph.log(
        passed=result.passed,
        checks=f"{passed}/{len(result.checks)}",
        artifacts=", ".join(result.artifacts),
    )


def _commit_plan(run, ph, previous):
    """Put the spec on record before any code exists to blur it."""
    _commit(run, ph, previous)
    run._sdlc_plan_sha = git_helper.rev("HEAD")
    return previous


def _retest(run, ph, previous):
    """A revision edited code after the suite last ran — the green light is
    stale. Re-run the gates; an env failure fails the run cleanly."""
    if not any(p.params.name.startswith("revise_") for p in run.phases):
        return None
    if not getattr(run, "_review_approved", False):
        return None
    result = quality.run_quality(run)
    _record(ph, result)
    run._quality_result = result
    if not result.passed:
        reason = quality.env_failure(result) or "quality failed after revision"
        raise ChainFailure(reason)
    return None


def _commit_build(run, ph, previous):
    """Land the code only now: green suite, approved review. Nothing to commit
    is the no-op re-run — blessed only when the builder changed nothing."""
    result = getattr(run, "_quality_result", None)
    verified = result is not None and result.passed and getattr(run, "_review_approved", False)
    if not verified:
        raise ChainFailure(
            "the suite or the review never came back clean — the code stays uncommitted"
        )
    landed = _commit(run, ph, previous, allow_empty=True)
    if landed:
        return None
    if previous.changed_files:
        head_now = git_helper.rev("HEAD")
        if head_now != run._sdlc_plan_sha:
            raise RuntimeError(
                "the builder committed its own work — the factory owns commits. "
                f"HEAD moved {run._sdlc_plan_sha[:7]} -> {head_now[:7]} before "
                "commit_build, so code landed before review."
            )
        raise RuntimeError(
            "builder reported changed files, but the working tree has no diff — "
            "the changes never landed, nothing to commit"
        )
    ph.log(
        note="nothing to commit — the plan's work is already implemented and "
        "verified (no-op re-run)"
    )
    run._sdlc_no_op = True
    return None


def _capture_changes(run, ph, previous):
    changeset = changes.capture(run, ChangeCapture(base=run._sdlc_baseline))
    ph.log(
        base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
        reason=changeset.base.reason,
        files=len(changeset.files) + len(changeset.untracked),
        lines=f"+{changeset.insertions} -{changeset.deletions}",
        diff=changeset.diff_path,
    )
    if changeset.empty:
        raise RuntimeError(
            f"nothing changed since {changeset.base.label} "
            f"({changeset.base.reason}) — there is nothing to document."
        )
    return changes.as_envelope(changeset, DOCUMENT_NOTES)


def _no_op_doc_exists(run) -> bool:
    return getattr(run, "_sdlc_no_op", False) and any(
        (run.repo_root / "adws" / "kb").glob(f"{run.adw_id}_*.md")
    )


def _confirm_doc(run, ph, previous):
    ph.log(note="documentation already exists — success run, no updated doc")


CHAIN = Chain(
    name="sdlc_full",
    required_agents=REQUIRED_AGENTS,
    phases=[
        AgentPhase(
            "plan",
            "planner",
            PlanOutput,
            description="Turn the request into an implementable plan",
            gates=[gates.artifacts_exist, gates.files_non_empty],
        ),
        CodePhase(
            "commit_plan",
            "git",
            _commit_plan,
            description="Put the spec on record before any code exists to blur it",
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
            description="Capture PRODUCT.md via /impeccable init — once per project",
            gates=[gates.artifacts_exist, gates.files_non_empty],
            previous="plan",
            when=_product_md_missing,
            user_directive=INIT_DIRECTIVE,
        ),
        AgentPhase(
            "design",
            "designer",
            BuildOutput,
            retries=1,
            description="Impeccable design pass (audit -> critique -> polish -> optimize) on the declared surface",
            gates=[gates.diff_matches_claims],
            previous="build",
            user_directive=DESIGN_DIRECTIVE,
        ),
        QualityLoop(),
        ReviewLoop(),
        CodePhase(
            "retest",
            "quality",
            _retest,
            description="Re-run the gates — a revision changed code after the last green result",
        ),
        CodePhase(
            "commit_build",
            "git",
            _commit_build,
            description="Land the code only now: green suite, approved review",
        ),
        CodePhase(
            "changes",
            "git",
            _capture_changes,
            description="Diff the whole run against its pinned baseline, for the documenter",
        ),
        AgentPhase(
            "document_design",
            "documenter",
            DocumentOutput,
            retries=1,
            description="impeccable document -> DESIGN.md when the design world changed",
            gates=[gates.artifacts_exist, gates.files_non_empty],
            previous="changes",
            when=_design_world_changed,
            user_directive=DESIGN_DOC_DIRECTIVE,
        ),
        CodePhase(
            "document",
            "git",
            _confirm_doc,
            description="Confirm the write-up exists — a no-op re-run ships no updated doc",
            when=_no_op_doc_exists,
        ),
        AgentPhase(
            "document",
            "documenter",
            DocumentOutput,
            retries=1,
            description="Write up the completed change",
            gates=[gates.artifacts_exist, gates.files_non_empty],
            previous="changes",
            when=lambda run: not _no_op_doc_exists(run),
        ),
        CommitPhase(
            name="commit_docs",
            description="Ship the write-up in its own commit, beside the code it describes",
        ),
    ],
)


def main(prompt: str, config: str | None = None, adw_id: str | None = None) -> int:
    cfg = agents.load_config(config or agents.default_config_path())
    # Create the session BEFORE validating: a validation failure (e.g. pi
    # --list-models hiccuping under concurrent container boots) then leaves a
    # visible failed session instead of nothing.
    run = session.ensure(cfg, adw_id)
    agents.validate(cfg, REQUIRED_AGENTS)
    run._sdlc_baseline = git_helper.rev("HEAD")  # pinned before this run commits anything
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
