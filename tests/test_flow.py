"""`sssf flow` — the three flows dispatch onto the existing chain runner (#89).

Seam 1 (CLI): command-function tests with tmp_path projects and monkeypatched
spawns — the test_ticket_cli.py pattern. Ad-hoc `sssf run` is removed; flows
are the only entry point for starting work.
"""

from __future__ import annotations

import os
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


def _seed_ticket(
    root: Path,
    ticket_id: str,
    status: str = "ready-for-agent",
    title: str = "Dark mode",
    description: str = "Make it dark",
    kind: str = "implementation",
) -> None:
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    ticketing.ensure_schema(conn)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status, kind)"
        " VALUES (?, 'internal', '', ?, ?, ?, ?)",
        (ticket_id, title, description, status, kind),
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


def _adb(root: Path) -> sqlite3.Connection:
    """The project db the flow writes (same as _db in commands/ticket)."""
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    ticketing.ensure_schema(conn)
    return conn


def test_flow_plan_with_ticket_builds_prompt_from_ticket(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(
        root,
        "internal:abc",
        status="needs-triage",
        kind="idea",
        title="Dark mode",
        description="Make it dark",
    )
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), "internal:abc", None, False, no_sandbox=True) == 0
    prompt = calls[0][2]
    assert "# Dark mode" in prompt
    assert "Make it dark" in prompt
    assert "Generated from internal ticket" in prompt


def test_flow_plan_refuses_implementation_tickets(tmp_path, monkeypatch, capsys):
    """Implementation tickets are terminal plan inputs (#91 AC5/AC6): the plan
    flow never runs on them — not even with --revise."""
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(root, "internal:abc")
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), "internal:abc", None, False, no_sandbox=True) == 1
    assert "implementation ticket" in capsys.readouterr().err
    assert calls == []  # nothing spawned
    assert flow.plan(Path.cwd(), "internal:abc", None, False, no_sandbox=True, revise=True) == 1
    assert calls == []


def test_flow_plan_plan_once_refuses_planned_idea_without_revise(tmp_path, monkeypatch, capsys):
    """Plan-once: an idea ticket that already went through the plan flow needs
    the explicit --revise escape (#91 AC5)."""
    root = _setup_project(tmp_path, monkeypatch)
    _seed_ticket(root, "internal:idea", status="needs-triage", kind="idea", title="Dark mode")
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    conn.execute("UPDATE tickets SET spec='adws/specs/old.md' WHERE id='internal:idea'")
    conn.commit()
    conn.close()
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True) == 1
    assert "--revise" in capsys.readouterr().err
    assert calls == []
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True, revise=True) == 0
    assert calls  # revise relaxed the guard and dispatched


def test_flow_plan_links_the_run_before_the_spawn(tmp_path, monkeypatch):
    """The plan run's trace link (tickets.adw_id + ticket_runs) exists BEFORE
    the spawn — a mid-run read sees the link, and the sandbox monitor settles
    the transform from it (#91)."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:idea", status="needs-triage", kind="idea", title="Dark mode")
    calls: list[list[str]] = []
    seen_link: list[str | None] = []

    def fake_call(argv, **kw):
        conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
        seen_link.append(
            conn.execute("SELECT adw_id FROM tickets WHERE id='internal:idea'").fetchone()[0]
        )
        conn.close()
        calls.append(argv)
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True) == 0
    linked = calls[0][calls[0].index("--adw-id") + 1]
    assert seen_link == [linked]  # mid-run read sees the link
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    row = conn.execute("SELECT adw_id FROM tickets WHERE id='internal:idea'").fetchone()
    runs = conn.execute("SELECT adw_id FROM ticket_runs WHERE ticket_id='internal:idea'").fetchall()
    conn.close()
    assert row == (linked,)
    assert [r[0] for r in runs] == [linked]


def test_flow_plan_no_sandbox_success_lands_the_transform(tmp_path, monkeypatch, capsys):
    """A successful --no-sandbox plan run settles the transform host-side: the
    idea ticket gets its spec reference and ready-for-agent children."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:idea", status="needs-triage", kind="idea", title="Dark mode")

    def fake_call(argv, **kw):
        adw_id = argv[argv.index("--adw-id") + 1]
        conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
        conn.execute(
            "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
            " VALUES (?,?,?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
            (adw_id, "adw_plan", "success"),
        )
        conn.commit()
        conn.close()
        specs = root / "adws" / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        (specs / f"{adw_id}_spec-dark-mode.md").write_text("# Dark mode\n\nSpec body.\n")
        (specs / f"{adw_id}_tickets-dark-mode.md").write_text(
            "# Slices\n\n## Toggle\n\nA toggle.\n\n## Persist\n\nPersist it.\n"
        )
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True) == 0
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    row = conn.execute("SELECT spec FROM tickets WHERE id='internal:idea'").fetchone()
    children = conn.execute("SELECT title FROM tickets WHERE parent_id='internal:idea'").fetchall()
    conn.close()
    assert row[0].endswith("_spec-dark-mode.md")
    assert [c[0] for c in children] == ["Toggle", "Persist"]


