import os
import stat
import subprocess
from pathlib import Path

import pytest

from sssf.sandbox import SandboxError, build_image, docker_available, run_sandbox, stop_remove, wait_exit


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


def test_build_failure_raises(fake_docker, tmp_path, monkeypatch):
    bin_dir = fake_docker.parent / "bin"
    (bin_dir / "docker").write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
    (bin_dir / "docker").chmod((bin_dir / "docker").stat().st_mode | stat.S_IEXEC)
    with pytest.raises(SandboxError):
        build_image("sssf-runner", tmp_path / "Dockerfile")


def test_run_sandbox_flags(fake_docker, tmp_path):
    run_sandbox(
        "sssf-runner", "sssf-abc",
        worktree=tmp_path / "wt", data_dir=tmp_path / "data",
        pi_home=tmp_path / "pi", git_dir=tmp_path / "proj" / ".git",
        config_dir=tmp_path / ".config",
        host_port=3456, container_port=3000,
        uid=501, gid=20, env={"REVIEW_HOST_PORT": "3456"}, cmd=["python", "adws/adw_simple_sdlc.py"],
    )
    calls = fake_docker.read_text().splitlines()
    run = next(c for c in calls if c.startswith("run"))
    assert "--name sssf-abc" in run
    assert f"{tmp_path}/wt:/work" in run
    assert f"{tmp_path}/data:/work/adws/adw_data" in run
    assert f"{tmp_path}/pi:/opt/pi-agent-host:ro" in run
    assert f"{tmp_path}/proj/.git:{tmp_path}/proj/.git:rw" in run
    assert f"{tmp_path}/.config:/tmp/.config:ro" in run
    assert "-p 3456:3000" in run
    assert "--user 501:20" in run
    assert "-e REVIEW_HOST_PORT=3456" in run


def test_wait_exit_and_stop_remove_idempotent(fake_docker):
    assert wait_exit("sssf-abc", timeout_s=5) == 0
    stop_remove("sssf-abc")
    stop_remove("sssf-abc")
