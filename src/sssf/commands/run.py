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

    # `sssf run approve|reject|stop <adw_id>` — the review decision / stop.
    if adw in ("approve", "reject", "stop"):
        return _decide(cwd, adw, args, explicit_project)

    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/ directory). Run `sssf init` first.", file=sys.stderr)
        return 1
    name = adw if adw.startswith("adw_") else f"adw_{adw}"
    adw_file = root / "adws" / f"{name}.py"
    if not adw_file.exists():
        print(f"sssf: no ADW named '{adw}' (looked for adws/{name}.py)", file=sys.stderr)
        return 1
    registry.update_last_run(root)

    if no_sandbox or not _sandbox_enabled(root):
        return subprocess.call([sys.executable, str(adw_file), *args], cwd=root)
    return _run_sandboxed(root, adw_file, args)


def _sandbox_enabled(root: Path) -> bool:
    try:
        from sssf.adw_modules.agents import load_config
        cfg = load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml"))
        return cfg.sandbox.enabled
    except Exception:
        return False


def _run_sandboxed(root: Path, adw_file: Path, args: list[str]) -> int:
    """Create the per-run sandbox (worktree + container) and run the ADW inside.
    Deterministic Python; the cwd is never touched."""
    from sssf.sandbox import (SandboxError, allocate_port, docker_available,
                              sandbox_env, spawn_sandbox)

    if not docker_available():
        print("sssf: docker is not available — run `sssf sandbox build`? "
              "or use --no-sandbox", file=sys.stderr)
        return 1
    from sssf.adw_modules.agents import load_config
    cfg = load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml"))

    adw_id = uuid.uuid4().hex[:8]
    port = allocate_port(cfg.sandbox.port_base)
    data_dir, pi_home, env = sandbox_env(root)
    env["REVIEW_HOST_PORT"] = str(port)
    try:
        spawn_sandbox(
            root, adw_id,
            cmd=["python", "adws/adw_simple_sdlc.py", *args, "--adw-id", adw_id],
            port=port, image=cfg.sandbox.image,
            data_dir=data_dir, pi_home=pi_home,
            container_port=cfg.review.port, env=env,
        )
    except SandboxError as e:
        print(f"sssf: sandbox spawn failed: {e}", file=sys.stderr)
        return 1
    print(f"sssf: sandboxed run spawned — adw_id {adw_id}, port {port}")
    return 0


def _decide(cwd: Path, decision: str, args: list[str], explicit_project: str | None) -> int:
    if not args:
        print(f"sssf: usage: sssf run {decision} <adw_id>", file=sys.stderr)
        return 1
    from sssf.sandbox import decide_and_teardown, sandbox_env, stop_run
    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf: no project here.", file=sys.stderr)
        return 1
    adw_id = args[0]
    data_dir, _pi, _env = sandbox_env(root)
    if decision == "stop":
        return stop_run(root, adw_id, data_dir)
    return decide_and_teardown(root, adw_id, decision, data_dir)
