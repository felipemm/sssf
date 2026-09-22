"""The schema contract (Task 1): db_schema's model-generated DDL must
reproduce the status-quo schema exactly — columns, types, nullability,
defaults, PK ordinals, FKs, indexes — and apply_schema must be idempotent
and stamp PRAGMA user_version."""

import sqlite3

import pytest

from sssf import db_schema, notify, ticketing
from sssf.adw_modules import tracer


def _table_shape(conn: sqlite3.Connection) -> dict[str, list]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    shape: dict[str, list] = {}
    for t in sorted(tables):
        shape[t] = [
            (r[1], r[2], r[3], r[4], r[5])  # name, type, notnull, dflt_value, pk
            for r in conn.execute(f"PRAGMA table_info({t})")
        ]
    return shape


def _index_shape(conn: sqlite3.Connection) -> dict[str, tuple]:
    idx: dict[str, tuple] = {}
    for r in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ):
        cols = tuple(c[2] for c in conn.execute(f"PRAGMA index_info({r[0]})"))
        idx[r[0]] = (r[1], cols)
    return idx


def _fk_shape(conn: sqlite3.Connection) -> dict[str, list]:
    fks: dict[str, list] = {}
    for table in sorted(
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ):
        rows = [
            (r[2], r[3], r[4])  # from, table, to
            for r in conn.execute(f"PRAGMA foreign_key_list({table})")
        ]
        if rows:
            fks[table] = rows
    return fks


@pytest.fixture
def status_quo(tmp_path):
    """The union of today's writers on a project db: ticketing + notify DDLs
    first (ticket-first flows run before any tracer), then the trace SCHEMA
    (its legacy `tickets` DDL no-ops — ticketing already created the table)."""
    conn = sqlite3.connect(tmp_path / "statusquo.db")
    ticketing.ensure_schema(conn)
    notify.ensure_schema(conn)
    conn.executescript(tracer.SCHEMA)
    return conn


@pytest.fixture
def contract(tmp_path):
    conn = sqlite3.connect(tmp_path / "contract.db")
    db_schema.apply_schema(conn)
    return conn


def test_contract_reproduces_status_quo_schema(status_quo, contract):
    assert _table_shape(contract) == _table_shape(status_quo)
    assert _index_shape(contract) == _index_shape(status_quo)
    assert _fk_shape(contract) == _fk_shape(status_quo)


def test_apply_schema_is_idempotent_and_stamps_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    db_schema.apply_schema(conn)
    first = _table_shape(conn)
    db_schema.apply_schema(conn)
    assert _table_shape(conn) == first
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_schema.SCHEMA_VERSION


def test_apply_schema_migrates_from_v0(tmp_path):
    """A db with no schema at all gets the full contract; a db with an older
    user_version is migrated forward (Task 4 fills MIGRATIONS — this pins the
    mechanics: version bumps to SCHEMA_VERSION and never regresses)."""
    conn = sqlite3.connect(tmp_path / "y.db")
    db_schema.apply_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_schema.SCHEMA_VERSION
    # re-apply on an already-current db keeps the version
    db_schema.apply_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_schema.SCHEMA_VERSION


# ── Task 2: Literal tightening — model JSON Schema unions == viz unions ────


def _enum(model: type[db_schema.Row], field: str) -> set[str]:
    """The field's Literal members as declared in its JSON Schema. Nullable
    Literals emit anyOf(const… + null), plain Literals emit enum — read both."""
    prop = model.model_json_schema()["properties"][field]
    out: set[str] = set()
    for alt in [prop, *prop.get("anyOf", [])]:
        if "enum" in alt:
            out |= {v for v in alt["enum"] if v is not None}
        elif "const" in alt and alt["const"] is not None:
            out.add(alt["const"])
    return out


def test_status_unions_match_the_viz():
    """sessions/phases/events statuses are Literal unions; the generated TS
    types must carry exactly the values shared/types.ts declares."""
    assert _enum(db_schema.SessionsRow, "status") == {"running", "success", "fail"}
    assert _enum(db_schema.PhasesRow, "status") == {
        "queued", "running", "success", "fail", "not_passed",
    }
    assert _enum(db_schema.PhasesRow, "kind") == {"engineer", "code", "agent"}


def test_event_type_union_matches_the_engine():
    """events.type carries every type the engine emits — the viz union is
    missing `integration` (sandbox.py emits it), so the contract pins the
    engine's real set and codegen fixes the stale viz union."""
    assert _enum(db_schema.EventsRow, "type") == {
        "phase_start", "phase_end", "agent_start", "agent_end", "tool_call",
        "handoff", "gate_pass", "gate_fail", "log", "error", "integration",
    }
