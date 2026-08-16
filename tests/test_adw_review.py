import os
import socket
import sqlite3
import subprocess
import threading
import time

from sssf.adw_modules.data_types import ReviewConfig, SSSFConfig
from sssf.adw_modules.review import auto_review_command, human_review
from sssf.adw_modules.tracer import Tracer
from sssf.adw_modules.session import Run


class _Ph:
    def __init__(self): self.logged = []
    def log(self, **kw): self.logged.append(kw)


def _run(tmp_path) -> tuple[Run, Tracer, str]:
    db_path = str(tmp_path / "sssf.db")
    tracer = Tracer(db_path, str(tmp_path / "events.jsonl"))
    cfg = SSSFConfig()
    r = Run(cfg=cfg, adw_id="rvw1", tracer=tracer, engineer="E")
    return r, tracer, db_path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_auto_review_command(tmp_path):
    # bun.lock + dev script -> bun run dev
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    (tmp_path / "bun.lock").write_text("")
    assert auto_review_command(tmp_path) == "bun run dev"
    # package-lock -> npm run dev
    (tmp_path / "bun.lock").unlink()
    (tmp_path / "package-lock.json").write_text("")
    assert auto_review_command(tmp_path) == "npm run dev"
    # python-only -> None
    (tmp_path / "package.json").unlink()
    (tmp_path / "pyproject.toml").write_text("")
    assert auto_review_command(tmp_path) is None


def test_human_review_approves(tmp_path):
    run, tracer, db_path = _run(tmp_path)
    port = _free_port()
    cfg = SSSFConfig(review=ReviewConfig(command=f"python -m http.server {port}", port=port, poll_seconds=1))
    ph = _Ph()
    os.environ["REVIEW_HOST_PORT"] = str(port)
    try:
        result = {}
        def decide():
            # The decision must arrive through its OWN connection — a
            # cross-thread use of the tracer's conn raises (check_same_thread)
            # and would hang the poll forever. This mirrors the real flow: the
            # CLI decides from a separate process/connection.
            time.sleep(1.5)
            conn = sqlite3.connect(db_path, isolation_level=None)
            conn.execute("UPDATE run_reviews SET status='approved' WHERE adw_id=?", (run.adw_id,))
            conn.close()
        threading.Thread(target=decide, daemon=True).start()
        ok = human_review(run, cfg, ph, "review me")
        assert ok is True
        assert tracer.review_status(run.adw_id) == "approved"
        # the URL was logged
        assert any("http://localhost:" in str(x) for x in ph.logged)
    finally:
        os.environ.pop("REVIEW_HOST_PORT", None)


def test_human_review_rejects(tmp_path):
    run, tracer, db_path = _run(tmp_path)
    port = _free_port()
    cfg = SSSFConfig(review=ReviewConfig(command=f"python -m http.server {port}", port=port, poll_seconds=1))
    ph = _Ph()
    os.environ["REVIEW_HOST_PORT"] = str(port)
    try:
        def decide():
            time.sleep(1.5)
            conn = sqlite3.connect(db_path, isolation_level=None)
            conn.execute("UPDATE run_reviews SET status='rejected' WHERE adw_id=?", (run.adw_id,))
            conn.close()
        threading.Thread(target=decide, daemon=True).start()
        ok = human_review(run, cfg, ph, "review me")
        assert ok is False
    finally:
        os.environ.pop("REVIEW_HOST_PORT", None)
