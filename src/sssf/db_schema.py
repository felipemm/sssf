"""The per-project schema contract: one pydantic Row model per table, DDL
generated from the models, and versioned migrations applied via PRAGMA
user_version.

The models are the single source of truth for the per-project sssf.db shape.
The DDL mapper emits the exact status-quo clauses (NOT NULL / DEFAULT /
PRIMARY KEY / REFERENCES placement), so a fresh db built by apply_schema is
indistinguishable from one built by the legacy writers. Migrations are
additive-only (ADR-0005 fix-forward) and keyed by user_version, which the
read-only TS reader (db.ts) also reads to gate optional columns."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field

# ── base ───────────────────────────────────────────────────────────────────

class Row(BaseModel):
    """Base for every table row. extra='ignore' lets the generic run-db
    copier (sync_run_db) validate rows copied from older source dbs without
    rejecting columns the current contract no longer declares."""

    model_config = ConfigDict(extra="ignore")


# ── models ─────────────────────────────────────────────────────────────────

class SessionsRow(Row):
    adw_id: str = Field(json_schema_extra={"pk": True})
    adw_name: str | None = None
    request: str | None = None
    status: Literal["running", "success", "fail"] | None = None
    engineer: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    total_tokens: int = 0
    total_cost: float = 0
    archived: int = 0


class PhasesRow(Row):
    phase_id: str = Field(json_schema_extra={"pk": True})
    # read=True: the reader and UI treat adw_id as always present even though
    # the column is nullable — the codegen renders it non-null.
    adw_id: str | None = Field(
        default=None, json_schema_extra={"ref": "sessions", "read": True}
    )
    seq: int | None = None
    name: str | None = None
    kind: Literal["engineer", "code", "agent"] | None = None
    owner: str | None = None
    description: str | None = None
    status: Literal["queued", "running", "success", "fail", "not_passed"] = "fail"
    attempt: int = 0
    retries: int = 0
    error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class EventsRow(Row):
    # virtual: the sqlite rowid the reader selects as a stable ordering key —
    # part of the read surface, never a column or a written field. Optional in
    # the model so write-validation passes without it.
    rowid: int | None = Field(default=None, json_schema_extra={"virtual": True})
    event_id: str = Field(json_schema_extra={"pk": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    phase_id: str | None = Field(default=None, json_schema_extra={"ref": "phases"})
    parent_id: str | None = None
    type: Literal[
        "phase_start", "phase_end", "agent_start", "agent_end", "tool_call",
        "handoff", "gate_pass", "gate_fail", "log", "error", "integration",
    ] | None = None
    name: str | None = None
    payload_json: str | None = None
    tokens: int | None = None
    started_at: str | None = None
    ended_at: str | None = None


class EnvelopesRow(Row):
    envelope_id: str = Field(json_schema_extra={"pk": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    phase_id: str | None = Field(default=None, json_schema_extra={"ref": "phases"})
    agent: str | None = None
    output_type: str | None = None
    payload_json: str | None = None
    valid: int | None = None
    attempt: int | None = None
    created_at: str | None = None


class GateResultsRow(Row):
    id: int | None = Field(default=None, json_schema_extra={"pk": True, "autoincrement": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    phase_id: str | None = Field(default=None, json_schema_extra={"ref": "phases"})
    attempt: int | None = None
    gate: str | None = None
    passed: int | None = None
    violations_json: str | None = None
    checks_json: str | None = None
    created_at: str | None = None


class ProcessesRow(Row):
    id: int | None = Field(default=None, json_schema_extra={"pk": True, "autoincrement": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    kind: str | None = None
    name: str | None = None
    pid: int | None = None
    command: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class AgentSessionsRow(Row):
    adw_id: str = Field(json_schema_extra={"pk": True, "ref": "sessions"})
    agent: str = Field(json_schema_extra={"pk": True})
    coding_agent: str | None = None
    model: str | None = None
    color: str | None = None
    session_id: str | None = None
    context_tokens: int | None = None
    context_window: int | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class TicketsRow(Row):
    id: str = Field(json_schema_extra={"pk": True})
    provider: str = Field(json_schema_extra={"not_null": True})
    external_id: str | None = None
    title: str = Field(json_schema_extra={"not_null": True})
    description: str | None = None
    status: str = Field(default="needs-triage", json_schema_extra={"not_null": True})
    prompt_file: str | None = None
    adw_id: str | None = None
    source_url: str | None = None
    context: str = Field(default="", json_schema_extra={"not_null": True})
    kind: str = Field(default="implementation", json_schema_extra={"not_null": True})
    tracked: int = Field(default=1, json_schema_extra={"not_null": True})
    origin: str = Field(default="internal", json_schema_extra={"not_null": True})
    parent_id: str | None = None
    spec: str = Field(default="", json_schema_extra={"not_null": True})
    rejection_feedback: str = Field(default="", json_schema_extra={"not_null": True})
    created_at: str | None = None
    updated_at: str | None = None


class TicketEventsRow(Row):
    id: int | None = Field(default=None, json_schema_extra={"pk": True, "autoincrement": True})
    ticket_id: str = Field(json_schema_extra={"not_null": True})
    event_type: str = Field(json_schema_extra={"not_null": True})
    actor: str = Field(default="system", json_schema_extra={"not_null": True})
    payload: str = Field(default="{}", json_schema_extra={"not_null": True})
    created_at: str = Field(json_schema_extra={"not_null": True})


class TicketRunsRow(Row):
    ticket_id: str = Field(json_schema_extra={"pk": True, "not_null": True})
    adw_id: str = Field(json_schema_extra={"pk": True, "not_null": True})
    created_at: str | None = None


class TicketMrsRow(Row):
    ticket_id: str = Field(json_schema_extra={"pk": True})
    repo: str = Field(json_schema_extra={"not_null": True})
    iid: str = Field(json_schema_extra={"not_null": True})
    url: str = Field(default="", json_schema_extra={"not_null": True})
    created_at: str | None = None


class NotifyThreadsRow(Row):
    ticket_id: str = Field(json_schema_extra={"pk": True})
    channel: str = Field(json_schema_extra={"not_null": True})
    thread_ts: str = Field(json_schema_extra={"not_null": True})
    updated_at: str | None = None


class NotifyEventsRow(Row):
    id: int | None = Field(default=None, json_schema_extra={"pk": True, "autoincrement": True})
    ticket_id: str = Field(json_schema_extra={"not_null": True})
    ts: str = Field(json_schema_extra={"not_null": True})
    ok: int = Field(json_schema_extra={"not_null": True})
    error: str | None = None
    attempts: int = Field(json_schema_extra={"not_null": True})
    thread_ts: str | None = None
    channel: str | None = None


class SandboxRunRow(Row):
    adw_id: str = Field(json_schema_extra={"pk": True})
    container: str = Field(json_schema_extra={"not_null": True})
    container_port: int | None = None
    host_port: int | None = None
    review_url: str | None = None
    review_command: str | None = None
    instructions: str = ""
    status: str | None = None
    updated_at: str | None = None


# Table name → model class, in DDL order.
TABLES: dict[str, type[Row]] = {
    "sessions": SessionsRow,
    "phases": PhasesRow,
    "events": EventsRow,
    "envelopes": EnvelopesRow,
    "gate_results": GateResultsRow,
    "processes": ProcessesRow,
    "agent_sessions": AgentSessionsRow,
    "tickets": TicketsRow,
    "ticket_events": TicketEventsRow,
    "ticket_runs": TicketRunsRow,
    "ticket_mrs": TicketMrsRow,
    "notify_threads": NotifyThreadsRow,
    "notify_events": NotifyEventsRow,
    "sandbox_run": SandboxRunRow,
}

# Non-model indexes (models cannot express them). (name, table, columns)
INDEXES: list[tuple[str, str, tuple[str, ...]]] = [
    ("idx_ticket_events_ticket", "ticket_events", ("ticket_id", "created_at")),
]

def _add_machine_columns(conn: sqlite3.Connection) -> None:
    """Add the ticket-machine columns a pre-machine db lacks. Guarded: a db
    the old ticketing already upgraded must not re-ALTER (sqlite has no
    ADD COLUMN IF NOT EXISTS)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
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



