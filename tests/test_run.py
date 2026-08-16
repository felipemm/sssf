from pathlib import Path

from sssf import registry
from sssf.commands import init, run


def _setup_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(registry, "registry_path",
                        lambda: tmp_path / ".sssf" / "projects.json")
    init.run(root)
    # a stub ADW that proves the engine import works end-to-end
    stub = root / "adws" / "adw_stub_check.py"
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
