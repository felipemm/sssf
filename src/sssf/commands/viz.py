"""`sssf viz` — run the global trace visualizer (Vue + bun) as a background service.

    sssf viz [start]   spawn the bun server detached, then open the browser
    sssf viz stop      terminate the running server

Runtime state lives beside the registry in ~/.sssf/: viz.pid (the server pid)
and viz.log (server output).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from importlib import resources
from pathlib import Path

from sssf.commands import misc

APP_DIR = Path(resources.files("sssf") / "apps" / "visualizer")
STATE_DIR = Path.home() / ".sssf"


def _pid_file() -> Path:
    return STATE_DIR / "viz.pid"


def _log_file() -> Path:
    return STATE_DIR / "viz.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _running_pid() -> int | None:
    """The pid of a live viz server, or None (stale/missing pid file counts as none)."""
    pid_file = _pid_file()
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return None
    return pid if _pid_alive(pid) else None


def _spawn(port: int, db_override: str | None, project: str | None) -> int:
    env = dict(os.environ)
    env["PORT"] = str(port)  # the bun server reads PORT env, not --port argv
    if db_override:
        env["SSSF_DB"] = str(Path(db_override).resolve())
    if project:
        env["SSSF_REGISTRY"] = str(Path(project).resolve() / ".sssf" / "projects.json")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_log_file(), "a") as log:
        proc = subprocess.Popen(
            ["bun", "run", "server/index.ts", "--port", str(port)],
            cwd=APP_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # survives the terminal closing
        )
    _pid_file().write_text(str(proc.pid))
    return proc.pid


def _wait_for_server(url: str, tries: int = 10, interval: float = 0.5) -> bool:
    """Poll GET /api/health until it answers; True when it ever did."""
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"{url}/api/health", timeout=1).close()
            return True
        except Exception:
            time.sleep(interval)
    return False


def start(port: int, db_override: str | None, project: str | None) -> int:
    if misc.which("bun") is None:
        print("sssf: bun is required for `sssf viz` — install it globally once.", file=sys.stderr)
        return 1
    url = f"http://localhost:{port}"
    pid = _running_pid()
    if pid is not None:
        print(f"sssf viz: already running (pid {pid}) — {url}")
        webbrowser.open(url)
        return 0
    pid = _spawn(port, db_override, project)
    _wait_for_server(url)  # give it a moment to bind — or to crash
    if not _pid_alive(pid):
        print(
            f"sssf viz: server exited during startup (is port {port} in use?) — see {_log_file()}",
            file=sys.stderr,
        )
        _pid_file().unlink(missing_ok=True)
        return 1
    print(f"sssf viz: started (pid {pid}) — {url}")
    print(f"sssf viz: log at {_log_file()} · stop with `sssf viz stop`")
    # the self-healing monitor rides along with the viz
    try:
        from sssf import healer

        if healer.running_pid() is None:
            healer.start()
    except Exception:
        pass
    webbrowser.open(url)
    return 0


def stop() -> int:
    pid = _running_pid()
    _pid_file().unlink(missing_ok=True)
    if pid is None:
        print("sssf viz: not running")
        return 0
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    print(f"sssf viz: stopped (pid {pid})")
    return 0
