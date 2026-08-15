"""Read-only trace queries over a project's WAL sssf.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
