"""The `sssf` entry point. Dispatch only — logic lives in sssf.commands modules."""

import argparse
import sys
from pathlib import Path

from sssf import __version__
from sssf.commands import (
    flow,
    heal,
    init,
    misc,
    notify_cmd,
    sandbox_cmd,
    sweep,
    ticket,
    viz,
)


def _dispatch_flow_implement(a, cwd: Path) -> int:
    """Route `sssf flow implement` — a ticket id runs one ticket; the `afk`
    keyword runs the unattended queue loop (issue #93)."""
    if a.ticket_id is None:
        print("sssf flow: implement needs a ticket id, or 'afk'", file=sys.stderr)
        return 2
    if a.ticket_id == "afk":
        return flow.implement_afk(
            cwd, a.project, cap=a.cap, wait_seconds=a.wait_seconds, no_sandbox=a.no_sandbox
        )
    return flow.implement(cwd, a.ticket_id, a.project, a.no_sandbox)


def _dispatch_ticket(a) -> int:
    action = a.ticket_action
    if action == "new":
        return ticket.new(a.title, a.project)
    if action == "sync":
        return ticket.sync(a.project, provider=a.provider)
    if action == "list":
        return ticket.list_tickets(a.project, backlog_only=a.backlog)
    if action == "context":
        return ticket.ticket_context(a.ticket_id, a.project, a.set_text)
    if action == "backlog":
        return ticket.backlog(a.ticket_id, a.project, feedback=a.feedback)
    return 1


