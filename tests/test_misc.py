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


def test_viz_healer_start_failure_is_loud(tmp_path, monkeypatch, capsys):
    """A healer-start failure is surfaced, not swallowed (audit A3)."""
    import sssf.commands.viz as viz

    def boom():
        raise RuntimeError("healer broken")

    monkeypatch.setattr(viz, "_running_pid", lambda: None)
    monkeypatch.setattr(viz, "_spawn", lambda *a, **k: 99999)
    monkeypatch.setattr(viz, "_wait_for_server", lambda *a, **k: None)
    monkeypatch.setattr(viz, "_pid_alive", lambda *a, **k: True)
    monkeypatch.setattr(viz, "webbrowser", type("B", (), {"open": staticmethod(lambda u: True)})())
    import sssf.healer as healer_mod
    monkeypatch.setattr(healer_mod, "start", boom)
    viz.start(4600, None, None)
    assert "healer start failed" in capsys.readouterr().err
