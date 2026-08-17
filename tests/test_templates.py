import importlib.util
import shutil
import sys
from pathlib import Path

from sssf.adw_modules import agents

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "sssf" / "templates"


def test_thirteen_starter_chains():
    adws = sorted((TEMPLATES / "adws").glob("adw_*.py"))
    assert len(adws) == 13
    for adw in adws:
        spec = importlib.util.spec_from_file_location(adw.stem, adw)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[adw.stem] = mod
        spec.loader.exec_module(mod)   # imports sssf.adw_modules — proves engine link


def test_no_inline_uv_headers():
    for adw in (TEMPLATES / "adws").glob("adw_*.py"):
        assert "# /// script" not in adw.read_text()


def test_starter_config_validates(tmp_path, monkeypatch):
    # agents.validate needs prompt files on disk + a resolvable model (R1):
    # replicate the stamped layout in tmp, chdir, and stub the catalog.
    monkeypatch.setattr(agents.agent_pi, "resolve_model",
                        lambda pattern: ("openai", "gpt-4o-mini"))
    data = tmp_path / "adws" / "adw_data"
    shutil.copytree(TEMPLATES / "prompt_engineering", data / "prompt_engineering")
    cfg_dir = tmp_path / "adws" / "adw_sssf_config"
    cfg_dir.mkdir(parents=True)
    shutil.copy(TEMPLATES / "sssf.config.yaml", cfg_dir / "sssf.config.yaml")
    monkeypatch.chdir(tmp_path)
    cfg = agents.load_config(cfg_dir / "sssf.config.yaml")
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter", "designer"])


def test_artifact_folders_live_under_adws():
    """specs/ and app_docs/ must stay under adws/ in every template — root-level
    artifact folders clutter the project root and drift from the convention."""
    for path in [*TEMPLATES.rglob("*.md"), *TEMPLATES.rglob("*.py"),
                 TEMPLATES / "sssf.config.yaml"]:
        text = path.read_text()
        for folder in ("specs/", "app_docs/"):
            assert (folder not in text or f"adws/{folder}" in text), \
                f"{path.relative_to(TEMPLATES)} references bare {folder!r}"


def test_builder_prompt_forbids_committing():
    """The factory owns commits — the builder must never run git commit itself.
    (Field incident: a builder committed its own work mid-phase, so commit_build
    found a clean tree and the claim-mismatch check fired.)"""
    text = (TEMPLATES / "prompt_engineering" / "builder" / "user.md").read_text()
    assert "you never commit" in text.lower() and "git commit" in text


def test_quality_design_variant_has_impeccable_phases():
    text = (TEMPLATES / "adws" / "adw_design_sdlc.py").read_text()
    for needle in ('name="init"', 'name="design"', 'owner="designer"',
                   'owner="documenter"', 'name="document"', 'impeccable'):
        assert needle in text, f"variant missing {needle}"


def test_noop_rerun_walks_the_doc_chain():
    """A no-op re-run must not silently skip documentation: it confirms an
    existing write-up (success run, no updated doc) or produces the missing one.
    (Field gap: the FTS5 work was committed by a failed run that never reached
    the document phase, and the no-op re-run skipped docs entirely.)"""
    text = (TEMPLATES / "adws" / "adw_simple_sdlc.py").read_text()
    assert "no updated doc" in text
    assert "app_docs" in text
    assert "run.repo_root" in text


def test_document_chain_ends_in_commit():
    """The standalone adw_document chain must commit the write-up — a doc left
    uncommitted in the working tree is a lost record. (Field gap: adw_document
    wrote adws/app_docs/<id>.md but never committed it.)"""
    text = (TEMPLATES / "adws" / "adw_document.py").read_text()
    assert "commit_docs" in text
    assert "git_helper.commit_all" in text


def test_template_ships_default_checks():
    cfg = (TEMPLATES / "sssf.config.yaml").read_text()
    assert '- name: design' in cfg
    assert '"impeccable", "detect", "site/dist"' in cfg
    assert '- name: snyk' in cfg
    assert '"snyk", "test"' in cfg
    # runners stay honest placeholders — never defaulted
    assert "PLACEHOLDER test" in cfg


def test_designer_prompt_files_exist():
    for label in ("system", "user"):
        path = TEMPLATES / "prompt_engineering" / "designer" / f"{label}.md"
        assert path.is_file(), f"designer {label} prompt missing"
