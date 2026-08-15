"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""
import argparse
import sys
from pathlib import Path

from sssf import __version__
from sssf import registry
from sssf.commands import init, misc, obs_cmds, run, sweep, ticket, viz
from sssf.project import find_project, data_dir


def _dispatch_ticket(a) -> int:
    action = a.ticket_action
    if action == "add":
        return ticket.add(a.title, a.project)
    if action == "sync":
        return ticket.sync(a.project)
    if action == "list":
        return ticket.list_tickets(a.project)
    if action == "run":
        return ticket.run(a.ticket_id, a.project)
    return 1


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

    p = sub.add_parser("projects", help="list / remove registered projects")
    p.add_argument("action", nargs="?", default="list", choices=["list", "remove"])
    p.add_argument("name", nargs="?")
    p.set_defaults(func=lambda a: misc.projects(a.action, a.name))

    sub.add_parser("doctor", help="check global prerequisites and project state") \
       .set_defaults(func=lambda a: misc.doctor())
    sub.add_parser("upgrade", help="uv tool upgrade sssf") \
       .set_defaults(func=lambda a: misc.upgrade())

    p_viz = sub.add_parser("viz", help="run the global trace visualizer as a background service")
    p_viz.add_argument("action", nargs="?", default="start", choices=["start", "stop"])
    p_viz.add_argument("--port", type=int, default=4600)
    p_viz.add_argument("--db", default=None, help="adhoc single-db mode")
    p_viz.add_argument("--project", default=None, help="use this project's registry")
    p_viz.set_defaults(func=lambda a: viz.start(a.port, a.db, a.project)
                       if a.action == "start" else viz.stop())

    p_sweep = sub.add_parser("sweep", help="archive finished sessions older than the interval (all registered projects)")
    p_sweep.add_argument("--project", default=None, help="sweep one project root instead of the whole registry")
    p_sweep.add_argument("--days", type=int, default=30)
    p_sweep.set_defaults(func=lambda a: sweep.run(a.project, a.days))

    p_ticket = sub.add_parser("ticket", help="ticketing integration (add / sync / list / run)")
    tsub = p_ticket.add_subparsers(dest="ticket_action", required=True)
    p_add = tsub.add_parser("add", help="create an internal ticket")
    p_add.add_argument("title")
    p_add.add_argument("--project", default=None)
    p_sync = tsub.add_parser("sync", help="fetch external tickets into the backlog")
    p_sync.add_argument("--project", default=None)
    p_list = tsub.add_parser("list", help="list tickets")
    p_list.add_argument("--project", default=None)
    p_run = tsub.add_parser("run", help="spawn simple_sdlc for a ticket")
    p_run.add_argument("ticket_id")
    p_run.add_argument("--project", default=None)
    p_ticket.set_defaults(func=lambda a: _dispatch_ticket(a))

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
