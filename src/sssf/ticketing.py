"""Ticketing adapters: fetch backlog tickets from configured providers.

Providers are a set (any subset of jira | linear | internal). External sync is
read-only: Jira goes through the user-authenticated `acli` CLI, Linear through
its GraphQL API with a token from the project .env. All tickets land in the
trace db's `tickets` table; the kanban reads that.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from sssf import db_schema

TICKETING_FILE = "adws/config/ticketing.yaml"
LINEAR_API = "https://api.linear.app/graphql"

# Ticket machine statuses (issue #87). `backlog` is gone: the backlog is
# exactly the `ready-for-agent` queue.
STATUS_NEEDS_TRIAGE = "needs-triage"
STATUS_READY = "ready-for-agent"
STATUS_IN_PROGRESS = "in-progress"
STATUS_SIGNOFF = "ready-for-signoff"
STATUS_DEPLOY = "ready-to-deploy"
STATUS_DONE = "done"
STATUS_BLOCKED = "blocked"

MACHINE_STATUSES = frozenset(
    {
        STATUS_NEEDS_TRIAGE,
        STATUS_READY,
        STATUS_IN_PROGRESS,
        STATUS_SIGNOFF,
        STATUS_DEPLOY,
        STATUS_DONE,
        STATUS_BLOCKED,
    }
)

# The implementable queue: what the unattended loop and the operator agree on
# as "next to build".
BACKLOG_STATUS = STATUS_READY

# The canonical lifecycle. Rejection edges re-enter the implement flow
# fix-forward (back to ready-for-agent with the human's feedback attached); a
# canary failure parks the ticket in `blocked`; the human unblocks it back
# into the queue. `done` is terminal.
TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_NEEDS_TRIAGE: frozenset({STATUS_READY}),
    STATUS_READY: frozenset({STATUS_IN_PROGRESS}),
    STATUS_IN_PROGRESS: frozenset({STATUS_SIGNOFF, STATUS_READY}),
    STATUS_SIGNOFF: frozenset({STATUS_DEPLOY, STATUS_READY}),
    STATUS_DEPLOY: frozenset({STATUS_DONE, STATUS_BLOCKED}),
    STATUS_BLOCKED: frozenset({STATUS_READY}),
    STATUS_DONE: frozenset(),
}

# Legacy `tickets.status` values (backlog era) -> machine vocabulary.
@dataclass
class TicketRecord:
    provider: str
    external_id: str
    title: str
    description: str
    source_url: str


@dataclass
class TicketingConfig:
    providers: list[str]
    jira: dict = field(default_factory=dict)
    linear: dict = field(default_factory=dict)
    github: dict = field(default_factory=dict)
    gitlab: dict = field(default_factory=dict)


@dataclass
class ProviderSyncResult:
    provider: str
    tickets: int = 0
    error: str | None = None
    warning: str | None = None


def load_config(root: Path) -> TicketingConfig | None:
    """Parse ticketing.yaml; None when missing or no providers enabled."""
    path = root / TICKETING_FILE
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        raise RuntimeError(f"invalid {path}: {error}") from error
    providers = data.get("providers") or []
    if not providers:
        return None
    return TicketingConfig(
        providers=list(providers),
        jira=data.get("jira") or {},
        linear=data.get("linear") or {},
        github=data.get("github") or {},
        gitlab=data.get("gitlab") or {},
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The schema contract: tables from the db_schema models plus
    versioned migrations. Kept as a named alias so call sites read as
    intent — the historical ALTERs and data backfills are migrations."""
    db_schema.apply_schema(conn)

def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def can_transition(from_status: str, to_status: str) -> bool:
    """True when the machine allows the edge. Every edge not in TRANSITIONS is
    illegal — no self-loops, no jumps, `done` is terminal."""
    return to_status in TRANSITIONS.get(from_status, frozenset())


def add_ticket_event(
    conn: sqlite3.Connection,
    ticket_id: str,
    event_type: str,
    *,
    actor: str = "system",
    payload: dict | None = None,
) -> None:
    """Append one audit row; payload is stored as JSON."""
    conn.execute(
        "INSERT INTO ticket_events (ticket_id, event_type, actor, payload, created_at)"
        " VALUES (?,?,?,?,?)",
        (ticket_id, event_type, actor, json.dumps(payload or {}, ensure_ascii=False), _now()),
    )


def ticket_events(conn: sqlite3.Connection, ticket_id: str) -> list[dict]:
    """A ticket's full audit trail, oldest first."""
    rows = conn.execute(
        "SELECT event_type, actor, payload, created_at FROM ticket_events"
        " WHERE ticket_id=? ORDER BY id ASC",
        (ticket_id,),
    ).fetchall()
    events = []
    for event_type, actor, payload, created_at in rows:
        try:
            parsed = json.loads(payload or "{}")
        except ValueError:
            parsed = {}
        events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "payload": parsed,
                "created_at": created_at,
            }
        )
    return events


