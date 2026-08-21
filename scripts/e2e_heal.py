#!/usr/bin/env python3
"""E2E: the self-healing monitor — inject stuck states, verify recovery.

Sets up a fresh project, then runs three deterministic scenarios through
`heal_once` (the daemon's pass logic):

  1. dead run   — a 'running' session with no container/worktree → finalized fail
  2. mon crash  — a 'running' session whose container is gone but the worktree
                  + per-run db remain → the terminal state is synced + torn down
  3. bad spawn  — a ticket stuck 'starting' past the threshold → back to backlog

Run: uv run python scripts/e2e_heal.py
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path("/tmp/sbx-heal")


def sh(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def make_project() -> Path:
    if PROJECT.exists():
        shutil.rmtree(PROJECT)
    PROJECT.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=PROJECT)
    sh("git", "config", "user.email", "t@t", cwd=PROJECT)
    sh("git", "config", "user.name", "T", cwd=PROJECT)
    sh("sssf", "init", "--force", cwd=PROJECT)
    (PROJECT / "adws/adw_sssf_config/ticketing.yaml").write_text("providers:\n  - internal\n")
    sh("git", "add", "-A", cwd=PROJECT)
    sh("git", "commit", "-qm", "init", cwd=PROJECT)
    sh("sssf", "projects", "add", str(PROJECT))
    return PROJECT


def db(root: Path) -> Path:
    return root / "adws/adw_data/sssf.db"


def main() -> int:
    root = make_project()
    from sssf.healer import heal_once

    results = []
    data = root / "adws" / "adw_data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db(root)), isolation_level=None, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (adw_id TEXT PRIMARY KEY, status TEXT, started_at TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, provider TEXT, external_id TEXT, title TEXT, description TEXT, status TEXT, prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)"
    )

    # ── scenario 1: dead run ───────────────────────────────────────────────
    conn.execute(
        "INSERT INTO sessions (adw_id, status, started_at) VALUES ('dead1', 'running', datetime('now'))"
    )
    conn.commit()
    actions = heal_once()
    s = conn.execute("SELECT status FROM sessions WHERE adw_id='dead1'").fetchone()[0]
    ok1 = s == "fail" and any("dead1" in a and "finalized" in a for a in actions)
    results.append(("dead run finalized", ok1, f"status={s} actions={actions}"))

    # ── scenario 2: monitor crash — container gone, worktree + per-run db stay
    import sssf.sandbox as sb

    wt = sb.sandbox_dir(root, "mon1")
    (wt / "adws/adw_data").mkdir(parents=True, exist_ok=True)
    pr = wt / "adws/adw_data/sssf.db"
    pconn = sqlite3.connect(str(pr))
    pconn.execute("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)")
    pconn.execute("INSERT INTO sessions VALUES ('mon1', 'success', datetime('now'))")
    pconn.commit()
    pconn.close()
    conn.execute(
        "INSERT INTO sessions (adw_id, status, started_at) VALUES ('mon1', 'running', datetime('now'))"
    )
    conn.commit()
    conn.close()
    actions = heal_once()
    ok2 = (
        wt.exists() is False
        and conn is not None
        and (
            sqlite3.connect(str(db(root)), isolation_level=None)
            .execute("SELECT status FROM sessions WHERE adw_id='mon1'")
            .fetchone()[0]
            == "success"
        )
    )
    conn = sqlite3.connect(str(db(root)), isolation_level=None, timeout=10)
    results.append(
        ("monitor-crash recovered", ok2, f"worktree_removed={not wt.exists()} actions={actions}")
    )

    # ── scenario 3: ticket stuck 'starting' past the threshold ─────────────
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60 * 30))
    conn.execute(
        "INSERT INTO tickets (id, provider, title, status, adw_id, updated_at) VALUES ('internal:stuck1','internal','x','starting','tkt1',?)",
        (old,),
    )
    conn.commit()
    conn.close()
    actions = heal_once()
    conn = sqlite3.connect(str(db(root)), isolation_level=None, timeout=10)
    t = conn.execute("SELECT status, adw_id FROM tickets WHERE id='internal:stuck1'").fetchone()
    conn.close()
    ok3 = t[0] == "backlog" and t[1] is None
    results.append(("stuck ticket back to backlog", ok3, f"ticket={t} actions={actions}"))

    print("\n=== E2E heal report ===")
    all_ok = True
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}")
        all_ok = all_ok and ok
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
