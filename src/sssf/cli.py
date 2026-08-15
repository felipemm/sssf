"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""
import argparse
import sys
from pathlib import Path

from sssf import __version__
from sssf import registry
from sssf.commands import init, obs_cmds, run
from sssf.project import find_project, data_dir


def _register_obs(sub: argparse._SubParsersAction) -> None:
    """sessions / phases / tail / procs — each resolves the project db, then renders."""
    def db_path(explicit: str | None) -> Path:
        root = find_project(Path.cwd(), explicit)
        if root is None:
            print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
            raise SystemExit(1)
        return data_dir(root) / "sssf.db"

    def sessions_cmd(a):
        return obs_cmds.sessions(db_path(a.project))

    def scoped_cmd(fn):
        return lambda a: fn(db_path(a.project), a.adw_id)

    p = sub.add_parser("sessions", help="trace: recent ADW runs")
    p.add_argument("--project", default=None)
    p.set_defaults(func=sessions_cmd)

    for name, fn in (("phases", obs_cmds.phases), ("tail", obs_cmds.tail),
                     ("procs", obs_cmds.procs)):
        p = sub.add_parser(name, help=f"trace: {name} <adw_id>")
        p.add_argument("--project", default=None)
        p.add_argument("adw_id")
        p.set_defaults(func=scoped_cmd(fn))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sssf", description="Super Simple Software Factory CLI")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="stamp the sssf footprint into this project")
    p_init.add_argument("--project", default=None, help="project root (default: cwd)")
    p_init.add_argument("--refresh", action="store_true", help="copy only missing files")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.set_defaults(func=lambda a: init.run(Path(a.project or ".").resolve(),
                                                refresh=a.refresh, force=a.force))

    p_run = sub.add_parser("run", help="execute an ADW chain: sssf run <adw> \"<prompt>\" [--adw-id X]")
    p_run.add_argument("adw", help="chain name; the adw_ prefix is optional")
    p_run.add_argument("args", nargs=argparse.REMAINDER, help="passed through to the ADW")
    p_run.add_argument("--project", default=None)
    p_run.set_defaults(func=lambda a: run.run(Path.cwd(), a.adw, a.args, a.project))

    _register_obs(sub)

    args = parser.parse_args(argv)
    if args.version:
        print(f"sssf {__version__}")
        return 0
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args) if callable(args.func) else 1


if __name__ == "__main__":
    sys.exit(main())
