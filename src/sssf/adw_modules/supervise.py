"""Container-side supervisor — the container's PID 1.

Runs the ADW command, then (whatever its exit code) the project's configured
review command, then idles forever so the container stays up for review. The
run's end is signalled to the host monitor by an exit-marker file written into
the bind-mounted worktree (data_dir/sessions/<adw_id>.supervisor-exit); the
monitor cannot rely on the container exiting, because it deliberately does
not.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _idle() -> None:
    while True:
        time.sleep(3600)


def _call(argv: list[str], **kwargs) -> int:
    return subprocess.call(argv, **kwargs)


def _adw_id(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--adw-id" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def run(argv: list[str], *, data_dir: Path, review_cmd: list[str] | None) -> int:
    """Run the ADW command; then, whatever its exit code, the review command;
    then idle. Returns the ADW's exit code (never reached in practice — _idle
    keeps the container alive)."""
    adw_id = _adw_id(argv)
    rc = _call(argv)
    if adw_id:
        marker = Path(data_dir) / "sessions" / f"{adw_id}.supervisor-exit"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(rc))
    if review_cmd:
        _call(review_cmd)
    _idle()  # keep the container up for review
    return rc


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    review_cmd: list[str] | None = None
    data_dir = Path("adws/data")
    try:
        from sssf.adw_modules.agents import default_config_path, load_config

        cfg = load_config(str(default_config_path()))
        review_cmd = cfg.sandbox.review.command
        data_dir = Path(cfg.defaults.data_dir)
    except Exception:
        pass  # no review config / config missing — still supervise the run
    return run(argv, data_dir=data_dir, review_cmd=review_cmd)


if __name__ == "__main__":
    sys.exit(main())
