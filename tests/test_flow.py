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
                 title: str = "Dark mode", description: str = "Make it dark",
                 kind: str = "implementation") -> None:
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
    _seed_ticket(root, "internal:abc", status="needs-triage", kind="idea",
                 title="Dark mode", description="Make it dark")
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
        seen_link.append(conn.execute(
            "SELECT adw_id FROM tickets WHERE id='internal:idea'"
        ).fetchone()[0])
        conn.close()
        calls.append(argv)
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.plan(Path.cwd(), "internal:idea", None, False, no_sandbox=True) == 0
    linked = calls[0][calls[0].index("--adw-id") + 1]
    assert seen_link == [linked]  # mid-run read sees the link
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    row = conn.execute(
        "SELECT adw_id FROM tickets WHERE id='internal:idea'"
    ).fetchone()
    runs = conn.execute(
        "SELECT adw_id FROM ticket_runs WHERE ticket_id='internal:idea'"
    ).fetchall()
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
    row = conn.execute(
        "SELECT spec FROM tickets WHERE id='internal:idea'"
    ).fetchone()
    children = conn.execute(
        "SELECT title FROM tickets WHERE parent_id='internal:idea'"
    ).fetchall()
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
    row = conn.execute(
        "SELECT spec, status FROM tickets WHERE id='internal:idea'"
    ).fetchone()
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
    status = conn.execute(
        "SELECT status FROM tickets WHERE id='internal:idea'"
    ).fetchone()[0]
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
    parents = conn.execute(
        "SELECT id, title, kind FROM tickets WHERE kind='idea'"
    ).fetchall()
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
    conn.execute(
        "UPDATE tickets SET adw_id='run1' WHERE id='internal:abc'"
    )
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


def _session_for_argv(root: Path, argv: list[str], status: str, adw_name: str = "adw_implement") -> None:
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
    runs = conn.execute(
        "SELECT adw_id FROM ticket_runs WHERE ticket_id='internal:abc'"
    ).fetchall()
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
            " '{\"approved\": false, \"blocking\": [\"move the button above the fold\"]}',"
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
    linked = conn.execute(
        "SELECT adw_id FROM tickets WHERE id='internal:abc'"
    ).fetchone()[0]
    conn.close()
    assert linked == calls[0][calls[0].index("--adw-id") + 1]


def test_flow_implement_missing_ticket_is_loud(tmp_path, monkeypatch, capsys):
    _root = _setup_project(tmp_path, monkeypatch)
    assert flow.implement(Path.cwd(), "internal:nope", None, no_sandbox=True) == 1
    assert "no ticket internal:nope" in capsys.readouterr().err


# ── implement afk: the unattended queue loop (#93) ──────────────────────────


def test_flow_implement_afk_iterates_the_queue_to_empty(tmp_path, monkeypatch, capsys):
    """afk works the ready-for-agent queue one ticket per round; every round
    settles before the next (no-sandbox rounds settle synchronously), and an
    empty queue ends the loop with 0."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    for tid in ("internal:a", "internal:b", "internal:c"):
        _seed_ticket(root, tid, title=tid)
    calls: list[list[str]] = []
    adw_ids: list[str] = []

    def fake_call(argv, **kw):
        _session_for_argv(root, argv, "success")
        adw_ids.append(argv[argv.index("--adw-id") + 1])
        calls.append(argv)
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.implement_afk(
        Path.cwd(), cap=30, wait_seconds=60, no_sandbox=True
    ) == 0
    assert len(calls) == 3  # one round per ticket
    assert len(set(adw_ids)) == 3  # each round is a fresh run / context window
    conn = _adb(root)
    rows = conn.execute("SELECT id, status FROM tickets ORDER BY id").fetchall()
    conn.close()
    assert rows == [
        ("internal:a", "ready-for-signoff"),
        ("internal:b", "ready-for-signoff"),
        ("internal:c", "ready-for-signoff"),
    ]
    out = capsys.readouterr().out
    assert "round 1/30" in out and "round 3/30" in out
    assert "queue empty after 3 round(s)" in out


def test_flow_implement_afk_empty_queue_terminates_immediately(tmp_path, monkeypatch, capsys):
    _root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    calls = _capture_call(monkeypatch)
    assert flow.implement_afk(
        Path.cwd(), cap=30, wait_seconds=60, no_sandbox=True
    ) == 0
    assert calls == []  # nothing spawned
    assert "queue empty after 0 round(s)" in capsys.readouterr().out


def test_flow_implement_afk_cap_stops_the_loop(tmp_path, monkeypatch, capsys):
    """--cap bounds ROUNDS: the loop stops at the cap with work still queued
    (exit 0 — the operator re-runs afk to continue)."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    for tid in ("internal:a", "internal:b", "internal:c", "internal:d", "internal:e"):
        _seed_ticket(root, tid)
    calls: list[list[str]] = []

    def fake_call(argv, **kw):
        _session_for_argv(root, argv, "success")
        calls.append(argv)
        return 0

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.implement_afk(
        Path.cwd(), cap=2, wait_seconds=60, no_sandbox=True
    ) == 0
    assert len(calls) == 2
    conn = _adb(root)
    done = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status='ready-for-signoff'"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status='ready-for-agent'"
    ).fetchone()[0]
    conn.close()
    assert (done, pending) == (2, 3)  # only the cap's rounds ran
    assert "cap reached at 2 round(s)" in capsys.readouterr().out


