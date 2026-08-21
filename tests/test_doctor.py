"""sssf doctor — the existing misc.doctor now also verifies the project-scope
interview prerequisites (provider, pi, skills presence + freshness)."""

from __future__ import annotations

import shutil
from pathlib import Path

from sssf.adw_modules import skills_install
from sssf.commands import misc


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / "adws" / "config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def test_doctor_project_checks_healthy(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(skills_install, "check_skills", lambda r: {
        s: {"present": True, "pinned": "a" * 7, "latest": "a" * 7, "stale": False}
        for s in skills_install.SOURCES})
    assert misc.doctor() == 0
    out = capsys.readouterr().out
    assert "internal ticketing provider" in out and "skill grilling" in out


def test_doctor_project_reports_missing_and_stale(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(skills_install, "check_skills", lambda r: {
        "brainstorming": {"present": False, "pinned": None, "latest": "a" * 7, "stale": False},
        "grilling": {"present": True, "pinned": "b" * 7, "latest": "c" * 7, "stale": True},
        "grill-me": {"present": True, "pinned": "b" * 7, "latest": "b" * 7, "stale": False},
        "grill-with-docs": {"present": True, "pinned": "b" * 7, "latest": "b" * 7, "stale": False},
    })
    assert misc.doctor() == 1
    out = capsys.readouterr().out
    assert "missing" in out and "stale" in out


def test_doctor_no_project_skips_project_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert misc.doctor() == 0
    assert "no project here" in capsys.readouterr().out
