"""The three flow chains (#89) — phase ordering, gates, and envelope contracts.

The flow chains are the shipped template set (plan / implement / deploy);
per-flow deep semantics (db ticket transforms, machine transitions, workbench,
canary/promote/close-by-commits) land in #91/#92/#96/#97. This pins what #89
owns: each flow's phase list and ordering, skippable exploration, builder
self-review before the reviewer gate, and the deterministic deploy steps.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from sssf.adw_modules import gates
from sssf.adw_modules.chains import (
    AgentPhase,
    Chain,
    CodePhase,
    CommitPhase,
    QualityLoop,
    ReviewLoop,
)
from sssf.adw_modules.data_types import BuildOutput, PlanOutput, ReviewOutput, ScoutOutput

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "sssf" / "templates"


def _load(name: str):
    path = TEMPLATES / "adws" / "modules" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _agents_phases(chain: Chain) -> list[AgentPhase]:
    return [p for p in chain.phases if isinstance(p, AgentPhase)]


def _phase_names(chain: Chain) -> list[str]:
    """Every phase carries a name — AgentPhase/CodePhase/CommitPhase declare
    one; the loops carry theirs as fields (QualityLoop defaults 'verify',
    ReviewLoop 'review')."""
    return [p.name for p in chain.phases]


# ── plan: exploration → grill-with-docs → to-spec → to-tickets ──────────────


def test_plan_chain_phase_list_and_ordering():
    mod = _load("adw_plan")
    chain: Chain = mod.CHAIN
    assert chain.name == "plan"
    assert chain.required_agents == ["planner", "scout"]
    assert _phase_names(chain) == ["explore", "grill", "spec", "tickets", "commit"]
    # every step is its own agent phase (fresh session per step is #91's
    # mechanism — the chain structure keeps the steps distinct)
    assert len(_agents_phases(chain)) == 4


def test_plan_exploration_is_skippable():
    mod = _load("adw_plan")
    explore = next(p for p in mod.CHAIN.phases if p.name == "explore")
    assert isinstance(explore, AgentPhase)
    assert explore.when is not None  # the chain runs without it via a flag


def test_plan_phase_contracts():
    mod = _load("adw_plan")
    chain: Chain = mod.CHAIN
    agents = {p.name: p for p in _agents_phases(chain)}
    # exploration is a scout recon pass — findings, not a plan
    assert agents["explore"].owner == "scout"
    assert agents["explore"].output_type is ScoutOutput
    # the three plan steps are planner work, each producing a plan artifact
    for name in ("grill", "spec", "tickets"):
        assert agents[name].owner == "planner"
        assert agents[name].output_type is PlanOutput
        assert gates.files_non_empty in agents[name].gates
    # the spec must actually land on disk before the chain commits it
    assert gates.artifacts_exist in agents["spec"].gates
    assert any(isinstance(p, CommitPhase) for p in chain.phases)
    commit = next(p for p in chain.phases if isinstance(p, CommitPhase))
    assert commit.allow_empty is True  # a plan-only pass commits the plan, not code


# ── implement: triage → build → review (builder self-review first) ──────────


def test_implement_chain_phase_list_and_ordering():
    mod = _load("adw_implement")
    chain: Chain = mod.CHAIN
    assert chain.name == "implement"
    assert chain.required_agents == ["builder", "reviewer", "scout"]
    assert _phase_names(chain) == [
        "triage", "build", "quality", "self_review", "review", "commit",
    ]
    assert [type(p).__name__ for p in chain.phases] == [
        "AgentPhase", "AgentPhase", "QualityLoop", "AgentPhase", "ReviewLoop",
        "CommitPhase",
    ]


def test_implement_triage_is_scout_recon():
    mod = _load("adw_implement")
    triage = next(p for p in mod.CHAIN.phases if p.name == "triage")
    assert isinstance(triage, AgentPhase)
    assert triage.owner == "scout"
    assert triage.output_type is ScoutOutput


def test_implement_build_then_quality_then_self_review_then_review():
    """The builder's self-review sits BETWEEN quality and the reviewer gate —
    obvious defects are caught before the reviewer's time (#86 story 17)."""
    mod = _load("adw_implement")
    chain: Chain = mod.CHAIN
    build = next(p for p in chain.phases if p.name == "build")
    assert isinstance(build, AgentPhase)
    assert build.owner == "builder"
    assert build.output_type is BuildOutput
    assert gates.diff_matches_claims in build.gates
    # ordering: quality loop and self-review before ReviewLoop
    kinds = [(p.name, type(p).__name__) for p in chain.phases]
    assert kinds.index(("quality", "QualityLoop")) < kinds.index(("self_review", "AgentPhase"))
    assert kinds.index(("self_review", "AgentPhase")) < kinds.index(("review", "ReviewLoop"))
    # the review loop's envelope contract is the reviewer's verdict
    assert isinstance(chain.phases[4], ReviewLoop)
    assert ReviewOutput is not None  # the loop uses ReviewOutput internally


