"""sssf projects / doctor / upgrade."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from rich.console import Console
from rich.table import Table

from sssf import registry

which = shutil.which
console = Console()

CORE_TOOLS = ("uv", "pi", "bun", "sqlite3")


def projects(action: str, name: str | None) -> int:
    if action == "remove":
        if not name:
            console.print("[red]usage: sssf projects remove <name>[/red]")
            return 1
        if registry.remove_project(name):
            console.print(f"removed {name}")
            return 0
        console.print(f"[red]no project named {name}[/red]")
        return 1
    rows = registry.list_projects()
    table = Table(title="registered projects")
    for col in ("name", "root", "last_run"):
        table.add_column(col)
    for row in rows:
        table.add_row(row.get("name", ""), row.get("root", ""), row.get("last_run") or "—")
    console.print(table)
    return 0


def _recent_spawn_failures(limit: int = 5) -> list[tuple[str, str]]:
    """[(adw_id, hint)] from the current project's db — read-only."""
    import json
    import sqlite3

    from sssf.adw_modules import paths

    root = Path.cwd()
    db = paths.data_dir(root) / "sssf.db"
    if not (root / "adws").exists() or not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        rows = conn.execute(
            "SELECT adw_id FROM sessions"
            " WHERE adw_name='adw_simple_sdlc (never started)'"
            " ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[tuple[str, str]] = []
        for (adw_id,) in rows:
            ev = conn.execute(
                "SELECT payload_json FROM events WHERE adw_id=?"
                " AND name='sandbox spawn failure'"
                " ORDER BY started_at DESC LIMIT 1",
                (adw_id,),
            ).fetchone()
            hint = ""
            if ev:
                payload = json.loads(ev[0])
                hint = payload.get("remediation") or (
                    payload.get("container_log_tail") or ""
                )[-120:]
            out.append((adw_id, hint))
        conn.close()
        return out
    except (sqlite3.Error, ValueError):
        return []


def doctor() -> int:
    ok = True
    for tool in CORE_TOOLS:
        found = which(tool)
        if found:
            console.print(f"[green]ok[/green]  {tool} -> {found}")
        else:
            console.print(f"[red]missing[/red]  {tool}")
            ok = False
    bin_dir = Path.home() / ".local" / "bin"
    on_path = str(bin_dir) in os.environ.get("PATH", "")
    console.print(
        f"[{'green' if on_path else 'red'}]{'ok' if on_path else 'missing'}[/]  ~/.local/bin on PATH"
    )
    failures = _recent_spawn_failures()
    if failures:
        console.print("\n[yellow]recent spawn failures[/yellow]")
        for adw_id, hint in failures:
            console.print(f"  {adw_id}  {hint or '(no hint classified)'}")
    return 0 if ok else 1


def upgrade() -> int:
    return subprocess.call(["uv", "tool", "upgrade", "sssf"])
