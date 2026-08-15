"""`sssf viz` — boot the global trace visualizer (Vue + bun)."""
from __future__ import annotations

import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

from sssf.commands import misc

APP_DIR = Path(resources.files("sssf") / "apps" / "visualizer")


def run(port: int, db_override: str | None, project: str | None) -> int:
    if misc.which("bun") is None:
        print("sssf: bun is required for `sssf viz` — install it globally once.", file=sys.stderr)
        return 1
    env = dict(os.environ)
    env["PORT"] = str(port)   # the bun server reads PORT env, not --port argv
    if db_override:
        env["SSSF_DB"] = str(Path(db_override).resolve())
    if project:
        env["SSSF_REGISTRY"] = str(Path(project).resolve() / ".sssf" / "projects.json")
    print(f"sssf viz: http://localhost:{port} (api on the same port)")
    return subprocess.call(["bun", "run", "server/index.ts", "--port", str(port)],
                           cwd=APP_DIR, env=env)
