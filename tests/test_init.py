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
    assert (root / "adws/config/sssf.config.yaml").exists()
    assert (root / "adws/modules/adw_prompt.py").exists()
    assert (root / "adws/data/prompt_engineering/planner/system.md").exists()
    assert (root / ".env.sample").exists()
    agents_md = (root / "AGENTS.md").read_text()
    assert "sssf" in agents_md
    gitignore = (root / ".gitignore").read_text()
    assert "adws/data/sssf.db" in gitignore
    assert "adws/data/sssf.db-wal" in gitignore
    assert "adws/data/sssf.db-shm" in gitignore
    assert len(registry.list_projects()) == 1


def test_init_is_idempotent_and_does_not_clobber(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    adw = root / "adws/modules/adw_prompt.py"
    original = adw.read_text()
    adw.write_text(original + "\n# user edit\n")
    assert _run_init(root, monkeypatch) == 0
    assert adw.read_text() == original + "\n# user edit\n"


def test_refresh_adds_missing_only(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    (root / "adws/modules/adw_prompt.py").unlink()
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert (root / "adws/modules/adw_prompt.py").exists()


def test_init_stamps_commented_ticketing_template(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    cfg = root / "adws/config/ticketing.yaml"
    assert cfg.exists()
    text = cfg.read_text()
    assert text.lstrip().startswith("#")          # fully commented
    assert "providers" in text
    # and it parses as "not configured":
    from sssf import ticketing
    assert ticketing.load_config(root) is None


def test_refresh_prompts_and_keeps_on_no(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    adw = root / "adws/modules/adw_prompt.py"
    original = adw.read_text()
    adw.write_text(original + "\n# user edit\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert adw.read_text() == original + "\n# user edit\n"   # nothing clobbered


def test_refresh_overwrites_on_yes(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    adw = root / "adws/modules/adw_prompt.py"
    template = adw.read_text()
    adw.write_text(template + "\n# user edit\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert adw.read_text() == template                  # template restored


def test_refresh_yes_to_all_overwrites_every_adw(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    adw = root / "adws/modules/adw_prompt.py"
    adw.write_text(adw.read_text() + "\n# user edit\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert "# user edit" not in adw.read_text()


def test_refresh_auto_accepts_all_without_stdin(tmp_path, monkeypatch):
    """--auto must overwrite every adws file without ever prompting."""
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0          # initial stamp
    target = root / "adws" / "modules" / "adw_simple_sdlc.py"
    target.write_text("OLD")                          # drift it
    monkeypatch.setattr("builtins.input",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted!")))
    rc = init.run(root, refresh=True, auto=True)
    assert rc == 0
    assert target.read_text() != "OLD"                # overwritten by the template
    assert (root / "adws" / "config" / "ticketing.yaml").exists()


def test_refresh_without_auto_still_prompts(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    target = root / "adws" / "modules" / "adw_simple_sdlc.py"
    target.write_text("OLD")
    calls: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(str(prompt)) or "n")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert calls and "overwrite" in calls[0]          # the y/N/a prompt ran
    assert target.read_text() == "OLD"                # answered 'n'


def test_init_stamps_v2_layout(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    assert (root / "adws/modules/adw_prompt.py").exists()
    assert (root / "adws/config/sssf.config.yaml").exists()
    assert (root / "adws/config/ticketing.yaml").exists()
    assert (root / "adws/data/prompt_engineering/planner/system.md").exists()
    for folder in ("prompts", "specs", "kb"):
        assert (root / "adws" / folder / "README.md").is_file()
    assert not (root / "adws/adw_sssf_config").exists()
    assert not (root / "adws/adw_data").exists()
    assert not (root / "adws/adw_prompt.py").exists()


def test_refresh_migrates_legacy_layout(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    # Build a v1 project by hand
    (root / "adws" / "adw_sssf_config").mkdir(parents=True)
    (root / "adws" / "adw_sssf_config" / "sssf.config.yaml").write_text(
        "roster: v1\nsystem: adws/adw_data/prompt_engineering/scout/system.md\n")
    (root / "adws" / "adw_data").mkdir(parents=True)
    (root / "adws" / "adw_data" / "sssf.db").write_text("db")
    (root / "adws" / "app_docs").mkdir(parents=True)
    (root / "adws" / "app_docs" / "note.md").write_text("note")
    custom = root / "adws" / "adw_custom.py"
    custom.write_text('config: str = "adws/adw_sssf_config/sssf.config.yaml"\n')
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    # moved to v2
    assert (root / "adws/config/sssf.config.yaml").read_text() == (
        "roster: v1\nsystem: adws/data/prompt_engineering/scout/system.md\n")
    assert (root / "adws/data/sssf.db").read_text() == "db"
    assert (root / "adws/kb/note.md").read_text() == "note"
    assert (root / "adws/modules/adw_custom.py").exists()
    # literal rewritten in the moved chain
    moved = (root / "adws/modules/adw_custom.py").read_text()
    assert "adws/adw_sssf_config" not in moved and "adws/config/" in moved
    # backup exists and is gitignored
    backups = list(root.glob("adws.backup.*"))
    assert len(backups) == 1 and backups[0].is_dir()
    assert "adws.backup." in (root / ".gitignore").read_text()
    # legacy names gone
    assert not (root / "adws/adw_sssf_config").exists()
    assert not (root / "adws/adw_data").exists()
    assert not (root / "adws/app_docs").exists()


def test_refresh_on_v2_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _run_init(root, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert _run_init(root, monkeypatch, ["--refresh"]) == 0
    assert not list(root.glob("adws.backup.*"))
    assert (root / "adws/modules/adw_prompt.py").exists()
