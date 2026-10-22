"""`sssf init` — stamp the hidden customization surface and register it.

The v2 footprint lives under ``.adws/``. When upgrading a project with the old
visible ``adws/`` tree, init backs up that tree, preserves and migrates only
its SQLite database, then stamps a fresh hidden footprint.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from importlib import resources
from pathlib import Path

from sssf import __version__, registry, ticketing
from sssf.adw_modules import paths

AGENTS_BLOCK = """
<!-- sssf -->
This repo runs the **sssf** software factory (global CLI). Run `sssf` commands
to operate it: `sssf flow plan|implement|deploy` (the only entry point for
    work), `sssf viz`.
Edit your chains in `.adws/modules/adw_*.py` and your roster in
`.adws/config/sssf.config.yaml`. See `sssf --help`.
<!-- /sssf -->
"""

AGENTS_INSTRUCTIONS = """
## Instructions

- **When reporting information, be extremely concise and sacrifice grammar for the sake of concision.**
"""

GITIGNORE_ENTRIES = [
    ".adws/data/sessions/",
    ".adws/data/sssf.db",
    # The WAL sidecars are runtime state too — if they ever get tracked, agents
    # see them as clutter and may git-checkout them over a live db, which breaks
    # open reader connections (SQLITE_IOERR_VNODE). Keep them untracked.
    ".adws/data/sssf.db-wal",
    ".adws/data/sssf.db-shm",
    "sssf-*/",
]

_BACKUP_PREFIX = "sssf-"


def _copy_tree(
    src,
    dest: Path,
    *,
    force: bool = False,
    confirm: bool = False,
    auto: bool = False,
    label: str = "",
) -> list[str]:
    """Copy a template tree into dest, skipping existing files unless forced."""
    copied = []
    state = {"all": False}

    def ask(rel: Path) -> bool:
        if state["all"]:
            return True
        prefix = f"{label}/" if label else ""
        try:
            answer = input(f"overwrite {prefix}{rel}? [y/N/a] ").strip().lower()
        except EOFError:
            return False
        if answer in ("a", "all"):
            state["all"] = True
            return True
        return answer in ("y", "yes")

    def walk(trav, rel: Path) -> None:
        for item in trav.iterdir():
            if item.name == "__pycache__" or item.suffix == ".pyc":
                continue
            if item.is_dir():
                walk(item, rel / item.name)
                continue
            target = dest / rel / item.name
            if target.exists() and not force and not (confirm and (auto or ask(rel / item.name))):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.read_text())
            copied.append(str(rel / item.name))

    walk(src, Path())
    return copied


def _backup_legacy_adws(root: Path) -> Path | None:
    legacy = root / paths.LEGACY_ADWS
    if not legacy.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = root / f"{_BACKUP_PREFIX}{stamp}"
    suffix = 1
    while dest.exists():
        dest = root / f"{_BACKUP_PREFIX}{stamp}-{suffix}"
        suffix += 1
    shutil.copytree(legacy, dest)
    return dest


def _database_source(root: Path) -> Path | None:
    candidates = (
        paths.data_dir(root) / "sssf.db",
        root / paths.LEGACY_ADWS / "data" / "sssf.db",
        root / paths.LEGACY_ADWS / "adw_data" / "sssf.db",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _migrate_database(root: Path, source: Path | None) -> Path | None:
    """Make an online SQLite copy and apply pending schema migrations to it."""
    if source is None:
        return None
    fd, temp_name = tempfile.mkstemp(prefix=".sssf-db-migration-", dir=root)
    os.close(fd)
    temp_db = Path(temp_name)
    try:
        with closing(sqlite3.connect(source)) as source_conn, closing(
            sqlite3.connect(temp_db)
        ) as target_conn:
            source_conn.backup(target_conn)
            ticketing.ensure_schema(target_conn)
            target_conn.commit()
    except Exception:
        temp_db.unlink(missing_ok=True)
        raise
    return temp_db


def _reset_legacy_layout(root: Path) -> bool:
    legacy = root / paths.LEGACY_ADWS
    if not paths.is_legacy_layout(root):
        return False

    print(
        "sssf init: upgrading the legacy adws/ footprint. Its chains, prompts, and "
        "config will be backed up, not migrated; only the database is carried into "
        "a fresh .adws/ tree.",
        file=sys.stderr,
    )
    backup = _backup_legacy_adws(root)
    if backup is not None:
        print(f"sssf init: backed up adws/ -> {backup.relative_to(root)}", file=sys.stderr)

    db_source = _database_source(root)
    try:
        migrated_db = _migrate_database(root, db_source)
    except Exception as error:
        print(f"sssf init: could not migrate project database: {error}", file=sys.stderr)
        return False

    shutil.rmtree(legacy)
    hidden = root / paths.ADWS
    if hidden.exists():
        shutil.rmtree(hidden)
    templates = resources.files("sssf") / "templates" / "adws"
    _copy_tree(templates, hidden, force=True, label=".adws")
    if migrated_db is not None:
        destination = paths.data_dir(root) / "sssf.db"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(migrated_db), destination)
    return True


def run(
    root: Path,
    *,
    refresh: bool = False,
    force: bool = False,
    auto: bool = False,
    design_quality: str | None = None,
) -> int:
    templates = resources.files("sssf") / "templates"
    root.mkdir(parents=True, exist_ok=True)

    upgraded = _reset_legacy_layout(root)
    if paths.is_legacy_layout(root) and not upgraded:
        return 1

    # The package template tree is named adws; the stamped project footprint is hidden.
    _copy_tree(
        templates / "adws",
        root / paths.ADWS,
        force=force or upgraded,
        confirm=refresh,
        auto=auto,
        label=".adws",
    )

    env_dest = root / ".env.sample"
    if not env_dest.exists() or force:
        env_dest.write_text((templates / "env.sample").read_text())

    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        text = agents_md.read_text()
        start = text.find("<!-- sssf -->")
        end_marker = "<!-- /sssf -->"
        end = text.find(end_marker, start) if start >= 0 else -1
        if start >= 0 and end >= 0:
            end += len(end_marker)
            text = text[:start] + AGENTS_BLOCK.strip("\n") + text[end:]
            agents_md.write_text(text)
        elif start < 0:
            agents_md.write_text(text.rstrip() + "\n" + AGENTS_BLOCK)
    else:
        agents_md.write_text("# Project\n" + AGENTS_BLOCK)
    text = agents_md.read_text()
    if "sacrifice grammar for the sake of concision" not in text:
        agents_md.write_text(text.rstrip() + "\n" + AGENTS_INSTRUCTIONS)

    gitignore = root / ".gitignore"
    if gitignore.exists():
        text = gitignore.read_text()
        missing = [line for line in GITIGNORE_ENTRIES if line not in text]
        if missing:
            gitignore.write_text(text.rstrip() + "\n" + "\n".join(missing) + "\n")
    else:
        gitignore.write_text("\n".join(GITIGNORE_ENTRIES) + "\n")

    registry.register_project(root, paths.data_dir(root) / "sssf.db", __version__, added=True)

    if design_quality is not None:
        from sssf.commands import design_quality as dq

        rc = dq.configure(root, design_quality)
        if rc != 0:
            return rc

    try:
        from sssf.adw_modules import skills_install

        skills_install.install_skills(root, refresh=refresh)
    except Exception as error:
        print(f"sssf: skills install skipped ({error}) — run `sssf doctor`", file=sys.stderr)
    return 0
