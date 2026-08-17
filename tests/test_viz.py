import contextlib
import subprocess

from sssf.commands import misc, viz


def test_viz_start_rejects_missing_bun(monkeypatch):
    monkeypatch.setattr(misc, "which", lambda name: None)
    assert viz.start(4600, None, None) == 1


class _FakeProc:
    def __init__(self, pid: int):
        self.pid = pid


def _health_down(monkeypatch) -> None:
    def _down(_url, timeout=0):
        raise OSError("down")

    monkeypatch.setattr(viz.urllib.request, "urlopen", _down)


def test_viz_start_spawns_writes_pid_and_opens_browser(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/bun")
    monkeypatch.setattr(viz, "_pid_file", lambda: tmp_path / "viz.pid")
    monkeypatch.setattr(viz, "_log_file", lambda: tmp_path / "viz.log")
    monkeypatch.setattr(viz, "_pid_alive", lambda pid: True)
    spawned: dict = {}

    def _popen(*_a, **_k):
        spawned["called"] = True
        return _FakeProc(4242)

    monkeypatch.setattr(viz.subprocess, "Popen", _popen)
    opened: list[str] = []
    monkeypatch.setattr(viz.webbrowser, "open", lambda url: opened.append(url))
    _health_down(monkeypatch)

    assert viz.start(4600, None, None) == 0
    out = capsys.readouterr().out
    assert "started (pid 4242)" in out
    assert spawned.get("called")
    assert (tmp_path / "viz.pid").read_text() == "4242"
    assert opened == ["http://localhost:4600"]


def test_viz_start_reports_spawn_failure_and_opens_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/bun")
    monkeypatch.setattr(viz, "_pid_file", lambda: tmp_path / "viz.pid")
    monkeypatch.setattr(viz, "_log_file", lambda: tmp_path / "viz.log")
    monkeypatch.setattr(viz, "_pid_alive", lambda pid: False)  # process died at startup
    monkeypatch.setattr(viz.subprocess, "Popen", lambda *a, **k: _FakeProc(7777))
    opened: list[str] = []
    monkeypatch.setattr(viz.webbrowser, "open", lambda url: opened.append(url))
    _health_down(monkeypatch)

    assert viz.start(4600, None, None) == 1
    err = capsys.readouterr().err
    assert "exited during startup" in err
    assert opened == []  # no browser on failure
    assert not (tmp_path / "viz.pid").exists()  # stale pid cleaned up


def test_viz_start_already_running_opens_browser(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(misc, "which", lambda name: "/usr/bin/bun")
    monkeypatch.setattr(viz, "_pid_file", lambda: tmp_path / "viz.pid")
    (tmp_path / "viz.pid").write_text("9999")
    monkeypatch.setattr(viz, "_pid_alive", lambda pid: True)
    opened: list[str] = []
    monkeypatch.setattr(viz.webbrowser, "open", lambda url: opened.append(url))
    _health_down(monkeypatch)

    assert viz.start(4600, None, None) == 0
    out = capsys.readouterr().out
    assert "already running (pid 9999)" in out
    assert opened == ["http://localhost:4600"]


def test_viz_stop_when_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(viz, "_pid_file", lambda: tmp_path / "viz.pid")
    assert viz.stop() == 0
    assert "not running" in capsys.readouterr().out


def test_viz_stop_kills_server_and_cleans_pid(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(viz, "_pid_file", lambda: tmp_path / "viz.pid")
    proc = subprocess.Popen(["sleep", "100"])  # stands in for the bun server
    (tmp_path / "viz.pid").write_text(str(proc.pid))

    assert viz.stop() == 0
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=3)
    assert proc.poll() is not None, "server process was not terminated"
    assert not (tmp_path / "viz.pid").exists()
    assert "stopped" in capsys.readouterr().out


def test_wait_for_server_true_when_health_answers(monkeypatch):
    class _FakeResp:
        def close(self):
            pass

    monkeypatch.setattr(viz.urllib.request, "urlopen", lambda url, timeout=0: _FakeResp())
    assert viz._wait_for_server("http://localhost:4600", tries=2, interval=0) is True


def test_wait_for_server_false_when_down(monkeypatch):
    def _down(_url, timeout=0):
        raise OSError("down")

    monkeypatch.setattr(viz.urllib.request, "urlopen", _down)
    assert viz._wait_for_server("http://localhost:4600", tries=2, interval=0) is False
