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
    status: str | None = None
    engineer: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    total_tokens: int = 0
    total_cost: float = 0
    archived: int = 0


class PhasesRow(Row):
    phase_id: str = Field(json_schema_extra={"pk": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    seq: int | None = None
    name: str | None = None
    kind: str | None = None
    owner: str | None = None
    description: str | None = None
    status: str = "fail"
    attempt: int = 0
    retries: int = 0
    error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class EventsRow(Row):
    event_id: str = Field(json_schema_extra={"pk": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    phase_id: str | None = Field(default=None, json_schema_extra={"ref": "phases"})
    parent_id: str | None = None
    type: str | None = None
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
    id: int = Field(json_schema_extra={"pk": True, "autoincrement": True})
    adw_id: str | None = Field(default=None, json_schema_extra={"ref": "sessions"})
    phase_id: str | None = Field(default=None, json_schema_extra={"ref": "phases"})
    attempt: int | None = None
    gate: str | None = None
    passed: int | None = None
    violations_json: str | None = None
    checks_json: str | None = None
    created_at: str | None = None


class ProcessesRow(Row):
    id: int = Field(json_schema_extra={"pk": True, "autoincrement": True})
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
    id: int = Field(json_schema_extra={"pk": True, "autoincrement": True})
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
    id: int = Field(json_schema_extra={"pk": True, "autoincrement": True})
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

# The current schema version. Bump on every schema-affecting change.
SCHEMA_VERSION = 1

# Ordered, additive migrations keyed by the version they land in:
# (version, description, sql). Applied at open when user_version < version.
# Task 4 folds ticketing/notify's historical ALTERs and data backfills in
# here as explicit entries.
MIGRATIONS: list[tuple[int, str, str]] = []


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
        lines.append(f"  {column_ddl(name, field, hints[name], inline_pk)},")
    if len(pk_fields) > 1:
        lines.append(f"  PRIMARY KEY ({', '.join(pk_fields)})")
    body = "\n".join(lines).rstrip(",")
    return f"CREATE TABLE IF NOT EXISTS {table} (\n{body}\n);"


# ── apply ──────────────────────────────────────────────────────────────────

def apply_schema(conn: sqlite3.Connection) -> None:
    """Bring a connection's db to the current contract: create tables from
    the models, create indexes, then run any pending migrations and stamp
    PRAGMA user_version. Idempotent — safe on every open."""
    for table, model in TABLES.items():
        conn.execute(create_table(table, model))
    for name, table, columns in INDEXES:
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({', '.join(columns)})"
        )
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        for v, _description, sql in MIGRATIONS:
            if v > version:
                conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
