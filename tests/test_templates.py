import importlib.util
import shutil
import sys
from pathlib import Path

from sssf.adw_modules import agents

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "sssf" / "templates"


def test_thirteen_starter_chains():
    adws = sorted((TEMPLATES / "adws" / "modules").glob("adw_*.py"))
    assert len(adws) == 13
    for adw in adws:
        spec = importlib.util.spec_from_file_location(adw.stem, adw)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[adw.stem] = mod
        spec.loader.exec_module(mod)  # imports sssf.adw_modules — proves engine link


def test_no_inline_uv_headers():
    for adw in (TEMPLATES / "adws" / "modules").glob("adw_*.py"):
        assert "# /// script" not in adw.read_text()


def test_starter_config_validates(tmp_path, monkeypatch):
    # agents.validate needs prompt files on disk + a resolvable model (R1):
    # replicate the stamped layout in tmp, chdir, and stub the catalog.
    monkeypatch.setattr(agents.agent_pi, "resolve_model", lambda pattern: ("openai", "gpt-4o-mini"))
    data = tmp_path / "adws" / "data"
    shutil.copytree(TEMPLATES / "adws" / "data" / "prompt_engineering", data / "prompt_engineering")
    cfg_dir = tmp_path / "adws" / "config"
    cfg_dir.mkdir(parents=True)
    shutil.copy(TEMPLATES / "adws" / "config" / "sssf.config.yaml", cfg_dir / "sssf.config.yaml")
    monkeypatch.chdir(tmp_path)
    cfg = agents.load_config(cfg_dir / "sssf.config.yaml")
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter", "designer"])


def test_artifact_folders_live_under_adws():
    """specs/ and kb/ must stay under adws/ in every template — root-level
    artifact folders clutter the project root and drift from the convention."""
    for path in [
        *TEMPLATES.rglob("*.md"),
        *TEMPLATES.rglob("*.py"),
        TEMPLATES / "adws" / "config" / "sssf.config.yaml",
    ]:
        text = path.read_text()
        for folder in ("specs/", "kb/"):
            assert folder not in text or f"adws/{folder}" in text, (
                f"{path.relative_to(TEMPLATES)} references bare {folder!r}"
            )


def test_builder_prompt_forbids_committing():
    """The factory owns commits — the builder must never run git commit itself.
    (Field incident: a builder committed its own work mid-phase, so commit_build
    found a clean tree and the claim-mismatch check fired.)"""
    text = (TEMPLATES / "adws" / "data" / "prompt_engineering" / "builder" / "user.md").read_text()
    assert "you never commit" in text.lower() and "git commit" in text


def test_quality_design_variant_has_impeccable_phases():
    text = (TEMPLATES / "adws" / "modules" / "adw_design_sdlc.py").read_text()
    for needle in (
        '"init"',
        '"design"',
        '"designer"',
        '"documenter"',
        '"document"',
        "impeccable",
        "QualityLoop",
    ):
        assert needle in text, f"variant missing {needle}"


def test_template_scaffolds_prompts_specs_kb():
    for folder in ("prompts", "specs", "kb"):
        readme = TEMPLATES / "adws" / folder / "README.md"
        assert readme.is_file(), f"missing scaffold README in {folder}"


def test_noop_rerun_walks_the_doc_chain():
    """A no-op re-run must not silently skip documentation: it confirms an
    existing write-up (success run, no updated doc) or produces the missing one.
    (Field gap: the FTS5 work was committed by a failed run that never reached
    the document phase, and the no-op re-run skipped docs entirely.)"""
    text = (TEMPLATES / "adws" / "modules" / "adw_simple_sdlc.py").read_text()
    assert "no updated doc" in text
    assert "kb" in text
    assert "run.repo_root" in text


def test_document_chain_ends_in_commit():
    """The standalone adw_document chain must commit the write-up — a doc left
    uncommitted in the working tree is a lost record. (Field gap: adw_document
    wrote adws/app_docs/<id>.md but never committed it.)"""
    text = (TEMPLATES / "adws" / "modules" / "adw_document.py").read_text()
    assert "commit_docs" in text
    assert "CommitPhase" in text  # the commit lives in the shared executor


def test_template_ships_default_checks():
    cfg = (TEMPLATES / "adws" / "config" / "sssf.config.yaml").read_text()
    assert "- name: design" in cfg
    assert '"impeccable", "detect", "site/dist"' in cfg
    assert "- name: snyk" in cfg
    assert '"snyk", "test"' in cfg
    # runners stay honest placeholders — never defaulted
    assert "PLACEHOLDER test" in cfg


def test_designer_prompt_files_exist():
    for label in ("system", "user"):
        path = TEMPLATES / "adws" / "data" / "prompt_engineering" / "designer" / f"{label}.md"
        assert path.is_file(), f"designer {label} prompt missing"


def test_adws_resolve_config_at_runtime():
    """--config defaults to None and main() resolves via paths — a chain must
    never bake a layout literal that the v2 migration rewrites."""
    for adw in (TEMPLATES / "adws" / "modules").glob("adw_*.py"):
        text = adw.read_text()
        assert "agents.default_config_path" in text
        assert "adws/adw_sssf_config" not in text
        assert "adws/app_docs" not in text


def test_fix_loop_adws_break_on_env_failure():
    """Every ADW with a builder fix loop must break on an environment failure
    (missing binary / missing target) instead of handing it to the builder —
    no code edit can fix a missing binary, and the loop would burn all
    MAX_FIX_LOOPS iterations on it (issue #16). The break lives in the shared
    chains.QualityLoop; each fix-loop ADW must declare it."""
    import inspect

    import sssf.adw_modules.chains as chains_mod

    assert "quality.env_failure" in inspect.getsource(chains_mod._quality_loop)
    for name in (
        "adw_simple_sdlc",
        "adw_build_test",
        "adw_plan_build_test",
        "adw_plan_build_test_quality",
        "adw_design_sdlc",
    ):
        text = (TEMPLATES / "adws" / "modules" / f"{name}.py").read_text()
        assert "QualityLoop" in text, f"{name} missing the shared quality loop"