def transition_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    to_status: str,
    *,
    actor: str = "system",
    feedback: str | None = None,
    comment: str | None = None,
) -> None:
    """Move a ticket through the machine. Every mutation writes a
    `ticket_events` audit row; illegal edges raise ValueError and change
    nothing. `feedback` attaches rejection feedback to the ticket; it is
    cleared once the ticket reaches `done`.
    """
    row = conn.execute("SELECT status FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        raise KeyError(ticket_id)
    from_status = row[0]
    if not can_transition(from_status, to_status):
        raise ValueError(
            f"illegal transition {from_status!r} -> {to_status!r} for ticket {ticket_id}"
        )
    payload: dict = {"from": from_status, "to": to_status}
    if feedback is not None:
        payload["feedback"] = feedback
    if comment is not None:
        payload["comment"] = comment
    new_feedback = feedback if feedback is not None else ("" if to_status == STATUS_DONE else None)
    conn.execute(
        "UPDATE tickets SET status=?, updated_at=?, rejection_feedback="
        "COALESCE(?, rejection_feedback) WHERE id=?",
        (to_status, _now(), new_feedback, ticket_id),
    )
    add_ticket_event(conn, ticket_id, "transition", actor=actor, payload=payload)


def comment_ticket(
    conn: sqlite3.Connection, ticket_id: str, text: str, *, actor: str = "system"
) -> None:
    add_ticket_event(conn, ticket_id, "comment", actor=actor, payload={"text": text})


def label_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    label: str,
    *,
    add: bool = True,
    actor: str = "system",
) -> None:
    add_ticket_event(
        conn,
        ticket_id,
        "label",
        actor=actor,
        payload={"label": label, "action": "add" if add else "remove"},
    )


# The implement flow's run kind — the only flow that claims tickets through
# the machine (the legacy ticket.run path uses adw_simple_sdlc and settles by
# hand; plan/deploy never transition tickets).
IMPLEMENT_ADW = "adw_implement"


def _run_failure_feedback(conn: sqlite3.Connection, adw_id: str) -> str:
    """The feedback a failed implement run attaches to its ticket (fix-forward).

    The reviewer's blocking findings are the primary feedback — a rejected
    review is the human gate's answer, and the ticket must carry it back to
    the builder. Failing that, the run's last error event (quality/env reason,
    phase error, not_accepted) is the next best answer; a generic line is the
    floor.
    """
    row = conn.execute(
        "SELECT payload_json FROM envelopes WHERE adw_id=?"
        " AND output_type='ReviewOutput' ORDER BY rowid DESC LIMIT 1",
        (adw_id,),
    ).fetchone()
    if row:
        try:
            payload = json.loads(row[0] or "{}")
        except ValueError:
            payload = {}
        if not payload.get("approved"):
            blocking = payload.get("blocking") or []
            if blocking:
                return "Reviewer feedback: " + "; ".join(str(b) for b in blocking[:5])
            unmet = [
                f.get("requirement") for f in payload.get("findings") or [] if not f.get("met")
            ]
            if unmet:
                return "Reviewer feedback: unmet requirements: " + "; ".join(
                    str(u) for u in unmet[:5]
                )
            return "Reviewer feedback: the review was not approved"
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
            return str(text)[:500]
        return f"the run failed ({row[0]})"
    return "the implement flow run failed"


def finish_implement_run(
    conn: sqlite3.Connection, adw_id: str, *, actor: str = "system"
) -> str | None:
    """The implement flow's terminal machine step (issue #92), host-side.

    After an implement run ends, settle its ticket: success moves it
    `in-progress -> ready-for-signoff`; failure returns it `in-progress ->
    ready-for-agent` with the run's failure feedback attached (fix-forward).
    Both edges are legal machine transitions and are audited like every write.

    Host-side by design: the `tickets` table is project-owned — the sandbox's
    per-run db never contains tickets and `sync_run_db` never merges them, so
    only a host process (the sandbox monitor, or `flow implement --no-sandbox`
    after its blocking call) can move a ticket.

    Guards (a ticket is never yanked from under its operator):
    - only implement-flow runs settle (session `adw_name` other than
      `adw_implement` is left alone — legacy `adw_simple_sdlc` runs requeue by
      hand); a missing session row counts as a failed run (the ADW never
      wrote one — same rule as `record_never_started`);
    - only a ticket whose `adw_id` links THIS run and whose machine status is
      `in-progress` is moved — a ticket already requeued, signed off, or
      handed to another run is untouched.

    Returns the outcome (`"signoff"` | `"requeued"`) or None when there is
    nothing to settle.
    """
    session = conn.execute(
        "SELECT status, adw_name FROM sessions WHERE adw_id=?", (adw_id,)
    ).fetchone()
    if session is None:
        session_status, adw_name = None, None
    else:
        session_status, adw_name = session
    if adw_name not in (None, "", IMPLEMENT_ADW):
        return None  # a legacy ticket run or another flow — settles by hand
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE adw_id=? AND status=?",
        (adw_id, STATUS_IN_PROGRESS),
    ).fetchone()
    if ticket is None:
        return None  # nothing we own in-flight
    ticket_id = ticket[0]
    if session_status == "success":
        transition_ticket(
            conn,
            ticket_id,
            STATUS_SIGNOFF,
            actor=actor,
            comment="implement flow run succeeded — ready for signoff",
        )
        return "signoff"
    feedback = _run_failure_feedback(conn, adw_id)
    transition_ticket(
        conn,
        ticket_id,
        STATUS_READY,
        actor=actor,
        feedback=feedback,
        comment="implement flow run failed — requeued fix-forward",
    )
    return "requeued"