def test_flow_plan_no_sandbox_failure_lands_nothing(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:idea", status="needs-triage", kind="idea", title="Dark mode")

    def fake_call(argv, **kw):
        return 1  # the ADW failed

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True) == 1
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    row = conn.execute("SELECT spec, status FROM tickets WHERE id='internal:idea'").fetchone()
    conn.close()
    assert row == ("", "needs-triage")  # untouched


def test_flow_plan_sandbox_spawn_failure_leaves_the_ticket_planable(tmp_path, monkeypatch):
    """A sandbox spawn that fails before the ADW starts leaves the idea ticket
    needs-triage (nothing to requeue — the parent never claimed a machine
    state); the operator re-runs."""
    root = _setup_project(tmp_path, monkeypatch)  # sandbox enabled by default
    _seed_ticket(root, "internal:idea", status="needs-triage", kind="idea", title="Dark mode")
    monkeypatch.setattr(flow, "_dispatch_chain", lambda *a, **k: 1)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=False) == 1
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    status = conn.execute("SELECT status FROM tickets WHERE id='internal:idea'").fetchone()[0]
    conn.close()
    assert status == "needs-triage"


def test_flow_plan_no_args_success_creates_idea_ticket_and_children(tmp_path, monkeypatch, capsys):
    """No-args mode: exploration -> brief -> idea ticket -> spec -> slices in
    one pass — the settle creates the idea ticket from the landed spec."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)

    def fake_call(argv, **kw):
        adw_id = argv[argv.index("--adw-id") + 1]
        conn = _adb(root)
        conn.execute(
            "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
            " VALUES (?,?,?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
            (adw_id, "adw_plan", "success"),
        )
        conn.commit()
        conn.close()
        specs = root / "adws" / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        (specs / f"{adw_id}_spec-dark-mode.md").write_text("# Dark mode\n\nSpec body.\n")
        (specs / f"{adw_id}_tickets-dark-mode.md").write_text(
            "# Slices\n\n## Toggle\n\nA toggle.\n"
        )
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.plan(Path.cwd(), None, None, False, no_sandbox=True) == 0
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    parents = conn.execute("SELECT id, title, kind FROM tickets WHERE kind='idea'").fetchall()
    conn.close()
    assert len(parents) == 1 and parents[0][1] == "Dark mode"
    out = capsys.readouterr().out
    assert "Dark mode" in out  # the landed ticket is announced
    assert "1 implementation ticket" in out


def test_flow_plan_does_not_forward_revise_to_the_chain(tmp_path, monkeypatch):
    """--revise is a dispatch-level guard relaxation, never a chain argument
    (the ADW does not know about it)."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:idea", status="needs-triage", kind="idea", title="Dark mode")
    calls = _capture_call(monkeypatch)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True, revise=True) == 0
    assert "--revise" not in calls[0]


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
    conn.execute("UPDATE tickets SET adw_id='run1' WHERE id='internal:abc'")
    conn.execute("INSERT INTO sessions (adw_id, status) VALUES ('run1', 'running')")
    conn.commit()
    conn.close()
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=True) == 1
    assert "live run" in capsys.readouterr().err


def _adb(root: Path) -> sqlite3.Connection:
    """The project db the flow writes (same as _db in commands/ticket)."""
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    ticketing.ensure_schema(conn)
    return conn


