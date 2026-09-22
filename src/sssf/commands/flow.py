"""`sssf flow` — the three human-invoked flows: plan / implement / deploy.

Flows are the ONLY entry point for starting work (#89): ad-hoc `sssf run <adw>
"<prompt>"` is removed. Already-stamped projects' legacy ADWs stay runnable
through the legacy ticket-run path (`sssf ticket run`). The flow command is a
thin orchestration layer over the existing chain runner — it builds the prompt
from the ticket, then dispatches the flow chain (sandboxed by default).

The implement flow drives the ticket machine (#92): it claims the ticket
(ready-for-agent → in-progress) before the run spawns, records every run in
ticket_runs, and settles the outcome — success moves the ticket to
ready-for-signoff, failure requeues it fix-forward with the run's feedback.
The plan flow lands its db transform (#91) host-side: the sandbox monitor
settles a sandboxed run's end, `--no-sandbox` runs settle here from the ADW's
exit code.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from sssf import registry
from sssf.project import find_project


def _root(cwd: Path, explicit: str | None) -> Path | None:
    return find_project(cwd, explicit)


def _adw_file(root: Path, name: str) -> Path | None:
    from sssf.adw_modules import paths

    """Prefer the INSTALLED template for standard ADWs — a project's committed
    copy goes stale after an sssf upgrade. Custom ADWs (no installed template)
    fall back to the project's file (the legacy path for already-stamped
    projects whose flow chains were edited in place)."""
    project_file = paths.modules_dir(root) / f"{name}.py"
    import sssf

    installed = Path(sssf.__file__).parent / "templates" / "adws" / "modules" / f"{name}.py"
    if installed.exists():
        return installed
    return project_file if project_file.exists() else None


def _sandbox_enabled(root: Path) -> bool:
    from sssf import sandbox

    return sandbox.enabled(root, command="flow")


def _run_sandboxed(
    root: Path, adw_file: Path, args: list[str], adw_id: str | None = None, attach: bool = False
) -> int:
    """Create the per-run sandbox (worktree + container), run the ADW inside,
    and detach a teardown monitor (the sandbox tears itself down when the ADW
    exits — success or fail). The cwd is never touched; the run's branch
    sssf/<adw_id> survives as the deliverable. Deterministic Python."""
    from sssf.sandbox import SandboxError, spawn_monitor, spawn_sandbox
    from sssf.sandbox.docker import docker_available
    from sssf.sandbox.session_env import sandbox_env

    if not docker_available():
        print(
            "sssf: docker is not available — run `sssf sandbox build`? or use --no-sandbox",
            file=sys.stderr,
        )
        return 1
    from sssf.adw_modules import paths
    from sssf.adw_modules.agents import load_config

    cfg = load_config(str(paths.config_file(root)))

    adw_id = adw_id or uuid.uuid4().hex[:8]
    data_dir, pi_home, env = sandbox_env(root)
    try:
        spawn_sandbox(
            root,
            adw_id,
            cmd=["python", f"adws/modules/{adw_file.name}", *args, "--adw-id", adw_id],
            image=cfg.sandbox.image,
            data_dir=data_dir,
            pi_home=pi_home,
            env=env,
            attach=attach,
            review=cfg.sandbox.review.model_dump(),
        )
    except SandboxError as e:
        from sssf.sandbox import abort_sandbox

        abort_sandbox(root, adw_id)  # remove the stuck container + worktree
        print(f"sssf: sandbox spawn failed: {e}", file=sys.stderr)
        return 1
    spawn_monitor(root, adw_id)
    print(f"sssf: sandboxed run spawned — adw_id {adw_id} (auto-teardown on exit)")
    return 0


def _dispatch_chain(
    root: Path,
    adw_name: str,
    prompt: str,
    extra_args: list[str],
    no_sandbox: bool,
    adw_id: str | None = None,
) -> int:
    """Run a flow chain through the existing chain runner. The prompt is passed
    inline (a file path resolves via the ADW's resolve_prompt when given).

    `adw_id` pins the run (forwarded as `--adw-id` in both modes) — the
    implement and plan flows need the ticket link to point at the run that
    actually executes. None mints a fresh id inside the sandbox/ADW.
    """
    from sssf.adw_modules import paths

    paths.warn_if_legacy(root, command="flow")
    adw_file = _adw_file(root, adw_name)
    if adw_file is None:
        print(
            f"sssf: no flow chain '{adw_name}' (looked for adws/modules/{adw_name}.py)",
            file=sys.stderr,
        )
        return 1
    registry.update_last_run(root)
    if no_sandbox or not _sandbox_enabled(root):
        argv = [sys.executable, str(adw_file), prompt, *extra_args]
        if adw_id:
            argv += ["--adw-id", adw_id]
        return subprocess.call(argv, cwd=root)
    return _run_sandboxed(root, adw_file, [prompt, *extra_args], adw_id=adw_id)


def _ticket_prompt(root: Path, ticket_id: str) -> tuple[str, str, str] | None:
    """The prompt for a ticket flow: title + description + stored context.
    Returns None when the ticket does not exist."""
    import sqlite3

    from sssf import ticketing
    from sssf.adw_modules import paths

    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)
    row = conn.execute(
        "SELECT title, description, context, status, provider, external_id, source_url"
        " FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    title, description, context, status, provider, external_id, source_url = row
    from sssf.commands.ticket import _prompt_text

    prompt = _prompt_text(title, description or "", context or "", provider,
                          external_id or "", source_url or "")
    return prompt, status, title


_PLAN_NO_ARGS_PROMPT = (
    "Plan this feature request: explore the problem space against the "
    "codebase, grill the proposal against the project's ADRs and "
    "CONTEXT.md, write the spec, and slice it into implementation tickets."
)


def _land_plan_run(root: Path, conn: sqlite3.Connection, adw_id: str) -> int:
    """The plan flow's host-side settle (#91): run the db transform and report
    the landed lineage. Returns 0 when it landed (or had nothing to land); a
    successful run whose artifacts cannot be parsed is surfaced as an error.
    The caller owns conn (commit/close)."""
    from sssf import ticketing

    try:
        landed = ticketing.finish_plan_run(root, conn, adw_id, actor=_actor())
    except ValueError as error:
        print(
            f"sssf flow: plan run {adw_id} succeeded but the transform failed: {error}",
            file=sys.stderr,
        )
        return 1
    if landed:
        count = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE parent_id=?", (landed,)
        ).fetchone()[0]
        title_row = conn.execute(
            "SELECT title FROM tickets WHERE id=?", (landed,)
        ).fetchone()
        title = title_row[0] if title_row else ""
        print(
            f"sssf flow: plan landed — {landed} ({title}) now has"
            f" {count} implementation ticket(s)"
        )
    return 0


def plan(
    cwd: Path,
    ticket_id: str | None,
    explicit_project: str | None = None,
    skip_exploration: bool = False,
    no_sandbox: bool = False,
    revise: bool = False,
) -> int:
    """`sssf flow plan [<ticket-id>]` — turn an idea into a spec plus
    implementation tickets (issue #91).

    With a ticket, the plan chain runs on the ticket's prompt; the dispatch
    guards plan-once (implementation tickets are terminal plan inputs; an
    already-planned idea ticket needs `--revise` — the only re-plan escape),
    links the run (tickets.adw_id + ticket_runs) before the spawn, and the
    settle transforms the idea ticket into a spec reference + ready-for-agent
    children (sandboxed: the monitor; --no-sandbox: here from the exit code).

    With no arguments, exploration runs first and the settle creates the idea
    ticket from the landed spec — exploration → brief → idea ticket → spec →
    slices in one pass. Exploration is skippable (`--skip-exploration`).
    """
    import sqlite3

    from sssf import ticketing
    from sssf.adw_modules import paths

    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    extra = ["--skip-exploration"] if skip_exploration else []
    if ticket_id is None:
        adw_id = uuid.uuid4().hex[:8]
        code = _dispatch_chain(
            root, "adw_plan", _PLAN_NO_ARGS_PROMPT, extra, no_sandbox, adw_id=adw_id
        )
        if code == 0 and (no_sandbox or not _sandbox_enabled(root)):
            conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
            ticketing.ensure_schema(conn)
            code = _land_plan_run(root, conn, adw_id)
            conn.commit()
            conn.close()
        return code
    row = _ticket_prompt(root, ticket_id)
    if row is None:
        print(f"sssf flow: no ticket {ticket_id}", file=sys.stderr)
        return 1
    prompt, _status, _title = row

    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)
    guard = ticketing.plan_guard(conn, ticket_id, revise=revise)
    if guard:
        print(f"sssf flow: {guard}", file=sys.stderr)
        conn.close()
        return 1
    if _has_live_run(root, ticket_id):
        print(f"sssf flow: ticket {ticket_id} has a live run — wait for it to finish", file=sys.stderr)
        conn.close()
        return 1

    # Link the run BEFORE the spawn (the live-run guard and the settle both
    # read the link). The parent stays needs-triage — the machine has no
    # plan-claim edge, so the transform is the settle, not a transition.
    adw_id = uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT OR IGNORE INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)",
        (ticket_id, adw_id, _now()),
    )
    conn.execute(
        "UPDATE tickets SET adw_id=?, updated_at=? WHERE id=?",
        (adw_id, _now(), ticket_id),
    )
    conn.commit()

    sandboxed = not no_sandbox and _sandbox_enabled(root)
    code = _dispatch_chain(root, "adw_plan", prompt, extra, no_sandbox, adw_id=adw_id)
    if code == 0 and not sandboxed:
        code = _land_plan_run(root, conn, adw_id)
    conn.commit()
    conn.close()
    return code


def implement(
    cwd: Path,
    ticket_id: str,
    explicit_project: str | None = None,
    no_sandbox: bool = False,
) -> int:
    """`sssf flow implement <ticket-id>` — one ready-for-agent ticket end to
    end (triage → build → review) unattended, with the ticket machine (issue
    #92):

    - start: claims the ticket `ready-for-agent → in-progress` and records the
      run (ticket_runs + tickets.adw_id) BEFORE the run spawns, so a mid-run
      read sees in-progress and the sandbox monitor can settle it later;
    - success: the run settles `in-progress → ready-for-signoff` (sandboxed:
      the monitor, after the run ends; --no-sandbox: here, from the ADW's
      exit code);
    - rejection/failure: the ticket returns to `ready-for-agent` with the
      run's feedback attached (fix-forward) — the reviewer's blocking findings
      when the review rejected the build;
    - a sandbox spawn that dies before the ADW starts requeues the claim
      instead of leaving the ticket stuck in in-progress.
    """
    import sqlite3

    from sssf import ticketing
    from sssf.adw_modules import paths

    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    row = _ticket_prompt(root, ticket_id)
    if row is None:
        print(f"sssf flow: no ticket {ticket_id}", file=sys.stderr)
        return 1
    prompt, status, _title = row

    if status != ticketing.STATUS_READY:
        print(
            f"sssf flow: ticket {ticket_id} is {status!r} — only ready-for-agent"
            " tickets can start an implement run",
            file=sys.stderr,
        )
        return 1
    if _has_live_run(root, ticket_id):
        print(f"sssf flow: ticket {ticket_id} has a live run — wait for it to finish", file=sys.stderr)
        return 1

    # Claim the ticket + record the run BEFORE the run spawns (machine write,
    # audited). tickets is project-owned, so the claim happens here on the
    # host — never inside the sandbox.
    adw_id = uuid.uuid4().hex[:8]
    sandboxed = not no_sandbox and _sandbox_enabled(root)
    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)
    ticketing.transition_ticket(conn, ticket_id, ticketing.STATUS_IN_PROGRESS, actor=_actor())
    conn.execute(
        "INSERT OR IGNORE INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)",
        (ticket_id, adw_id, _now()),
    )
    conn.execute(
        "UPDATE tickets SET adw_id=?, updated_at=? WHERE id=?",
        (adw_id, _now(), ticket_id),
    )
    conn.commit()

    code = _dispatch_chain(root, "adw_implement", prompt, [], no_sandbox, adw_id=adw_id)
    if code != 0 and sandboxed:
        # A sandboxed spawn failure: nothing will ever run (no monitor is
        # watching), so requeue the claim fix-forward — never leave the
        # ticket stuck in in-progress.
        ticketing.transition_ticket(
            conn,
            ticket_id,
            ticketing.STATUS_READY,
            actor=_actor(),
            feedback="sandbox spawn failed — no run started",
        )
    elif not sandboxed:
        # The ADW ran on the host with direct db access — its session row is
        # already in the project db, so settle the machine now that the
        # outcome is known (sandboxed runs settle in the monitor).
        ticketing.finish_implement_run(conn, adw_id, actor=_actor())
    conn.commit()
    conn.close()
    return code


DEFAULT_AFK_CAP = 30


def implement_afk(
    cwd: Path,
    explicit_project: str | None = None,
    cap: int = DEFAULT_AFK_CAP,
    no_sandbox: bool = False,
) -> int:
    """`sssf flow implement afk` — the ralph loop (issue #93): work the
    ready-for-agent queue one ticket per run until it is empty or the cap is
    hit (default 30, `--cap`).

    Each run is a fresh context window: `implement()` spawns a new
    process/sandbox with a freshly minted adw_id per ticket, and agent
    sessions derive from that adw_id (`sssf-<adw_id>-…`), so nothing carries
    across rounds. A failed run is requeued fix-forward by `implement()` —
    the ticket stays ready-for-agent at the head of the queue (oldest first)
    and gets another shot next round; the cap bounds an unpassable ticket.

    Exit code 0 when the queue emptied; 1 when the cap stopped the loop with
    tickets still ready-for-agent (work outstanding — re-invoke afk to keep
    going).
    """
    import sqlite3

    from sssf import ticketing
    from sssf.adw_modules import paths

    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    if cap < 1:
        print(f"sssf flow: afk cap must be >= 1 (got {cap})", file=sys.stderr)
        return 1
    db_path = paths.data_dir(root) / "sssf.db"
    rounds = 0
    while rounds < cap:
        # A fresh connection per round: the previous round's implement()
        # wrote on its own connection (claims/settles) — never hold a stale
        # read snapshot or a lock across a run.
        conn = sqlite3.connect(str(db_path))
        ticketing.ensure_schema(conn)
        queue = ticketing.backlog_tickets(conn)
        conn.close()
        if not queue:
            print(
                "sssf flow: ralph loop done — ready-for-agent queue empty"
                f" after {rounds} run(s)"
            )
            return 0
        ticket = queue[0]
        rounds += 1
        # backlog_tickets rows are plain tuples: (id, provider, title, status,
        # kind, tracked, spec, adw_id).
        print(f"sssf flow: afk {rounds}/{cap} — {ticket[0]}: {ticket[2]}")
        # The run's outcome settles the machine inside implement(); a failure
        # requeues fix-forward and the loop keeps working the queue.
        implement(cwd, ticket[0], explicit_project, no_sandbox)
    conn = sqlite3.connect(str(db_path))
    ticketing.ensure_schema(conn)
    remaining = len(ticketing.backlog_tickets(conn))
    conn.close()
    if remaining:
        print(
            f"sssf flow: ralph loop stopped at the cap ({cap} run(s)) —"
            f" {remaining} ticket(s) still ready-for-agent",
            file=sys.stderr,
        )
        return 1
    return 0


def deploy(
    cwd: Path,
    explicit_project: str | None = None,
    yes: bool = False,
    no_sandbox: bool = False,
) -> int:
    """`sssf flow deploy` — batch-level release train dev → main. No ticket:
    the batch is the dev snapshot (signoff is batch-level)."""
    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    prompt = "Deploy the dev integration branch batch to main (release train)."
    return _dispatch_chain(root, "adw_deploy", prompt, ["--yes"] if yes else [], no_sandbox)


def _has_live_run(root: Path, ticket_id: str) -> bool:
    """True when the ticket's latest run is still 'running' — a live session
    must not be double-spawned (mirrors ticket.run's in-progress guard; read
    only, no transitions here)."""
    import sqlite3

    from sssf.adw_modules import paths

    try:
        conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
        row = conn.execute(
            "SELECT s.status FROM tickets t LEFT JOIN sessions s ON s.adw_id = t.adw_id"
            " WHERE t.id=?",
            (ticket_id,),
        ).fetchone()
        conn.close()
        return bool(row and row[0] == "running")
    except sqlite3.Error:
        return False


def _actor() -> str:
    """The operator behind a machine mutation — the audit trail's actor."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return "system"


def _now() -> str:
    """ISO-8601 UTC timestamp for the run-history rows (mirrors ticket.py)."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds")
