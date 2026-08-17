"""sssf sessions / phases / tail / procs — the justfile recipes as commands."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from sssf import obs

SESSIONS_SQL = (
    "select adw_id, adw_name, status, substr(request,1,50), total_tokens, "
    "round(total_cost,4) from sessions order by started_at desc limit ?;"
)
PHASES_SQL = (
    "select seq, name, kind, owner, status, attempt from phases where adw_id=? order by seq;"
)
TAIL_SQL = (
    "select rowid, type, name, started_at from events where adw_id=? order by rowid desc limit 25;"
)
PROCS_SQL = (
    "select kind, name, pid, command, started_at from processes "
    "where adw_id=? and ended_at is null order by id;"
)

console = Console()


def _render(db: Path, sql: str, params: tuple, title: str, limit: int | None = None) -> int:
    if not db.exists():
        console.print(f"[yellow]no trace db at {db}[/yellow] — run an ADW first")
        return 0
    rows = obs.query(db, sql, params)
    table = Table(title=title)
    if rows:
        for col in rows[0].keys():
            table.add_column(col)
        for row in rows:
            table.add_row(*[str(v) if v is not None else "" for v in row])
    console.print(table)
    return 0


def sessions(db: Path, limit: int = 10) -> int:
    return _render(db, SESSIONS_SQL, (limit,), "recent runs")


def phases(db: Path, adw_id: str) -> int:
    return _render(db, PHASES_SQL, (adw_id,), f"phases {adw_id}")


def tail(db: Path, adw_id: str) -> int:
    return _render(db, TAIL_SQL, (adw_id,), f"events {adw_id}")


def procs(db: Path, adw_id: str) -> int:
    return _render(db, PROCS_SQL, (adw_id,), f"live processes {adw_id}")
