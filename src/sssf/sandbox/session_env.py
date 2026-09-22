"""Sandbox env + session reopen: the env passed to the container
(credentials + git identity only, never project files) and reopening a
terminal session row for a restart.
"""

import os
import subprocess
from pathlib import Path

from sssf.sandbox.rundb import project_db_path


def _git_identity(project_root: Path) -> tuple[str, str]:
    """The operator's git identity, resolved from the project root so both
    repo-local and global config work. Empty strings when unset — the
    container then has no identity and git fails loudly instead of silently
    attributing the commit."""

    def get(key: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(project_root), "config", key],
            capture_output=True,
            text=True,
            check=False,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    return get("user.name"), get("user.email")


def sandbox_env(project_root: Path) -> tuple[Path, Path, dict[str, str]]:
    """The per-run data dir (shared, bind-mounted rw), the pi home (read-only
    mount), and the env passed to the container: credentials + git identity
    only — never project files."""
    from sssf.adw_modules import paths

    data_dir = paths.data_dir(project_root)
    pi_home = Path(os.environ.get("PI_HOME", Path.home() / ".pi" / "agent"))
    env: dict[str, str] = {}
    if os.environ.get("OPENAI_API_KEY"):
        # The standard OpenAI env vars — litellm/pi read these natively for
        # OpenAI-compatible endpoints (e.g. GenPlat). No GENPLAT_TOKEN: it is
        # not a standard var and no tooling in the container reads it.
        env["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL"):
        env["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
    # SNYK_TOKEN is deliberately NEVER forwarded: auth is OAuth-only. snyk in
    # the sandbox authenticates via the operator's session — spawn_sandbox
    # mounts ~/.config (where snyk keeps configstore/snyk.json) read-only at
    # /tmp/.config with HOME=/tmp in the image. An exported SNYK_TOKEN would
    # outrank that session (snyk env precedence) and stale/UAT tokens 401
    # against prod (SNYK-0005); a token-less container cannot be shadowed.
    name, email = _git_identity(project_root)
    if name and email:
        # Author AND committer — git needs both pairs inside the container, or
        # `git commit` dies with "Committer identity unknown". ENGINEER_NAME is
        # how engineer_name() resolves the run's engineer label in the sandbox.
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
                "ENGINEER_NAME": name,
            }
        )
    return data_dir, pi_home, env


def reopen_session(data_dir: Path, adw_id: str) -> None:
    """A restart (attach) re-opens the project row of a TERMINAL session so the
    UI reflects the new run. Without this the host row keeps the first run's
    terminal state forever: the monitor's forward-merge only updates un-ended
    rows (ended_at IS NULL), so a restarted run — however long it lives or
    however it ends — never flips the row back to running and never records its
    own outcome (session 9701903a: status stayed 'fail / ended 21:30' while a
    restarted run was live).

    The previous run's events and phase rows are cleared too: the restarted run
    reuses the SAME phase_ids, and the phases merge is forward-only (an ended
    host phase row is never overwritten), so without the reset the trace's
    waterfall would keep the old run's statuses (04_build fail 'finalized by
    the healer') on top of the new run's events. Events are already replaced
    wholesale by every sync — the trace is "the current run" by design.
    Best-effort: the run proceeds even if a write fails."""
    import datetime
    import sqlite3

    db_path = project_db_path(data_dir)
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        # events first — events.phase_id references phases
        for table in ("events", "phases"):
            conn.execute(f"DELETE FROM {table} WHERE adw_id=?", (adw_id,))
        conn.execute(
            "UPDATE sessions SET status='running', started_at=?, ended_at=NULL WHERE adw_id=?",
            (datetime.datetime.now(datetime.UTC).isoformat(), adw_id),
        )
        conn.close()
    except sqlite3.Error:
        pass
