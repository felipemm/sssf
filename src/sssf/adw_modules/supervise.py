"""Container-side supervisor — the container's PID 1.

Runs the ADW command, writes the run's end marker into the bind-mounted
worktree (data_dir/sessions/<adw_id>.supervisor-exit), and EXITS with the
ADW's code. The runner is cheap and disposable (ADR-0004): it executes work
and ends — the old behavior of idling to keep the app up for review is gone;
the deploy flow's QA surface is the workbench (workbench.py), a separate
disposable container from the `dev` branch. The host monitor treats either
the marker or the container exiting as the run's end.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _call(argv: list[str], **kwargs) -> int:
    return subprocess.call(argv, **kwargs)


def _adw_id(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--adw-id" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def run(argv: list[str], *, data_dir: Path) -> int:
    """Run the ADW command, write the end marker, and exit with its code —
    the container ends with the run (no review launch, no idle)."""
    adw_id = _adw_id(argv)
    rc = _call(argv)
    if adw_id:
        marker = Path(data_dir) / "sessions" / f"{adw_id}.supervisor-exit"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(rc))
    return rc


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    data_dir = Path("adws/data")
    try:
        from sssf.adw_modules.agents import default_config_path, load_config

        cfg = load_config(str(default_config_path()))
        data_dir = Path(cfg.defaults.data_dir)
    except Exception:
        pass  # config missing — still supervise the run
    return run(argv, data_dir=data_dir)


if __name__ == "__main__":
    sys.exit(main())
