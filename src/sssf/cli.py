"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""
import argparse
import sys

from sssf import __version__


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"sssf {__version__}")
        return 0
    print("sssf: see `sssf --help` (subcommands arrive in later tasks)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
