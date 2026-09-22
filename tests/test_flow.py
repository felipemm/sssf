"""`sssf flow` — the three flows dispatch onto the existing chain runner (#89).

Seam 1 (CLI): command-function tests with tmp_path projects and monkeypatched
spawns — the test_ticket_cli.py pattern. Ad-hoc `sssf run` is removed; flows
are the only entry point for starting work.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from sssf import registry, ticketing
from sssf.commands import flow


def _setup_project(tmp_path, monkeypatch, *, sandbox_key: bool = False) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    (root / "adws" / "prompts").mkdir(parents=True)
    cfg = "defaults:\n  coding_agent: pi\n  model: openai/gpt-4o-mini\n"
    if sandbox_key:
        cfg += "sandbox:\n  enabled: false\n"
    (root / "adws" / "config" / "sssf.config.yaml").write_text(cfg)
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    registry.register_project(root, root / "adws" / "data" / "sssf.db", "1.0.0")
    monkeypatch.chdir(root)
    return root


def _seed_ticket(root: Path, ticket_id: str, status: str = "ready-for-agent",
                 title: str = "Dark mode", description: str = "Make it dark") -> None:
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    ticketing.ensure_schema(conn)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status)"
        " VALUES (?, 'internal', '', ?, ?, ?)",
        (ticket_id, title, description, status),
    )
    conn.commit()
    conn.close()


def _capture_call(monkeypatch, target=flow.subprocess):
    calls: list[list[str]] = []

    def fake_call(argv, **kw):
        calls.append(argv)
        return 0

    monkeypatch.setattr(target, "call", fake_call)
    return calls


# ── plan ─────────────────────────────────────────────────────────────────────


def test_flow_plan_without_ticket_dispatches_plan_chain(tmp_path, monkeypatch):
    _root = _setup_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), None, None, skip_exploration=False, no_sandbox=True) == 0
    argv = calls[0]
    assert argv[0] == sys.executable
    assert argv[1].endswith("adw_plan.py")
    assert "Plan this feature request" in argv[2]
    assert "--skip-exploration" not in argv


def test_flow_plan_skip_exploration_is_forwarded(tmp_path, monkeypatch):
    _root = _setup_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), None, None, skip_exploration=True, no_sandbox=True) == 0
    assert "--skip-exploration" in calls[0]


def test_flow_plan_with_ticket_builds_prompt_from_ticket(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(root, "internal:abc", title="Dark mode", description="Make it dark")
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), "internal:abc", None, False, no_sandbox=True) == 0
    prompt = calls[0][2]
    assert "# Dark mode" in prompt
    assert "Make it dark" in prompt
    assert "Generated from internal ticket" in prompt


def test_flow_plan_missing_ticket_is_loud(tmp_path, monkeypatch, capsys):
    _root = _setup_project(tmp_path, monkeypatch)
    assert flow.plan(Path.cwd(), "internal:nope", None, False, no_sandbox=True) == 1
    assert "no ticket internal:nope" in capsys.readouterr().err


# ── implement ────────────────────────────────────────────────────────────────


def test_flow_implement_dispatches_implement_chain(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(root, "internal:abc")
    calls = _capture_call(monkeypatch)
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=True) == 0
    argv = calls[0]
    assert argv[1].endswith("adw_implement.py")
    assert "Make it dark" in argv[2]


def test_flow_implement_requires_ready_for_agent(tmp_path, monkeypatch, capsys):
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(root, "internal:idea", status="needs-triage")
    calls = _capture_call(monkeypatch)
    assert flow.implement(Path.cwd(), "internal:idea", None, no_sandbox=True) == 1
    assert "only ready-for-agent" in capsys.readouterr().err
    assert calls == []  # nothing spawned


def test_flow_implement_rejects_live_run(tmp_path, monkeypatch, capsys):
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(root, "internal:abc")
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (adw_id TEXT PRIMARY KEY, status TEXT)")
    conn.execute(
        "UPDATE tickets SET adw_id='run1' WHERE id='internal:abc'"
    )
    conn.execute("INSERT INTO sessions (adw_id, status) VALUES ('run1', 'running')")
    conn.commit()
    conn.close()
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=True) == 1
    assert "live run" in capsys.readouterr().err


def test_flow_implement_missing_ticket_is_loud(tmp_path, monkeypatch, capsys):
    _root = _setup_project(tmp_path, monkeypatch)
    assert flow.implement(Path.cwd(), "internal:nope", None, no_sandbox=True) == 1
    assert "no ticket internal:nope" in capsys.readouterr().err


# ── deploy ───────────────────────────────────────────────────────────────────


def test_flow_deploy_dispatches_deploy_chain(tmp_path, monkeypatch):
    _root = _setup_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    assert flow.deploy(Path.cwd(), None, yes=False, no_sandbox=True) == 0
    argv = calls[0]
    assert argv[1].endswith("adw_deploy.py")
    assert "--yes" not in argv


def test_flow_deploy_yes_is_forwarded(tmp_path, monkeypatch):
    _root = _setup_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    assert flow.deploy(Path.cwd(), None, yes=True, no_sandbox=True) == 0
    assert "--yes" in calls[0]


# ── shared dispatch ──────────────────────────────────────────────────────────


def test_flow_no_project_is_loud(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no adws/ anywhere up the tree
    assert flow.plan(Path.cwd(), None, None, False, no_sandbox=True) == 1
    assert "no project here" in capsys.readouterr().err


def test_flow_sandboxed_dispatch_uses_the_sandbox(tmp_path, monkeypatch):
    """No `sandbox:` key in the config → sandboxed by default: the spawn goes
    through _run_sandboxed, never a bare subprocess call."""
    _root = _setup_project(tmp_path, monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(flow, "_run_sandboxed", lambda *a, **k: captured.update(a=a) or 0)
    assert flow.deploy(Path.cwd(), None, yes=False, no_sandbox=False) == 0
    assert captured["a"][1].name == "adw_deploy.py"


def test_flow_warns_on_legacy_layout(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws" / "adw_data").mkdir(parents=True)  # v1 layout marker
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    assert flow.plan(root, None, None, False, no_sandbox=True) != 0
    assert "legacy adws layout" in capsys.readouterr().err
