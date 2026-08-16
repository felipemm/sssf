"""The human review gate: run the changed app, wait for the engineer's
approve/reject (read from the shared db), report the decision."""
import os
import socket
import subprocess
import time
from pathlib import Path


def auto_review_command(root: Path) -> str | None:
    """Detect a dev command from project markers. Python-only projects get
    None — the review stage is skipped with a hint."""
    pkg = root / "package.json"
    if pkg.exists():
        try:
            import json
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except Exception:
            scripts = {}
        if scripts.get("dev"):
            return "bun run dev" if (root / "bun.lock").exists() else "npm run dev"
    return None


def _port_open(port: int, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def human_review(run, cfg, ph, prompt: str) -> bool:
    """Start review.command (or the auto-detected one), wait for the port,
    mark pending, log the URL, poll for a decision. Returns True on approve."""
    review = cfg.review
    command = review.command or auto_review_command(run.repo_root)
    if not command:
        ph.log(input="no review command configured — skipping the human gate")
        return True   # treat as approved; the run completes without the gate

    host_port = int(os.environ.get("REVIEW_HOST_PORT", review.port))
    proc = subprocess.Popen(command, cwd=str(run.repo_root), shell=True)

    try:
        if not _port_open(review.port):
            ph.log(input=f"dev server did not open port {review.port} — skipping gate")
            return True
        run.tracer.review_pending(run.adw_id, host_port=host_port)
        run.tracer.session_pause(run.adw_id)   # the factory is done; the engineer decides
        ph.log(input=f"reviewing at http://localhost:{host_port}")
        while True:
            status = run.tracer.review_status(run.adw_id)
            if status == "approved":
                ph.log(input="approved")
                return True
            if status == "rejected":
                ph.log(input="rejected")
                return False
            time.sleep(review.poll_seconds)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
