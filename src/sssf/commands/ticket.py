"""`sssf ticket` — ticketing integration (add / sync / list / run)."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sssf import ticketing
from sssf.project import find_project


def _root(explicit: str | None) -> Path | None:
    return find_project(Path.cwd(), explicit)


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "adw_data" / "sssf.db")
    conn.execute(ticketing.TICKETS_DDL)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def add(title: str, project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print("sssf ticket: the internal provider is not enabled in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    ticket_id = f"internal:{uuid.uuid4().hex[:12]}"
    now = _now()
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status,"
        " source_url, created_at, updated_at) VALUES (?,?,'',?,'','backlog','',?,?)",
        (ticket_id, "internal", title, now, now))
    conn.commit()
    conn.close()
    print(f"sssf ticket: added internal ticket {title!r} ({ticket_id})")
    return 0


def sync(project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    results = ticketing.sync_tickets(root, cfg)
    for r in results:
        if r.error:
            print(f"sssf ticket: {r.provider}: {r.error}")
        else:
            print(f"sssf ticket: {r.provider}: {r.tickets} ticket(s) synced")
    return 0


def list_tickets(project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    conn = _db(root)
    rows = conn.execute(
        "SELECT id, provider, title, status, adw_id FROM tickets ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    for row in rows:
        print(f"{row[0]:20} {row[1]:8} {row[2][:50]:50} {row[3]:8} {row[4] or ''}")
    print(f"sssf ticket: {len(rows)} ticket(s)")
    return 0


def run(ticket_id: str, project: str | None = None, no_sandbox: bool = False) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute(
        "SELECT id, title, description, status, provider, external_id, source_url"
        " FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    tid, title, description, status, provider, external_id, source_url = row
    if status == "running":
        conn.close()
        print(f"sssf ticket: {ticket_id} is already running", file=sys.stderr)
        return 1
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40] or "ticket"
    adw_id = uuid.uuid4().hex[:8]
    adw_file = root / "adws" / "adw_simple_sdlc.py"
    if not adw_file.exists():
        conn.close()
        print(f"sssf ticket: no adws/adw_simple_sdlc.py in {root}", file=sys.stderr)
        return 1

    sandboxed = not no_sandbox and _sandbox_enabled(root)
    if sandboxed:
        # The prompt lives in the WORKTREE (per-run dir → no NN race) and is
        # committed with the run; the container runs from the worktree.
        from sssf.sandbox import (SandboxError, docker_available, sandbox_env,
                                  spawn_monitor, spawn_sandbox)
        if not docker_available():
            conn.close()
            print("sssf ticket: docker is not available — run `sssf sandbox build`? "
                  "or --no-sandbox", file=sys.stderr)
            return 1
        from sssf.sandbox import create_worktree
        wt = create_worktree(root, adw_id)
        prompt_path = ticketing.next_prompt_name(wt, slug)
        prompt_path.write_text(
            f"# {title}\n\n{description}\n\n---\n"
            f"Generated from {provider} ticket {external_id or ''} ({source_url})\n")
        cfg = _config_for_sandbox(root)
        data_dir, pi_home, env = sandbox_env(root)
        try:
            spawn_sandbox(
                root, adw_id,
                cmd=["python", "adws/adw_simple_sdlc.py",
                     f"run prompt adws/prompts/{prompt_path.name}", "--adw-id", adw_id],
                image=cfg.sandbox.image,
                data_dir=data_dir, pi_home=pi_home, env=env,
                worktree=wt,   # already created above — the prompt lives here
            )
        except SandboxError as e:
            conn.close()
            from sssf.sandbox import abort_sandbox
            abort_sandbox(root, adw_id)   # remove the stuck container + worktree
            print(f"sssf ticket: sandbox spawn failed: {e}", file=sys.stderr)
            return 1
        spawn_monitor(root, adw_id)
        rel_prompt = Path("adws") / "prompts" / prompt_path.name
    else:
        prompt_path = ticketing.next_prompt_name(root, slug)
        prompt_path.write_text(
            f"# {title}\n\n{description}\n\n---\n"
            f"Generated from {provider} ticket {external_id or ''} ({source_url})\n")
        rel_prompt = prompt_path.relative_to(root)
        subprocess.Popen(
            [sys.executable, str(adw_file), f"run prompt {rel_prompt}", "--adw-id", adw_id],
            cwd=root, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    conn.execute("UPDATE tickets SET status='starting', adw_id=?, prompt_file=? WHERE id=?",
                 (adw_id, str(rel_prompt), tid))
    conn.commit()
    conn.close()
    print(f"sssf ticket: run spawned for {ticket_id} — adw_id {adw_id}, prompt {rel_prompt}"
          + (" (sandboxed)" if sandboxed else ""))
    return 0


def _sandbox_enabled(root: Path) -> bool:
    try:
        return _config_for_sandbox(root).sandbox.enabled
    except Exception:
        return False


def _config_for_sandbox(root: Path):
    from sssf.adw_modules.agents import load_config
    return load_config(str(root / "adws" / "adw_sssf_config" / "sssf.config.yaml"))
