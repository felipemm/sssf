"""sssf spec create — interactive product-manager interview launcher."""

from __future__ import annotations

from pathlib import Path

from sssf.commands import spec


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    (root / "adws" / "prompts").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / "adws" / "config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def test_create_writes_context_and_spawns_pi(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(spec.misc, "which", lambda name: "/usr/local/bin/pi")
    calls = {}

    def fake_call(argv, cwd):
        calls["argv"] = argv
        calls["cwd"] = cwd
        return 0
    monkeypatch.setattr(spec.subprocess, "call", fake_call)

    assert spec.create("idea", "Explore dark mode", Path.cwd()) == 0
    assert calls["cwd"] == root
    assert calls["argv"][0] == "pi"
    assert calls["argv"][1] == "--append-system-prompt"
    context = Path(calls["argv"][2])
    assert context.exists()
    text = context.read_text()
    assert "PRODUCT MANAGER" in text and "Project context" in text
    assert "dark-mode" in context.name  # the slug lands in the context filename


def test_create_modes_select_template(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(spec.misc, "which", lambda name: "/usr/local/bin/pi")
    monkeypatch.setattr(spec.subprocess, "call", lambda argv, cwd: 0)
    for mode, marker in (("bug", "grill-me"), ("feature", "ACCEPTANCE"), ("idea", "grilling")):
        assert spec.create(mode, "Something", Path.cwd()) == 0
        ctx = list((root / "adws" / "data" / "spec_interview").glob(f"{mode}-*.md"))[-1]
        assert marker.lower() in ctx.read_text().lower()


def test_create_guards(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert spec.create("idea", "x", Path.cwd()) == 1
    assert "no project here" in capsys.readouterr().err

    root = _project(tmp_path, monkeypatch, "providers:\n  - internal\n")
    monkeypatch.setattr(spec.misc, "which", lambda name: None)
    assert spec.create("idea", "x", Path.cwd()) == 1
    assert "pi binary not found" in capsys.readouterr().err
