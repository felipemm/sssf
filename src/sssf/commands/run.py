"""`sssf run` — execute a user ADW chain with the tool venv's python."""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from sssf import registry
from sssf.project import find_project


def run(cwd: Path, adw: str, args: list[str], explicit_project: str | None = None,
        no_sandbox: bool = False) -> int:
    # The run parser takes the prompt as a REMAINDER positional, so options
    # after it (e.g. `sssf run simple_sdlc "<prompt>" --project X`) land in
    # args. Pull a trailing --project out when the caller didn't pass one.
    if explicit_project is None and "--project" in args:
        i = args.index("--project")
        if i + 1 < len(args):
            explicit_project = args[i + 1]
            args = args[:i] + args[i + 2:]

    # `sssf run stop <adw_id>` / `sssf run restart <adw_id>` — run control.
    if adw == "stop":
        return _stop(cwd, args, explicit_project)
    if adw == "restart":
        return _restart(cwd, args, explicit_project)

    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/ directory). Run `sssf init` first.", file=sys.stderr)
        return 1
    name = adw if adw.startswith("adw_") else f"adw_{adw}"
    adw_file = _adw_file(root, name)
    if adw_file is None:
        print(f"sssf: no ADW named '{adw}' (looked for adws/{name}.py)", file=sys.stderr)
        return 1
    registry.update_last_run(root)

    if no_sandbox or not _sandbox_enabled(root):
        return subprocess.call([sys.executable, str(adw_file), *args], cwd=root)
    return _run_sandboxed(root, adw_file, args)


def _adw_file(root: Path, name: str) -> Path | None:
    """Prefer the INSTALLED template for standard ADWs — a project's committed
    copy goes stale after an sssf upgrade (e.g. the review-gate removal broke
    every pre-existing adw_simple_sdlc.py). Custom ADWs (no installed template)
    fall back to the project's file."""
    project_file = root / "adws" / f"{name}.py"
    import sssf
    installed = Path(sssf.__file__).parent / "templates" / "adws" / f"{name}.py"
    if installed.exists():
        return installed
    return project_file if project_file.exists() else None


def _sandbox_enabled(root: Path) -> bool:
    try:
        from sssf.adw_modules.agents import load_config
        cfg = load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml"))
        return cfg.sandbox.enabled
    except Exception:
        return False


def _run_sandboxed(root: Path, adw_file: Path, args: list[str], adw_id: str | None = None,
                    attach: bool = False) -> int:
    """Create the per-run sandbox (worktree + container), run the ADW inside,
    and detach a teardown monitor (the sandbox tears itself down when the ADW
    exits — success or fail). The cwd is never touched; the run's branch
    sssf/<adw_id> survives as the deliverable. Deterministic Python."""
    from sssf.sandbox import (SandboxError, docker_available, sandbox_env,
                              spawn_monitor, spawn_sandbox)

    if not docker_available():
        print("sssf: docker is not available — run `sssf sandbox build`? "
              "or use --no-sandbox", file=sys.stderr)
        return 1
    from sssf.adw_modules.agents import load_config
    cfg = load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml"))

    adw_id = adw_id or uuid.uuid4().hex[:8]
    data_dir, pi_home, env = sandbox_env(root)
    try:
        spawn_sandbox(
            root, adw_id,
            cmd=["python", "adws/adw_simple_sdlc.py", *args, "--adw-id", adw_id],
            image=cfg.sandbox.image,
            data_dir=data_dir, pi_home=pi_home, env=env, attach=attach,
        )
    except SandboxError as e:
        print(f"sssf: sandbox spawn failed: {e}", file=sys.stderr)
        return 1
    spawn_monitor(root, adw_id)
    print(f"sssf: sandboxed run spawned — adw_id {adw_id} (auto-teardown on exit)")
    return 0


def _restart(cwd: Path, args: list[str], explicit_project: str | None) -> int:
    """Re-run a session: reuse its adw_id (the ADW joins and reaps the old
    state) with the original request as the prompt. The sandbox attaches to the
    existing sssf/<adw_id> branch."""
    if not args:
        print("sssf: usage: sssf run restart <adw_id>", file=sys.stderr)
        return 1
    import sqlite3
    from sssf.sandbox import project_db_path, sandbox_env, _session_status
    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    adw_id = args[0]
    data_dir, _pi, _env = sandbox_env(root)
    if _session_status(data_dir, adw_id) is None:
        print(f"sssf: no session {adw_id}", file=sys.stderr)
        return 1
    db_path = project_db_path(data_dir)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    row = conn.execute("SELECT request FROM sessions WHERE adw_id=?", (adw_id,)).fetchone()
    conn.close()
    if not row or not row[0]:
        print(f"sssf: session {adw_id} has no request to re-run", file=sys.stderr)
        return 1
    adw_file = root / "adws" / "adw_simple_sdlc.py"
    return _run_sandboxed(root, adw_file, [row[0]], adw_id=adw_id, attach=True)


def _stop(cwd: Path, args: list[str], explicit_project: str | None) -> int:
    if not args:
        print("sssf: usage: sssf run stop <adw_id>", file=sys.stderr)
        return 1
    from sssf.sandbox import sandbox_env, stop_run
    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    adw_id = args[0]
    data_dir, _pi, _env = sandbox_env(root)
    return stop_run(root, adw_id, data_dir)
