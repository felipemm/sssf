"""`sssf ticket` — ticketing integration (add / sync / list / run)."""

from __future__ import annotations

import sqlite3
import sys
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
        print("sssf: no project here (no .adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print(
            "sssf ticket: the internal provider is not enabled in .adws/config/ticketing.yaml",
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
        print("sssf: no project here (no .adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in .adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    results = ticketing.sync_tickets(
        root, cfg, providers=[provider] if provider else None
    )
    successful_providers = {
        result.provider for result in results if result.error is None and result.warning is None
    }
    mirror_failures: dict[str, int] = {}
    if successful_providers:
        from sssf import writeback

        conn = _db(root)
        mirror_failures = writeback.mirror_tickets(
            conn, providers=successful_providers, actor=_actor()
        )
        conn.close()
    for r in results:
        if r.warning:
            print(f"sssf ticket: {r.provider}: skipped: {r.warning}")
        elif r.error:
            print(f"sssf ticket: {r.provider}: {r.error}")
        else:
            suffix = (
                f"; {mirror_failures[r.provider]} tracker write(s) failed"
                if mirror_failures.get(r.provider)
                else ""
            )
            print(f"sssf ticket: {r.provider}: {r.tickets} ticket(s) synced{suffix}")
    return 0


def list_tickets(project: str | None = None, *, backlog_only: bool = False) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no .adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in .adws/config/ticketing.yaml",
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


def ticket_context(ticket_id: str, project: str | None = None, set_text: str | None = None) -> int:
    """Read (print) or set the persisted extra context of a ticket."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no .adws/). Run `sssf init` first.", file=sys.stderr)
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
        print("sssf: no project here (no .adws/). Run `sssf init` first.", file=sys.stderr)
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
    conn.close()
    print(f"sssf ticket: {ticket_id} back to the backlog (adw_id kept — history preserved)")
    return 0