def _session_for_argv(
    root: Path, argv: list[str], status: str, adw_name: str = "adw_implement"
) -> None:
    """Simulate the ADW's host-side trace: the adw-id in the dispatch argv gets
    its session row (the real ADW writes it directly to the project db)."""
    adw_id = argv[argv.index("--adw-id") + 1]
    conn = _adb(root)
    conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES (?,?,?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
        (adw_id, adw_name, status),
    )
    conn.commit()
    conn.close()


def test_flow_implement_claims_ticket_before_the_run(tmp_path, monkeypatch):
    """The machine claim (ready-for-agent → in-progress) and the run-history
    link exist BEFORE the run spawns — a mid-run read sees in-progress, and
    the sandbox monitor can settle the ticket later."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:abc")
    calls: list[list[str]] = []
    seen_in_progress: list[bool] = []

    def fake_call(argv, **kw):
        # while "running", the ticket must already read in-progress
        conn = _adb(root)
        seen_in_progress.append(
            conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone()[0]
        )
        conn.close()
        _session_for_argv(root, argv, "success")
        calls.append(argv)
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=True) == 0
    assert seen_in_progress == ["in-progress"]
    conn = _adb(root)
    row = conn.execute("SELECT status, adw_id FROM tickets WHERE id='internal:abc'").fetchone()
    runs = conn.execute("SELECT adw_id FROM ticket_runs WHERE ticket_id='internal:abc'").fetchall()
    conn.close()
    # success → the run settles the machine to ready-for-signoff
    assert row == ("ready-for-signoff", calls[0][calls[0].index("--adw-id") + 1])
    assert [r[0] for r in runs] == [row[1]]  # the run history carries the attempt


def test_flow_implement_no_sandbox_failure_requeues_with_feedback(tmp_path, monkeypatch):
    """A failed no-sandbox run returns the ticket to ready-for-agent with the
    reviewer's feedback attached (fix-forward)."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:abc")
    calls: list[list[str]] = []

    def fake_call(argv, **kw):
        adw_id = argv[argv.index("--adw-id") + 1]
        conn = _adb(root)
        conn.execute(
            "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
            " VALUES (?,?,?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
            (adw_id, "adw_implement", "fail"),
        )
        conn.execute(
            "INSERT INTO envelopes (envelope_id, adw_id, agent, output_type, payload_json,"
            " valid, attempt, created_at) VALUES (?,?, 'reviewer', 'ReviewOutput',"
            ' \'{"approved": false, "blocking": ["move the button above the fold"]}\','
            " 1, 1, '2026-09-01T01:00:00+00:00')",
            ("env1", adw_id),
        )
        conn.commit()
        conn.close()
        calls.append(argv)
        return 1

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=True) == 1
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    conn.close()
    assert row[0] == "ready-for-agent"
    assert "move the button above the fold" in row[1]


def test_flow_implement_sandbox_spawn_failure_requeues(tmp_path, monkeypatch):
    """A sandbox spawn that fails before the ADW starts must not leave the
    ticket stuck in in-progress — the claim is requeued fix-forward."""
    root = _setup_project(tmp_path, monkeypatch)  # sandbox enabled by default
    _seed_ticket(root, "internal:abc")
    monkeypatch.setattr(flow, "_dispatch_chain", lambda *a, **k: 1)  # spawn failed
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=False) == 1
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    conn.close()
    assert row[0] == "ready-for-agent"
    assert "sandbox spawn failed" in row[1]


