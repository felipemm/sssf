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

    monkeypatch.setattr(
        viz.misc, "which", lambda name: "/usr/local/bin/bun"
    )  # CI python job has no bun
    monkeypatch.setattr(viz, "_running_pid", lambda: None)
    monkeypatch.setattr(viz, "_spawn", lambda *a, **k: 99999)
    monkeypatch.setattr(viz, "_wait_for_server", lambda *a, **k: None)
    monkeypatch.setattr(viz, "_pid_alive", lambda *a, **k: True)
    monkeypatch.setattr(viz, "webbrowser", type("B", (), {"open": staticmethod(lambda u: True)})())
    import sssf.healer as healer_mod

    monkeypatch.setattr(
        healer_mod, "running_pid", lambda: None
    )  # prior tests may start the real daemon
    monkeypatch.setattr(healer_mod, "start", boom)
    viz.start(4600, None, None)
    assert "healer start failed" in capsys.readouterr().err


def test_doctor_lists_recent_spawn_failures(tmp_path, monkeypatch, capsys):
    """A recorded spawn-death surfaces its remediation hint in doctor."""
    from sssf.adw_modules.tracer import Tracer

    project = tmp_path / "proj"
    (project / "adws" / "data").mkdir(parents=True)
    tracer = Tracer(
        project / "adws" / "data" / "sssf.db",
        project / "adws" / "data" / "sessions" / "abc1" / "events.jsonl",
    )
    tracer.conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES ('abc1', 'adw_simple_sdlc (never started)', 'fail',"
        " '2026-08-18T00:00:00+00:00', '2026-08-18T00:00:01+00:00')"
    )
    tracer.conn.execute(
        "INSERT INTO events (event_id, adw_id, type, name, payload_json, started_at)"
        " VALUES ('evt1', 'abc1', 'error', 'sandbox spawn failure',"
        " '{\"exit_code\": \"2\", \"remediation\": \"commit the layout\"}',"
        " '2026-08-18T00:00:00+00:00')"
    )
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    # The spawn-failure listing is under test; main's project env checks
    # (ticketing/skills) are out of scope for a bare db fixture.
    monkeypatch.setattr(misc, "_doctor_project", lambda ok: ok)
    monkeypatch.chdir(project)
    assert misc.doctor() == 0
    out = capsys.readouterr().out
    assert "recent spawn failures" in out
    assert "abc1" in out
    assert "commit the layout" in out


def test_doctor_no_spawn_failures_is_quiet(tmp_path, monkeypatch, capsys):
    """No 'recent spawn failures' section when there is nothing to report."""
    project = tmp_path / "proj"
    (project / "adws" / "data").mkdir(parents=True)
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    # The spawn-failure listing is under test; main's project env checks
    # (ticketing/skills) are out of scope for a bare db fixture.
    monkeypatch.setattr(misc, "_doctor_project", lambda ok: ok)
    monkeypatch.chdir(project)
    assert misc.doctor() == 0
    assert "recent spawn failures" not in capsys.readouterr().out


def test_doctor_spawn_failure_uses_log_tail_fallback(tmp_path, monkeypatch, capsys):
    """Null remediation falls back to the container log tail (last 120 chars)."""
    import json

    from sssf.adw_modules.tracer import Tracer

    project = tmp_path / "proj"
    (project / "adws" / "data").mkdir(parents=True)
    tracer = Tracer(
        project / "adws" / "data" / "sssf.db",
        project / "adws" / "data" / "sessions" / "abc2" / "events.jsonl",
    )
    tracer.conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES ('abc2', 'adw_simple_sdlc (never started)', 'fail',"
        " '2026-08-18T00:00:00+00:00', '2026-08-18T00:00:01+00:00')"
    )
    tail = "tail-" + ("z" * 200)
    tracer.conn.execute(
        "INSERT INTO events (event_id, adw_id, type, name, payload_json, started_at)"
        " VALUES ('evt2', 'abc2', 'error', 'sandbox spawn failure', ?,"
        " '2026-08-18T00:00:00+00:00')",
        (json.dumps({"remediation": None, "container_log_tail": tail}),),
    )
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    # The spawn-failure listing is under test; main's project env checks
    # (ticketing/skills) are out of scope for a bare db fixture.
    monkeypatch.setattr(misc, "_doctor_project", lambda ok: ok)
    monkeypatch.chdir(project)
    assert misc.doctor() == 0
    out = capsys.readouterr().out
    assert "recent spawn failures" in out
    assert "abc2" in out
    # Rich wraps long lines; flatten whitespace so the excerpt is contiguous.
    out_flat = "".join(out.split())
    assert tail[-120:] in out_flat
    assert "tail-" not in out_flat  # only the last 120 chars are rendered


def test_doctor_spawn_failure_without_hint_is_labeled(tmp_path, monkeypatch, capsys):
    """Neither remediation nor log tail -> '(no hint classified)' is shown."""
    from sssf.adw_modules.tracer import Tracer

    project = tmp_path / "proj"
    (project / "adws" / "data").mkdir(parents=True)
    tracer = Tracer(
        project / "adws" / "data" / "sssf.db",
        project / "adws" / "data" / "sessions" / "abc3" / "events.jsonl",
    )
    tracer.conn.execute(
        "INSERT INTO sessions (adw_id, adw_name, status, started_at, ended_at)"
        " VALUES ('abc3', 'adw_simple_sdlc (never started)', 'fail',"
        " '2026-08-18T00:00:00+00:00', '2026-08-18T00:00:01+00:00')"
    )
    tracer.conn.execute(
        "INSERT INTO events (event_id, adw_id, type, name, payload_json, started_at)"
        " VALUES ('evt3', 'abc3', 'error', 'sandbox spawn failure',"
        " '{\"remediation\": null, \"container_log_tail\": null}',"
        " '2026-08-18T00:00:00+00:00')"
    )
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/" + name)
    # The spawn-failure listing is under test; main's project env checks
    # (ticketing/skills) are out of scope for a bare db fixture.
    monkeypatch.setattr(misc, "_doctor_project", lambda ok: ok)
    monkeypatch.chdir(project)
    assert misc.doctor() == 0
    out = capsys.readouterr().out
    assert "recent spawn failures" in out
    assert "abc3" in out
    assert "(no hint classified)" in out
