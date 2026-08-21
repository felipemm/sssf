"""sssf doctor — prerequisite + skill-freshness verification."""

from __future__ import annotations

import shutil
from pathlib import Path

from sssf.adw_modules import skills_install
from sssf.commands import doctor


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / "adws" / "config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def test_doctor_healthy(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/pi" if name == "pi" else None)
    monkeypatch.setattr(skills_install, "check_skills", lambda r: {
        s: {"present": True, "pinned": "a" * 7, "latest": "a" * 7, "stale": False}
        for s in skills_install.SOURCES})
    assert doctor.run(Path.cwd()) == 0
    assert "all checks passed" in capsys.readouterr().out


def test_doctor_reports_missing_and_stale(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/pi" if name == "pi" else None)
    monkeypatch.setattr(skills_install, "check_skills", lambda r: {
        "brainstorming": {"present": False, "pinned": None, "latest": "a" * 7, "stale": False},
        "grilling": {"present": True, "pinned": "b" * 7, "latest": "c" * 7, "stale": True},
        "grill-me": {"present": True, "pinned": "b" * 7, "latest": "b" * 7, "stale": False},
        "grill-with-docs": {"present": True, "pinned": "b" * 7, "latest": "b" * 7, "stale": False},
    })
    assert doctor.run(Path.cwd()) == 1
    err = capsys.readouterr().err
    assert "NOT INSTALLED" in err and "STALE" in err


def test_doctor_no_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert doctor.run(Path.cwd()) == 1
    assert "no project here" in capsys.readouterr().err
