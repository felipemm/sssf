"""Best-effort tracker writers used only by `sssf ticket sync`.

Tracker state remains a mirror of the local ticket machine. Sync records
write failures as ticket events and never blocks the local ticket workflow.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

CONTROL_LABELS = ("needs-triage", "ready-for-agent", "blocked")
CLOSING_COMMENT = "Closed by sssf: ticket reached done."


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
    """Append an audit row and commit it — a failed sync must be durably visible."""
    conn.execute(
        "INSERT INTO ticket_events (ticket_id, event_type, actor, payload, created_at)"
        " VALUES (?,?,?,?,?)",
        (ticket_id, event_type, actor, json.dumps(payload, ensure_ascii=False), _now()),
    )
    conn.commit()


def _split_external(origin: str, external_id: str) -> tuple[str, str] | None:
    """Parse hosted-forge `<repo>#<number>` ids and Jira issue keys."""
    if origin == "jira":
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", external_id):
            return external_id, external_id
        return None
    if "#" not in external_id:
        return None
    repo, _, number = external_id.rpartition("#")
    if not repo or not number:
        return None
    return repo, number


# ── per-origin writers ──────────────────────────────────────────────────────


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


def _jira_state(_key: str, key: str, state: str) -> None:
    status = "Closed" if state == "closed" else state
    _run(["acli", "jira", "workitem", "transition", "--key", key, "--status", status, "--yes"])


def _jira_comment(_key: str, key: str, text: str) -> None:
    _run(["acli", "jira", "workitem", "comment", "create", "--key", key, "--body", text])


def _jira_label(_key: str, key: str, label: str, add: bool) -> None:
    flag = "--labels" if add else "--remove-labels"
    _run(["acli", "jira", "workitem", "edit", "--key", key, flag, label, "--yes"])


WriterOperation = Literal["state", "comment", "label"]
ORIGIN_WRITERS: dict[str, dict[WriterOperation, Callable[..., None]]] = {
    "github": {"state": _github_state, "comment": _github_comment, "label": _github_label},
    "gitlab": {"state": _gitlab_state, "comment": _gitlab_comment, "label": _gitlab_label},
    "jira": {"state": _jira_state, "comment": _jira_comment, "label": _jira_label},
}


def _dispatch(
    conn: sqlite3.Connection,
    ticket_id: str,
    operation: WriterOperation,
    actor: str,
    *writer_args,
) -> bool:
    """Resolve the ticket's origin and run one writer operation best-effort."""
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
        return False
    origin, external_id = row
    if origin == "internal":
        return True
    if origin not in ORIGIN_WRITERS or not external_id:
        _record(
            conn,
            ticket_id,
            "writeback_skipped",
            actor,
            {"origin": origin, "operation": operation},
        )
        return False
    parsed = _split_external(origin, external_id)
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
        return False
    repo, number = parsed
    try:
        ORIGIN_WRITERS[origin][operation](repo, number, *writer_args)
    except (RuntimeError, OSError) as error:
        _record(
            conn,
            ticket_id,
            "writeback_failed",
            actor,
            {"origin": origin, "operation": operation, "error": str(error)},
        )
        return False
    return True


def _dispatch_once(
    conn: sqlite3.Connection,
    ticket_id: str,
    checkpoint_key: str,
    operation: WriterOperation,
    actor: str,
    *writer_args,
) -> bool:
    """Run a one-shot mirror operation once per machine transition."""
    if conn.execute(
        "SELECT 1 FROM ticket_mirror_operations WHERE ticket_id=? AND operation_key=?",
        (ticket_id, checkpoint_key),
    ).fetchone():
        return True
    if not _dispatch(conn, ticket_id, operation, actor, *writer_args):
        return False
    conn.execute(
        "INSERT INTO ticket_mirror_operations (ticket_id, operation_key, completed_at)"
        " VALUES (?,?,?)",
        (ticket_id, checkpoint_key, _now()),
    )
    conn.commit()
    return True


def _rejection_feedback(conn: sqlite3.Connection, ticket_id: str, status: str) -> str | None:
    """Return only feedback attached to the latest transition into the queue."""
    if status != "ready-for-agent":
        return None
    row = conn.execute(
        "SELECT payload FROM ticket_events WHERE ticket_id=? AND event_type='transition'"
        " ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        return None
    feedback = payload.get("feedback")
    if payload.get("to") == status and isinstance(feedback, str) and feedback.strip():
        return feedback
    return None


def mirror_tickets(
    conn: sqlite3.Connection,
    *,
    providers: set[str] | None = None,
    actor: str = "system",
) -> dict[str, int]:
    """Ensure control labels and mirror close/comments for synced tickets.

    Labels are ensured on each sync by touching only the known control-label
    vocabulary. Successful close and comment operations are checkpointed
    independently; machine transitions clear those checkpoints. ``mirrored_at``
    remains the watermark for a completely successful mirror pass.
    Returns failed write counts grouped by origin.
    """
    query = (
        "SELECT id, origin, status FROM tickets"
        " WHERE tracked=0 AND origin != 'internal'"
    )
    params: tuple = ()
    if providers is not None:
        if not providers:
            return {}
        provider_list = sorted(providers)
        query += f" AND origin IN ({','.join('?' for _ in provider_list)})"
        params = tuple(provider_list)
    rows = conn.execute(query + " ORDER BY id", params).fetchall()
    failures: dict[str, int] = {}
    for ticket_id, origin, status in rows:
        if origin not in ORIGIN_WRITERS:
            continue
        failed = 0
        desired = status if status in CONTROL_LABELS else None
        for label in CONTROL_LABELS:
            if label != desired and not _dispatch(
                conn, ticket_id, "label", actor, label, False
            ):
                failed += 1
        if desired is not None and not _dispatch(
            conn, ticket_id, "label", actor, desired, True
        ):
            failed += 1

        if status == "done":
            if not _dispatch_once(
                conn, ticket_id, "done:close", "state", actor, "closed"
            ):
                failed += 1
            if not _dispatch_once(
                conn,
                ticket_id,
                "done:comment",
                "comment",
                actor,
                CLOSING_COMMENT,
            ):
                failed += 1
        else:
            feedback = _rejection_feedback(conn, ticket_id, status)
            if feedback is not None and not _dispatch_once(
                conn, ticket_id, "feedback:comment", "comment", actor, feedback
            ):
                failed += 1

        if failed:
            failures[origin] = failures.get(origin, 0) + failed
        else:
            conn.execute("UPDATE tickets SET mirrored_at=? WHERE id=?", (_now(), ticket_id))
    conn.commit()
    return failures


def writeback_state(conn: sqlite3.Connection, ticket_id: str, state: str, *, actor: str = "system") -> None:
    """Push a state change through the origin writer."""
    _dispatch(conn, ticket_id, "state", actor, state)


def writeback_comment(conn: sqlite3.Connection, ticket_id: str, text: str, *, actor: str = "system") -> None:
    """Push a comment through the origin writer."""
    _dispatch(conn, ticket_id, "comment", actor, text)


def writeback_label(
    conn: sqlite3.Connection, ticket_id: str, label: str, *, add: bool = True, actor: str = "system"
) -> None:
    """Add or remove one label through the origin writer."""
    _dispatch(conn, ticket_id, "label", actor, label, add)