# The current schema version. Bump on every schema-affecting change.
SCHEMA_VERSION = 4

# Columns the read-only viz reader (db.ts SINCE_VERSION) can serve as NULL on
# dbs stamped below the version that guarantees them. The codegen renders
# these nullable in the generated TS types even though the writer models them
# non-null — the reader degrades them, so the contract types must too.
VERSION_GATED_COLUMNS: set[str] = {
    "sessions.adw_name",
    "sessions.archived",
    "agent_sessions.color",
    "agent_sessions.context_tokens",
    "agent_sessions.context_window",
    "gate_results.checks_json",
}

# Ordered, additive migrations keyed by the version they land in:
# (version, description, step) where step is SQL text or a callable taking
# the connection. Applied at open when user_version < version. Steps are the
# historical pre-contract ALTERs/data moves (from the old ticketing
# ensure_schema), so pre-existing project dbs upgrade in place.
MIGRATIONS: list[tuple[int, str, str | Callable[[sqlite3.Connection], None]]] = [
    (
        2,
        "ticket-machine columns on tickets",
        _add_machine_columns,  # guarded — real dbs may already have them
    ),
    (
        3,
        "legacy ticket statuses onto the machine vocabulary",
        """
        UPDATE tickets SET status='ready-for-agent',
          updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
          WHERE status='backlog';
        UPDATE tickets SET status='in-progress' WHERE status='starting';
        UPDATE tickets SET status='in-progress' WHERE status='running';
        UPDATE tickets SET status='ready-for-agent' WHERE status='failed';
        UPDATE tickets SET status='done' WHERE status='success';
        """,
    ),
    (
        4,
        "tracked/origin backfill for synced rows (tracked is permanent)",
        """
        UPDATE tickets SET tracked=0 WHERE provider != 'internal' AND tracked=1;
        UPDATE tickets SET origin=provider
          WHERE provider != 'internal' AND origin='internal';
        """,
    ),
]


