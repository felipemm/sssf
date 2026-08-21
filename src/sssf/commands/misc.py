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
    return 0 if ok else 1


def upgrade() -> int:
    return subprocess.call(["uv", "tool", "upgrade", "sssf"])
