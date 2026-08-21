"""`sssf doctor` — verify the interview prerequisites and skill freshness."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from sssf import ticketing
from sssf.adw_modules import skills_install
from sssf.project import find_project


def run(cwd: Path, explicit: str | None = None) -> int:
    problems = 0
    root = find_project(cwd, explicit)
    if root is None:
        print("sssf doctor: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    print(f"sssf doctor: {root}")

    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        problems += 1
        print("  ✗ internal ticketing provider is not enabled in adws/config/ticketing.yaml",
              file=sys.stderr)
    else:
        print("  ✓ internal ticketing provider enabled")

    if shutil.which("pi") is None:
        problems += 1
        print("  ✗ pi binary not found on PATH", file=sys.stderr)
    else:
        print(f"  ✓ pi binary ({shutil.which('pi')})")

    state = skills_install.check_skills(root)
    for skill, s in state.items():
        if not s["present"]:
            problems += 1
            print(f"  ✗ skill {skill}: NOT INSTALLED — run `sssf init --refresh`", file=sys.stderr)
        elif s["stale"]:
            problems += 1
            print(f"  ✗ skill {skill}: STALE (pinned {s['pinned'][:7]} ≠ remote {s['latest'][:7]}) "
                  f"— run `sssf init --refresh`", file=sys.stderr)
        elif s["latest"] is None:
            print(f"  ~ skill {skill}: installed, freshness unverifiable (offline)")
        else:
            print(f"  ✓ skill {skill}: installed, up to date ({s['pinned'][:7]})")

    if problems:
        print(f"sssf doctor: {problems} problem(s) found", file=sys.stderr)
        return 1
    print("sssf doctor: all checks passed")
    return 0
