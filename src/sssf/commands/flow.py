"""`sssf flow` — the three human-invoked flows: plan / implement / deploy.

Flows are the ONLY entry point for starting work (#89): ad-hoc `sssf run <adw>
"<prompt>"` is removed. Already-stamped projects' legacy ADWs stay runnable
through the legacy ticket-run path (`sssf ticket run`).

The implement flow drives the ticket machine (#92): it claims the ticket
(ready-for-agent → in-progress) before the run spawns, records every run in
ticket_runs, and settles the outcome — success moves the ticket to
ready-for-signoff, failure requeues it fix-forward with the run's feedback.
The plan flow lands its db transform (#91) host-side: the sandbox monitor
settles a sandboxed run's end, `--no-sandbox` runs settle here from the ADW's
exit code. The deploy flow (#96) is a HOST-side orchestration: it brings up
the QA workbench from the `dev` branch (one workbench, one batch verdict),
signs the batch off at the terminal, and runs the deterministic release train
(bump → MR → e2e → release) in a release worktree at `dev` — rejection
re-queues the failing tickets fix-forward, approval moves them to
ready-to-deploy once the MR exists, and `--revert` is the escape hatch that
removes a genuinely unwanted ticket from dev by its own commits.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from sssf import registry
from sssf.project import find_project

DEPLOY_CONFIG = "adws/config/deploy.yaml"


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

    prompt = _prompt_text(
        title, description or "", context or "", provider, external_id or "", source_url or ""
    )
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
        title_row = conn.execute("SELECT title FROM tickets WHERE id=?", (landed,)).fetchone()
        title = title_row[0] if title_row else ""
        print(
            f"sssf flow: plan landed — {landed} ({title}) now has {count} implementation ticket(s)"
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
        print(
            f"sssf flow: ticket {ticket_id} has a live run — wait for it to finish", file=sys.stderr
        )
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
        print(
            f"sssf flow: ticket {ticket_id} has a live run — wait for it to finish", file=sys.stderr
        )
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


_DEPLOY_PROMPT = "Deploy the dev integration branch batch to main (release train)."


def _git(root: Path, *args: str) -> str:
    """One git query against the project root; empty string on failure."""
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip()


def _batch_commits(root: Path, dev: str) -> list[str]:
    """The dev snapshot: commits on dev not in main (newest first, as git
    logs them) — the batch this deploy ships."""
    base = (
        "origin/main"
        if _git(root, "rev-parse", "--verify", "--quiet", "origin/main^{commit}")
        else "main"
    )
    out = _git(root, "log", "--oneline", f"{base}..{dev}")
    return [line for line in out.splitlines() if line.strip()]


def _batch_tickets(conn: sqlite3.Connection, root: Path, commits: list[str]) -> list[str]:
    """The batch's tickets: the ready-for-signoff tickets whose work
    demonstrably landed in the dev snapshot. A ticket's commits are matched by
    message — the id (`#<ticket-id>`) or any of the ticket's run adw_ids
    (`sssf(<adw_id>)`, the chain's fallback commit subject). When NO commit
    reference parses, the operator's batch is everything awaiting signoff
    (ready-for-signoff is "work on dev waiting for the batch verdict").
    """
    from sssf import ticketing

    signoff = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM tickets WHERE status=?", (ticketing.STATUS_SIGNOFF,)
        ).fetchall()
    ]
    if not signoff:
        return []
    runs: dict[str, list[str]] = {}
    for tid, adw_id in conn.execute("SELECT ticket_id, adw_id FROM ticket_runs").fetchall():
        runs.setdefault(tid, []).append(adw_id)
    text = "\n".join(commits)
    matched = [
        tid
        for tid in signoff
        if f"#{tid}" in text or any(f"sssf({a})" in text for a in runs.get(tid, []))
    ]
    return matched or signoff


def _release_worktree(root: Path, adw_id: str, dev: str) -> Path | None:
    """The release-train worktree: a checkout at the `dev` BRANCH so the
    chain's commits (the bump) land on dev — never on the operator's checkout.
    When the operator is already ON dev, the chain runs in the project tree
    (the bump lands on dev either way). Returns None when the worktree cannot
    be created."""
    head = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if head == "dev":
        if _git(root, "status", "--porcelain"):
            print(
                "sssf flow: your checkout is on dev with uncommitted changes — the"
                " release train commits the bump onto dev (git add -A), so a dirty"
                " tree would sweep unrelated files. Commit or stash, or run deploy"
                " from a checkout on main.",
                file=sys.stderr,
            )
            return None
        return root
    wt = root / ".worktrees" / f"deploy-{adw_id}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    if dev == "dev":
        r = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "-q", str(wt), "dev"],
            capture_output=True,
            text=True,
        )
    else:  # origin/dev: create the local dev branch in the worktree
        r = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "-q", "-b", "dev", str(wt), "origin/dev"],
            capture_output=True,
            text=True,
        )
    if r.returncode != 0:
        print(
            "sssf flow: cannot create the release worktree at dev:"
            f" {r.stderr.strip()[:300]}\n"
            "  (is `dev` checked out in another worktree? run deploy from a"
            " checkout on main, or on dev itself)",
            file=sys.stderr,
        )
        return None
    return wt


def _repo(root: Path) -> str:
    """The origin remote as org/repo (for MR registration); '' without a
    remote or a parseable URL."""
    url = _git(root, "config", "--get", "remote.origin.url")
    if not url:
        return ""
    cleaned = url.removesuffix(".git")
    parts = [p for p in cleaned.replace(":", "/").split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else cleaned


def _read_mr_record(wt: Path, adw_id: str) -> dict:
    """The MR the deploy chain opened (title/url/iid), or {} when none was
    recorded (no glab — the payload markdown is the operator's manual path)."""
    path = wt / "adws" / "data" / "deploy" / f"{adw_id}-mr.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _deploy_failure_feedback(conn: sqlite3.Connection, adw_id: str) -> str:
    """What the failed release train attaches to the batch's tickets
    (fix-forward): the run's last error event, else a generic line."""
    row = conn.execute(
        "SELECT name, payload_json FROM events WHERE adw_id=? AND type='error'"
        " ORDER BY rowid DESC LIMIT 1",
        (adw_id,),
    ).fetchone()
    if row:
        try:
            payload = json.loads(row[1] or "{}")
        except ValueError:
            payload = {}
        text = payload.get("reason") or payload.get("error") or ""
        if text:
            return f"release train failed: {str(text)[:400]}"
        return f"release train failed ({row[0]})"
    return "the deploy flow run failed"


def _run_release_train(
    root: Path, conn: sqlite3.Connection, adw_id: str, dev: str, batch: list[str]
) -> int:
    """The deterministic release train after batch approval (#96): run the
    adw_deploy chain (bump → MR → e2e → release) in a release worktree at
    `dev`, sync its per-run db into the project db, then settle the batch's
    machine edges from the outcome — success moves the batch to
    ready-to-deploy and registers the MRs (#98 monitor); failure re-queues it
    fix-forward with the run's feedback."""
    from sssf import ticketing
    from sssf.sandbox.rundb import sync_run_db

    wt = _release_worktree(root, adw_id, dev)
    if wt is None:
        return 1
    adw_file = _adw_file(root, "adw_deploy")
    if adw_file is None:
        print(
            "sssf flow: no deploy chain 'adw_deploy' (looked for"
            " adws/modules/adw_deploy.py)",
            file=sys.stderr,
        )
        return 1
    argv = [sys.executable, str(adw_file), _DEPLOY_PROMPT, "--adw-id", adw_id]
    code = subprocess.call(argv, cwd=wt)
    try:  # the chain's per-run db (worktree adws/data) merges like any run
        sync_run_db(conn, wt / "adws" / "data" / "sssf.db", adw_id)
    except Exception as error:  # the trace is best-effort — the exit code is the truth
        print(f"sssf flow: could not sync the deploy run trace ({error})", file=sys.stderr)
    if code != 0:
        moved = ticketing.reject_deploy_batch(
            conn,
            batch,
            actor=_actor(),
            feedback=_deploy_failure_feedback(conn, adw_id),
        )
        conn.commit()
        print(
            f"sssf flow: release train failed — {len(moved)} ticket(s) requeued"
            " fix-forward (ready-for-agent)",
            file=sys.stderr,
        )
        return 1
    mr = _read_mr_record(wt, adw_id)
    moved = ticketing.approve_deploy_batch(
        conn,
        batch,
        actor=_actor(),
        mr_url=mr.get("url", ""),
        mr_iid=mr.get("iid", ""),
        repo=_repo(root),
    )
    conn.commit()
    print(f"sssf flow: release train landed — {len(moved)} ticket(s) moved to ready-to-deploy")
    if mr.get("url"):
        print(f"sssf flow: MR {mr['url']} — the monitor (#98) watches it")
    else:
        print(
            "sssf flow: no MR opened (glab absent) — the payload is under"
            " adws/data/deploy/; open the dev→main MR, then `sssf mr add` to"
            " register it for the monitor"
        )
    return 0


def _release_config(root: Path) -> dict:
    """The project's `release:` block from adws/config/deploy.yaml (issue
    #97): the per-project canary command, the promote command, and the promote
    poll settings. {} when absent — the tompero commands are per-project, so
    a project without a deployment pipeline skips the gates but still gets
    the release train and close-by-commits."""
    path = root / DEPLOY_CONFIG
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data.get("release") or {}


def _confirm_release_step(yes: bool, label: str) -> bool:
    """The operator's terminal confirmation for a release step (issue #97):
    a human checkpoint like the signoff — `--yes` is the explicit automation
    escape (it RUNS the step; it does not skip it)."""
    if yes:
        return True
    try:
        return input(f"{label} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


_RELEASE_DECLINED = 2
"""The gate's 'operator declined' outcome: the release pauses (tickets stay
ready-to-deploy, the MR is registered) — the deploy returns 0, unlike a
failure which parks tickets blocked and returns 1."""


def _park_batch_blocked(
    conn: sqlite3.Connection, batch: list[str], feedback: str, label: str
) -> int:
    """A release-gate failure parks the batch's ready-to-deploy tickets in
    `blocked` (visible and actionable, never silently retried) with the
    failure feedback attached — then the deploy returns 1. Returns 1 so the
    gate call sites can `return _park_batch_blocked(...)` directly."""
    from sssf import ticketing

    moved = ticketing.block_deploy_tickets(conn, batch, actor=_actor(), feedback=feedback)
    conn.commit()
    print(
        f"sssf flow: {label} — {len(moved)} ticket(s) parked in blocked",
        file=sys.stderr,
    )
    return 1


def _run_canary_gate(
    root: Path, conn: sqlite3.Connection, batch: list[str], rel: dict, yes: bool
) -> int:
    """The canary step (issue #97 AC1/AC2): runs only after the operator's
    terminal confirmation. A non-zero exit parks the batch's tickets in
    `blocked` (visible and actionable, never silently retried) with the
    command's stderr as fix-forward feedback. Returns 0 when the release
    continues (confirmed and passed, or no command configured); 1 on failure
    or when the operator declines (the release pauses — tickets stay
    ready-to-deploy, the MR is registered and the monitor watches it)."""

    command = (rel.get("canary") or {}).get("command")
    if not command:
        print("sssf flow: no canary command configured — skipping the canary step")
        return 0
    if not _confirm_release_step(yes, "run the canary step?"):
        print(
            "sssf flow: canary declined — release paused; tickets stay"
            " ready-to-deploy (MR registered, monitor watching)"
        )
        return _RELEASE_DECLINED
    r = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()[-300:] or "canary command failed"
        return _park_batch_blocked(
            conn, batch, f"canary failed: {detail}", "canary failed"
        )
    print("sssf flow: canary passed")
    return 0


def _run_promote_gate(
    root: Path, conn: sqlite3.Connection, batch: list[str], rel: dict, yes: bool
) -> int:
    """The promote step (issue #97 AC3): the per-project tompero
    canary-promote command runs only after the operator's terminal
    confirmation, then `status_command` is polled every `poll_interval_s`
    until its output carries `promoted_match` (default "promoted") or
    `poll_timeout_s` elapses. Failure or timeout parks the batch's tickets in
    `blocked`. Returns 0 when the release continues; 1 on failure/decline.
    """

    promote = rel.get("promote") or {}
    command = promote.get("command")
    if not command:
        print("sssf flow: no promote command configured — skipping the promote step")
        return 0
    if not _confirm_release_step(yes, "promote the canary to full deployment?"):
        print(
            "sssf flow: promote declined — release paused; tickets stay"
            " ready-to-deploy (MR registered, monitor watching)"
        )
        return _RELEASE_DECLINED
    r = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip()[-300:] or "promote command failed"
        return _park_batch_blocked(
            conn, batch, f"promote failed: {detail}", "promote failed"
        )
    status_command = promote.get("status_command")
    if not status_command:
        print("sssf flow: promote ran — no status_command configured, nothing to poll")
        return 0
    match = promote.get("promoted_match") or "promoted"
    interval = float(promote.get("poll_interval_s") or 15)
    timeout = float(promote.get("poll_timeout_s") or 1800)
    deadline = time.monotonic() + timeout
    detail = f"promotion not complete after {timeout:g}s (never matched {match!r})"
    while time.monotonic() < deadline:
        s = subprocess.run(status_command, cwd=root, capture_output=True, text=True)
        if match in (s.stdout + s.stderr):
            print("sssf flow: deployment fully promoted")
            return 0
        time.sleep(interval)
    return _park_batch_blocked(
        conn, batch, f"promote timed out: {detail}", "promote timed out"
    )


def _close_by_commits(root: Path, conn: sqlite3.Connection, batch: list[str]) -> list[str]:
    """The release close (issue #97 AC4): close-by-commits closes every
    ready-to-deploy ticket parsed from the MR's commit set (commits since the
    last tag on the dev snapshot; the whole snapshot when no tag exists yet).
    A ticket whose id (`#<id>`) or a run adw_id (`sssf(<adw_id>)`) appears in
    the commit set closes with the release — implementation tickets and their
    features close together. Only ready-to-deploy tickets move; a ticket
    parked blocked by a failed canary stays blocked. Returns the closed ids.
    """
    from sssf import ticketing

    last_tag = _git(root, "describe", "--tags", "--abbrev=0")
    if last_tag:
        commit_text = _git(root, "log", "--oneline", f"{last_tag}..HEAD")
    else:
        # first release: the whole dev snapshot is the MR's commit set
        commit_text = "\n".join(_batch_commits(root, "dev"))
    candidates = ticketing.release_ticket_ids(conn, commit_text)
    return ticketing.close_release_tickets(conn, candidates, actor=_actor())


def deploy(
    cwd: Path,
    explicit_project: str | None = None,
    yes: bool = False,
) -> int:
    """`sssf flow deploy` — batch-level release train dev → main, with the
    workbench signoff (issue #96). No ticket: the batch is the dev snapshot.

    Host-side by design: the signoff is the operator's terminal verdict (a
    detached runner container's stdin is /dev/null — the old sandboxed signoff
    could never be answered), and the only container the deploy flow runs is
    the WORKBENCH — the QA surface brought up from the `dev` branch with a
    published port. The deterministic release train (bump → MR → e2e →
    release) runs as the adw_deploy chain in a release worktree checked out at
    `dev`, so its git steps land on dev, and its per-run db is merged back
    into the project db.

    Machine edges (host, all legal transitions):
    - rejection: the failing ready-for-signoff tickets → ready-for-agent with
      the batch verdict as feedback (fix-forward), and the workbench is torn
      down ("the workbench is rebuilt" by the next run after the fix);
    - approval: the batch's ready-for-signoff tickets → ready-to-deploy once
      the dev→main MR exists, with the MR registered for the #98 monitor.
    """
    from sssf import ticketing, workbench
    from sssf.adw_modules import agents, paths

    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    registry.update_last_run(root)
    paths.warn_if_legacy(root, command="flow")

    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)

    dev = workbench.dev_ref(root)
    if dev is None:
        print(
            "sssf flow: no dev integration branch — run an implement flow first"
            " (the release train lands on dev, and deploy ships dev → main)",
            file=sys.stderr,
        )
        conn.close()
        return 1
    commits = _batch_commits(root, dev)
    if not commits:
        print("sssf flow: dev has nothing beyond main — nothing to deploy", file=sys.stderr)
        conn.close()
        return 1
    batch = _batch_tickets(conn, root, commits)

    print("── dev snapshot to ship ──")
    for line in commits:
        print(f"  {line}")
    if batch:
        print("batch tickets:")
        for tid in batch:
            print(f"  {tid}")
    else:
        print("  (no ready-for-signoff tickets matched this snapshot — the MR")
        print("   still ships the commits)")
    print("──────────────────────────")

    adw_id = uuid.uuid4().hex[:8]
    try:
        cfg = agents.load_config(str(paths.config_file(root)))
        wb = workbench.bring_up(root, adw_id, cfg.sandbox.image)
    except Exception as error:  # workbench.WorkbenchError + docker/config surprises
        print(f"sssf flow: workbench failed: {error}", file=sys.stderr)
        conn.close()
        return 1
    print(
        f"sssf flow: workbench up — {wb['url'] or '(port not resolved yet)'}"
        f" (container {wb['container']}; tear down with `sssf flow deploy --down`)"
    )

    if yes:
        verdict = "y"
    else:
        try:
            verdict = input("QA the workbench. Sign off this batch on dev? [y/N] ").strip().lower()
        except EOFError:
            verdict = "n"
    if verdict not in ("y", "yes"):
        failing = batch
        if batch:
            try:
                picked = input(
                    "which tickets failed QA? (ids, comma-separated; blank = the whole batch) "
                ).strip()
            except EOFError:
                picked = ""
            if picked:
                picked_ids = [p.strip() for p in picked.split(",") if p.strip()]
                failing = [t for t in batch if t in picked_ids or t.split(":")[-1] in picked_ids]
        moved = ticketing.reject_deploy_batch(
            conn,
            failing,
            actor=_actor(),
            feedback="batch rejected at signoff on the workbench",
        )
        conn.commit()
        workbench.tear_down(root, adw_id)
        print(
            f"sssf flow: batch rejected — {len(moved)} ticket(s) requeued"
            " fix-forward (ready-for-agent)"
        )
        print(
            "  fix-forward: a new implement run stacks the fix on dev, then re-run"
            " `sssf flow deploy` (the workbench is rebuilt)"
        )
        print("  to remove a ticket from dev permanently: `sssf flow deploy --revert <ticket-id>`")
        conn.close()
        return 0  # rejection is the expected outcome, not an error

    conn.commit()
    try:
        code = _run_release_train(root, conn, adw_id, dev, batch)
        if code != 0:
            return code
        rel = _release_config(root)
        code = _run_canary_gate(root, conn, batch, rel, yes)
        if code == _RELEASE_DECLINED:
            return 0
        if code != 0:
            return code
        code = _run_promote_gate(root, conn, batch, rel, yes)
        if code == _RELEASE_DECLINED:
            return 0
        if code != 0:
            return code
        closed = _close_by_commits(root, conn, batch)
        conn.commit()
        if closed:
            print(
                f"sssf flow: release closed {len(closed)} ticket(s) by the MR's"
                f" commit set ({', '.join(closed)})"
            )
        return 0
    finally:
        conn.close()


def deploy_down(
    cwd: Path,
    explicit_project: str | None = None,
    adw_id: str | None = None,
) -> int:
    """`sssf flow deploy --down [<adw-id>]` — the human's workbench teardown
    (ADR-0004: the workbench is brought up by deploy and torn down by the
    human). With no adw_id the latest workbench is torn down."""
    from sssf import workbench

    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    if workbench.tear_down(root, adw_id):
        print(f"sssf flow: workbench torn down{(' (' + adw_id + ')') if adw_id else ''}")
        return 0
    print("sssf flow: no workbench to tear down", file=sys.stderr)
    return 1


def deploy_revert(
    cwd: Path,
    ticket_id: str,
    explicit_project: str | None = None,
) -> int:
    """`sssf flow deploy --revert <ticket-id>` — the revert escape hatch
    (issue #96): remove a genuinely unwanted ticket from dev by its own
    commits BEFORE the MR, so the dev→main MR carries the clean snapshot. The
    ticket's commits on dev are those whose message references the ticket
    (`#<id>`) or one of its run adw_ids (`sssf(<adw_id>)`); they are reverted
    (newest first) on the dev branch and pushed. The ticket comes back
    ready-for-agent fix-forward — it can be re-implemented if wanted.
    """
    import sqlite3 as _sqlite3

    from sssf import ticketing
    from sssf.adw_modules import paths

    root = _root(cwd, explicit_project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)
    from sssf import workbench

    dev = workbench.dev_ref(root)
    if dev is None:
        print("sssf flow: no dev integration branch", file=sys.stderr)
        conn.close()
        return 1
    commits = _batch_commits(root, dev)
    runs = [
        r[0]
        for r in conn.execute(
            "SELECT adw_id FROM ticket_runs WHERE ticket_id=?", (ticket_id,)
        ).fetchall()
    ]
    targets = [
        line.split()[0]
        for line in commits
        if f"#{ticket_id}" in line or any(f"sssf({a})" in line for a in runs)
    ]
    if not targets:
        print(
            f"sssf flow: no commits on dev reference {ticket_id} — nothing to revert",
            file=sys.stderr,
        )
        conn.close()
        return 1
    adw_id = uuid.uuid4().hex[:8]
    wt = _release_worktree(root, adw_id, dev)
    if wt is None:
        conn.close()
        return 1
    r = subprocess.run(
        ["git", "-C", str(wt), "revert", "--no-edit", *targets],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(
            f"sssf flow: git revert failed: {r.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        conn.close()
        return 1
    if _git(root, "config", "--get", "remote.origin.url"):
        p = subprocess.run(
            ["git", "-C", str(wt), "push", "-q", "origin", "dev"],
            capture_output=True,
            text=True,
        )
        if p.returncode != 0:
            print(
                f"sssf flow: reverts committed on dev but the push failed:"
                f" {p.stderr.strip()[:300]} — push dev manually",
                file=sys.stderr,
            )
    moved = ticketing.revert_deploy_ticket(
        conn,
        ticket_id,
        actor=_actor(),
        feedback="reverted from dev by its own commits before the MR",
    )
    conn.commit()
    conn.close()
    if moved:
        print(
            f"sssf flow: reverted {len(targets)} commit(s) on dev — ticket"
            f" {ticket_id} requeued fix-forward (ready-for-agent)"
        )
        print(
            "  the revert removed the ticket from the dev snapshot — the next"
            " `sssf flow deploy` ships the clean batch"
        )
    else:
        print(
            f"sssf flow: commits reverted on dev, but ticket {ticket_id} is not"
            " waiting for signoff (ready-to-deploy or later) — the machine did"
            " not move it",
            file=sys.stderr,
        )
    return 0


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
