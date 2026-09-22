"""Task 3: the tracer writes through the schema contract. A db built by a
Tracer alone must match db_schema's contract exactly (the legacy tickets DDL
is gone), and writes must validate against the Row models before SQL."""

import sqlite3

import pytest
from pydantic import ValidationError
from test_db_schema import _table_shape

from sssf import db_schema
from sssf.adw_modules.data_types import EventRecord
from sssf.adw_modules.tracer import Tracer


def test_tracer_db_matches_contract(tmp_path):
    """A fresh Tracer db has the full contract shape — no legacy tickets DDL
    (status defaults to the machine 'needs-triage' even before ticketing runs)."""
    trace_db = tmp_path / "trace.db"
    Tracer(trace_db, tmp_path / "events.jsonl")

    conn = sqlite3.connect(trace_db)
    try:
        shape = _table_shape(conn)
    finally:
        conn.close()

    contract = sqlite3.connect(tmp_path / "contract.db")
    try:
        db_schema.apply_schema(contract)
        assert shape == _table_shape(contract)
    finally:
        contract.close()

    assert ("status", "TEXT", 1, "'needs-triage'", 0) in shape["tickets"]


def test_tracer_event_validates_against_the_contract(tmp_path):
    """A write outside the declared unions fails model validation before any
    SQL — the enforcement half of the contract."""
    t = Tracer(tmp_path / "trace.db", tmp_path / "events.jsonl")
    with pytest.raises(ValidationError):
        t.event(
            EventRecord(
                adw_id="a", phase_id="p", type="not-a-real-event-type",
                name="x", payload={}, tokens=0,
            )
        )
