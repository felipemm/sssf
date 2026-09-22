"""The schema contract (Task 1): db_schema's model-generated DDL must
reproduce the status-quo schema exactly — columns, types, nullability,
defaults, PK ordinals, FKs, indexes — and apply_schema must be idempotent
and stamp PRAGMA user_version."""

import sqlite3

import pytest

from sssf import db_schema, notify, ticketing


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


# The pre-refactor tracer SCHEMA, frozen as the golden shape: db_schema must
# reproduce it exactly. (tracer.py no longer ships it — the models are the
# source of truth — so the snapshot lives here as the regression pin.)
_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  adw_id        TEXT PRIMARY KEY,
  adw_name      TEXT,
  request       TEXT,
  status        TEXT,
  engineer      TEXT,
  started_at    TEXT, ended_at TEXT,
  total_tokens  INTEGER DEFAULT 0, total_cost REAL DEFAULT 0,
  archived      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS phases (
  phase_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  seq           INTEGER,
  name TEXT, kind TEXT, owner TEXT, description TEXT,
  status        TEXT DEFAULT 'fail',
  attempt       INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
  error         TEXT,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  parent_id     TEXT,
  type          TEXT,
  name          TEXT,
  payload_json  TEXT,
  tokens        INTEGER,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS envelopes (
  envelope_id   TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  agent         TEXT,
  output_type   TEXT,
  payload_json  TEXT,
  valid         INTEGER,
  attempt       INTEGER,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS gate_results (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  attempt       INTEGER,
  gate          TEXT,
  passed        INTEGER,
  violations_json TEXT,
  checks_json   TEXT,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS processes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  adw_id        TEXT REFERENCES sessions,
  kind          TEXT,
  name          TEXT,
  pid           INTEGER,
  command       TEXT,
  started_at    TEXT, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_sessions (
  adw_id        TEXT REFERENCES sessions,
  agent         TEXT,
  coding_agent  TEXT, model TEXT, color TEXT,
  session_id    TEXT,
  context_tokens INTEGER,
  context_window INTEGER,
  created_at    TEXT, last_used_at TEXT,
  PRIMARY KEY (adw_id, agent)
);
CREATE TABLE IF NOT EXISTS tickets (
  id          TEXT PRIMARY KEY,
  provider    TEXT NOT NULL,
  external_id TEXT,
  title       TEXT NOT NULL,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'backlog',
  prompt_file TEXT,
  adw_id      TEXT,
  source_url  TEXT,
  created_at  TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sandbox_run (
  adw_id          TEXT PRIMARY KEY,
  container       TEXT NOT NULL,
  container_port  INTEGER,
  host_port       INTEGER,
  review_url      TEXT,
  review_command  TEXT,
  instructions    TEXT DEFAULT '',
  status          TEXT,
  updated_at      TEXT
);
"""


@pytest.fixture
def status_quo(tmp_path):
    """The historical union of writers on a project db: ticketing + notify
    DDLs first (ticket-first flows run before any tracer), then the frozen
    trace SCHEMA (its legacy `tickets` DDL no-ops — ticketing already created
    the table)."""
    conn = sqlite3.connect(tmp_path / "statusquo.db")
    ticketing.ensure_schema(conn)
    notify.ensure_schema(conn)
    conn.executescript(_LEGACY_SCHEMA)
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


# ── Task 4: historical migrations (machine columns + data backfills) ───────


def test_legacy_db_migrates_to_the_machine(tmp_path):
    """A pre-machine db (legacy tickets shape, user_version 0) is migrated
    forward by apply_schema: machine columns added, legacy statuses remapped,
    tracked/origin backfilled, version stamped."""
    conn = sqlite3.connect(tmp_path / "legacy.db")
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, status) "
        "VALUES ('jira:X-1', 'jira', 'X-1', 'Legacy ticket', 'backlog')"
    )
    conn.commit()

    db_schema.apply_schema(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
    for c in ("context", "kind", "tracked", "origin", "parent_id", "spec", "rejection_feedback"):
        assert c in cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ticket_events", "ticket_runs", "ticket_mrs"} <= tables
    row = conn.execute("SELECT status, tracked, origin FROM tickets WHERE id='jira:X-1'").fetchone()
    assert row[0] == "ready-for-agent"  # 'backlog' remapped onto the machine
    assert row[1] == 0  # synced row: untracked, permanent
    assert row[2] == "jira"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_schema.SCHEMA_VERSION


def test_apply_schema_tolerates_existing_machine_columns(tmp_path):
    """A db that already has the machine columns (pre-refactor ticketing ran
    on it) still migrates cleanly at version 0 — the column ALTERs are
    guarded, never re-adding what exists."""
    conn = sqlite3.connect(tmp_path / "mixed.db")
    conn.executescript(_LEGACY_SCHEMA)
    # simulate a pre-refactor ticketing pass: machine columns already present
    for ddl in (
        "ALTER TABLE tickets ADD COLUMN context TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE tickets ADD COLUMN kind TEXT NOT NULL DEFAULT 'implementation'",
        "ALTER TABLE tickets ADD COLUMN tracked INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE tickets ADD COLUMN origin TEXT NOT NULL DEFAULT 'internal'",
        "ALTER TABLE tickets ADD COLUMN spec TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE tickets ADD COLUMN rejection_feedback TEXT NOT NULL DEFAULT ''",
    ):
        conn.execute(ddl)
    conn.commit()

    db_schema.apply_schema(conn)  # must not raise "duplicate column"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_schema.SCHEMA_VERSION


# ── Task 5: generic writers validate copied rows ───────────────────────────


def test_sync_run_db_skips_rows_outside_the_contract(tmp_path):
    """sync_run_db validates copied rows against the models: a row the
    contract rejects (an unknown event type from a foreign/newer writer) is
    dropped, never copied into the project db."""
    from sssf.sandbox import sync_run_db

    project = sqlite3.connect(tmp_path / "project.db")
    db_schema.apply_schema(project)

    per_db = tmp_path / "run.db"
    src = sqlite3.connect(per_db)
    db_schema.apply_schema(src)
    src.execute(
        "INSERT INTO events (event_id, adw_id, type) VALUES ('evt_ok', 'r1', 'phase_start')"
    )
    src.execute(
        "INSERT INTO events (event_id, adw_id, type) VALUES ('evt_bad', 'r1', 'not-a-real-type')"
    )
    src.commit()
    src.close()

    sync_run_db(project, per_db, "r1")

    rows = project.execute("SELECT event_id FROM events WHERE adw_id='r1'").fetchall()
    assert [r[0] for r in rows] == ["evt_ok"]
    project.close()
