#!/usr/bin/env python3
"""E2E: ticketing — create N tickets, then launch them ALL at once.

Sets up a fresh project with ticketing enabled (internal provider) and every
agent on litellm/deepseek-v4-flash-official, creates N tickets, fires all N
`sssf ticket run` sandboxed runs concurrently, then waits for every ticket's
linked session to reach a terminal state and reports.

Run: uv run python scripts/e2e_ticketing.py [N]
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
PROJECT = Path("/tmp/sbx-tkt20")
INKWELL_CONFIG = Path.home() / "dev/lab/demos/inkwell/adws/adw_sssf_config/sssf.config.yaml"
MODEL = "litellm/deepseek-v4-flash-official"


def sh(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def main() -> int:
    # ── fresh project ──────────────────────────────────────────────────────
    if PROJECT.exists():
        shutil.rmtree(PROJECT)
    PROJECT.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=PROJECT)
    sh("git", "config", "user.email", "t@t", cwd=PROJECT)
    sh("git", "config", "user.name", "T", cwd=PROJECT)
    sh("sssf", "init", "--force", cwd=PROJECT)

    # config: inkwell's agents, every model -> deepseek-v4-flash-official
    import yaml

    raw = yaml.safe_load(INKWELL_CONFIG.read_text()) or {}
    for agent in raw.get("agents", []):
        agent["model"] = MODEL
    cfg_path = PROJECT / "adws/adw_sssf_config/sssf.config.yaml"
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    # ticketing: enable the internal provider
    tkt = PROJECT / "adws/adw_sssf_config/ticketing.yaml"
    tkt.write_text("providers:\n  - internal\n")

    sh("git", "add", "-A", cwd=PROJECT)
    sh("git", "commit", "-qm", "init", cwd=PROJECT)
    sh("sssf", "projects", "add", str(PROJECT))

    # ── create N tickets ───────────────────────────────────────────────────
    ids: list[str] = []
    for i in range(N):
        r = sh(
            "sssf",
            "ticket",
            "add",
            f"Ticket task {i}: create tkt{i}.txt with content tkt {i}",
            "--project",
            str(PROJECT),
            cwd=PROJECT,
        )
        line = r.stdout.strip().splitlines()[-1]
        ids.append(line.split("(")[1].split(")")[0])  # internal:<uuid>
    print(f"created {len(ids)} tickets")

    # ── launch them ALL at once ────────────────────────────────────────────
    t0 = time.time()
    procs = []
    for tid in ids:
        p = subprocess.Popen(
            ["sssf", "ticket", "run", tid, "--project", str(PROJECT)],
            cwd=str(PROJECT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        procs.append(p)
    dispatch_s = time.time() - t0
    for p in procs:
        p.wait(timeout=60)

    # ── wait for every ticket's session to reach a terminal state ──────────
    # Fast completion: the runs are done the moment no container is left.
    # The monitors' final syncs land within a short settle window; if a
    # monitor died, fall back to syncing any leftover per-run dbs ourselves.
    db = PROJECT / "adws/adw_data/sssf.db"
    import os

    sbx_dir = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / PROJECT.name
    from sssf.sandbox import sync_run_db

    deadline = time.time() + 15 * 60
    settle_deadline = time.time() + 30
    containers = float("inf")
    while time.time() < deadline:
        try:
            conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
            linked = conn.execute(
                "SELECT t.adw_id, s.status FROM tickets t LEFT JOIN sessions s ON s.adw_id = t.adw_id"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            linked = []
        containers = len(
            sh("docker", "ps", "-a", "--filter", "name=sssf-", "--format", "{{.Names}}")
            .stdout.strip()
            .splitlines()
        )
        terminal = [r for r in linked if r[1] in ("success", "fail")]
        if len(terminal) == N and containers == 0:
            break
        if containers == 0:
            # all containers exited — wait for the monitors' final syncs to land
            if time.time() >= settle_deadline:
                # manual fallback: sync any leftover per-run dbs, then finish
                if sbx_dir.exists():
                    for wt in sbx_dir.iterdir():
                        per_run = wt / "adws" / "adw_data" / "sssf.db"
                        if per_run.exists():
                            conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
                            try:
                                sync_run_db(conn, per_run, wt.name)
                            except Exception:
                                pass
                            conn.close()
                break
        else:
            settle_deadline = time.time() + 30  # a container is still running
        time.sleep(3)
    elapsed = time.time() - t0

    # ── report ─────────────────────────────────────────────────────────────
    conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
    rows = conn.execute(
        "SELECT t.id, t.adw_id, s.status, s.started_at, s.ended_at"
        " FROM tickets t LEFT JOIN sessions s ON s.adw_id = t.adw_id ORDER BY t.created_at"
    ).fetchall()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    success = sum(1 for r in rows if r[2] == "success")
    failed = sum(1 for r in rows if r[2] == "fail")
    # a ticket whose run never produced a session = failed to start (a startup
    # flake), not pending
    failed_to_start = sum(1 for r in rows if r[2] is None)
    pending = sum(1 for r in rows if r[2] not in ("success", "fail", None))
    finished = [r for r in rows if r[3] and r[4]]
    overlap = 0
    for i, a in enumerate(finished):
        for b in finished[i + 1 :]:
            if a[3] <= b[4] and b[3] <= a[4]:
                overlap += 1

    branches = sh("git", "branch", "--list", "sssf/*", cwd=PROJECT).stdout.strip().splitlines()
    import os

    sbx_dir = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / PROJECT.name
    remaining_wt = len(list(sbx_dir.glob("*"))) if sbx_dir.exists() else 0
    remaining_ct = len(
        sh("docker", "ps", "-a", "--filter", "name=sssf-", "--format", "{{.Names}}")
        .stdout.strip()
        .splitlines()
    )

    print("\n=== E2E ticketing report ===")
    print(f"tickets: {len(rows)} · launched all in {dispatch_s:.0f}s · finished in {elapsed:.0f}s")
    print(f"verdicts: success {success} · failed {failed} · pending {pending}")
    print(f"concurrent overlap: {overlap}/{N * (N - 1) // 2} run-pairs")
    print(
        f"db integrity: {integrity} · leftover containers: {remaining_ct} · worktrees: {remaining_wt}"
    )
    print(f"surviving branches: {len(branches)}")
    for tid, adw, status, _s, _e in rows:
        print(f"  {tid:<20} {adw or '-':<10} {status or 'unlinked':8}")
    infra_ok = (
        integrity == "ok"
        and pending == 0
        and overlap == N * (N - 1) // 2
        and remaining_ct == 0
        and remaining_wt == 0
        and len(branches) == N
    )
    print(
        "RESULT:",
        "PASS (infra)" if infra_ok else "FAIL",
        f"— LLM verdicts: {success}/{N} success · failed-to-start: {failed_to_start}",
    )
    return 0 if infra_ok else 1


if __name__ == "__main__":
    sys.exit(main())
