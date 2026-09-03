"""Container-side supervisor: runs the ADW command, then the project's review
command, then idles — the container never exits on its own, and the run's end
is signalled by an exit marker the host monitor can see through the bind mount.
"""

import sssf.adw_modules.supervise as sv


def test_supervise_runs_adw_then_review_and_marks_exit(tmp_path, monkeypatch):

    calls: list[list[str]] = []

    def fake_call(argv, **kwargs):
        calls.append(argv)
        return 7 if argv == ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"] else 0

    monkeypatch.setattr(sv, "_call", fake_call)
    monkeypatch.setattr(sv, "_idle", lambda: None)  # do not sleep forever
    data_dir = tmp_path / "adws" / "data"
    (data_dir / "sessions").mkdir(parents=True)

    sv.run(
        ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"],
        data_dir=data_dir,
        review_cmd=["npm", "run", "dev"],
    )

    assert calls == [
        ["python", "adws/modules/adw_simple_sdlc.py", "--adw-id", "abc1"],
        ["npm", "run", "dev"],
    ]
    marker = data_dir / "sessions" / "abc1.supervisor-exit"
    assert marker.read_text() == "7"  # the ADW's exit code is recorded


def test_supervise_no_review_command_still_marks_exit(tmp_path, monkeypatch):

    calls: list[list[str]] = []
    monkeypatch.setattr(sv, "_call", lambda argv, **k: calls.append(argv) or 0)
    monkeypatch.setattr(sv, "_idle", lambda: None)
    data_dir = tmp_path / "adws" / "data"
    sv.run(["echo", "hi"], data_dir=data_dir, review_cmd=None)
    assert calls == [["echo", "hi"]]
    assert (data_dir / "sessions").exists() is False  # no adw-id → no marker


def test_main_parses_dashdash_and_loads_review_config(tmp_path, monkeypatch):

    calls: list[list[str]] = []
    monkeypatch.setattr(sv, "_call", lambda argv, **k: calls.append(argv) or 0)
    monkeypatch.setattr(sv, "_idle", lambda: None)
    monkeypatch.setattr(sv, "sys", type("S", (), {"argv": ["supervise", "--", "python", "-c", "pass"]}))
    # config has a review command
    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text(
        "sandbox:\n  review:\n    command: [\"npm\", \"run\", \"dev\"]\n"
    )
    monkeypatch.chdir(tmp_path)

    import sssf.adw_modules.paths as paths

    monkeypatch.setattr(paths, "config_file", lambda root: cfg_file)
    sv.main()
    assert calls == [["python", "-c", "pass"], ["npm", "run", "dev"]]
