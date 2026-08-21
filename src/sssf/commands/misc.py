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
    ok = _doctor_project(ok)
    return 0 if ok else 1


def _doctor_project(ok: bool) -> bool:
    """Project-scope interview checks (spec-create): provider, skills presence
    + freshness. No project -> nothing to check."""
    from sssf.adw_modules import skills_install
    from sssf.project import find_project

    root = find_project(Path.cwd(), None)
    if root is None:
        console.print("[dim]no project here (adws/) — skipping project checks[/dim]")
        return ok
    console.print(f"[bold]{root}[/bold]")
    from sssf import ticketing

    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        console.print("[red]missing[/red]  internal ticketing provider "
                      "(adws/config/ticketing.yaml)")
        ok = False
    else:
        console.print("[green]ok[/green]  internal ticketing provider")
    if which("pi") is None:
        console.print("[red]missing[/red]  pi (interview session)")
        ok = False
    state = skills_install.check_skills(root)
    for skill, s in state.items():
        if not s["present"]:
            console.print(f"[red]missing[/red]  skill {skill} (run `sssf init --refresh`)")
            ok = False
        elif s["stale"]:
            console.print(f"[yellow]stale[/yellow]  skill {skill} "
                          f"(pinned {s['pinned'][:7]} != remote {s['latest'][:7]})")
            ok = False
        elif s["latest"] is None:
            console.print(f"[dim]ok[/dim]  skill {skill} (freshness unverifiable — offline)")
        else:
            console.print(f"[green]ok[/green]  skill {skill} ({s['pinned'][:7]})")
    return ok


def upgrade() -> int:
    return subprocess.call(["uv", "tool", "upgrade", "sssf"])
