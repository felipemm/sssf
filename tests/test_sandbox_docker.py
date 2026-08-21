import os
import stat
import subprocess

import pytest

import sssf.sandbox as sandbox
from sssf.sandbox import SandboxError, build_image, docker_available, run_sandbox, stop_remove


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """A docker shim that records invocations and answers canned outputs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    shim = bin_dir / "docker"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"with open({str(calls)!r}, 'a') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if ' info' in ' '.join(sys.argv):\n"
        "    print('Server Version: 29.1.3')\n"
        "elif ' wait ' in ' '.join(sys.argv):\n"
        "    print('0')\n"
        "elif ' rm ' in ' '.join(sys.argv):\n"
        "    sys.exit(0)\n"
        "else:\n"
        "    sys.exit(0)\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


def test_docker_available(fake_docker):
    assert docker_available() is True


def test_build_image_calls_docker(fake_docker, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    build_image("sssf-runner", dockerfile)
    calls = fake_docker.read_text().splitlines()
    assert any("build" in c and "Dockerfile" in c for c in calls)


def test_build_image_tags_the_image(fake_docker, tmp_path):
    """docker build without -t leaves the image untagged — runs keep using the
    stale sssf-runner:latest and a rebuilt image never takes effect."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    build_image("sssf-runner:latest", dockerfile)
    calls = fake_docker.read_text().splitlines()
    build = next(c for c in calls if c.startswith("build"))
    assert "-t sssf-runner:latest" in build


def test_build_failure_raises(fake_docker, tmp_path, monkeypatch):
    bin_dir = fake_docker.parent / "bin"
    (bin_dir / "docker").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
    (bin_dir / "docker").chmod((bin_dir / "docker").stat().st_mode | stat.S_IEXEC)
    with pytest.raises(SandboxError):
        build_image("sssf-runner", tmp_path / "Dockerfile")


def test_run_sandbox_flags(fake_docker, tmp_path):
    run_sandbox(
        "sssf-runner",
        "sssf-abc",
        worktree=tmp_path / "wt",
        data_dir=tmp_path / "data",
        pi_home=tmp_path / "pi",
        git_dir=tmp_path / "proj" / ".git",
        config_dir=tmp_path / ".config",
        uid=501,
        gid=20,
        env={"OPENAI_API_KEY": "x", "OPENAI_BASE_URL": "https://genplat.example.com/v1"},
        cmd=["python", "adws/modules/adw_simple_sdlc.py"],
    )
    calls = fake_docker.read_text().splitlines()
    run = next(c for c in calls if c.startswith("run"))
    assert "--name sssf-abc" in run
    assert f"{tmp_path}/wt:/work" in run
    assert "adws/adw_data" not in run  # the run writes its OWN db in the worktree
    assert f"{tmp_path}/pi:/opt/pi-agent-host:ro" in run
    assert f"{tmp_path}/proj/.git:{tmp_path}/proj/.git:rw" in run
    assert f"{tmp_path}/.config:/tmp/.config:ro" in run
    assert "--user 501:20" in run
    assert "-e OPENAI_API_KEY=x" in run
    assert "-e OPENAI_BASE_URL=https://genplat.example.com/v1" in run
    assert "-p" not in run

    stop_remove("sssf-abc")
    stop_remove("sssf-abc")


def test_ensure_image_current_real_fingerprint(fake_docker, monkeypatch):
    """Exercises the REAL _engine_fingerprint (not a stub) — regression for the
    missing-import bug that made it crash with NameError."""
    from sssf.sandbox import _engine_fingerprint

    real = _engine_fingerprint() + "\n"

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=real, stderr="")

    monkeypatch.setattr(sandbox, "_docker", fake)
    sandbox.ensure_image_current("sssf-real")  # no raise — real fingerprint path


def _fake_docker_stdout(monkeypatch, stdout: str, rc: int = 0):
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr="")

    monkeypatch.setattr(sandbox, "_docker", fake)
    monkeypatch.setattr(sandbox, "_engine_fingerprint", lambda: "FPWANT")


def test_ensure_image_current_matches(fake_docker, monkeypatch):
    _fake_docker_stdout(monkeypatch, "FPWANT\n")
    sandbox.ensure_image_current("sssf-match")  # no raise