def test_flow_implement_forwards_adw_id_in_no_sandbox_dispatch(tmp_path, monkeypatch):
    """The no-sandbox dispatch must pin the run's adw-id (--adw-id) so the
    ticket link points at the run that actually executes — today the ADW would
    mint its own and the link would dangle."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:abc")
    calls = _capture_call(monkeypatch)
    assert flow.implement(Path.cwd(), "internal:abc", None, no_sandbox=True) == 0
    assert "--adw-id" in calls[0]
    conn = _adb(root)
    linked = conn.execute("SELECT adw_id FROM tickets WHERE id='internal:abc'").fetchone()[0]
    conn.close()
    assert linked == calls[0][calls[0].index("--adw-id") + 1]


def test_flow_implement_missing_ticket_is_loud(tmp_path, monkeypatch, capsys):
    _root = _setup_project(tmp_path, monkeypatch)
    assert flow.implement(Path.cwd(), "internal:nope", None, no_sandbox=True) == 1
    assert "no ticket internal:nope" in capsys.readouterr().err


# ── deploy (#96): batch-level release train + workbench signoff ────────────

# A fake docker on PATH: every docker verb succeeds; `port` resolves the
# workbench's random host port. The tests cross the same PATH seam the
# real docker shim does (resolved per call).
_DOCKER_SHIM = "#!/usr/bin/env python3\nimport sys\nif sys.argv[1:] and sys.argv[1] == 'port':\n    print('0.0.0.0:41234')\nsys.exit(0)\n"


def _deploy_project(
    tmp_path, monkeypatch, *, ticket="internal:abc", dev_commit="feat: dark mode (#internal:abc)"
):
    """A project with main + dev branches; one ready-for-signoff ticket whose
    commit is on dev, its run recorded, the workbench config stamped, and a
    fake docker on PATH (bring_up/tear_down cross the PATH seam)."""
    import subprocess

    root = _setup_project(tmp_path, monkeypatch)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
        ["git", "checkout", "-q", "-b", "dev"],
    ):
        subprocess.run(cmd, cwd=root, check=True)
    (root / "feature.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", dev_commit], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=root, check=True)
    _seed_ticket(root, ticket, status="ready-for-signoff")
    conn = _adb(root)
    conn.execute(
        "INSERT INTO ticket_runs (ticket_id, adw_id, created_at)"
        " VALUES (?, 'r1', '2026-09-01T00:00:00+00:00')",
        (ticket,),
    )
    conn.commit()
    conn.close()
    (root / "adws" / "config" / "deploy.yaml").write_text(
        "version_files:\n  - pyproject.toml\n"
        'workbench:\n  command: ["bun", "run", "dev"]\n  container_port: 3000\n'
    )
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    shim = fake / "docker"
    shim.write_text(_DOCKER_SHIM)
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
    return root


def _workbench_rows(root: Path) -> list[tuple]:
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    rows = conn.execute(
        "SELECT adw_id, container, host_port, url, status FROM workbench_runs"
    ).fetchall()
    conn.close()
    return rows


def test_flow_deploy_brings_up_workbench_and_approves_batch(tmp_path, monkeypatch):
    """Approve at the signoff: the workbench is up (recorded with the
    published URL), the release-train chain runs with a pinned adw-id, and the
    batch's ready-for-signoff ticket moves through ready-to-deploy and closes
    with the release (close-by-commits, issue #97 — the dev commit references
    the ticket)."""
    root = _deploy_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    # the workbench came up from dev and was recorded
    rows = _workbench_rows(root)
    assert len(rows) == 1
    assert rows[0][1].startswith("sssf-wb-")  # container
    assert rows[0][2] == 41234  # host port resolved from the fake docker
    assert rows[0][3] == "http://127.0.0.1:41234"
    assert rows[0][4] == "up"
    # the release train ran as the adw_deploy chain with a pinned adw-id
    assert len(calls) == 1
    assert calls[0][1].endswith("adw_deploy.py")
    assert "--adw-id" in calls[0]
    # the batch settled: ready-for-signoff -> ready-to-deploy, then closed by
    # the release commit set (the dev commit references the ticket)
    conn = _adb(root)
    row = conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone()
    assert row == ("done",)
    conn.close()


def test_flow_deploy_yes_autoapproves_without_prompt(tmp_path, monkeypatch):
    """--yes never asks: the signoff, the canary gate, and the promote gate
    all auto-confirm (the explicit automation escape), and the batch closes
    with the release."""
    root = _deploy_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "n")
    assert flow.deploy(Path.cwd(), None, yes=True) == 0
    assert prompts == []  # --yes never asks
    assert len(calls) == 1
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "done",
    )
    conn.close()


def test_flow_deploy_rejection_requeues_and_tears_down(tmp_path, monkeypatch):
    """A 'no' at the signoff re-queues the failing tickets fix-forward with
    the batch verdict and tears the workbench down ("rebuilt" by the next
    run) — and returns 0, because rejection is the expected outcome."""
    root = _deploy_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    answers = iter(["n", ""])  # reject, then blank = the whole batch failed
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    assert calls == []  # no release train ran
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    assert row[0] == "ready-for-agent"
    assert "batch rejected at signoff" in row[1]
    ev = ticketing.ticket_events(conn, "internal:abc")[-1]
    assert ev["payload"]["to"] == "ready-for-agent"
    conn.close()
    # the workbench was torn down (record marked down; container removed)
    rows = _workbench_rows(root)
    assert rows and rows[0][4] == "down"


def test_flow_deploy_rejection_with_picked_tickets_only_requeues_them(tmp_path, monkeypatch):
    """The operator names the failing tickets at rejection: only those
    re-queue; the rest of the batch stays ready-for-signoff for the next run."""
    root = _deploy_project(tmp_path, monkeypatch)
    conn = _adb(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status, kind)"
        " VALUES ('internal:def', 'internal', '', 'Another', 'ready-for-signoff',"
        " 'implementation')"
    )
    conn.commit()
    conn.close()
    answers = iter(["n", "internal:abc"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "ready-for-agent",
    )
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:def'").fetchone() == (
        "ready-for-signoff",
    )  # not named — not requeued
    conn.close()


def test_flow_deploy_no_dev_branch_is_loud(tmp_path, monkeypatch, capsys):
    _setup_project(tmp_path, monkeypatch)
    assert flow.deploy(Path.cwd(), None, yes=True) == 1
    assert "no dev integration branch" in capsys.readouterr().err


def test_flow_deploy_nothing_beyond_main_is_loud(tmp_path, monkeypatch, capsys):
    """dev exists but is not ahead of main: there is no batch to ship."""
    import subprocess

    root = _setup_project(tmp_path, monkeypatch)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
        ["git", "checkout", "-q", "-b", "dev"],
        ["git", "checkout", "-q", "main"],
    ):
        subprocess.run(cmd, cwd=root, check=True)
    assert flow.deploy(Path.cwd(), None, yes=True) == 1
    assert "nothing to deploy" in capsys.readouterr().err


def test_flow_deploy_down_tears_the_workbench(tmp_path, monkeypatch, capsys):
    """`sssf flow deploy --down` is the human's teardown (ADR-0004): the
    container is removed and the record flips to down; with nothing to tear
    down it is loud."""
    root = _deploy_project(tmp_path, monkeypatch)
    calls = _capture_call(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    calls.clear()
    assert flow.deploy_down(Path.cwd(), None) == 0
    assert "torn down" in capsys.readouterr().out
    assert _workbench_rows(root)[0][4] == "down"
    # a second teardown has nothing to remove
    assert flow.deploy_down(Path.cwd(), None) == 1


def test_flow_deploy_revert_removes_the_ticket_from_dev(tmp_path, monkeypatch):
    """The revert escape hatch: the ticket's own commits (message references
    the ticket id) are reverted on dev and the ticket comes back
    ready-for-agent fix-forward — the next deploy ships the clean snapshot."""
    import subprocess

    root = _deploy_project(tmp_path, monkeypatch)
    assert flow.deploy_revert(Path.cwd(), "internal:abc", None) == 0
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    assert row[0] == "ready-for-agent"
    assert "reverted from dev" in row[1]
    conn.close()
    # the dev snapshot no longer carries the ticket's change
    r = subprocess.run(
        ["git", "-C", str(root), "show", "dev:feature.txt"],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0  # the file is gone from dev after the revert
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline", "dev"],
        capture_output=True,
        text=True,
    )
    assert "Revert" in log.stdout  # the revert commit landed on dev


def test_flow_deploy_revert_without_commits_is_loud(tmp_path, monkeypatch, capsys):

    _deploy_project(tmp_path, monkeypatch, dev_commit="feat: unrelated (#internal:xyz)")
    assert flow.deploy_revert(Path.cwd(), "internal:abc", None) == 1
    assert "nothing to revert" in capsys.readouterr().err


def test_flow_deploy_workbench_failure_is_loud(tmp_path, monkeypatch, capsys):
    """A workbench that cannot come up (no `workbench:` config) stops the
    deploy before any machine write — never a silent skip."""

    root = _deploy_project(tmp_path, monkeypatch)
    (root / "adws" / "config" / "deploy.yaml").write_text("version_files:\n  - pyproject.toml\n")
    assert flow.deploy(Path.cwd(), None, yes=True) == 1
    assert "workbench" in capsys.readouterr().err
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "ready-for-signoff",
    )  # nothing moved
    conn.close()


def test_flow_deploy_failed_release_train_requeues(tmp_path, monkeypatch):
    """The batch was approved but the release train itself failed (e2e red,
    MR refused): the batch re-queues fix-forward with the run's feedback —
    approval is not a guarantee."""
    root = _deploy_project(tmp_path, monkeypatch)

    def failing_call(argv, **kw):
        return 1

    monkeypatch.setattr(flow.subprocess, "call", failing_call)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 1
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    assert row[0] == "ready-for-agent"
    conn.close()


# ── release gates + close-by-commits (issue #97) ────────────────────────────

_RELEASE_YAML = (
    "version_files:\n  - pyproject.toml\n"
    'workbench:\n  command: ["bun", "run", "dev"]\n  container_port: 3000\n'
    "release:\n"
    "  canary:\n"
    '    command: ["tompero", "deployment", "canary", "start", "--service", "demo"]\n'
    "  promote:\n"
    '    command: ["tompero", "deployment", "canary", "promote", "--service", "demo"]\n'
    '    status_command: ["tompero", "deployment", "get", "--service", "demo", "-O", "json"]\n'

    '    promoted_match: "promoted"\n'
    "    poll_interval_s: 0\n"
    "    poll_timeout_s: 60\n"
)


class _FakeRun:
    """A fake subprocess.run result for the release gates."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _release_project(tmp_path, monkeypatch, release_yaml=_RELEASE_YAML):
    """The deploy fixture with a `release:` block in deploy.yaml (canary +
    promote commands and the promote poll settings)."""
    root = _deploy_project(tmp_path, monkeypatch)
    (root / "adws" / "config" / "deploy.yaml").write_text(release_yaml)
    return root


