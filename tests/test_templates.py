import importlib.util
import shutil
import sys
from pathlib import Path

from sssf.adw_modules import agents

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "sssf" / "templates"


def test_twelve_starter_chains():
    adws = sorted((TEMPLATES / "adws").glob("adw_*.py"))
    assert len(adws) == 12
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
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter"])


def test_artifact_folders_live_under_adws():
    """specs/ and app_docs/ must stay under adws/ in every template — root-level
    artifact folders clutter the project root and drift from the convention."""
    for path in [*TEMPLATES.rglob("*.md"), *TEMPLATES.rglob("*.py"),
                 TEMPLATES / "sssf.config.yaml"]:
        text = path.read_text()
        for folder in ("specs/", "app_docs/"):
            assert (folder not in text or f"adws/{folder}" in text), \
                f"{path.relative_to(TEMPLATES)} references bare {folder!r}"