def test_ensure_image_current_stale_raises(fake_docker, monkeypatch):
    _fake_docker_stdout(monkeypatch, "OLDHASH\n")
    with pytest.raises(SandboxError, match="stale"):
        sandbox.ensure_image_current("sssf-stale")


def test_ensure_image_current_missing_raises(fake_docker, monkeypatch):
    """An image without the marker (or docker failure) is refused loudly —
    never a silent spawn into a stale engine."""
    _fake_docker_stdout(monkeypatch, "")
    with pytest.raises(SandboxError, match="missing or unreadable"):
        sandbox.ensure_image_current("sssf-missing")


def test_record_never_started_leaves_evidence(monkeypatch, tmp_path):
    """A spawn-death (container exits before the ADW ever writes a session)
    records a failed session + the container log tail and flips the linked
    ticket — the monitor no longer erases the only evidence."""
    from sssf.adw_modules.tracer import Tracer

    db = tmp_path / "proj" / "adws" / "data" / "sssf.db"
    tracer = Tracer(db, tmp_path / "proj" / "adws" / "data" / "sessions" / "abc123" / "events.jsonl")
    tracer.conn.execute(
        "INSERT INTO tickets (id, provider, title, status, adw_id) VALUES (?,?,?,?,?)",
        ("internal:x", "internal", "boom", "starting", "abc123"),
    )

    def fake_docker(*args, **kwargs):
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, stdout="1\n", stderr="")
        if args[0] == "logs":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=("python: can't open file 'adws/modules/adw_simple_sdlc.py':"
                        " [Errno 2] No such file or directory\n"),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox, "_docker", fake_docker)
    per_run = tmp_path / "proj" / ".worktrees" / "abc123" / "adws" / "data" / "sssf.db"
    sandbox.record_never_started(tmp_path / "proj", "abc123", tracer, per_run)

    row = tracer.conn.execute(
        "SELECT status, adw_name FROM sessions WHERE adw_id='abc123'"
    ).fetchone()
    assert row == ("fail", "adw_simple_sdlc (never started)")
    ev = tracer.conn.execute(
        "SELECT name, payload_json FROM events WHERE adw_id='abc123'"
    ).fetchone()
    assert ev[0] == "sandbox spawn failure"
    assert "1" in ev[1]  # exit code captured
    assert "No such file or directory" in ev[1]  # log tail captured
    status = tracer.conn.execute(
        "SELECT status FROM tickets WHERE id='internal:x'"
    ).fetchone()[0]
    assert status == "failed"


def test_record_never_started_skips_when_adw_started(monkeypatch, tmp_path):
    """A run that DID write a session row is left to the normal sync path —
    no synthetic failure row, even when the session exists only in the
    per-run db (not yet merged)."""
    from sssf.adw_modules.tracer import Tracer

    db = tmp_path / "proj" / "adws" / "data" / "sssf.db"
    tracer = Tracer(db, tmp_path / "proj" / "adws" / "data" / "sessions" / "abc123" / "events.jsonl")
    tracer.conn.execute(
        "INSERT INTO sessions (adw_id, status) VALUES ('abc123', 'running')"
    )

    def fake_docker(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox, "_docker", fake_docker)
    per_run = tmp_path / "proj" / ".worktrees" / "abc123" / "adws" / "data" / "sssf.db"
    sandbox.record_never_started(tmp_path / "proj", "abc123", tracer, per_run)

    rows = tracer.conn.execute(
        "SELECT count(*) FROM sessions WHERE adw_id='abc123'"
    ).fetchone()[0]
    assert rows == 1  # still just the ADW's own row


def test_teardown_poll_treats_docker_error_as_retry_not_gone(monkeypatch, capsys):
    """A docker hiccup during the teardown poll must not be read as
    'container gone' — that tears the run down prematurely (audit A2)."""
    from sssf.sandbox import _container_gone

    def flaky(*a, **k):
        raise RuntimeError("docker hiccup")

    assert _container_gone(flaky, "sssf-x") is False
    assert "retrying" in capsys.readouterr().err


def test_teardown_poll_gone_only_on_empty_output(monkeypatch):
    from sssf.sandbox import _container_gone

    def gone(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

    assert _container_gone(gone, "sssf-x") is True

    def up(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="Up 2 minutes", stderr="")

    assert _container_gone(up, "sssf-x") is False
