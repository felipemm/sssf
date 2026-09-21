"""`sssf mr` — attach an MR reference to a ticket (issue #98).

The monitor scans `ready-to-deploy` tickets with a registered MR and watches
GitLab for pipeline green / failed / merged. `sssf mr add` is the
deterministic way a ticket gets its MR (the deploy flow calls the same
`ticketing.register_mr` once it creates the dev→main MR); the human can also
register one by hand. One MR per ticket — registering again replaces it.
"""

from __future__ import annotations

import sqlite3
import sys
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


def _actor() -> str:
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return "unknown"


def add(
    ticket_id: str,
    repo: str,
    iid: str,
    project: str | None = None,
    *,
    url: str = "",
) -> int:
    """`sssf mr add <ticket-id> <repo> <iid> [--url URL]` — register an MR."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    try:
        row = conn.execute("SELECT id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if row is None:
            print(f"sssf mr: no ticket {ticket_id}", file=sys.stderr)
            return 1
        ticketing.register_mr(conn, ticket_id, repo, iid, url=url, actor=_actor())
        conn.commit()
    finally:
        conn.close()
    print(
        f"sssf mr: registered MR {repo}!{iid} for ticket {ticket_id}"
        + (f" ({url})" if url else "")
    )
    return 0


def list_mrs(project: str | None = None) -> int:
    """`sssf mr list` — every registered MR."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    try:
        rows = ticketing.ticket_mrs(conn)
    finally:
        conn.close()
    if not rows:
        print("sssf mr: no MRs registered")
        return 0
    for record in rows:
        ref = f"{record.repo}!{record.iid}"
        print(f"{record.ticket_id:<24} {ref}" + (f"  {record.url}" if record.url else ""))
    return 0


def rm(ticket_id: str, project: str | None = None) -> int:
    """`sssf mr rm <ticket-id>` — drop a ticket's MR reference."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    try:
        if ticketing.mr_for_ticket(conn, ticket_id) is None:
            print(f"sssf mr: no MR registered for {ticket_id}", file=sys.stderr)
            return 1
        ticketing.unregister_mr(conn, ticket_id, actor=_actor())
        conn.commit()
    finally:
        conn.close()
    print(f"sssf mr: removed MR reference from ticket {ticket_id}")
    return 0