def _fake_release_run(ran: list[list[str]], handler):
    """A subprocess.run fake that passes through everything except the
    release commands (tompero …): git/docker queries must run for real, only
    the canary/promote/status commands are faked per-test."""
    real_run = flow.subprocess.run

    def fake_run(argv, **kw):
        if argv and argv[0] == "tompero":
            ran.append(argv)
            return handler(argv)
        return real_run(argv, **kw)

    return fake_run


def test_flow_deploy_canary_runs_only_after_confirmation(tmp_path, monkeypatch):
    """The canary step runs only after the operator's terminal confirmation
    (issue #97 AC1): a 'y' runs the per-project canary command; the batch then
    flows on to promote and close-by-commits."""
    root = _release_project(tmp_path, monkeypatch)
    _capture_call(monkeypatch)  # fakes the release-train chain's subprocess.call
    ran: list[list[str]] = []

    def handler(argv):
        return _FakeRun(stdout='{"deployment": {"status": "promoted"}}')

    monkeypatch.setattr(flow.subprocess, "run", _fake_release_run(ran, handler))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    # the canary command ran first, then promote, then the status poll
    # (the canary/promote commands share the `tompero deployment canary`
    #  prefix — the verb at argv[3] distinguishes them; the poll is `get`)
    assert [a[3] for a in ran] == ["start", "promote", "--service"]
    assert ran[0][1:] == ["deployment", "canary", "start", "--service", "demo"]
    # the batch closed with the release (close-by-commits matched the dev commit)
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "done",
    )
    conn.close()


