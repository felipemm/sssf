from pathlib import Path

from sssf import registry
from sssf.commands import init, run


def _setup_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(registry, "registry_path",
                        lambda: tmp_path / ".sssf" / "projects.json")
    # minimal v2 project (init itself is covered in test_init.py)
    (root / "adws" / "modules").mkdir(parents=True)
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "config" / "sssf.config.yaml").write_text(
        "defaults:\n  coding_agent: pi\n  model: openai/gpt-4o-mini\n"
        "sandbox:\n  enabled: false\n"
        "observability:\n  db: adws/data/sssf.db\n")
    registry.register_project(root, root / "adws" / "data" / "sssf.db", "1.0.0")
    # a stub ADW that proves the engine import works end-to-end
    stub = root / "adws" / "modules" / "adw_stub_check.py"
    stub.write_text(
        "import sssf.adw_modules\n"
        "from sssf.adw_modules.data_types import EnvelopeBase\n"
        "print('STUB_OK')\n"
    )
    return root


def test_run_with_prefix(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "stub_check", [], None, no_sandbox=True) == 0


def test_run_without_prefix(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "stub_check", [], None, no_sandbox=True) == 0


def test_run_missing_adw_fails(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "does_not_exist", [], None) == 1


def test_run_updates_last_run(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    run.run(root, "stub_check", [], None)
    entry = registry.list_projects()[0]
    assert entry["last_run"] is not None


def test_run_warns_on_legacy_layout(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws" / "adw_data").mkdir(parents=True)
    monkeypatch.setattr(registry, "registry_path",
                        lambda: tmp_path / ".sssf" / "projects.json")
    assert run.run(root, "scout", [], None, no_sandbox=True) != 0
    assert "legacy adws layout" in capsys.readouterr().err


def test_sandbox_enabled_defaults_true_on_v2_project(tmp_path):
    """No sandbox key in the config → sandboxed by default. Regression: the
    v2 refactor left _sandbox_enabled using paths.config_file without the
    import — the NameError was swallowed and every run silently went local."""
    (tmp_path / "adws" / "config").mkdir(parents=True)
    (tmp_path / "adws" / "config" / "sssf.config.yaml").write_text(
        "defaults:\n  model: openai/gpt-4o-mini\n")
    from sssf.commands import run
    assert run._sandbox_enabled(tmp_path) is True


def test_sandbox_enabled_failure_is_loud(tmp_path, capsys):
    from sssf.commands import run
    assert run._sandbox_enabled(tmp_path) is False
    assert "sandbox decision failed" in capsys.readouterr().err
