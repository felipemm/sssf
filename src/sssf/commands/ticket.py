"""`sssf ticket` — ticketing integration (add / sync / list / run)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sssf import ticketing
from sssf.adw_modules import paths
from sssf.project import find_project


def _root(explicit: str | None) -> Path | None:
    return find_project(Path.cwd(), explicit)


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    ticketing.ensure_schema(conn)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def add(title: str, project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print(
            "sssf ticket: the internal provider is not enabled in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    ticket_id = f"internal:{uuid.uuid4().hex[:12]}"
    now = _now()
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status,"
        " source_url, created_at, updated_at) VALUES (?,?,'',?,'','backlog','',?,?)",
        (ticket_id, "internal", title, now, now),
    )
    conn.commit()
    conn.close()
    print(f"sssf ticket: added internal ticket {title!r} ({ticket_id})")
    return 0


def sync(project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
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
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
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


def _prompt_text(
    title: str,
    description: str,
    context: str,
    provider: str,
    external_id: str,
    source_url: str,
) -> str:
    """The prompt body: title, description, optional operator context, provenance."""
    text = f"# {title}\n\n{description}\n"
    if context:
        text += f"\n## Run context\n\n{context}\n"
    return text + f"\n---\nGenerated from {provider} ticket {external_id or ''} ({source_url})\n"


def ticket_context(ticket_id: str, project: str | None = None, set_text: str | None = None) -> int:
    """Read (print) or set the persisted extra context of a ticket."""
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute("SELECT context FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    if set_text is None:
        print(row[0] or "")
        conn.close()
        return 0
    conn.execute(
        "UPDATE tickets SET context=?, updated_at=? WHERE id=?", (set_text, _now(), ticket_id)
    )
    conn.commit()
    conn.close()
    print(f"sssf ticket: context saved for {ticket_id}")
    return 0


def run(
    ticket_id: str, project: str | None = None, no_sandbox: bool = False, context: str = ""
) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="ticket")
    cfg = ticketing.load_config(root)
    if cfg is None:
        print(
            "sssf ticket: ticketing not configured — enable it in adws/config/ticketing.yaml",
            file=sys.stderr,
        )
        return 1
    conn = _db(root)
    row = conn.execute(
        "SELECT id, title, description, context, status, provider, external_id, source_url"
        " FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    tid, title, description, stored_context, status, provider, external_id, source_url = row
    if context:
        # --context wins for this run and is persisted for later ones.
        conn.execute(
            "UPDATE tickets SET context=?, updated_at=? WHERE id=?", (context, _now(), tid)
        )
        conn.commit()
    else:
        context = stored_context or ""
    if status == "running":
        conn.close()
        print(f"sssf ticket: {ticket_id} is already running", file=sys.stderr)
        return 1
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40] or "ticket"
    adw_id = uuid.uuid4().hex[:8]
    adw_file = paths.modules_dir(root) / "adw_simple_sdlc.py"
    if not adw_file.exists():
        conn.close()
        print(f"sssf ticket: no adws/modules/adw_simple_sdlc.py in {root}", file=sys.stderr)
        return 1

    sandboxed = not no_sandbox and _sandbox_enabled(root)
    if sandboxed:
        # The prompt lives in the WORKTREE (per-run dir → no NN race) and is
        # committed with the run; the container runs from the worktree.
        from sssf.sandbox import (
            SandboxError,
            docker_available,
            sandbox_env,
            spawn_monitor,
            spawn_sandbox,
        )

        if not docker_available():
            conn.close()
            print(
                "sssf ticket: docker is not available — run `sssf sandbox build`? or --no-sandbox",
                file=sys.stderr,
            )
            return 1
        from sssf.sandbox import create_worktree

        wt = create_worktree(root, adw_id)
        prompt_path = ticketing.next_prompt_name(wt, slug)
        prompt_path.write_text(
            _prompt_text(title, description, context, provider, external_id, source_url)
        )
        cfg = _config_for_sandbox(root)
        data_dir, pi_home, env = sandbox_env(root)
        try:
            spawn_sandbox(
                root,
                adw_id,
                cmd=[
                    "python",
                    "adws/modules/adw_simple_sdlc.py",
                    f"run prompt adws/prompts/{prompt_path.name}",
                    "--adw-id",
                    adw_id,
                ],
                image=cfg.sandbox.image,
                data_dir=data_dir,
                pi_home=pi_home,
                env=env,
                worktree=wt,  # already created above — the prompt lives here
            )
        except SandboxError as e:
            conn.close()
            from sssf.sandbox import abort_sandbox

            abort_sandbox(root, adw_id)  # remove the stuck container + worktree
            print(f"sssf ticket: sandbox spawn failed: {e}", file=sys.stderr)
            return 1
        spawn_monitor(root, adw_id)
        rel_prompt = Path("adws") / "prompts" / prompt_path.name
    else:
        prompt_path = ticketing.next_prompt_name(root, slug)
        prompt_path.write_text(
            _prompt_text(title, description, context, provider, external_id, source_url)
        )
        rel_prompt = prompt_path.relative_to(root)
        subprocess.Popen(
            [sys.executable, str(adw_file), f"run prompt {rel_prompt}", "--adw-id", adw_id],
            cwd=root,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    conn.execute(
        "UPDATE tickets SET status='starting', adw_id=?, prompt_file=?, updated_at=? WHERE id=?",
        (adw_id, str(rel_prompt), _now(), tid),
    )
    # The run's history: every spawn is a row, so a retried ticket accumulates
    # its attempts (the failed run stays linked for the trace and the retry
    # color). tickets.adw_id remains the LATEST run.
    conn.execute(
        "INSERT OR IGNORE INTO ticket_runs (ticket_id, adw_id, created_at) VALUES (?,?,?)",
        (tid, adw_id, _now()),
    )
    conn.commit()
    conn.close()
    print(
        f"sssf ticket: run spawned for {ticket_id} — adw_id {adw_id}, prompt {rel_prompt}"
        + (" (sandboxed)" if sandboxed else "")
    )
    return 0


def backlog(ticket_id: str, project: str | None = None) -> int:
    """Return a ticket to the backlog — the manual retry control.

    The adw_id link and ticket_runs history are PRESERVED: a retried ticket
    keeps its failed runs visible in the trace and in the ticket modal. The
    only refusal is a still-running session — no yanking a live run.
    """
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute("SELECT status, adw_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    _stored, adw_id = row
    if adw_id:
        try:
            session = conn.execute(
                "SELECT status FROM sessions WHERE adw_id=?", (adw_id,)
            ).fetchone()
            if session and session[0] == "running":
                conn.close()
                print(
                    f"sssf ticket: {ticket_id} is still running — wait for it to"
                    " finish before putting it back",
                    file=sys.stderr,
                )
                return 1
        except sqlite3.Error:
            pass  # no sessions table yet — nothing running
    conn.execute(
        "UPDATE tickets SET status='backlog', updated_at=? WHERE id=?", (_now(), ticket_id)
    )
    conn.commit()
    conn.close()
    print(f"sssf ticket: {ticket_id} back to backlog (adw_id kept — history preserved)")
    return 0


def _sandbox_enabled(root: Path) -> bool:
    from sssf import sandbox

    return sandbox.enabled(root, command="ticket")


def _config_for_sandbox(root: Path):
    from sssf.adw_modules import paths
    from sssf.adw_modules.agents import load_config

    return load_config(str(paths.config_file(root)))
