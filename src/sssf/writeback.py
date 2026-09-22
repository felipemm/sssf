"""Best-effort write-backs to origin trackers (issue #90).

State/label/comment changes on synced tickets can be pushed back to the
origin tracker through its CLI (gh / glab). Write-backs are best-effort: a
failure (missing binary, CLI error, tracker outage) appends a `ticket_events`
row (`writeback_failed`) and NEVER raises — the caller's flow is never
blocked by a tracker. Origins without a wired writer (jira, linear, unknown)
record a `writeback_skipped` event instead; `internal` tickets have no
external tracker and are a no-op.

The machine mutations themselves stay db-only (ticketing.py). These functions
are the host-side adapter that mirrors a mutation to the origin when the
operator wants it — called from `sssf ticket backlog` (reopen) and the
`sssf ticket writeback` command.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _run(cmd: list[str]) -> None:
    """Run one CLI command; nonzero exit raises RuntimeError with stderr."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )


def _record(conn: sqlite3.Connection, ticket_id: str, event_type: str, actor: str, payload: dict) -> None:
    """Append an audit row and commit it — a failed/skipped write-back must be
    durably visible, not lost with the caller's transaction."""
    conn.execute(
        "INSERT INTO ticket_events (ticket_id, event_type, actor, payload, created_at)"
        " VALUES (?,?,?,?,?)",
        (ticket_id, event_type, actor, json.dumps(payload, ensure_ascii=False), _now()),
    )
    conn.commit()


def _split_external(external_id: str) -> tuple[str, str] | None:
    """`owner/repo#12` → (repo, number); None when unparseable."""
    if "#" not in external_id:
        return None
    repo, _, number = external_id.rpartition("#")
    if not repo or not number:
        return None
    return repo, number


# ── per-origin writers ──────────────────────────────────────────────────────
# Each origin provides (state, comment, label) writers. Operations take the
# parsed (repo, number); state strings are passed through (github: open/closed;
# gitlab: opened/closed, with canonical "open" mapped to "opened").


def _github_state(repo: str, number: str, state: str) -> None:
    _run(["gh", "issue", "edit", number, "--repo", repo, "--state", state])


def _github_comment(repo: str, number: str, text: str) -> None:
    _run(["gh", "issue", "comment", number, "--repo", repo, "--body", text])


def _github_label(repo: str, number: str, label: str, add: bool) -> None:
    flag = "--add-label" if add else "--remove-label"
    _run(["gh", "issue", "edit", number, "--repo", repo, flag, label])


def _gitlab_state(repo: str, number: str, state: str) -> None:
    _run(["glab", "issue", "update", number, "--repo", repo, "--state", "opened" if state == "open" else state])


def _gitlab_comment(repo: str, number: str, text: str) -> None:
    _run(["glab", "issue", "note", number, "--repo", repo, "-m", text])


def _gitlab_label(repo: str, number: str, label: str, add: bool) -> None:
    flag = "--label" if add else "--unlabel"
    _run(["glab", "issue", "update", number, "--repo", repo, flag, label])


ORIGIN_WRITERS = {
    "github": (_github_state, _github_comment, _github_label),
    "gitlab": (_gitlab_state, _gitlab_comment, _gitlab_label),
}


def _dispatch(
    conn: sqlite3.Connection,
    ticket_id: str,
    operation: str,
    actor: str,
    op_index: int,
    *writer_args,
) -> None:
    """Resolve the ticket's origin and run the origin's writer best-effort.

    Every exit path is an event or a no-op — this function NEVER raises.
    """
    row = conn.execute(
        "SELECT origin, external_id FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if row is None:
        _record(
            conn,
            ticket_id,
            "writeback_failed",
            actor,
            {"operation": operation, "error": f"no ticket {ticket_id}"},
        )
        return
    origin, external_id = row
    if origin == "internal":
        return  # no external tracker to mirror to
    if origin not in ORIGIN_WRITERS or not external_id:
        _record(
            conn,
            ticket_id,
            "writeback_skipped",
            actor,
            {"origin": origin, "operation": operation},
        )
        return
    parsed = _split_external(external_id)
    if parsed is None:
        _record(
            conn,
            ticket_id,
            "writeback_failed",
            actor,
            {
                "origin": origin,
                "operation": operation,
                "error": f"cannot parse external_id {external_id!r}",
            },
        )
        return
    repo, number = parsed
    try:
        ORIGIN_WRITERS[origin][op_index](repo, number, *writer_args)
    except (RuntimeError, OSError) as error:
        _record(
            conn,
            ticket_id,
            "writeback_failed",
            actor,
            {"origin": origin, "operation": operation, "error": str(error)},
        )


def writeback_state(conn: sqlite3.Connection, ticket_id: str, state: str, *, actor: str = "system") -> None:
    """Push a state change to the origin tracker (best-effort)."""
    _dispatch(conn, ticket_id, "state", actor, 0, state)


def writeback_comment(conn: sqlite3.Connection, ticket_id: str, text: str, *, actor: str = "system") -> None:
    """Push a comment to the origin tracker (best-effort)."""
    _dispatch(conn, ticket_id, "comment", actor, 1, text)


def writeback_label(
    conn: sqlite3.Connection, ticket_id: str, label: str, *, add: bool = True, actor: str = "system"
) -> None:
    """Push a label add/remove to the origin tracker (best-effort)."""
    _dispatch(conn, ticket_id, "label", actor, 2, label, add)
