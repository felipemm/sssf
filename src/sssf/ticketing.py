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
_LEGACY_STATUS_MAP = {
    "backlog": STATUS_READY,
    "starting": STATUS_IN_PROGRESS,
    "running": STATUS_IN_PROGRESS,
    "failed": STATUS_READY,
    "success": STATUS_DONE,
}

TICKETS_DDL = """
CREATE TABLE IF NOT EXISTS tickets (
  id          TEXT PRIMARY KEY,
  provider    TEXT NOT NULL,
  external_id TEXT,
  title       TEXT NOT NULL,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'needs-triage',
  prompt_file TEXT,
  adw_id      TEXT,
  source_url  TEXT,
  context     TEXT NOT NULL DEFAULT '',
  kind        TEXT NOT NULL DEFAULT 'implementation',  -- idea | implementation
  tracked     INTEGER NOT NULL DEFAULT 1,              -- permanent origin-of-creation flag
  origin      TEXT NOT NULL DEFAULT 'internal',        -- internal | jira | gitlab | github
  parent_id   TEXT,                                    -- lineage: implementation -> feature
  spec        TEXT NOT NULL DEFAULT '',                -- spec reference once planned
  rejection_feedback TEXT NOT NULL DEFAULT '',
  created_at  TEXT, updated_at TEXT
);
"""

# Audit trail: every mutation (transition, comment, label, creation) is a row
# with actor and timestamp — traceable, never overwritten.
TICKET_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS ticket_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id  TEXT NOT NULL,
  event_type TEXT NOT NULL,            -- created | transition | comment | label
  actor      TEXT NOT NULL DEFAULT 'system',
  payload    TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
"""
TICKET_EVENTS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket ON ticket_events (ticket_id, created_at)"
)

# One row per run of a ticket — history survives retries. `tickets.adw_id`
# stays the LATEST run; this table keeps every earlier one so a retried
# ticket shows its full run list instead of losing the failed attempt.
TICKET_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS ticket_runs (
  ticket_id  TEXT NOT NULL,
  adw_id     TEXT NOT NULL,
  created_at TEXT,
  PRIMARY KEY (ticket_id, adw_id)
);
"""


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


@dataclass
class ProviderSyncResult:
    provider: str
    tickets: int = 0
    error: str | None = None


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
        providers=list(providers), jira=data.get("jira") or {}, linear=data.get("linear") or {}
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables and migrate existing ones in place. Never re-creates:
    CREATE-IF-NOT-EXISTS for tables, ALTER only when a column is missing, so
    pre-existing databases keep every row. Idempotent — safe on every open.

    Adds the ticket-machine columns (kind, tracked, origin, parent_id, spec,
    rejection_feedback), the `ticket_events` audit table, maps legacy status
    values onto the machine vocabulary, and backfills the tracked flag for
    synced rows once (tracked is permanent — later syncs never rewrite it).
    """
    conn.execute(TICKETS_DDL)
    conn.execute(TICKET_RUNS_DDL)
    conn.execute(TICKET_EVENTS_DDL)
    conn.execute(TICKET_EVENTS_INDEX_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
    added = set()
    for column, ddl in (
        ("context", "ALTER TABLE tickets ADD COLUMN context TEXT NOT NULL DEFAULT ''"),
        ("created_at", "ALTER TABLE tickets ADD COLUMN created_at TEXT"),
        ("updated_at", "ALTER TABLE tickets ADD COLUMN updated_at TEXT"),
        ("kind", "ALTER TABLE tickets ADD COLUMN kind TEXT NOT NULL DEFAULT 'implementation'"),
        ("tracked", "ALTER TABLE tickets ADD COLUMN tracked INTEGER NOT NULL DEFAULT 1"),
        ("origin", "ALTER TABLE tickets ADD COLUMN origin TEXT NOT NULL DEFAULT 'internal'"),
        ("parent_id", "ALTER TABLE tickets ADD COLUMN parent_id TEXT"),
        ("spec", "ALTER TABLE tickets ADD COLUMN spec TEXT NOT NULL DEFAULT ''"),
        (
            "rejection_feedback",
            "ALTER TABLE tickets ADD COLUMN rejection_feedback TEXT NOT NULL DEFAULT ''",
        ),
    ):
        if column not in cols:
            conn.execute(ddl)
            added.add(column)
    # Legacy status values -> machine vocabulary. Only touches rows still
    # holding a legacy value, so this is idempotent across repeated opens.
    for legacy, machine in _LEGACY_STATUS_MAP.items():
        conn.execute(
            "UPDATE tickets SET status=?, updated_at=? WHERE status=?",
            (machine, _now(), legacy),
        )
    # One-time backfill for pre-machine rows: synced tickets were implementable
    # backlog items created by a tracker, not by the plan flow — untracked with
    # origin = their provider, and both flags are permanent from here on.
    if "tracked" in added:
        conn.execute("UPDATE tickets SET tracked=0 WHERE provider != 'internal' AND tracked=1")
    if "origin" in added:
        conn.execute(
            "UPDATE tickets SET origin=provider WHERE provider != 'internal' AND origin='internal'"
        )


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


def upsert_tickets(db_path: Path, records: list[TicketRecord]) -> int:
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


def sync_tickets(root: Path, cfg: TicketingConfig) -> list[ProviderSyncResult]:
    """Load .env, fetch every enabled provider, upsert; one result per provider."""
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass
    from sssf.adw_modules import paths

    db_path = paths.data_dir(root) / "sssf.db"
    results: list[ProviderSyncResult] = []
    for provider in cfg.providers:
        try:
            if provider == "jira":
                records = fetch_jira(cfg)
            elif provider == "linear":
                records = fetch_linear(cfg)
            elif provider == "internal":
                continue  # internal tickets already live in the db
            else:
                results.append(ProviderSyncResult(provider, error=f"unknown provider {provider!r}"))
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
