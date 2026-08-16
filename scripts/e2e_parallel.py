#!/usr/bin/env python3
"""E2E: dispatch N concurrent sandboxed runs into a single test project.

Sets up a fresh project, fires N `sssf run` sandboxed runs back-to-back (they
run concurrently in their own worktree+container), then waits for all N to
finish and reports: statuses, phase counts, the concurrency overlap window,
db integrity, and the surviving branches.

Run: uv run python scripts/e2e_parallel.py [N]
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
PROJECT = Path("/tmp/sbx-par10")
INKWELL_CONFIG = Path.home() / "dev/lab/demos/inkwell/adws/adw_sssf_config/sssf.config.yaml"


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
    shutil.copy(INKWELL_CONFIG, PROJECT / "adws/adw_sssf_config/sssf.config.yaml")
    sh("git", "add", "-A", cwd=PROJECT)
    sh("git", "commit", "-qm", "init", cwd=PROJECT)
    sh("sssf", "projects", "add", str(PROJECT))

    # ── dispatch N concurrent sandboxed runs ───────────────────────────────
    t0 = time.time()
    for i in range(N):
        req = f"Create a file called task{i}.txt with the exact content: task {i} done"
        r = sh("sssf", "run", "simple_sdlc", req, "--project", str(PROJECT), cwd=PROJECT)
        ok = r.returncode == 0
        print(f"[dispatch {i:>2}] {'ok' if ok else 'FAIL ' + r.stderr.strip()[:120]} — {r.stdout.strip()}")
    dispatch_s = time.time() - t0

    # ── wait for all N to reach a terminal state ───────────────────────────
    db = PROJECT / "adws/adw_data/sssf.db"
    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        try:
            conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
            rows = conn.execute("SELECT status FROM sessions").fetchall()
            conn.close()
        except sqlite3.Error:
            rows = []
        if len(rows) >= N and not any(r[0] == "running" for r in rows):
            break
        time.sleep(5)
    elapsed = time.time() - t0

    # ── report ─────────────────────────────────────────────────────────────
    conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
    sessions = conn.execute(
        "SELECT adw_id, status, started_at, ended_at FROM sessions ORDER BY started_at").fetchall()
    phases = dict(conn.execute("SELECT adw_id, COUNT(*) FROM phases GROUP BY adw_id").fetchall())
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    success = sum(1 for s in sessions if s[1] == "success")
    # concurrency overlap: runs whose [started, ended] intervals overlap
    finished = [s for s in sessions if s[2] and s[3]]
    max_overlap = 0
    for i, a in enumerate(finished):
        for b in finished[i + 1:]:
            if a[2] <= b[3] and b[2] <= a[3]:
                max_overlap += 1
    overlap_pairs = max_overlap

    print("\n=== E2E parallel report ===")
    print(f"dispatched {N} runs in {dispatch_s:.0f}s · finished in {elapsed:.0f}s total")
    print(f"sessions: {len(sessions)} · success {success} · fail {len(sessions) - success}")
    for adw_id, status, started, ended in sessions:
        dur = ""
        if started and ended:
            dur = f" · {round((__import__('datetime').datetime.fromisoformat(ended.replace('Z','+00:00')) - __import__('datetime').datetime.fromisoformat(started.replace('Z','+00:00'))).total_seconds())}s"
        print(f"  {adw_id}  {status:8} phases={phases.get(adw_id, 0):>2}{dur}")
    print(f"concurrent overlap: {overlap_pairs} run-pairs overlapped (max possible {N * (N - 1) // 2})")
    print(f"db integrity: {integrity}")
    branches = sh("git", "branch", "--list", "sssf/*", cwd=PROJECT).stdout.strip().splitlines()
    print(f"surviving branches: {len(branches)}")
    # Infrastructure health is the test: full concurrency, a terminal state for
    # every run, clean teardown (no containers/worktrees left), db integrity,
    # and every branch surviving. The success/fail split is the ADW's own LLM
    # verdicts (the reviewer may reject a run — a model-level outcome, not a
    # sandbox failure), reported separately.
    remaining_containers = len(sh("docker", "ps", "-a", "--filter", "name=sssf-",
                                  "--format", "{{.Names}}").stdout.strip().splitlines())
    remaining_worktrees = len(list(PROJECT.glob("../../.sssf/sandboxes/sbx-par10/*"))) if False else 0
    import os
    sbx_dir = Path(os.environ.get("SSSF_HOME", Path.home() / ".sssf")) / "sandboxes" / "sbx-par10"
    remaining_worktrees = len(list(sbx_dir.glob("*"))) if sbx_dir.exists() else 0
    infra_ok = (integrity == "ok" and len(sessions) == N
                and overlap_pairs == N * (N - 1) // 2
                and remaining_containers == 0 and remaining_worktrees == 0
                and len(branches) == N)
    print(f"leftover containers: {remaining_containers} · leftover worktrees: {remaining_worktrees}")
    print("RESULT:", "PASS (infra)" if infra_ok else "FAIL", f"— LLM verdicts: {success}/{N} success")
    return 0 if infra_ok else 1


if __name__ == "__main__":
    sys.exit(main())