def create_idea_ticket(conn: sqlite3.Connection, title: str, *, actor: str = "system") -> str:
    """A title-only idea shell: blank spec, born `needs-triage`, tracked,
    internal origin. The plan flow turns it into a spec plus implementation
    tickets."""
    ticket_id = f"internal:{uuid.uuid4().hex[:12]}"
    now = _now()
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status,"
        " kind, tracked, origin, spec, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ticket_id,
            "internal",
            "",
            title,
            "",
            STATUS_NEEDS_TRIAGE,
            "idea",
            1,
            "internal",
            "",
            now,
            now,
        ),
    )
    add_ticket_event(conn, ticket_id, "created", actor=actor, payload={"kind": "idea"})
    return ticket_id


@dataclass
class MrRecord:
    """The MR registered against a ticket (issue #98)."""

    ticket_id: str
    repo: str
    iid: str
    url: str = ""
    created_at: str = ""


def _mr_row(row: sqlite3.Row) -> MrRecord:
    return MrRecord(
        ticket_id=row[0], repo=row[1], iid=row[2], url=row[3] or "", created_at=row[4] or ""
    )


def register_mr(
    conn: sqlite3.Connection,
    ticket_id: str,
    repo: str,
    iid: str,
    url: str = "",
    *,
    actor: str = "system",
) -> None:
    """Attach one MR to a ticket (upsert) and audit it. A ticket has at most
    one MR: registering again replaces the reference in place."""
    conn.execute(
        "INSERT INTO ticket_mrs (ticket_id, repo, iid, url, created_at)"
        " VALUES (?,?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET"
        " repo=excluded.repo, iid=excluded.iid, url=excluded.url, created_at=excluded.created_at",
        (ticket_id, repo, iid, url, _now()),
    )
    add_ticket_event(
        conn,
        ticket_id,
        "mr",
        actor=actor,
        payload={"action": "register", "repo": repo, "iid": iid, "url": url},
    )


def unregister_mr(conn: sqlite3.Connection, ticket_id: str, *, actor: str = "system") -> None:
    """Drop a ticket's MR reference (the deploy flow calls this when the MR
    is closed or the ticket is rejected before it). Audited like every write."""
    conn.execute("DELETE FROM ticket_mrs WHERE ticket_id=?", (ticket_id,))
    add_ticket_event(conn, ticket_id, "mr", actor=actor, payload={"action": "remove"})


