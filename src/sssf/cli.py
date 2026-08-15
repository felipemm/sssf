"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""
import argparse
import sys
from pathlib import Path

from sssf import __version__
from sssf import registry
from sssf.commands import init
from sssf.project import find_project


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
