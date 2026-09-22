"""Container-side supervisor (ADR-0004): runs the ADW command, writes the
run's end marker, and EXITS with the ADW's code — the runner executes work
and ends. The deploy flow's QA surface is the workbench, not the runner.
"""

import sssf.adw_modules.supervise as sv


def test_supervise_runs_adw_marks_exit_and_returns_code(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_call(argv, **kwargs):
        calls.append(argv)
        return 7 if argv == ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"] else 0

    monkeypatch.setattr(sv, "_call", fake_call)
    data_dir = tmp_path / "adws" / "data"
    (data_dir / "sessions").mkdir(parents=True)

    rc = sv.run(
        ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"],
        data_dir=data_dir,
    )

    assert rc == 7  # the supervisor exits with the ADW's code — no idle
    assert calls == [["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"]]
    marker = data_dir / "sessions" / "abc1.supervisor-exit"
    assert marker.read_text() == "7"  # the ADW's exit code is recorded


def test_supervise_no_adw_id_skips_marker(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(sv, "_call", lambda argv, **k: calls.append(argv) or 0)
    data_dir = tmp_path / "adws" / "data"
    assert sv.run(["echo", "hi"], data_dir=data_dir) == 0
    assert calls == [["echo", "hi"]]
    assert (data_dir / "sessions").exists() is False  # no adw-id → no marker


def test_main_parses_dashdash_and_resolves_data_dir(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(sv, "_call", lambda argv, **k: calls.append(argv) or 0)
    monkeypatch.setattr(
        sv, "sys", type("S", (), {"argv": ["supervise", "--", "python", "-c", "pass"]})
    )
    # a legacy project whose stamped config still carries the dropped review
    # block must still supervise cleanly (pydantic ignores the unknown key)
    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text('sandbox:\n  review:\n    command: ["npm", "run", "dev"]\n')
    monkeypatch.chdir(tmp_path)

    import sssf.adw_modules.paths as paths

    monkeypatch.setattr(paths, "config_file", lambda root: cfg_file)
    assert sv.main() == 0
    assert calls == [["python", "-c", "pass"]]
