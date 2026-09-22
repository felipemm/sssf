"""`sssf ticket` — ticketing integration (add / sync / list / run)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sssf import ticketing
from sssf.adw_modules import paths
from sssf.project import find_project


def _root(explicit: str | None) -> Path | None:
    return find_project(Path.cwd(), explicit)


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new(title: str, project: str | None = None) -> int:
    """`sssf ticket new` — capture an idea as a title-only shell, born
    needs-triage (blank spec, tracked, internal origin). The plan flow turns
    it into a spec plus implementation tickets."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print(
            "sssf ticket: the internal provider is not enabled in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    conn = _db(root)
    ticket_id = ticketing.create_idea_ticket(conn, title, actor=_actor())
    conn.commit()
    conn.close()
    print(
        f"sssf ticket: added idea ticket {title!r} ({ticket_id}) — born needs-triage,"
        " blank spec; run `sssf flow plan` (once landed) to spec and slice it"
    )
    return 0


def add(title: str, project: str | None = None, *, description: str = "",
        prompt_file: str | None = None) -> int:
    """The legacy `sssf ticket add` — keep its 'immediately implementable'
    semantics on the machine: a tracked internal implementation ticket born
    ready-for-agent (the backlog queue). prompt_file becomes the spec
    reference."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print(
            "sssf ticket: the internal provider is not enabled in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    ticket_id = f"internal:{uuid.uuid4().hex[:12]}"
    now = _now()
    conn = _db(root)
    rel_prompt = ""
    if prompt_file:
        rel_prompt = str(Path(prompt_file).resolve().relative_to(root))
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status,"
        " kind, tracked, origin, spec, source_url, prompt_file, created_at, updated_at)"
        " VALUES (?,?,'',?,?,?,'implementation',1,'internal',?,'',?,?,?)",
        (
            ticket_id,
            "internal",
            title,
            description,
            ticketing.STATUS_READY,
            rel_prompt,
            rel_prompt,
            now,
            now,
        ),
    )
    ticketing.add_ticket_event(
        conn, ticket_id, "created", actor=_actor(), payload={"kind": "implementation"}
    )
    conn.commit()
    conn.close()
    print(f"sssf ticket: added internal ticket {title!r} ({ticket_id}) — ready-for-agent")
    return 0


def _actor() -> str:
    """The operator behind a CLI mutation — the audit trail's actor."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return "system"