def mr_for_ticket(conn: sqlite3.Connection, ticket_id: str) -> MrRecord | None:
    """A ticket's registered MR, or None when it has none."""
    row = conn.execute(
        "SELECT ticket_id, repo, iid, url, created_at FROM ticket_mrs WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    return _mr_row(row) if row else None


def ticket_mrs(conn: sqlite3.Connection) -> list[MrRecord]:
    """Every registered MR, oldest first."""
    rows = conn.execute(
        "SELECT ticket_id, repo, iid, url, created_at FROM ticket_mrs ORDER BY created_at ASC"
    ).fetchall()
    return [_mr_row(row) for row in rows]


def mrs_for_status(conn: sqlite3.Connection, status: str) -> list[MrRecord]:
    """MRs of tickets currently in `status` — the monitor's scan set."""
    rows = conn.execute(
        "SELECT m.ticket_id, m.repo, m.iid, m.url, m.created_at"
        " FROM ticket_mrs m JOIN tickets t ON t.id = m.ticket_id"
        " WHERE t.status=? ORDER BY m.created_at ASC",
        (status,),
    ).fetchall()
    return [_mr_row(row) for row in rows]


# The plan flow's run kind — the only flow that turns idea tickets into
# spec + implementation children (the legacy ticket.run path uses
# adw_simple_sdlc and never plans).
PLAN_ADW = "adw_plan"


def plan_guard(conn: sqlite3.Connection, ticket_id: str, *, revise: bool = False) -> str | None:
    """Why this ticket cannot be planned, or None when it can (issue #91).

    The plan-once recursion guard: implementation tickets are terminal plan
    inputs (plan never runs on them, `--revise` or not), and an idea ticket
    that already holds a spec or children needs the explicit `--revise`
    escape. Nothing the plan flow emits is a valid plan input.
    """
    row = conn.execute("SELECT kind FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        return f"no ticket {ticket_id}"
    kind = row[0]
    if kind != "idea":
        return (
            f"ticket {ticket_id} is an implementation ticket — the plan flow only "
            "plans idea tickets"
        )
    if not revise and _is_planned(conn, ticket_id):
        return f"ticket {ticket_id} is already planned — pass --revise to re-plan"
    return None


def _is_planned(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """True when the idea ticket already went through the plan flow: it holds
    a spec reference or has implementation children."""
    row = conn.execute("SELECT spec FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        return False
    if row[0]:
        return True
    return conn.execute("SELECT 1 FROM tickets WHERE parent_id=?", (ticket_id,)).fetchone() is not None


def parse_ticket_breakdown(text: str) -> list[tuple[str, str]]:
    """The to-tickets breakdown file -> (title, description) slices.

    Convention (mandated by adw_plan's to-tickets directive): one `## `
    heading per implementation ticket — the heading is the title and the
    body under it the description. Anything before the first `## ` (the
    file's own title, preamble) is dropped; nested `### ` headings stay part
    of the description. Returns [] when the file has no H2 sections at all.
    """
    slices: list[tuple[str, str]] = []
    title: str | None = None
    body: list[str] = []

    def flush() -> None:
        if title is not None:
            slices.append((title, "\n".join(body).strip()))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            body = []
        elif title is not None:
            body.append(line)
    flush()
    return slices


def _spec_title(text: str) -> str:
    """The spec's first `# ` heading, stripped — the feature's name (the
    no-args mode titles its auto-created idea ticket from it)."""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title
    return ""


def _run_root(project_root: Path, conn: sqlite3.Connection, adw_id: str) -> Path:
    """Where the run's artifacts live: the per-run sandbox worktree for a
    sandboxed run (sandbox_run has a row), the project tree otherwise
    (--no-sandbox runs write directly into the project)."""
    sandboxed = conn.execute(
        "SELECT 1 FROM sandbox_run WHERE adw_id=?", (adw_id,)
    ).fetchone()
    if sandboxed:
        from sssf.sandbox.worktree_git import sandbox_dir

        return sandbox_dir(project_root, adw_id)
    return project_root


def _plan_artifacts(run_root: Path, adw_id: str) -> tuple[Path | None, Path | None]:
    """The plan run's spec and tickets files under adws/specs/, by the ADW's
    mandated naming convention (`<adw_id>_spec-<slug>.md` /
    `<adw_id>_tickets-<slug>.md`). Oldest-last so a re-run picks the newest."""
    specs = run_root / "adws" / "specs"
    if not specs.is_dir():
        return None, None
    spec_files = sorted(specs.glob(f"{adw_id}_spec-*.md"))
    ticket_files = sorted(specs.glob(f"{adw_id}_tickets-*.md"))
    return (spec_files[-1] if spec_files else None,
            ticket_files[-1] if ticket_files else None)


def finish_plan_run(
    project_root: Path, conn: sqlite3.Connection, adw_id: str, *, actor: str = "system"
) -> str | None:
    """The plan flow's terminal machine step (issue #91), host-side.

    After a SUCCESSFUL plan run, turn the idea ticket into a spec reference
    plus implementation children born `ready-for-agent` (parent_id -> the
    idea ticket) and record the lineage. The no-args mode (no linked idea
    ticket yet) creates the idea ticket from the spec's title first, then
    transforms in the same pass — exploration -> brief -> idea ticket ->
    spec -> slices in one human-invoked run.

    Host-side by design: `tickets` is project-owned — the sandbox's per-run
    db never contains tickets and `sync_run_db` never merges them, so only a
    host process (the sandbox monitor, or `flow plan --no-sandbox` after its
    blocking call) can land the transform.

    Guards: only plan-flow runs land (`sessions.adw_name` = adw_plan; a
    missing session row counts as never-started), and only a run that
    SUCCEEDED transforms — a failed plan run leaves its ticket untouched
    (the failure stays visible in the trace). The settle finds its ticket
    through the dispatch-time link `tickets.adw_id`; missing link = no-args
    mode.

    Returns the parent ticket id when the transform landed, None when there
    is nothing to land. Raises ValueError when the run succeeded but its
    artifacts cannot be parsed — the monitor catches and logs (best-effort),
    `flow plan --no-sandbox` surfaces it.
    """
    session = conn.execute(
        "SELECT status, adw_name FROM sessions WHERE adw_id=?", (adw_id,)
    ).fetchone()
    if session is None or session[1] not in (None, "", PLAN_ADW):
        return None
    if session[0] != "success":
        return None
    root = _run_root(project_root, conn, adw_id)
    spec_file, tickets_file = _plan_artifacts(root, adw_id)
    if spec_file is None or tickets_file is None:
        raise ValueError(
            f"plan run {adw_id} produced no spec/tickets under {root / 'adws' / 'specs'}"
        )
    row = conn.execute(
        "SELECT id FROM tickets WHERE adw_id=? AND kind='idea'", (adw_id,)
    ).fetchone()
    if row is None:
        # no-args mode: the idea ticket is born from the landed spec, and the
        # run's history row lands with it (the ticket path recorded its row at
        # dispatch time).
        title = _spec_title(spec_file.read_text()) or f"Planned feature ({adw_id})"
        ticket_id = create_idea_ticket(conn, title, actor=actor)
        conn.execute(
            "UPDATE tickets SET adw_id=?, updated_at=? WHERE id=?",
            (adw_id, _now(), ticket_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ticket_runs (ticket_id, adw_id, created_at)"
            " VALUES (?,?,?)",
            (ticket_id, adw_id, _now()),
        )
    else:
        ticket_id = row[0]
    return _apply_plan(
        conn, ticket_id, spec_file, tickets_file, run_root=root, adw_id=adw_id, actor=actor
    )


def _apply_plan(
    conn: sqlite3.Connection,
    ticket_id: str,
    spec_file: Path,
    tickets_file: Path,
    *,
    run_root: Path,
    adw_id: str,
    actor: str,
) -> str:
    """The db-level transform: parent.spec = committed spec path, one
    implementation child per breakdown slice, lineage events. Stale unclaimed
    children of a re-plan are commented as superseded — never deleted, never
    parked (the machine has no ready -> blocked edge)."""
    spec_ref = str(spec_file.relative_to(run_root))
    slices = parse_ticket_breakdown(tickets_file.read_text())
    if not slices:
        raise ValueError(
            f"the tickets breakdown {tickets_file} has no `## ` headings — cannot slice"
        )
    if _is_planned(conn, ticket_id):
        stale = conn.execute(
            "SELECT id FROM tickets WHERE parent_id=? AND status=?",
            (ticket_id, STATUS_READY),
        ).fetchall()
        for (child_id,) in stale:
            comment_ticket(
                conn,
                child_id,
                f"superseded by re-plan run {adw_id} — this slice was replaced",
                actor=actor,
            )
    conn.execute(
        "UPDATE tickets SET spec=?, updated_at=? WHERE id=?",
        (spec_ref, _now(), ticket_id),
    )
    child_ids: list[str] = []
    for title, description in slices:
        child_id = f"internal:{uuid.uuid4().hex[:12]}"
        now = _now()
        conn.execute(
            "INSERT INTO tickets (id, provider, external_id, title, description, status,"
            " kind, tracked, origin, parent_id, spec, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                child_id,
                "internal",
                "",
                title,
                description,
                STATUS_READY,
                "implementation",
                1,
                "internal",
                ticket_id,
                spec_ref,
                now,
                now,
            ),
        )
        add_ticket_event(
            conn,
            child_id,
            "created",
            actor=actor,
            payload={"kind": "implementation", "parent": ticket_id},
        )
        child_ids.append(child_id)
    add_ticket_event(
        conn,
        ticket_id,
        "planned",
        actor=actor,
        payload={"run": adw_id, "spec": spec_ref, "children": child_ids},
    )
    return ticket_id


def backlog_tickets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The backlog: exactly the `ready-for-agent` queue, oldest first.
    Untracked tickets are invisible here until marked ready-for-agent."""
    return conn.execute(
        "SELECT id, provider, title, status, kind, tracked, spec, adw_id FROM tickets"
        " WHERE status=? ORDER BY created_at ASC, rowid ASC",
        (BACKLOG_STATUS,),
    ).fetchall()


def _adf_inline(node: dict) -> str:
    """Render one inline ADF node (text + marks, hardBreak, inlineCard)."""
    ntype = node.get("type")
    if ntype == "text":
        text = node.get("text", "")
        for mark in node.get("marks") or []:
            mtype = mark.get("type")
            if mtype == "strong":
                text = f"**{text}**"
            elif mtype == "em":
                text = f"*{text}*"
            elif mtype == "code":
                text = f"`{text}`"
            elif mtype == "strike":
                text = f"~~{text}~~"
            elif mtype == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                text = f"[{text}]({href})"
        return text
    if ntype == "hardBreak":
        return "\n"
    if ntype == "inlineCard":
        return (node.get("attrs") or {}).get("url", "") or ""
    if ntype == "mention":
        return (node.get("attrs") or {}).get("text", "") or "@mention"
    return ""


def _adf_inline_join(nodes: list[dict]) -> str:
    return "".join(
        _adf_inline(n) if n.get("type") == "text" else adf_to_markdown(n) for n in nodes
    )


def _adf_list_item(node: dict, ordered: bool, index: int = 0) -> str:
    marker = f"{index}. " if ordered else "- "
    lines = []
    for child in node.get("content") or []:
        ctype = child.get("type")
        if ctype == "paragraph":
            lines.append(marker + _adf_inline_join(child.get("content") or []).strip())
            marker = "  "  # continuation lines stay under the item marker
        elif ctype in ("bulletList", "orderedList"):
            lines.extend("  " + line for line in adf_to_markdown(child).splitlines())
    return "\n".join(lines) + "\n"


def adf_to_markdown(node: dict | str) -> str:
    """Render an Atlassian Document Format node tree as Markdown text.

    acli's `--json` returns Jira descriptions as ADF objects (Jira Cloud API
    v3); storing str(dict) polluted the kanban and generated prompt files with
    raw JSON. Plain strings (Linear, older Jira) pass through unchanged.
    """
    if isinstance(node, str):
        return node
    ntype = node.get("type")
    content = node.get("content") or []

    if ntype == "text":
        return _adf_inline(node)
    if ntype == "paragraph":
        return _adf_inline_join(content).strip() + "\n\n"
    if ntype == "heading":
        level = int((node.get("attrs") or {}).get("level", 1))
        return "#" * level + " " + _adf_inline_join(content).strip() + "\n\n"
    if ntype == "bulletList":
        return "".join(_adf_list_item(c, ordered=False) for c in content) + "\n"
    if ntype == "orderedList":
        return "".join(
            _adf_list_item(c, ordered=True, index=i + 1) for i, c in enumerate(content)
        ) + "\n"
    if ntype == "codeBlock":
        lang = (node.get("attrs") or {}).get("language", "") or ""
        body = "".join(c.get("text", "") for c in content if c.get("type") == "text")
        return f"```{lang}\n{body}\n```\n\n"
    if ntype == "blockquote":
        inner = adf_to_markdown({"type": "doc", "content": content}).strip()
        return "\n".join(f"> {line}" for line in inner.splitlines()) + "\n\n"
    if ntype == "rule":
        return "---\n\n"
    # containers (doc, listItem, tableRow, ...) — concatenate children
    return "".join(adf_to_markdown(c) for c in content)


def _run_acli(args: list[str]) -> dict:
    if shutil.which("acli") is None:
        raise RuntimeError(
            "the Jira provider needs the acli CLI — install it and configure auth: "
            "https://github.com/zdharma-continuum/acli"
        )
    result = subprocess.run(["acli", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"acli failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}\n"
            "Is acli authenticated? Run its login/credentials setup first."
        )
    return json.loads(result.stdout or "[]")


def fetch_jira(cfg: TicketingConfig) -> list[TicketRecord]:
    jql = cfg.jira.get("jql")
    if not jql:
        raise RuntimeError("the jira provider needs a `jql` in ticketing.yaml")
    # acli >= 1.3 moved work items under `jira workitem search` (old
    # `acli issue list` was removed). Default fields exclude description,
    # so request it explicitly.
    data = _run_acli(
        [
            "jira",
            "workitem",
            "search",
            "--jql",
            jql,
            "--fields",
            "summary,description",
            "--limit",
            "100",
            "--json",
        ]
    )
    issues = data if isinstance(data, list) else (data.get("issues") or data.get("data") or [])
    base_host = (cfg.jira.get("base_url") or "").rstrip("/").removeprefix("https://")
    records = []
    for issue in issues:
        fields = issue.get("fields") or {}
        description = fields.get("description") or ""
        if isinstance(description, dict):
            description = adf_to_markdown(description).strip()
        key = str(issue.get("key") or "")
        self_url = issue.get("self") or ""
        host = self_url.split("/")[2] if self_url.startswith("http") else ""
        # Internal Atlassian `self` hosts (jira-prod-us-*.prod.atl-paas.net)
        # aren't browsable; prefer the configured base_url when present.
        if base_host and (not host or "atl-paas.net" in host):
            host = base_host
        records.append(
            TicketRecord(
                provider="jira",
                external_id=key,
                title=str(fields.get("summary") or key),
                description=str(description),
                source_url=f"https://{host}/browse/{key}" if host else "",
            )
        )
    return records


def fetch_linear(cfg: TicketingConfig) -> list[TicketRecord]:
    token_env = cfg.linear.get("token_env") or "LINEAR_TOKEN"
    token = os.environ.get(token_env, "")
    if not token:
        raise RuntimeError(f"the linear provider needs {token_env} set in the project .env")
    team = cfg.linear.get("team")
    if not team:
        raise RuntimeError("the linear provider needs a `team` key in ticketing.yaml")
    query = (
        f'query {{ issues(filter: {{team: {{key: {{eq: "{team}"}}}}}}, first: 100) '
        "{ nodes { id identifier title description url state { name } } } }"
    )
    req = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    nodes = (payload.get("data") or {}).get("issues", {}).get("nodes", [])
    wanted = {str(s).strip().lower() for s in (cfg.linear.get("states") or [])}
    records = []
    for node in nodes:
        state = ((node.get("state") or {}).get("name") or "").strip().lower()
        if wanted and state not in wanted:
            continue
        records.append(
            TicketRecord(
                provider="linear",
                external_id=str(node.get("identifier") or node.get("id")),
                title=str(node.get("title") or ""),
                description=str(node.get("description") or ""),
                source_url=str(node.get("url") or ""),
            )
        )
    return records


def detect_origin(root: Path) -> tuple[str, str] | None:
    """The project's git remote origin as (host, repo); None when missing.

    Runs `git config --get remote.origin.url` in the project and parses the
    common forms (SSH scp-like, HTTPS, ssh://); a trailing `.git` is
    stripped. Sync adapters for the hosted-git providers (github/gitlab)
    key off this — the repo must live on the forge they fetch from.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = result.stdout.strip() if result.returncode == 0 else ""
    return _parse_origin_url(url) if url else None


def _parse_origin_url(url: str) -> tuple[str, str] | None:
    """Parse a remote URL into (host, repo); None when unparseable."""
    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if not url:
        return None
    if url.startswith("ssh://"):
        rest = url[len("ssh://") :]
        host, _, path = rest.partition("/")
        if "@" in host:
            host = host.rsplit("@", 1)[1]
        if not path:
            return None
        return host, path
    if "://" in url:
        _, _, rest = url.partition("://")
        if "@" in rest:
            rest = rest.rsplit("@", 1)[1]
        host, _, path = rest.partition("/")
        if not path:
            return None
        return host, path
    # scp-like form: git@host:owner/repo
    if "@" in url and ":" in url:
        host = url.rsplit("@", 1)[1].split(":", 1)[0]
        path = url.split(":", 1)[1]
        if not host or not path:
            return None
        return host, path
    return None


def _origin_repo(
    block: dict,
    origin: tuple[str, str] | None,
    cloud_host: str,
    provider: str,
) -> tuple[str | None, str | None]:
    """Resolve the repo a provider fetches from → (repo, warning).

    The yaml `repo:` override wins outright (no host matching); otherwise
    the origin host must be the provider's expected host — the cloud
    standard URL, or the custom_url host when self_hosted (any host when
    self-hosted without custom_url). A mismatch or a missing origin returns
    (None, warning): the provider is SKIPPED, never fetched against the
    wrong forge.
    """
    if block.get("repo"):
        return str(block["repo"]), None
    if origin is None:
        return None, "no git remote origin — add a `repo:` override in ticketing.yaml"
    host, repo = origin
    if block.get("self_hosted"):
        custom = str(block.get("custom_url") or "").strip().rstrip("/")
        if custom:
            expected = custom.split("://")[-1].split("/", 1)[0]
            if host != expected:
                return None, (
                    f"{provider} configured with custom_url {custom} but origin is {host}"
                    " — fix custom_url or add a `repo:` override"
                )
            return repo, None
        return repo, None  # self-hosted without custom_url: any host
    if host != cloud_host:
        return None, (
            f"{provider} configured but origin is {host} — the cloud host is {cloud_host};"
            " is this a self-hosted instance? set `self_hosted: true` and `custom_url:`"
            " (or add a `repo:` override)"
        )
    return repo, None


def github_repo(cfg: TicketingConfig, origin: tuple[str, str] | None) -> tuple[str | None, str | None]:
    """The repo github sync fetches from, or (None, warning) to skip."""
    return _origin_repo(cfg.github or {}, origin, "github.com", "github")


def gitlab_repo(cfg: TicketingConfig, origin: tuple[str, str] | None) -> tuple[str | None, str | None]:
    """The repo gitlab sync fetches from, or (None, warning) to skip."""
    return _origin_repo(cfg.gitlab or {}, origin, "gitlab.com", "gitlab")


def _run_gh(args: list[str]) -> list[dict]:
    """Shell the user-authenticated gh CLI and parse its JSON stdout."""
    if shutil.which("gh") is None:
        raise RuntimeError(
            "the github provider needs the gh CLI — install gh and run `gh auth login`: "
            "https://cli.github.com"
        )
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"gh failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return json.loads(result.stdout or "[]")


def fetch_github(cfg: TicketingConfig, repo: str) -> list[TicketRecord]:
    """Open GitHub issues for a repo via `gh issue list` (gh owns auth+host).

    external_id is `<repo>#<number>` so the db id (`github:<repo>#<n>`) is
    the same dedupe key shape as jira/linear.
    """
    cmd = ["issue", "list", "--repo", repo, "--state", "open"]
    for label in cfg.github.get("labels") or []:
        cmd += ["--label", str(label)]
    cmd += ["--json", "number,title,body,url,state,labels", "--limit", "100"]
    records = []
    for issue in _run_gh(cmd):
        number = str(issue.get("number") or "")
        if not number:
            continue
        records.append(
            TicketRecord(
                provider="github",
                external_id=f"{repo}#{number}",
                title=str(issue.get("title") or ""),
                description=str(issue.get("body") or ""),
                source_url=str(issue.get("url") or ""),
            )
        )
    return records


def _run_glab(args: list[str]) -> list[dict]:
    """Shell the user-authenticated glab CLI and parse its JSON stdout."""
    if shutil.which("glab") is None:
        raise RuntimeError(
            "the gitlab provider needs the glab CLI — install glab and run `glab auth login`: "
            "https://gitlab.com/gitlab-org/cli"
        )
    result = subprocess.run(["glab", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"glab failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return json.loads(result.stdout or "[]")


def fetch_gitlab(cfg: TicketingConfig, repo: str) -> list[TicketRecord]:
    """Open GitLab issues for a repo via `glab issue list` (glab owns auth+host).

    external_id is `<repo>#<iid>`; labels are normalized defensively (glab
    may return a string or a `{"name": …}` object).
    """
    cmd = ["issue", "list", "--repo", repo, "--state", "opened"]
    for label in cfg.gitlab.get("labels") or []:
        cmd += ["--label", str(label)]
    cmd += ["--output", "json"]
    records = []
    for issue in _run_glab(cmd):
        iid = str(issue.get("iid") or "")
        if not iid:
            continue
        records.append(
            TicketRecord(
                provider="gitlab",
                external_id=f"{repo}#{iid}",
                title=str(issue.get("title") or ""),
                description=str(issue.get("description") or ""),
                source_url=str(issue.get("web_url") or ""),
            )
        )
    return records


def upsert_tickets(db_path: Path, records: list[TicketRecord]) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        count = 0
        for r in records:
            now = _now()
            # Synced tickets are born needs-triage and untracked (permanent):
            # invisible in the backlog until marked ready-for-agent. The
            # conflict update refreshes CONTENT only — kind/tracked/origin/
            # status are never rewritten by a later sync.
            cur = conn.execute(
                "INSERT INTO tickets (id, provider, external_id, title, description, status,"
                " kind, tracked, origin, source_url, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                " description=excluded.description, source_url=excluded.source_url,"
                " updated_at=excluded.updated_at",
                (
                    f"{r.provider}:{r.external_id}",
                    r.provider,
                    r.external_id,
                    r.title,
                    r.description,
                    STATUS_NEEDS_TRIAGE,
                    "idea",
                    0,
                    r.provider,
                    r.source_url,
                    now,
                    now,
                ),
            )
            count += cur.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def sync_tickets(
    root: Path, cfg: TicketingConfig, providers: list[str] | None = None
) -> list[ProviderSyncResult]:
    """Load .env, fetch every enabled provider (or the `providers` subset),
    upsert; one result per provider.

    Hosted-git providers (github/gitlab) resolve their repo from the git
    remote origin first — a host mismatch or a missing origin SKIPS the
    provider with a warning (never an error, never a fetch against the
    wrong forge). `internal` is a no-op: its tickets already live in the db.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass
    from sssf.adw_modules import paths

    db_path = paths.data_dir(root) / "sssf.db"
    origin = detect_origin(root)
    results: list[ProviderSyncResult] = []
    requested = set(providers) if providers is not None else None
    enabled = [p for p in cfg.providers if requested is None or p in requested]
    if requested is not None:
        for p in sorted(requested - set(cfg.providers)):
            results.append(
                ProviderSyncResult(p, error=f"{p!r} is not enabled in ticketing.yaml")
            )
    for provider in enabled:
        try:
            if provider == "jira":
                records = fetch_jira(cfg)
            elif provider == "linear":
                records = fetch_linear(cfg)
            elif provider == "github":
                repo, warning = github_repo(cfg, origin)
                if repo is None:
                    results.append(ProviderSyncResult(provider, warning=warning))
                    continue
                records = fetch_github(cfg, repo)
            elif provider == "gitlab":
                repo, warning = gitlab_repo(cfg, origin)
                if repo is None:
                    results.append(ProviderSyncResult(provider, warning=warning))
                    continue
                records = fetch_gitlab(cfg, repo)
            elif provider == "internal":
                continue  # internal tickets already live in the db
            else:
                results.append(
                    ProviderSyncResult(provider, error=f"unknown provider {provider!r}")
                )
                continue
            results.append(ProviderSyncResult(provider, tickets=upsert_tickets(db_path, records)))
        except (RuntimeError, OSError, sqlite3.Error) as error:
            results.append(ProviderSyncResult(provider, error=str(error)))
    return results


def next_prompt_name(root: Path, slug: str) -> Path:
    """The next enumerated prompt path: adws/prompts/NN-<slug>.md (collision suffix)."""
    prompts = root / "adws" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    numbers = [
        int(p.stem.split("-")[0]) for p in prompts.glob("*.md") if p.stem.split("-")[0].isdigit()
    ]
    n = max(numbers, default=0) + 1
    candidate = prompts / f"{n:02d}-{slug}.md"
    i = 1
    while candidate.exists():
        i += 1
        candidate = prompts / f"{n:02d}-{slug}-{i}.md"
    return candidate