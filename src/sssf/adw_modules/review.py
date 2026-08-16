"""The human review gate: run the changed app, wait for the engineer's
approve/reject (read from the shared db + a wake-up signal), report the decision."""
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

# The CLI wakes us with SIGUSR1 (approve) / SIGUSR2 (reject) — the db row is
# the durable record, the signal makes the phase end immediately (a long-open
# sqlite connection may not see the host's WAL commit, so we don't rely on the
# poll alone).
_SIGNAL_DECISION: dict[int, str] = {}


def _on_review_signal(signum: int, _frame) -> None:
    _SIGNAL_DECISION[signum] = "approved" if signum == signal.SIGUSR1 else "rejected"


def _install_review_signals() -> None:
    try:
        signal.signal(signal.SIGUSR1, _on_review_signal)
        signal.signal(signal.SIGUSR2, _on_review_signal)
    except (ValueError, OSError):
        pass   # non-main thread / platform without SIGUSR — the db poll still works


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
    mark pending, log the URL, wait for a decision.

    Returns True (approved), False (rejected), or None (gate skipped — the
    run's own verdict stands)."""
    review = cfg.review
    command = review.command or auto_review_command(run.repo_root)
    if not command:
        ph.log(input="no review command configured — skipping the human gate")
        return None  # no gate: the run's own verdict (tests + AI review) stands

    _install_review_signals()
    host_port = int(os.environ.get("REVIEW_HOST_PORT", review.port))
    proc = subprocess.Popen(command, cwd=str(run.repo_root), shell=True)

    try:
        if not _port_open(review.port):
            ph.log(input=f"dev server did not open port {review.port} — skipping gate")
            return None  # no gate: the run's own verdict stands
        run.tracer.review_pending(run.adw_id, host_port=host_port)
        run.tracer.session_pause(run.adw_id)   # the factory is done; the engineer decides
        ph.log(input=f"reviewing at http://localhost:{host_port}")
        while True:
            # The wake-up signal wins (immediate); the db row is the fallback
            # (a long-open sqlite connection may lag behind the host's writes).
            status = _SIGNAL_DECISION.popitem()[1] if _SIGNAL_DECISION else None
            if status is None:
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