def _dispatch_sandbox(a) -> int:
    action = a.sandbox_action
    if action == "build":
        return sandbox_cmd.build(a.project)
    if action == "list":
        return sandbox_cmd.list_(a.project)
    if action == "prune":
        return sandbox_cmd.prune(a.project, a.adw_id, a.all)
    if action == "stop":
        return sandbox_cmd.stop(a.project, a.adw_id)
    if action == "restart":
        return sandbox_cmd.restart(a.project, a.adw_id)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sssf", description="Super Simple Software Factory CLI")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="stamp the sssf footprint into this project")
    p_init.add_argument("--project", default=None, help="project root (default: cwd)")
    p_init.add_argument("--refresh", action="store_true", help="copy only missing files")
    p_init.add_argument("--force", action="store_true", help="overwrite existing files")
    p_init.add_argument(
        "--auto", action="store_true", help="--refresh without prompts (accept all — scripting)"
    )
    p_init.add_argument(
        "--design-quality",
        metavar="SURFACE",
        nargs="?",
        const="site/dist",
        default=None,
        help="configure the impeccable design gate + designer for a design surface "
        "(default: site/dist) — e.g. `sssf init --design-quality src/public`",
    )
    p_init.set_defaults(
        func=lambda a: init.run(
            Path(a.project or ".").resolve(),
            refresh=a.refresh,
            force=a.force,
            auto=a.auto,
            design_quality=a.design_quality,
        )
    )

    p_flow = sub.add_parser(
        "flow", help="the three flows — the ONLY entry point for work: plan / implement / deploy"
    )
    fsub = p_flow.add_subparsers(dest="flow_action", required=True)
    p_fplan = fsub.add_parser(
        "plan", help="plan a feature: exploration → grill-with-docs → spec → tickets"
    )
    p_fplan.add_argument(
        "ticket_id",
        nargs="?",
        help="idea ticket to plan; omit to run exploration-first on the cwd",
    )
    p_fplan.add_argument("--project", default=None)
    p_fplan.add_argument(
        "--skip-exploration",
        action="store_true",
        help="skip the scout exploration pass — the problem is already well understood",
    )
    p_fplan.add_argument(
        "--revise",
        action="store_true",
        help="re-plan an already-planned idea ticket (the explicit plan-once escape)",
    )
    p_fplan.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run in the current dir instead of a sandbox container",
    )
    p_fplan.set_defaults(
        func=lambda a: flow.plan(
            Path.cwd(), a.ticket_id, a.project, a.skip_exploration, a.no_sandbox, a.revise
        )
    )
    p_fimpl = fsub.add_parser(
        "implement", help="implement one ready-for-agent ticket: triage → build → review"
    )
    p_fimpl.add_argument(
        "ticket_id",
        nargs="?",
        help="the ticket to implement (ready-for-agent); or 'afk' to work the"
        " whole ready-for-agent queue unattended",
    )
    p_fimpl.add_argument("--project", default=None)
    p_fimpl.add_argument(
        "--cap",
        type=int,
        default=30,
        help="afk: max rounds before the loop stops (default 30)",
    )
    p_fimpl.add_argument(
        "--wait-seconds",
        type=int,
        default=7200,
        help="afk: how long to wait for one sandboxed round to settle before"
        " giving up (default 7200)",
    )
    p_fimpl.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run in the current dir instead of a sandbox container",
    )
    p_fimpl.set_defaults(
        func=lambda a: _dispatch_flow_implement(a, Path.cwd())
    )
    p_fdep = fsub.add_parser(
        "deploy", help="release train: batch signoff on dev → bump → MR → e2e → release"
    )
    p_fdep.add_argument("--project", default=None)
    p_fdep.add_argument(
        "--yes",
        action="store_true",
        help="auto-approve the batch signoff (explicit automation escape)",
    )
    p_fdep.add_argument(
        "--down",
        action="store_true",
        help="tear down the QA workbench (the human's teardown)",
    )
    p_fdep.add_argument(
        "--revert",
        default=None,
        metavar="TICKET_ID",
        help="revert a ticket's own commits from dev before the MR (escape hatch)",
    )
    p_fdep.set_defaults(
        func=lambda a: (
            flow.deploy_down(Path.cwd(), a.project)
            if a.down
            else flow.deploy_revert(Path.cwd(), a.revert, a.project)
            if a.revert
            else flow.deploy(Path.cwd(), a.project, a.yes)
        )
    )

    p = sub.add_parser("projects", help="list / remove registered projects")
    p.add_argument("action", nargs="?", default="list", choices=["list", "remove"])
    p.add_argument("name", nargs="?")
    p.set_defaults(func=lambda a: misc.projects(a.action, a.name))

    sub.add_parser("doctor", help="check global prerequisites and project state").set_defaults(
        func=lambda a: misc.doctor()
    )
    p_upgrade = sub.add_parser(
        "upgrade", help="update sssf (uv tool upgrade; git pull for editable installs)"
    )
    p_upgrade.add_argument(
        "--check",
        action="store_true",
        help="report whether an update is available (JSON, read-only)",
    )
    p_upgrade.set_defaults(func=lambda a: misc.upgrade(check=a.check))

    p_viz = sub.add_parser("viz", help="run the global trace visualizer as a background service")
    p_viz.add_argument("action", nargs="?", default="start", choices=["start", "stop"])
    p_viz.add_argument("--port", type=int, default=4600)
    p_viz.add_argument("--db", default=None, help="adhoc single-db mode")
    p_viz.add_argument("--project", default=None, help="use this project's registry")
    p_viz.set_defaults(
        func=lambda a: viz.start(a.port, a.db, a.project) if a.action == "start" else viz.stop()
    )

    p_sweep = sub.add_parser(
        "sweep", help="archive finished sessions older than the interval (all registered projects)"
    )
    p_sweep.add_argument(
        "--project", default=None, help="sweep one project root instead of the whole registry"
    )
    p_sweep.add_argument("--days", type=int, default=30)
    p_sweep.set_defaults(func=lambda a: sweep.run(a.project, a.days))

    p_ticket = sub.add_parser("ticket", help="ticketing integration (new / sync / list / context / backlog)")
    tsub = p_ticket.add_subparsers(dest="ticket_action", required=True)
    p_new = tsub.add_parser("new", help="create an idea ticket (title-only, born needs-triage)")
    p_new.add_argument("title")
    p_new.add_argument("--project", default=None)
    p_sync = tsub.add_parser(
        "sync", help="pull tickets and mirror machine labels, closes, and comments"
    )
    p_sync.add_argument("--project", default=None)
    p_sync.add_argument(
        "--provider",
        default=None,
        choices=("jira", "linear", "github", "gitlab"),
        help="sync only this provider (default: every enabled provider)",
    )
    p_list = tsub.add_parser("list", help="list tickets")
    p_list.add_argument("--project", default=None)
    p_list.add_argument(
        "--backlog",
        action="store_true",
        help="list only the backlog — exactly the ready-for-agent queue",
    )
    p_backlog = tsub.add_parser(
        "backlog", help="requeue a ticket to ready-for-agent (keeps run history)"
    )
    p_backlog.add_argument("ticket_id")
    p_backlog.add_argument(
        "--feedback",
        default=None,
        help="rejection feedback attached to the ticket on requeue",
    )
    p_backlog.add_argument("--project", default=None)
    p_context = tsub.add_parser("context", help="read or set a ticket's persisted extra context")
    p_context.add_argument("ticket_id")
    p_context.add_argument(
        "--set",
        dest="set_text",
        default=None,
        help="store this context on the ticket (printed when omitted)",
    )
    p_context.add_argument("--project", default=None)
    p_ticket.set_defaults(func=lambda a: _dispatch_ticket(a))

    p_notify = sub.add_parser(
        "notify", help="post an alert to a ticket's Slack thread (alerting only)"
    )
    p_notify.add_argument("ticket_id")
    p_notify.add_argument("text")
    p_notify.add_argument("--workbench", default=None, help="workbench URL")
    p_notify.add_argument("--mr", default=None, help="merge-request URL")
    p_notify.add_argument("--release", default=None, help="release URL")
    p_notify.add_argument("--tompero", default=None, help="tompero deployment URL")
    p_notify.add_argument("--project", default=None)
    p_notify.set_defaults(
        func=lambda a: notify_cmd.run(
            a.ticket_id,
            a.text,
            a.project,
            workbench=a.workbench,
            mr=a.mr,
            release=a.release,
            tompero=a.tompero,
        )
    )

    p_heal = sub.add_parser("heal", help="self-healing monitor daemon (start / stop / status)")
    p_heal.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status"])
    p_heal.set_defaults(func=lambda a: heal.main(a.action))

    p_sb = sub.add_parser(
        "sandbox", help="sandbox lifecycle (build / list / prune / stop / restart)"
    )
    sbsub = p_sb.add_subparsers(dest="sandbox_action", required=True)
    p_build = sbsub.add_parser(
        "build", help="build/refresh the sssf-runner image (streams docker progress)"
    )
    p_build.add_argument("--project", default=None)
    p_list = sbsub.add_parser("list", help="show sandboxes (adw_id · status · branch · container)")
    p_list.add_argument("--project", default=None)
    p_prune = sbsub.add_parser("prune", help="delete a run's branch + leftovers once resolved")
    p_prune.add_argument("adw_id", nargs="?", help="specific run; omit with --all")
    p_prune.add_argument("--all", action="store_true")
    p_prune.add_argument("--project", default=None)
    p_stop = sbsub.add_parser("stop", help="stop a live run (container + session)")
    p_stop.add_argument("adw_id")
    p_stop.add_argument("--project", default=None)
    p_restart = sbsub.add_parser("restart", help="re-run a session in its existing sandbox branch")
    p_restart.add_argument("adw_id")
    p_restart.add_argument("--project", default=None)
    p_sb.set_defaults(func=lambda a: _dispatch_sandbox(a))

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
