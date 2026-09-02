"""`sssf sweep` — archive stale finished sessions AND clear their sandbox
resources across registered projects.

Review triage on demand, independent of the visualizer: finished sessions
(success/fail) whose `ended_at` is older than the interval are marked archived
so the review surface stays current. Sandbox resources are KEPT by default
(the container + .worktrees/<adw_id> are the debugging surface) — sweep is the
explicit bulk cleanup: it also removes the kept container and worktree for
every swept run. The viz server runs the same archive policy on a timer
(`server/sweep.ts`); this is the CLI form.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sssf import registry

SWEEP_SQL = """UPDATE sessions SET archived = 1
  WHERE archived = 0 AND status IN ('success','fail')
    AND ended_at IS NOT NULL
    AND datetime(ended_at) < datetime('now', ?)"""


def sweep_db(db_path: Path, interval: str = "-30 days") -> list[str]:
    """Archive eligible sessions in one db; returns the swept adw_ids so the
    caller can also clear their sandbox resources."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT adw_id FROM sessions WHERE archived = 0"
            " AND status IN ('success','fail') AND ended_at IS NOT NULL"
            " AND datetime(ended_at) < datetime('now', ?)",
            (interval,),
        ).fetchall()
        if rows:
            conn.execute(SWEEP_SQL, (interval,))
            conn.commit()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _clear_sandbox(root: Path, adw_id: str) -> None:
    """Best-effort removal of a kept run's container + worktree (the resources
    teardown deliberately leaves behind for debugging)."""
    from sssf import sandbox

    try:
        sandbox.stop_remove(sandbox.container_name(adw_id))
    except Exception as error:
        print(f"sssf sweep: {root.name}: container cleanup failed: {error}")
    try:
        sandbox.remove_worktree(sandbox.sandbox_dir(root, adw_id))
    except Exception as error:
        print(f"sssf sweep: {root.name}: worktree cleanup failed: {error}")


def run(project_root: str | None = None, days: int = 30) -> int:
    interval = f"-{days} days"
    if project_root:
        root = Path(project_root).resolve()
        from sssf.adw_modules import paths

        paths.warn_if_legacy(root, command="sweep")
        targets = [(root.name, root, paths.data_dir(root) / "sssf.db")]
    else:
        targets = [
            (p["name"], Path(p["root"]), Path(p["db"]))
            for p in registry.list_projects()
        ]

    if not targets:
        print("sssf sweep: no projects registered — run `sssf init` first")
        return 0

    total = 0
    for name, root, db_path in targets:
        try:
            adw_ids = sweep_db(db_path, interval)
        except sqlite3.Error as error:
            print(f"sssf sweep: {name}: {error}")
            continue
        for adw_id in adw_ids:
            _clear_sandbox(root, adw_id)
        if adw_ids:
            print(
                f"sssf sweep: {name}: archived {len(adw_ids)} session(s)"
                f" and cleared their sandbox resources"
            )
        total += len(adw_ids)
    print(f"sssf sweep: done — {total} session(s) archived")
    return 0
