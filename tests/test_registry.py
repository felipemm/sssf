import json
from pathlib import Path

from sssf import registry


def _write_registry(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / ".sssf" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))
    return path


def test_register_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    proj = tmp_path / "repo-a"
    proj.mkdir()
    registry.register_project(proj, proj / "adws/adw_data/sssf.db", "0.1.0", added=True)
    projects = registry.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "repo-a"
    assert projects[0]["db"].endswith("adws/adw_data/sssf.db")


def test_update_last_run(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    proj = tmp_path / "repo-a"
    proj.mkdir()
    registry.register_project(proj, proj / "sssf.db", "0.1.0")
    registry.update_last_run(proj)
    projects = registry.list_projects()
    assert projects[0]["last_run"] is not None


def test_remove_project(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    proj = tmp_path / "repo-a"
    proj.mkdir()
    registry.register_project(proj, proj / "sssf.db", "0.1.0")
    assert registry.remove_project("repo-a") is True
    assert registry.list_projects() == []


def test_missing_registry_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    assert registry.list_projects() == []


def test_corrupt_registry_is_empty(tmp_path, monkeypatch):
    path = tmp_path / ".sssf" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    monkeypatch.setattr(registry, "registry_path", lambda: path)
    assert registry.list_projects() == []
