"""`sssf sweep` — archive stale finished sessions across registered projects.

Review triage on demand, independent of the visualizer: finished sessions
(success/fail) whose `ended_at` is older than the interval are marked archived
so the review surface stays current. The viz server runs the same policy on a
timer (`server/sweep.ts`); this is the CLI form — identical SQL.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sssf import registry

SWEEP_SQL = """UPDATE sessions SET archived = 1
  WHERE archived = 0 AND status IN ('success','fail')
    AND ended_at IS NOT NULL
    AND datetime(ended_at) < datetime('now', ?)"""


def sweep_db(db_path: Path, interval: str = "-30 days") -> int:
    """Archive eligible sessions in one db; returns how many were archived."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(SWEEP_SQL, (interval,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def run(project_root: str | None = None, days: int = 30) -> int:
    interval = f"-{days} days"
    if project_root:
        root = Path(project_root).resolve()
        targets = [(root.name, root / "adws" / "adw_data" / "sssf.db")]
    else:
        targets = [(p["name"], Path(p["db"])) for p in registry.list_projects()]

    if not targets:
        print("sssf sweep: no projects registered — run `sssf init` first")
        return 0

    total = 0
    for name, db_path in targets:
        try:
            n = sweep_db(db_path, interval)
        except sqlite3.Error as error:
            print(f"sssf sweep: {name}: {error}")
            continue
        if n:
            print(f"sssf sweep: {name}: archived {n} session(s)")
        total += n
    print(f"sssf sweep: done — {total} session(s) archived")
    return 0
