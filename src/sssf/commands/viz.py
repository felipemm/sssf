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

from sssf import registry, ticketing
from sssf.commands import misc

APP_DIR = Path(str(resources.files("sssf") / "apps" / "visualizer"))
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


def _reconcile_ticket_databases(db_override: str | None, project: str | None) -> None:
    """Reconcile each database the visualizer is about to serve, once."""
    registry_setting = None
    if project:
        registry_setting = os.environ.get("SSSF_REGISTRY")
        os.environ["SSSF_REGISTRY"] = str(Path(project).resolve() / ".sssf" / "projects.json")
    try:
        paths: set[Path] = set()
        for entry in registry.list_projects():
            if not isinstance(entry, dict):
                continue
            db_path = entry.get("db")
            if isinstance(db_path, str) and db_path:
                paths.add(Path(db_path).resolve())
    finally:
        if project:
            if registry_setting is None:
                os.environ.pop("SSSF_REGISTRY", None)
            else:
                os.environ["SSSF_REGISTRY"] = registry_setting
    active_override = db_override if db_override is not None else os.environ.get("SSSF_DB")
    if active_override:
        paths.add(Path(active_override).resolve())

    for db_path in sorted(paths):
        if not db_path.is_file():
            continue
        try:
            settled = ticketing.reconcile_project_db(db_path)
            if settled:
                print(f"sssf viz: reconciled {settled} stale ticket(s) in {db_path}")
        except Exception as error:
            print(f"sssf viz: could not reconcile tickets in {db_path} ({error})", file=sys.stderr)


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
    _reconcile_ticket_databases(db_override, project)
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
    except Exception as error:  # the browser still opens
        print(f"sssf viz: healer start failed ({error})", file=sys.stderr)
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
