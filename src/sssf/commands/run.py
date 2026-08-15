"""`sssf run` — execute a user ADW chain with the tool venv's python."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sssf import registry
from sssf.project import find_project


def run(cwd: Path, adw: str, args: list[str], explicit_project: str | None = None) -> int:
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
    return subprocess.call([sys.executable, str(adw_file), *args], cwd=root)
