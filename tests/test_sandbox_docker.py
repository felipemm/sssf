"""Docker image + container lifecycle: build, fingerprint guard, run/stop,
and the runner image upkeep. Docker is faked via the conftest fake_docker
PATH shim; module-attr patches target sssf.sandbox.docker.

Mirrors src/sssf/sandbox/docker.py.
"""

import stat
import subprocess

import pytest

import sssf.sandbox.docker as sandbox
from sssf.sandbox.docker import (
    SandboxError,
    build_image,
    docker_available,
    run_sandbox,
    stop_remove,
)


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


def test_build_image_stream_invokes_docker_with_progress(fake_docker, tmp_path):
    """stream=True (the interactive CLI) runs docker build with output attached
    to the terminal — never the silent-capture path that reads as a freeze."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    build_image("sssf-runner", dockerfile, stream=True)
    calls = fake_docker.read_text().splitlines()
    build = next(c for c in calls if c.startswith("build"))
    assert "-t sssf-runner" in build and "Dockerfile" in build


def test_build_image_stream_failure_raises(fake_docker, tmp_path):
    """A streamed build that exits non-zero raises SandboxError (docker output
    already reached the terminal, so the message points at it)."""
    bin_dir = fake_docker.parent / "bin"
    (bin_dir / "docker").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n")
    (bin_dir / "docker").chmod((bin_dir / "docker").stat().st_mode | stat.S_IEXEC)
    with pytest.raises(SandboxError, match="exit 2"):
        build_image("sssf-runner", tmp_path / "Dockerfile", stream=True)


def test_build_image_captured_timeout_raises_helpful(fake_docker, tmp_path, monkeypatch):
    """A docker build that exceeds the ceiling must surface a readable
    SandboxError (healer/captured path), not a raw TimeoutExpired traceback."""

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout_s", 300))

    monkeypatch.setattr("sssf.sandbox.docker._docker", slow)
    with pytest.raises(SandboxError, match="timed out"):
        build_image("sssf-runner", tmp_path / "Dockerfile")


def test_build_image_stream_timeout_raises_helpful(fake_docker, tmp_path, monkeypatch):
    """Same readable timeout error on the streaming (CLI) path."""

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 1800))

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(SandboxError, match="timed out"):
        build_image("sssf-runner", tmp_path / "Dockerfile", stream=True)


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


def test_run_sandbox_removes_stale_container_first(fake_docker, tmp_path):
    """Containers are kept after a run, so a retry/restart finds an Exited
    container with the same name — run_sandbox must clear it before running."""
    run_sandbox(
        "sssf-runner",
        "sssf-abc",
        worktree=tmp_path / "wt",
        data_dir=tmp_path / "data",
        pi_home=tmp_path / "pi",
    )
    calls = fake_docker.read_text().splitlines()
    rm = next(c for c in calls if c.startswith("rm -f"))
    run = next(c for c in calls if c.startswith("run"))
    assert "sssf-abc" in rm
    assert calls.index(rm) < calls.index(run)


def test_ensure_image_current_real_fingerprint(fake_docker, monkeypatch):
    """Exercises the REAL _engine_fingerprint (not a stub) — regression for the
    missing-import bug that made it crash with NameError."""
    from sssf.sandbox.docker import _engine_fingerprint

    real = _engine_fingerprint() + "\n"

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=real, stderr="")

    monkeypatch.setattr("sssf.sandbox.docker._docker", fake)
    sandbox.ensure_image_current("sssf-real")  # no raise — real fingerprint path


def _fake_docker_stdout(monkeypatch, stdout: str, rc: int = 0):
    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr="")

    monkeypatch.setattr("sssf.sandbox.docker._docker", fake)
    monkeypatch.setattr("sssf.sandbox.docker._engine_fingerprint", lambda: "FPWANT")


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


def test_image_is_current_real_fingerprint(fake_docker, monkeypatch):
    """A baked marker matching the local engine reports current — the real
    fingerprint path, mirroring test_ensure_image_current_real_fingerprint."""
    from sssf.sandbox.docker import _engine_fingerprint

    sandbox._fingerprint_cache.clear()  # never leak a cached marker between tests
    real = _engine_fingerprint() + "\n"

    def fake(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=real, stderr="")

    monkeypatch.setattr("sssf.sandbox.docker._docker", fake)
    assert sandbox.image_is_current("sssf-current") is True


def test_image_is_current_stale(monkeypatch):
    monkeypatch.setattr("sssf.sandbox.docker._engine_fingerprint", lambda: "FPWANT")
    monkeypatch.setattr("sssf.sandbox.docker.image_engine_fingerprint", lambda image: "OLDHASH")
    assert sandbox.image_is_current("sssf-stale") is False


def test_image_is_current_missing(monkeypatch):
    monkeypatch.setattr("sssf.sandbox.docker._engine_fingerprint", lambda: "FPWANT")
    monkeypatch.setattr("sssf.sandbox.docker.image_engine_fingerprint", lambda image: None)
    assert sandbox.image_is_current("sssf-missing") is False


def test_build_runner_image_clears_fingerprint_cache(fake_docker):
    """After a rebuild the in-process fingerprint cache must not keep reporting
    the OLD marker — otherwise the next guard would still refuse the fresh
    image (and the healer would rebuild it on every pass)."""
    sandbox._fingerprint_cache["sssf-runner"] = "stale-marker"
    sandbox.build_runner_image("sssf-runner")
    assert "sssf-runner" not in sandbox._fingerprint_cache
    calls = fake_docker.read_text().splitlines()
    assert any("build" in c and "sssf-runner.Dockerfile" in c for c in calls)


def test_build_runner_image_missing_dockerfile_raises(monkeypatch):
    monkeypatch.setattr("sssf.sandbox.docker.runner_dockerfile", lambda: None)
    with pytest.raises(SandboxError, match=r"sssf-runner\.Dockerfile"):
        sandbox.build_runner_image("sssf-runner")


def test_run_sandbox_publishes_no_ports(tmp_path, monkeypatch):
    """ADR-0004: the runner publishes NO ports — it executes work and exits.
    The interactive QA surface is the deploy flow's workbench, a separate
    disposable container."""
    import sssf.sandbox.docker as sb

    captured: list[list[str]] = []

    def fake_docker(*args, timeout_s=30):
        captured.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr("sssf.sandbox.docker._docker", fake_docker)
    sb.run_sandbox(
        "sssf-runner",
        "sssf-x1",
        worktree=tmp_path / "wt",
        data_dir=tmp_path / "adws" / "data",
        pi_home=tmp_path / "pi",
        cmd=["python", "-c", "pass"],
    )
    run_args = next(a for a in captured if a[0] == "run")
    assert "-p" not in run_args


def test_run_sandbox_skips_publish_without_port(tmp_path, monkeypatch):
    import sssf.sandbox.docker as sb

    captured: list[list[str]] = []
    monkeypatch.setattr(
        "sssf.sandbox.docker._docker",
        lambda *a, timeout_s=30: (
            captured.append(list(a)) or subprocess.CompletedProcess(list(a), 0, "", "")
        ),
    )
    sb.run_sandbox(
        "sssf-runner",
        "sssf-x2",
        worktree=tmp_path / "wt",
        data_dir=tmp_path / "adws" / "data",
        pi_home=tmp_path / "pi",
        cmd=["true"],
    )
    run_args = next(a for a in captured if a[0] == "run")
    assert "-p" not in run_args


def test_stop_container_stops_and_keeps(tmp_path, monkeypatch):
    """docker stop keeps the container (logs + review surface). Deletion is
    sweep's job."""
    import sssf.sandbox.docker as sb

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "sssf.sandbox.docker._docker",
        lambda *a, timeout_s=30: (
            calls.append(list(a)) or subprocess.CompletedProcess(list(a), 0, "", "")
        ),
    )
    sb.stop_container("sssf-r9")
    assert calls == [["stop", "-t", "5", "sssf-r9"]]
    assert not any(a[0] == "rm" for a in calls)