# ── DDL mapper ─────────────────────────────────────────────────────────────


def _resolve(annotation: Any) -> tuple[type | Any, bool]:
    """(base type, nullable) from a field annotation; unwraps Optional/X|None."""
    origin = get_origin(annotation)
    if origin is not None and origin in (Union, UnionType):
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0], True
    if origin is Literal:
        return annotation, False
    return annotation, False


def _sql_type(base: Any) -> str:
    if get_origin(base) is Literal:
        return "TEXT"
    if base is str:
        return "TEXT"
    if base is int:
        return "INTEGER"
    if base is float:
        return "REAL"
    if base is bool:
        return "INTEGER"
    raise TypeError(f"db_schema: no sqlite type for {base!r}")


def _sql_literal(value: Any) -> str:
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _meta(field: Any) -> dict[str, Any]:
    """Field metadata; json_schema_extra may be a callable (pydantic), not
    just a dict — only the dict form is ours."""
    extra = field.json_schema_extra
    return extra if isinstance(extra, dict) else {}


def column_ddl(name: str, field: Any, annotation: Any, inline_pk: bool = False) -> str:
    """One column clause, reproducing the status-quo clause placement:
    name TYPE [PRIMARY KEY [AUTOINCREMENT]] [NOT NULL] [DEFAULT x] [REFERENCES t].
    Inline PRIMARY KEY only for single-field pks; composite pks get the
    table-level clause from create_table."""
    base, nullable = _resolve(annotation)
    meta = _meta(field)
    parts = [name, _sql_type(base)]
    if inline_pk and meta.get("pk"):
        parts.append("PRIMARY KEY")
        if meta.get("autoincrement"):
            parts.append("AUTOINCREMENT")
    if meta.get("not_null") and not nullable:
        parts.append("NOT NULL")
    if not field.is_required() and field.default is not None:
        parts.append(f"DEFAULT {_sql_literal(field.default)}")
    if meta.get("ref"):
        parts.append(f"REFERENCES {meta['ref']}")
    return " ".join(parts)


def create_table(table: str, model: type[Row]) -> str:
    """CREATE TABLE IF NOT EXISTS from a model, matching the status quo shape."""
    hints = get_type_hints(model)
    pk_fields = [
        name
        for name, field in model.model_fields.items()
        if _meta(field).get("pk")
    ]
    inline_pk = len(pk_fields) == 1
    lines = []
    for name, field in model.model_fields.items():
        if _meta(field).get("virtual"):
            continue  # read-surface only, never a column
        lines.append(f"  {column_ddl(name, field, hints[name], inline_pk)},")
    if len(pk_fields) > 1:
        lines.append(f"  PRIMARY KEY ({', '.join(pk_fields)})")
    body = "\n".join(lines).rstrip(",")
    return f"CREATE TABLE IF NOT EXISTS {table} (\n{body}\n);"


# ── apply ──────────────────────────────────────────────────────────────────

def apply_schema(conn: sqlite3.Connection) -> None:
    """Bring a connection's db to the current contract: create tables from
    the models, create indexes, then run any pending migrations and stamp
    PRAGMA user_version. Idempotent — safe on every open.

    A brand-new db (version 0, no tables) is stamped directly: the models
    already carry every column, so running the historical ALTERs would fail
    on "duplicate column". A version-0 db WITH tables is a pre-contract
    database and takes the full migration path."""
    for table, model in TABLES.items():
        conn.execute(create_table(table, model))
    for name, table, columns in INDEXES:
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({', '.join(columns)})"
        )
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and not _has_tables(conn):
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    if version < SCHEMA_VERSION:
        for v, _description, step in MIGRATIONS:
            if v > version:
                if callable(step):
                    step(conn)
                else:
                    conn.executescript(step)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _has_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return bool(row and row[0] > 0)