def sync(project: str | None = None, *, provider: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    results = ticketing.sync_tickets(
        root, cfg, providers=[provider] if provider else None
    )
    for r in results:
        if r.warning:
            print(f"sssf ticket: {r.provider}: skipped: {r.warning}")
        elif r.error:
            print(f"sssf ticket: {r.provider}: {r.error}")
        else:
            print(f"sssf ticket: {r.provider}: {r.tickets} ticket(s) synced")
    return 0


def list_tickets(project: str | None = None, *, backlog_only: bool = False) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    conn = _db(root)
    if backlog_only:
        # The backlog is exactly the ready-for-agent queue, nothing else.
        rows = ticketing.backlog_tickets(conn)
    else:
        rows = conn.execute(
            "SELECT id, provider, title, status, spec, adw_id FROM tickets"
            " ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    conn.close()
    for row in rows:
        print(f"{row[0]:20} {row[1]:8} {row[2][:50]:50} {row[3]:14} {row[4] or ''} {row[5] or ''}")
    if backlog_only:
        print(f"sssf ticket: backlog ({len(rows)} ready-for-agent ticket(s))")
    else:
        print(f"sssf ticket: {len(rows)} ticket(s)")
    return 0


def _prompt_text(
    title: str,
    description: str,
    context: str,
    provider: str,
    external_id: str,
    source_url: str,
) -> str:
    """The prompt body: title, description, optional operator context, provenance."""
    text = f"# {title}\n\n{description}\n"
    if context:
        text += f"\n## Run context\n\n{context}\n"
    return text + f"\n---\nGenerated from {provider} ticket {external_id or ''} ({source_url})\n"


def _is_generated_prompt(prompt: Path, provider: str) -> bool:
    """True for a prompt written by a previous `sssf ticket run` (it carries
    the provenance trailer); False for an interview-flow spec."""
    return f"Generated from {provider} ticket" in prompt.read_text()


def ticket_context(ticket_id: str, project: str | None = None, set_text: str | None = None) -> int:
    """Read (print) or set the persisted extra context of a ticket."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute("SELECT context FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    if set_text is None:
        print(row[0] or "")
        conn.close()
        return 0
    conn.execute(
        "UPDATE tickets SET context=?, updated_at=? WHERE id=?", (set_text, _now(), ticket_id)
    )
    conn.commit()
    conn.close()
    print(f"sssf ticket: context saved for {ticket_id}")
    return 0


def run(
    ticket_id: str, project: str | None = None, no_sandbox: bool = False, context: str = ""
) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    conn = _db(root)
    row = conn.execute(
        "SELECT id, title, description, context, status, provider, external_id,"
        " source_url, prompt_file FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    tid, title, description, stored_context, status, provider, external_id, source_url, prompt_file = row
    if context:
        # --context wins for this run and is persisted for later ones.
        conn.execute(
            "UPDATE tickets SET context=?, updated_at=? WHERE id=?", (context, _now(), tid)
        )
        conn.commit()
    else:
        context = stored_context or ""
    if status == ticketing.STATUS_IN_PROGRESS:
        conn.close()
        print(f"sssf ticket: {ticket_id} is already running", file=sys.stderr)
        return 1
    if status != ticketing.STATUS_READY:
        conn.close()
        print(
            f"sssf ticket: {ticket_id} is {status!r} — only ready-for-agent tickets can"
            " start a run",
            file=sys.stderr,
        )
        return 1
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40] or "ticket"
    adw_id = uuid.uuid4().hex[:8]
    # The LEGACY ticket-run path: runs the project's own adw_simple_sdlc.py.
    # Already-stamped projects keep theirs after the chain-set reduction (#89);
    # fresh projects use the implement flow instead.
    adw_file = paths.modules_dir(root) / "adw_simple_sdlc.py"
    if not adw_file.exists():
        conn.close()
        print(
            f"sssf ticket: no adws/modules/adw_simple_sdlc.py in {root} — this"
            " legacy path exists for already-stamped projects; fresh projects run"
            " `sssf flow implement <ticket-id>`",
            file=sys.stderr,
        )
        return 1

    sandboxed = not no_sandbox and _sandbox_enabled(root)
    if sandboxed:
        # The prompt lives in the WORKTREE (per-run dir → no NN race) and is
        # committed with the run; the container runs from the worktree.
        from sssf.sandbox import SandboxError, spawn_monitor, spawn_sandbox
        from sssf.sandbox.docker import docker_available
        from sssf.sandbox.session_env import sandbox_env

        if not docker_available():
            conn.close()
            print(
                "sssf ticket: docker is not available — run `sssf sandbox build`? or --no-sandbox",
                file=sys.stderr,
            )
            return 1
        from sssf.sandbox.worktree_git import create_worktree

        wt = create_worktree(root, adw_id)
        # The interview flow's spec is an input artifact, like the prompt: the
        # worktree checked out origin/main and may not carry a spec that is
        # only committed locally or not yet pushed, so copy it verbatim.
        # Run-generated prompts (provenance trailer) are regenerated so the
        # current --context / stored context is baked in.
        if prompt_file and (root / prompt_file).exists() and not _is_generated_prompt(
            root / prompt_file, provider
        ):
            spec_path = ticketing.next_prompt_name(wt, slug)
            spec_path.write_text((root / prompt_file).read_text())
            prompt_path = spec_path
        else:
            prompt_path = ticketing.next_prompt_name(wt, slug)
            prompt_path.write_text(
                _prompt_text(title, description, context, provider, external_id, source_url)
            )
        cfg = _config_for_sandbox(root)
        data_dir, pi_home, env = sandbox_env(root)
        try:
            spawn_sandbox(
                root,
                adw_id,
                cmd=[
                    "python",
                    "adws/modules/adw_simple_sdlc.py",
                    f"run prompt adws/prompts/{prompt_path.name}",
                    "--adw-id",
                    adw_id,
                ],
                image=cfg.sandbox.image,
                data_dir=data_dir,
                pi_home=pi_home,
                env=env,
                worktree=wt,  # already created above — the prompt lives here
            )
        except SandboxError as e:
            conn.close()
            from sssf.sandbox import abort_sandbox

            abort_sandbox(root, adw_id)  # remove the stuck container + worktree
            print(f"sssf ticket: sandbox spawn failed: {e}", file=sys.stderr)
            return 1
        spawn_monitor(root, adw_id)
        rel_prompt = Path("adws") / "prompts" / prompt_path.name
    else:
        # The interview flow pre-writes the spec and links it as prompt_file —
        # honor it verbatim; only generate a thin prompt when none exists.
        # A prompt_file left by a PREVIOUS run is a generated thin prompt (it
        # carries the provenance trailer) — regenerate it so the current
        # --context / stored context is baked in.
        if (
            prompt_file
            and (root / prompt_file).exists()
            and not _is_generated_prompt(root / prompt_file, provider)
        ):
            rel_prompt = Path(prompt_file)
            subprocess.Popen(
                [sys.executable, str(adw_file), f"run prompt {rel_prompt}", "--adw-id", adw_id],
                cwd=root,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ticketing.transition_ticket(conn, tid, ticketing.STATUS_IN_PROGRESS, actor=_actor())
            conn.execute(
                "INSERT OR IGNORE INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)",
                (tid, adw_id, _now()),
            )
            conn.execute(
                "UPDATE tickets SET adw_id=?, prompt_file=?, updated_at=? WHERE id=?",
                (adw_id, str(rel_prompt), _now(), tid),
            )
            conn.commit()
            conn.close()
            print(
                f"sssf ticket: run spawned for {tid} — prompt adws/prompts/{rel_prompt.name} (existing prompt_file)"
            )
            return 0
        prompt_path = ticketing.next_prompt_name(root, slug)
        prompt_path.write_text(
            _prompt_text(title, description, context, provider, external_id, source_url)
        )
        rel_prompt = prompt_path.relative_to(root)
        subprocess.Popen(
            [sys.executable, str(adw_file), f"run prompt {rel_prompt}", "--adw-id", adw_id],
            cwd=root,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    ticketing.transition_ticket(conn, tid, ticketing.STATUS_IN_PROGRESS, actor=_actor())
    # The run's history: every spawn is a row, so a retried ticket accumulates
    # its attempts (the failed run stays linked for the trace and the retry
    # color). tickets.adw_id remains the LATEST run.
    conn.execute(
        "INSERT OR IGNORE INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)",
        (tid, adw_id, _now()),
    )
    conn.execute(
        "UPDATE tickets SET adw_id=?, prompt_file=?, updated_at=? WHERE id=?",
        (adw_id, str(rel_prompt), _now(), tid),
    )
    conn.commit()
    conn.close()
    print(
        f"sssf ticket: run spawned for {ticket_id} — adw_id {adw_id}, prompt {rel_prompt}"
        + (" (sandboxed)" if sandboxed else "")
    )
    return 0


def backlog(ticket_id: str, project: str | None = None, *, feedback: str | None = None) -> int:
    """Requeue a ticket to ready-for-agent — the manual retry control.

    Legal from needs-triage (triage passes), in-progress (rejection,
    fix-forward), ready-for-signoff (signoff rejection), or blocked (human
    unblocks); `done` is terminal and `ready-to-deploy` stays in the pipeline.
    The adw_id link and ticket_runs history are PRESERVED: a retried ticket
    keeps its failed runs visible in the trace and in the ticket modal. The
    only refusal is a still-running session — no yanking a live run.
    """
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute("SELECT status, adw_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    _stored, adw_id = row
    if adw_id:
        try:
            session = conn.execute(
                "SELECT status FROM sessions WHERE adw_id=?", (adw_id,)
            ).fetchone()
            if session and session[0] == "running":
                conn.close()
                print(
                    f"sssf ticket: {ticket_id} is still running — wait for it to"
                    " finish before putting it back",
                    file=sys.stderr,
                )
                return 1
        except sqlite3.Error:
            pass  # no sessions table yet — nothing running
    try:
        ticketing.transition_ticket(
            conn, ticket_id, ticketing.BACKLOG_STATUS, actor=_actor(), feedback=feedback
        )
    except ValueError as error:
        conn.close()
        print(f"sssf ticket: {error}", file=sys.stderr)
        return 1
    conn.commit()
    # Requeue = reopen on the origin tracker (best-effort; internal no-op;
    # failures land in ticket_events and never block the requeue).
    from sssf import writeback

    writeback.writeback_state(conn, ticket_id, "open", actor=_actor())
    conn.close()
    print(f"sssf ticket: {ticket_id} back to the backlog (adw_id kept — history preserved)")
    return 0


def writeback_cmd(
    ticket_id: str,
    project: str | None = None,
    *,
    state: str | None = None,
    comment: str | None = None,
    label: str | None = None,
    remove_label: str | None = None,
) -> int:
    """`sssf ticket writeback <ticket-id> [--state S] [--comment TEXT]
    [--label L] [--remove-label L]` — push state/label/comment changes to the
    origin tracker best-effort. Failures are recorded as ticket_events and
    never raise; internal tickets are a no-op.
    """
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    if state is None and comment is None and label is None and remove_label is None:
        print(
            "sssf ticket: writeback needs at least one of --state, --comment,"
            " --label, --remove-label",
            file=sys.stderr,
        )
        return 1
    from sssf import writeback

    conn = _db(root)
    if state is not None:
        writeback.writeback_state(conn, ticket_id, state, actor=_actor())
    if comment is not None:
        writeback.writeback_comment(conn, ticket_id, comment, actor=_actor())
    if label is not None:
        writeback.writeback_label(conn, ticket_id, label, add=True, actor=_actor())
    if remove_label is not None:
        writeback.writeback_label(conn, ticket_id, remove_label, add=False, actor=_actor())
    conn.close()
    print(f"sssf ticket: writeback recorded for {ticket_id}")
    return 0


def _sandbox_enabled(root: Path) -> bool:
    from sssf import sandbox

    return sandbox.enabled(root, command="ticket")


def _config_for_sandbox(root: Path):
    from sssf.adw_modules import paths
    from sssf.adw_modules.agents import load_config

    return load_config(str(paths.config_file(root)))
