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
    mr,
    notify_cmd,
    obs_cmds,
    sandbox_cmd,
    spec,
    sweep,
    ticket,
    viz,
)
from sssf.project import data_dir, find_project


def _dispatch_ticket(a) -> int:
    action = a.ticket_action
    if action == "new":
        return ticket.new(a.title, a.project)
    if action == "add":
        return ticket.add(
            a.title, a.project, description=a.description or "", prompt_file=a.prompt_file
        )
    if action == "sync":
        return ticket.sync(a.project)
    if action == "list":
        return ticket.list_tickets(a.project, backlog_only=a.backlog)
    if action == "run":
        return ticket.run(a.ticket_id, a.project, a.no_sandbox, a.context or "")
    if action == "context":
        return ticket.ticket_context(a.ticket_id, a.project, a.set_text)
    if action == "backlog":
        return ticket.backlog(a.ticket_id, a.project, feedback=a.feedback)
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

    for name, fn in (
        ("phases", obs_cmds.phases),
        ("tail", obs_cmds.tail),
        ("procs", obs_cmds.procs),
    ):
        p = sub.add_parser(name, help=f"trace: {name} <adw_id>")
        p.add_argument("--project", default=None)
        p.add_argument("adw_id")
        p.set_defaults(func=scoped_cmd(fn))


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
        help="the ticket to implement (ready-for-agent), or `afk` to work the whole queue",
    )
    p_fimpl.add_argument(
        "--cap",
        type=int,
        default=flow.DEFAULT_AFK_CAP,
        help=f"afk: max rounds before stopping (default {flow.DEFAULT_AFK_CAP})",
    )
    p_fimpl.add_argument("--project", default=None)
    p_fimpl.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run in the current dir instead of a sandbox container",
    )
    p_fimpl.set_defaults(
        func=lambda a: (
            flow.implement_afk(Path.cwd(), a.project, a.cap, a.no_sandbox)
            if a.ticket_id == "afk"
            else flow.implement(Path.cwd(), a.ticket_id, a.project, a.no_sandbox)
        )
    )
    p_fdep = fsub.add_parser(
        "deploy", help="release train: batch signoff on dev → bump → MR → e2e → release"
    )
    p_fdep.add_argument("--project", default=None)
    p_fdep.add_argument(
        "--yes",
        action="store_true",
        help="auto-approve the signoff gate (explicit automation escape)",
    )
    p_fdep.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run in the current dir instead of a sandbox container",
    )
    p_fdep.set_defaults(func=lambda a: flow.deploy(Path.cwd(), a.project, a.yes, a.no_sandbox))

    _register_obs(sub)

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

    p_ticket = sub.add_parser(
        "ticket", help="ticketing integration (new / add / sync / list / run)"
    )
    tsub = p_ticket.add_subparsers(dest="ticket_action", required=True)
    p_new = tsub.add_parser("new", help="create an idea ticket (title-only, born needs-triage)")
    p_new.add_argument("title")
    p_new.add_argument("--project", default=None)
    p_add = tsub.add_parser(
        "add", help="create an internal implementation ticket (ready-for-agent)"
    )
    p_add.add_argument("title")
    p_add.add_argument("--description", default=None, help="short summary stored on the ticket")
    p_add.add_argument(
        "--prompt-file",
        default=None,
        help="relative path to the interview spec (adws/prompts/NN-<slug>.md) linked to this ticket",
    )
    p_add.add_argument("--project", default=None)
    p_sync = tsub.add_parser(
        "sync", help="fetch external tickets into the queue (born needs-triage, untracked)"
    )
    p_sync.add_argument("--project", default=None)
    p_list = tsub.add_parser("list", help="list tickets")
    p_list.add_argument("--project", default=None)
    p_list.add_argument(
        "--backlog",
        action="store_true",
        help="list only the backlog — exactly the ready-for-agent queue",
    )
    p_run = tsub.add_parser("run", help="spawn simple_sdlc for a ticket")
    p_run.add_argument("ticket_id")
    p_run.add_argument("--project", default=None)
    p_run.add_argument(
        "--context",
        default=None,
        help="extra context/steer appended to the prompt for this run",
    )
    p_run.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run in the current dir instead of a sandbox container",
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
    p_context = tsub.add_parser(
        "context", help="read or set a ticket's persisted extra context"
    )
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

    p_mr = sub.add_parser("mr", help="register an MR against a ticket (monitor scan set)")
    msub = p_mr.add_subparsers(dest="mr_action", required=True)
    p_mr_add = msub.add_parser("add", help="attach one MR to a ticket (upsert)")
    p_mr_add.add_argument("ticket_id")
    p_mr_add.add_argument("repo", help="GitLab project path, e.g. group/project")
    p_mr_add.add_argument("iid")
    p_mr_add.add_argument("--url", default="", help="human-facing MR URL (notified)")
    p_mr_add.add_argument("--project", default=None)
    p_mr_add.set_defaults(func=lambda a: mr.add(a.ticket_id, a.repo, a.iid, a.project, url=a.url))
    p_mr_list = msub.add_parser("list", help="list every registered MR")
    p_mr_list.add_argument("--project", default=None)
    p_mr_list.set_defaults(func=lambda a: mr.list_mrs(a.project))
    p_mr_rm = msub.add_parser("rm", help="drop a ticket's MR reference")
    p_mr_rm.add_argument("ticket_id")
    p_mr_rm.add_argument("--project", default=None)
    p_mr_rm.set_defaults(func=lambda a: mr.rm(a.ticket_id, a.project))

    p_heal = sub.add_parser("heal", help="self-healing monitor daemon (start / stop / status)")
    p_heal.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status"])
    p_heal.set_defaults(func=lambda a: heal.main(a.action))

    p_spec = sub.add_parser("spec", help="interview-driven ticket creation")
    p_spec_create = p_spec.add_subparsers(dest="spec_action", required=True)
    p_sc = p_spec_create.add_parser("create", help="start a product-manager interview")
    p_sc.add_argument("--mode", default="idea", choices=["idea", "bug", "feature"])
    p_sc.add_argument("--title", default=None)
    p_sc.add_argument("--project", default=None)
    p_sc.set_defaults(func=lambda a: spec.create(a.mode, a.title, Path.cwd(), a.project))


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
    p_restart = sbsub.add_parser(
        "restart", help="re-run a session in its existing sandbox branch"
    )
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