def test_flow_implement_afk_failed_round_requeues_and_is_retried(tmp_path, monkeypatch):
    """A failed round returns the ticket to ready-for-agent with feedback; the
    next round re-picks it (fix-forward retry), bounded by the cap — and every
    attempt stays in the ticket's run history."""
    root = _setup_project(tmp_path, monkeypatch, sandbox_key=True)
    _seed_ticket(root, "internal:a")
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
            " '{\"approved\": false, \"blocking\": [\"fix the redirect\"]}',"
            " 1, 1, '2026-09-01T01:00:00+00:00')",
            (f"env1-{adw_id}", adw_id),
        )
        conn.commit()
        conn.close()
        calls.append(argv)
        return 1

    monkeypatch.setattr(flow.subprocess, "call", fake_call)
    assert flow.implement_afk(
        Path.cwd(), cap=3, wait_seconds=60, no_sandbox=True
    ) == 0
    assert len(calls) == 3  # the requeued ticket is picked again each round
    conn = _adb(root)
    row = conn.execute(
        "SELECT status, rejection_feedback FROM tickets WHERE id='internal:a'"
    ).fetchone()
    runs = conn.execute(
        "SELECT COUNT(*) FROM ticket_runs WHERE ticket_id='internal:a'"
    ).fetchone()[0]
    conn.close()
    assert row[0] == "ready-for-agent"
    assert "fix the redirect" in row[1]
    assert runs == 3  # every attempt is in the run history


def test_flow_implement_afk_sandboxed_waits_for_the_settle(tmp_path, monkeypatch):
    """Sandboxed rounds spawn detached and settle in the monitor; afk must
    not dispatch the next round until the machine settle lands — one ticket
    live at a time (no concurrent sandboxes)."""
    root = _setup_project(tmp_path, monkeypatch)  # sandbox enabled (default)
    _seed_ticket(root, "internal:a")
    _seed_ticket(root, "internal:b")
    seen: list[tuple[str, list[str]]] = []

    def fake_dispatch(root_, adw_name, prompt, extra, no_sandbox, adw_id=None):
        conn = _adb(root_)
        ticket_id = conn.execute(
            "SELECT id FROM tickets WHERE adw_id=?", (adw_id,)
        ).fetchone()[0]
        # exactly the round's own claim may be in-progress at spawn time — a
        # previous round's ticket must already be settled
        in_progress = sorted(
            r[0] for r in conn.execute(
                "SELECT id FROM tickets WHERE status='in-progress'"
            ).fetchall()
        )
        conn.execute(
            "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
            " VALUES (?,?,?, '2026-09-01T00:00:00+00:00', '2026-09-01T01:00:00+00:00')",
            (adw_id, "adw_implement", "success"),
        )
        conn.commit()
        conn.close()
        # simulate the monitor's settle: success → ready-for-signoff
        conn = _adb(root_)
        ticketing.transition_ticket(
            conn, ticket_id, ticketing.STATUS_SIGNOFF, actor="test"
        )
        conn.commit()
        conn.close()
        seen.append((ticket_id, in_progress))
        return 0

    monkeypatch.setattr(flow, "_dispatch_chain", fake_dispatch)
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)
    assert flow.implement_afk(
        Path.cwd(), cap=30, wait_seconds=60, no_sandbox=False
    ) == 0
    # each round spawned with only its own claim in-progress — the previous
    # round had settled before the next dispatch
    assert seen == [("internal:a", ["internal:a"]), ("internal:b", ["internal:b"])]
    conn = _adb(root)
    rows = conn.execute("SELECT id, status FROM tickets ORDER BY id").fetchall()
    conn.close()
    assert rows == [
        ("internal:a", "ready-for-signoff"),
        ("internal:b", "ready-for-signoff"),
    ]


def test_flow_implement_afk_sandboxed_wait_timeout_is_loud(tmp_path, monkeypatch, capsys):
    """A sandboxed run that never settles must not hang the loop forever: the
    per-round wait times out, afk exits 1, and no further round is dispatched
    on top of the live run."""
    root = _setup_project(tmp_path, monkeypatch)  # sandbox enabled (default)
    _seed_ticket(root, "internal:a")
    _seed_ticket(root, "internal:b")
    spawned: list[str] = []

    def fake_dispatch(root_, adw_name, prompt, extra, no_sandbox, adw_id=None):
        conn = _adb(root_)
        ticket_id = conn.execute(
            "SELECT id FROM tickets WHERE adw_id=?", (adw_id,)
        ).fetchone()[0]
        conn.close()
        spawned.append(ticket_id)
        return 0  # the run never settles (no session row, ticket stays in-progress)

    monkeypatch.setattr(flow, "_dispatch_chain", fake_dispatch)
    monkeypatch.setattr(flow.time, "sleep", lambda s: None)
    assert flow.implement_afk(
        Path.cwd(), cap=30, wait_seconds=1, no_sandbox=False
    ) == 1
    assert spawned == ["internal:a"]  # never stacked round 2 on the live run
    assert "still live after 1s" in capsys.readouterr().err


def test_flow_implement_afk_sandboxed_spawn_failure_aborts(tmp_path, monkeypatch, capsys):
    """A sandboxed spawn failure means the run never started — afk aborts
    instead of burning the cap retrying the same broken environment."""
    root = _setup_project(tmp_path, monkeypatch)  # sandbox enabled (default)
    _seed_ticket(root, "internal:a")
    _seed_ticket(root, "internal:b")

    def fake_dispatch(root_, adw_name, prompt, extra, no_sandbox, adw_id=None):
        return 1  # sandbox spawn failed → implement() requeues the claim

    monkeypatch.setattr(flow, "_dispatch_chain", fake_dispatch)
    assert flow.implement_afk(
        Path.cwd(), cap=30, wait_seconds=60, no_sandbox=False
    ) == 1
    assert "did not start" in capsys.readouterr().err
    conn = _adb(root)
    row = conn.execute(
        "SELECT status FROM tickets WHERE id='internal:a'"
    ).fetchone()
    conn.close()
    assert row[0] == "ready-for-agent"  # the claim was requeued, not stuck


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
