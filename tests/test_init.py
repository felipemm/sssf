import subprocess
from pathlib import Path

from sssf import registry
from sssf.commands import init


def _run_init(root: Path, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr(registry, "registry_path",
                        lambda: root.parent / ".sssf" / "projects.json")
    return init.run(root, refresh="--refresh" in (argv or []),
                    force="--force" in (argv or []))


def test_init_stamps_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    assert (root / "adws/adw_sssf_config/sssf.config.yaml").exists()
    assert (root / "adws/adw_prompt.py").exists()
    assert (root / "adws/adw_data/prompt_engineering/planner/system.md").exists()
    assert (root / ".env.sample").exists()
    agents_md = (root / "AGENTS.md").read_text()
    assert "sssf" in agents_md
    gitignore = (root / ".gitignore").read_text()
    assert "adws/adw_data/sssf.db" in gitignore
    assert "adws/adw_data/sssf.db-wal" in gitignore
    assert "adws/adw_data/sssf.db-shm" in gitignore
    assert len(registry.list_projects()) == 1


def test_init_is_idempotent_and_does_not_clobber(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    adw = root / "adws/adw_prompt.py"
    original = adw.read_text()
    adw.write_text(original + "\n# user edit\n")
    assert _run_init(root, monkeypatch) == 0
    assert adw.read_text() == original + "\n# user edit\n"


def test_refresh_adds_missing_only(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    (root / "adws/adw_prompt.py").unlink()
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert (root / "adws/adw_prompt.py").exists()