def test_flow_deploy_canary_skipped_without_confirmation(tmp_path, monkeypatch, capsys):
    """A 'n' at the canary prompt means the release pauses: no canary, no
    promote, no close — the approved tickets stay ready-to-deploy (the MR is
    registered and the monitor watches it)."""
    root = _release_project(tmp_path, monkeypatch)
    _capture_call(monkeypatch)
    ran: list[list[str]] = []

    def handler(argv):
        return _FakeRun()

    monkeypatch.setattr(flow.subprocess, "run", _fake_release_run(ran, handler))
    answers = iter(["y", "n"])  # approve at the signoff, decline the canary
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    assert ran == []  # neither canary nor promote ran
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "ready-to-deploy",
    )
    conn.close()


def test_flow_deploy_canary_failure_parks_blocked(tmp_path, monkeypatch):
    """A canary failure parks the batch's tickets in `blocked` (issue #97
    AC2) — visible and actionable, never silently retried — with the command's
    stderr as fix-forward feedback."""
    root = _release_project(tmp_path, monkeypatch)
    _capture_call(monkeypatch)

    def handler(argv):
        if argv[3] == "start":
            return _FakeRun(returncode=1, stderr="crash loop on canary")
        return _FakeRun(stdout='{"status": "promoted"}')

    monkeypatch.setattr(flow.subprocess, "run", _fake_release_run([], handler))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 1
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    assert row[0] == "blocked"
    assert "crash loop on canary" in row[1]
    conn.close()


