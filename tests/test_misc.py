from sssf import registry
from sssf.commands import misc


def test_projects_list_and_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    root = tmp_path / "proj"
    root.mkdir()
    registry.register_project(root, root / "adws/adw_data/sssf.db", "0.1.0")
    assert misc.projects("list", None) == 0
    assert "proj" in capsys.readouterr().out
    assert misc.projects("remove", "proj") == 0
    assert registry.list_projects() == []


def test_doctor_reports_missing_binary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(misc, "which", lambda name: None)
    assert misc.doctor() == 1
    out = capsys.readouterr().out
    assert "missing" in out


def test_doctor_ok_when_all_present(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    assert misc.doctor() == 0
    assert "ok" in capsys.readouterr().out
