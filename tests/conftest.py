import os
import stat
import subprocess
from pathlib import Path

import pytest


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
        "elif ' --entrypoint cat ' in ' '.join(sys.argv):\n"
        "    print('FPFIXED')\n"
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


@pytest.fixture(autouse=True)
def sssf_home(tmp_path, monkeypatch):
    """Keep every sandbox test hermetic — never touch the real ~/.sssf."""
    home = tmp_path / "sssf-home"
    home.mkdir()
    monkeypatch.setenv("SSSF_HOME", str(home))
    return home