def test_flow_deploy_promote_polls_until_fully_promoted(tmp_path, monkeypatch):
    """Promote executes the tompero canary-promote command only after
    confirmation, then polls deployment status until fully promoted (issue
    #97 AC3): the status command is polled until its output carries the
    promoted match, then the release closes the batch."""
    root = _release_project(tmp_path, monkeypatch)
    _capture_call(monkeypatch)
    ran: list[list[str]] = []

    def handler(argv):
        if argv[3] == "promote":
            return _FakeRun()
        return _FakeRun(stdout='{"deployment": {"status": "promoted"}}')

    monkeypatch.setattr(flow.subprocess, "run", _fake_release_run(ran, handler))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    # promote command + status polls; the promote command ran after the canary
    assert [a[3] for a in ran] == ["start", "promote", "--service"]
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "done",
    )
    conn.close()


def test_flow_deploy_promote_timeout_parks_blocked(tmp_path, monkeypatch):
    """A promote that never reaches fully-promoted within the poll timeout
    parks the batch blocked too — the release is not silently abandoned."""
    root = _release_project(tmp_path, monkeypatch)
    _capture_call(monkeypatch)
    ran: list[list[str]] = []
    calls = {"n": 0}

    def fake_monotonic():
        # call 1 = deadline anchor (0), then each loop check advances 20s:
        # 20, 40, 60 -> expires after two polls (poll_interval_s 0 => tight)
        calls["n"] += 1
        return {1: 0.0, 2: 20.0, 3: 40.0, 4: 60.0}.get(calls["n"], 999.0)

    monkeypatch.setattr(flow.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)

    def handler(argv):
        if argv[3] == "promote":
            return _FakeRun()
        return _FakeRun(stdout='{"deployment": {"status": "starting"}}')

    monkeypatch.setattr(flow.subprocess, "run", _fake_release_run(ran, handler))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 1
    assert len([a for a in ran if a[2] == "get"]) > 1  # it polled, never matched
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:abc'"
    ).fetchone()
    assert row[0] == "blocked"
    assert "promoted" in row[1]
    conn.close()


def test_flow_deploy_without_release_block_skips_gates_but_closes(tmp_path, monkeypatch, capsys):
    """No `release:` block in deploy.yaml: the canary/promote gates are
    skipped (the tompero commands are per-project — a project without a
    deployment pipeline still gets the release train), but close-by-commits
    still closes the batch by the MR's commit set."""
    root = _deploy_project(tmp_path, monkeypatch)
    _capture_call(monkeypatch)
    ran: list[list[str]] = []

    def handler(argv):
        return _FakeRun()

    monkeypatch.setattr(flow.subprocess, "run", _fake_release_run(ran, handler))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert flow.deploy(Path.cwd(), None, yes=False) == 0
    assert ran == []  # no release commands configured — nothing ran
    conn = _adb(root)
    assert conn.execute("SELECT status FROM tickets WHERE id='internal:abc'").fetchone() == (
        "done",
    )
    conn.close()


# ── shared dispatch ──────────────────────────────────────────────────────────


def test_flow_no_project_is_loud(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no adws/ anywhere up the tree
    assert flow.plan(Path.cwd(), None, None, False, no_sandbox=True) == 1
    assert "no project here" in capsys.readouterr().err


def test_flow_sandboxed_dispatch_uses_the_sandbox(tmp_path, monkeypatch):
    """No `sandbox:` key in the config → sandboxed by default: the spawn goes
    through _run_sandboxed, never a bare subprocess call (the plan flow's
    dispatch — deploy is host-side by design since #96)."""
    _root = _setup_project(tmp_path, monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(flow, "_run_sandboxed", lambda *a, **k: captured.update(a=a) or 0)
    assert flow.plan(Path.cwd(), None, None, skip_exploration=False, no_sandbox=False) == 0
    assert captured["a"][1].name == "adw_plan.py"


def test_flow_warns_on_legacy_layout(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws" / "adw_data").mkdir(parents=True)  # v1 layout marker
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    assert flow.plan(root, None, None, False, no_sandbox=True) != 0
    assert "legacy adws layout" in capsys.readouterr().err
