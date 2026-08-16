import sqlite3

from sssf.adw_modules.tracer import Tracer
from sssf.adw_modules.data_types import ConfigDefaults, ObservabilityConfig
from sssf.sandbox import review_db_path


def _tracer(tmp_path) -> Tracer:
    db = tmp_path / "sssf.db"
    return Tracer(str(db), str(tmp_path / "events.jsonl"))


def test_pending_decide_status(tmp_path):
    t = _tracer(tmp_path)
    t.review_pending("abc123", host_port=3456)
    assert t.review_status("abc123") == "pending"
    t.review_decide("abc123", "approved")
    assert t.review_status("abc123") == "approved"
    t.review_decide("abc123", "approved")   # idempotent
    assert t.review_status("abc123") == "approved"


def test_reject(tmp_path):
    t = _tracer(tmp_path)
    t.review_pending("def456", host_port=3457)
    t.review_decide("def456", "rejected")
    assert t.review_status("def456") == "rejected"


def test_unknown_status_is_none(tmp_path):
    t = _tracer(tmp_path)
    assert t.review_status("nope") is None


def test_busy_timeout_set(tmp_path):
    t = _tracer(tmp_path)
    row = t.conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000


def test_host_port_recorded(tmp_path):
    t = _tracer(tmp_path)
    t.review_pending("xyz", host_port=4001)
    row = t.conn.execute("SELECT host_port FROM run_reviews WHERE adw_id='xyz'").fetchone()
    assert row[0] == 4001
