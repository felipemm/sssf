"""Sandbox identity: the container inherits the operator's git identity so
`git commit` and the engineer label work inside the sandbox, plus session
reopen for restarts.

Mirrors src/sssf/sandbox/session_env.py.
"""

from __future__ import annotations

import sqlite3
import subprocess

from sssf.sandbox.rundb import project_db_path
from sssf.sandbox.session_env import reopen_session, sandbox_env


def test_sandbox_env_carries_full_git_identity(tmp_path, monkeypatch):
    """Author AND committer env vars + ENGINEER_NAME flow from the operator's
    git config into the container env. Without the committer pair, `git
    commit` fails with 'Committer identity unknown'; without ENGINEER_NAME the
    engineer label degrades."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SNYK_TOKEN", raising=False)  # operator token must not leak into this test
    (tmp_path / ".gitconfig").write_text(
        "[user]\n\tname = Ada Lovelace\n\temail = ada@example.com\n"
    )
    _, _, env = sandbox_env(tmp_path)
    assert env["GIT_AUTHOR_NAME"] == "Ada Lovelace"
    assert "SNYK_TOKEN" not in env  # not set on the operator machine — not invented
    assert env["GIT_AUTHOR_EMAIL"] == "ada@example.com"
    assert env["GIT_COMMITTER_NAME"] == "Ada Lovelace"
    assert env["GIT_COMMITTER_EMAIL"] == "ada@example.com"
    assert env["ENGINEER_NAME"] == "Ada Lovelace"




def test_sandbox_env_reads_repo_local_identity(tmp_path, monkeypatch):
    """Identity may live in the project's local git config, not ~/.gitconfig —
    resolve from the project root so both work."""
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    (tmp_path / "nohome").mkdir()
    sub = subprocess_quiet(["git", "init", "-q", str(tmp_path / "proj")])
    assert sub == 0
    set_ok = subprocess_quiet(
        ["git", "-C", str(tmp_path / "proj"), "config", "user.name", "Repo Local"]
    )
    assert set_ok == 0
    set_ok = subprocess_quiet(
        ["git", "-C", str(tmp_path / "proj"), "config", "user.email", "local@example.com"]
    )
    assert set_ok == 0
    _, _, env = sandbox_env(tmp_path / "proj")
    assert env["GIT_COMMITTER_NAME"] == "Repo Local"
    assert env["GIT_COMMITTER_EMAIL"] == "local@example.com"
    assert env["ENGINEER_NAME"] == "Repo Local"




def test_sandbox_env_never_forwards_snyk_token(tmp_path, monkeypatch):
    """snyk auth in the sandbox is OAuth-only: SNYK_TOKEN is NEVER forwarded,
    even a production-shaped one. The token would outrank the mounted OAuth
    session (snyk env precedence) and stale/UAT tokens 401 against prod
    (SNYK-0005); a token-less container cannot be shadowed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gitconfig").write_text(
        "[user]\n\tname = Ada Lovelace\n\temail = ada@example.com\n"
    )
    for token in ("tok123", "snyk_uat.1fcad39e.stale", "11111111-2222-3333-4444-555555555555"):
        monkeypatch.setenv("SNYK_TOKEN", token)
        _, _, env = sandbox_env(tmp_path)
        assert "SNYK_TOKEN" not in env




def test_sandbox_env_without_identity_sets_nothing(tmp_path, monkeypatch):
    """No git identity anywhere → no identity env vars (git's auto-detect then
    fails loudly rather than silently attributing the commit)."""
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    (tmp_path / "nohome").mkdir()
    _, _, env = sandbox_env(tmp_path)
    assert "GIT_AUTHOR_NAME" not in env
    assert "GIT_COMMITTER_NAME" not in env
    assert "ENGINEER_NAME" not in env




def subprocess_quiet(argv: list[str]) -> int:

    return subprocess.run(argv, capture_output=True, text=True, check=False).returncode


def test_sandbox_env_forwards_openai_vars(tmp_path, monkeypatch):
    """The standard OpenAI env vars reach the container — litellm/pi read
    OPENAI_API_KEY + OPENAI_BASE_URL natively for OpenAI-compatible
    endpoints (e.g. GenPlat)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gitconfig").write_text(
        "[user]\n\tname = Ada Lovelace\n\temail = ada@example.com\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test123")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://genplat.example.com/v1")
    _, _, env = sandbox_env(tmp_path)
    assert env["OPENAI_API_KEY"] == "sk-test123"
    assert env["OPENAI_BASE_URL"] == "https://genplat.example.com/v1"



def test_sandbox_env_rereads_environment_each_call(tmp_path, monkeypatch):
    """No env is ever cached: a re-run (fresh spawn) sees the host env as it
    is NOW. Mutate the env between calls and the second spawn reflects it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gitconfig").write_text("[user]\n\tname = A\n\temail = a@b\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, _, first = sandbox_env(tmp_path)
    assert "OPENAI_API_KEY" not in first
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fresh")
    _, _, second = sandbox_env(tmp_path)
    assert second["OPENAI_API_KEY"] == "sk-fresh"

def test_reopen_session_flips_terminal_row_to_running(tmp_path):
    """A restart re-opens the host row of a terminal session (status running,
    ended_at cleared). Without it the UI keeps the previous run's fail state
    and the restarted run's own outcome is never recorded either — the
    monitor's forward-merge only updates rows whose ended_at IS NULL. The
    previous run's phases/events are cleared so the restarted run (which reuses
    the same phase_ids) is authoritative in the trace."""


    data = tmp_path / "adws" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(project_db_path(data)))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT,"
        " started_at TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT,"
        " status TEXT, error TEXT, ended_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, adw_id TEXT,"
        " phase_id TEXT, type TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('r9','fail','2026-09-02T20:48:16',"
        " '2026-09-02T21:30:23')"
    )
    conn.execute(
        "INSERT INTO phases VALUES ('r9_04_build','r9','fail',"
        " 'finalized by the healer: restart budget exhausted','2026-09-02T21:30:23')"
    )
    conn.execute("INSERT INTO events VALUES ('e1','r9','r9_01_request','phase_start')")
    conn.commit()
    conn.close()

    reopen_session(data, "r9")
    conn = sqlite3.connect(str(project_db_path(data)))
    status, started, ended = conn.execute(
        "SELECT status, started_at, ended_at FROM sessions WHERE adw_id='r9'"
    ).fetchone()
    phases = conn.execute("SELECT COUNT(*) FROM phases WHERE adw_id='r9'").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM events WHERE adw_id='r9'").fetchone()[0]
    conn.close()
    assert status == "running"
    assert ended is None
    assert started is not None and started > "2026-09-02T21:30:23"
    assert phases == 0 and events == 0  # the new run is authoritative