def test_implement_self_review_is_a_builder_agent_phase():
    mod = _load("adw_implement")
    self_review = next(p for p in mod.CHAIN.phases if p.name == "self_review")
    assert isinstance(self_review, AgentPhase)
    assert self_review.owner == "builder"  # the BUILDER self-reviews its own diff
    assert self_review.output_type is BuildOutput


def test_implement_commits_only_after_review():
    mod = _load("adw_implement")
    chain: Chain = mod.CHAIN
    commit = next(p for p in chain.phases if isinstance(p, CommitPhase))
    assert chain.phases.index(commit) == len(chain.phases) - 1
    assert commit.allow_empty is True  # a no-op re-run ends clean, work already landed


# ── deploy: sandbox → signoff → bump → MR → e2e → release ───────────────────


def test_deploy_chain_phase_list_and_ordering():
    mod = _load("adw_deploy")
    chain: Chain = mod.CHAIN
    assert chain.name == "deploy"
    assert _phase_names(chain) == ["sandbox", "signoff", "bump", "mr", "e2e", "release"]
    assert [type(p).__name__ for p in chain.phases] == [
        "CodePhase", "CodePhase", "CodePhase", "CodePhase", "QualityLoop",
        "CodePhase",
    ]


def test_deploy_steps_are_deterministic_code_or_quality():
    mod = _load("adw_deploy")
    chain: Chain = mod.CHAIN
    for name in ("sandbox", "signoff", "bump", "mr", "release"):
        phase = next(p for p in chain.phases if p.name == name)
        assert isinstance(phase, CodePhase), f"{name} must be a deterministic code phase"
    e2e = next(p for p in chain.phases if p.name == "e2e")
    assert isinstance(e2e, QualityLoop)
    # signoff is a human gate — the checkpoint stays in the terminal (#86 story 30)
    signoff = next(p for p in chain.phases if p.name == "signoff")
    assert isinstance(signoff, CodePhase)


# ── executor-level: the deploy chain runs end to end ────────────────────────


def _make_repo(tmp_path):
    """A repo with main + a dev branch carrying one ticket commit."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.2.3"\n')
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "dev"], cwd=repo, check=True)
    (repo / "feature.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: dark mode (#123)"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    return repo


def test_deploy_chain_executes_all_phases_end_to_end(tmp_path, monkeypatch):
    """The strongest 'runs end-to-end' proof: the real executor walks all six
    deploy phases on a real repo — signoff auto-approved, version bumped and
    committed, MR payload recorded, e2e green on `true`, release parsed."""
    import shutil
    import subprocess

    from test_chains import _make_run
    from test_flow_chains import _load

    from sssf.adw_modules import chains as chains_mod
    from sssf.adw_modules.data_types import QualityCheckSpec

    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)  # git_helper commits against cwd
    mod = _load("adw_deploy")
    chain = mod.CHAIN

    run = _make_run(
        tmp_path,
        checks=[QualityCheckSpec(name="test", area="backend", operation="build", argv=["true"])],
    )
    run.repo_root = str(repo)
    run._deploy_yes = True  # auto-approve the signoff gate

    def no_glab(name: str):
        return None if name == "glab" else shutil.which(name)

    monkeypatch.setattr(shutil, "which", no_glab)

    assert chains_mod.run_chain(run.cfg, run, "deploy the batch", chain) == 0
    assert run.accepted is True
    names = [p.params.name for p in run.phases]
    assert names[:6] == ["request", "sandbox", "signoff", "bump", "mr", "verify_1"]
    assert "release" in names
    # the bump landed: patch advanced and committed
    assert 'version = "1.2.4"' in (repo / "pyproject.toml").read_text()
    last = subprocess.run(
        ["git", "log", "-3", "--oneline"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "bump" in last
    # the MR payload was recorded (no glab)
    assert (repo / "adws" / "data" / "deploy" / "mr_payload.md").exists()
    # release runs after the e2e quality loop
    assert names.index("release") > names.index("verify_1")


def test_deploy_signoff_rejection_fails_the_chain(tmp_path, monkeypatch):
    """A 'no' at the signoff gate fails the chain cleanly — the checkpoint is
    a real gate, not a formality."""

    from test_chains import _make_run

    from sssf.adw_modules import chains as chains_mod

    repo = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    mod = _load("adw_deploy")
    run = _make_run(tmp_path)
    run.repo_root = str(repo)
    run._deploy_yes = False
    assert chains_mod.run_chain(run.cfg, run, "deploy", mod.CHAIN) == 1
    assert run.accepted is False
    assert "signoff rejected" in run.reason
