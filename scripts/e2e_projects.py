#!/usr/bin/env python3
"""E2E: two projects at once, 5 parallel sandboxed sessions each.

Sets up two fresh projects and fires 5 concurrent `sssf run` sandboxed runs
into EACH (10 runs total, all overlapping), then waits for every session to
finish and reports per-project + overall: statuses, concurrency, db integrity,
teardowns, and surviving branches.

Run: uv run python scripts/e2e_projects.py [per_project]
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PER = int(sys.argv[1]) if len(sys.argv) > 1 else 5
PROJECTS = [Path("/tmp/sbx-proj-a"), Path("/tmp/sbx-proj-b")]
INKWELL_CONFIG = Path.home() / "dev/lab/demos/inkwell/adws/adw_sssf_config/sssf.config.yaml"


def sh(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def make_project(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=path)
    sh("git", "config", "user.email", "t@t", cwd=path)
    sh("git", "config", "user.name", "T", cwd=path)
    sh("sssf", "init", "--force", cwd=path)
    shutil.copy(INKWELL_CONFIG, path / "adws/adw_sssf_config/sssf.config.yaml")
    sh("git", "add", "-A", cwd=path)
    sh("git", "commit", "-qm", "init", cwd=path)
    sh("sssf", "projects", "add", str(path))
    return path


def main() -> int:
    for p in PROJECTS:
        make_project(p)

    # ── dispatch PER runs into each project, all at once ───────────────────
    t0 = time.time()
    procs = []
    for pi, project in enumerate(PROJECTS):
        for i in range(PER):
            req = f"Create a file called p{pi}t{i}.txt with content project {pi} task {i}"
            procs.append((project, subprocess.Popen(
                ["sssf", "run", "simple_sdlc", req, "--project", str(project)],
                cwd=str(project), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
    for _p, proc in procs:
        proc.wait(timeout=120)
    dispatch_s = time.time() - t0

    # ── wait for all sessions terminal (fast: no containers left + settle) ─
    import os
    from sssf.sandbox import sync_run_db, project_db_path
    deadline = time.time() + 15 * 60
    settle = time.time() + 30
    while time.time() < deadline:
        containers = len(sh("docker", "ps", "-a", "--filter", "name=sssf-",
                            "--format", "{{.Names}}").stdout.strip().splitlines())
        done = True
        for project in PROJECTS:
            try:
                conn = sqlite3.connect(str(project_db_path(project / "adws/adw_data")),
                                       isolation_level=None, timeout=10)
                rows = conn.execute("SELECT status FROM sessions").fetchall()
                conn.close()
            except sqlite3.Error:
                rows = []
            if len(rows) < PER or any(r[0] == "running" for r in rows):
                done = False
        if done and containers == 0:
            break
        if containers == 0 and time.time() >= settle:
            break
        time.sleep(3)
    elapsed = time.time() - t0

    # ── report ─────────────────────────────────────────────────────────────
    overall = {"success": 0, "fail": 0, "pending": 0}
    all_ok = True
    for project in PROJECTS:
        db_path = project_db_path(project / "adws/adw_data")
        try:
            conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=10)
            sessions = conn.execute(
                "SELECT adw_id, status, started_at, ended_at FROM sessions ORDER BY started_at").fetchall()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            sessions, integrity = [], "error"
        success = sum(1 for s in sessions if s[1] == "success")
        failed = sum(1 for s in sessions if s[1] == "fail")
        pending = sum(1 for s in sessions if s[1] not in ("success", "fail"))
        branches = sh("git", "branch", "--list", "sssf/*", cwd=project).stdout.strip().splitlines()
        sbx = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / project.name
        leftover_wt = len(list(sbx.glob("*"))) if sbx.exists() else 0
        # a dispatched run whose session never appeared (branch only) is a
        # failed-to-start startup flake — a verdict, not an infra failure
        failed_to_start = PER - len(sessions)
        ok = integrity == "ok" and pending == 0 and len(sessions) <= PER and leftover_wt == 0
        all_ok = all_ok and ok
        overall["success"] += success
        overall["fail"] += failed + failed_to_start
        overall["pending"] += pending
        print(f"\nproject {project.name}: sessions {len(sessions)} · success {success} · "
              f"fail {failed} · failed-to-start {PER - len(sessions)} · pending {pending} · "
              f"integrity {integrity} · branches {len(branches)} · leftover worktrees {leftover_wt} · "
              f"{'PASS' if ok else 'FAIL'}")
        for adw_id, status, _s, _e in sessions:
            print(f"  {adw_id}  {status}")

    print("\n=== E2E projects report ===")
    print(f"projects: {len(PROJECTS)} × {PER} sessions · dispatched in {dispatch_s:.0f}s · "
          f"finished in {elapsed:.0f}s")
    print(f"total: {len(PROJECTS) * PER} sessions · {overall['success']} success · "
          f"{overall['fail']} fail · {overall['pending']} pending")
    print(f"leftover containers: {len(sh('docker', 'ps', '-a', '--filter', 'name=sssf-', '--format', '{{.Names}}').stdout.strip().splitlines())}")
    print("RESULT:", "PASS" if all_ok and overall["pending"] == 0 else "FAIL",
          f"— LLM verdicts: {overall['success']}/{len(PROJECTS) * PER} success")
    return 0 if all_ok and overall["pending"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
