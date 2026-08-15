"""`sssf init` — stamp the customization surface into a project and register it."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from sssf import __version__
from sssf import registry

AGENTS_BLOCK = """
<!-- sssf -->
This repo runs the **sssf** software factory (global CLI). Run `sssf` commands
to operate it: `sssf run <adw> "<prompt>"`, `sssf sessions`, `sssf viz`.
Edit your chains in `adws/adw_*.py` and your roster in
`adws/adw_sssf_config/sssf.config.yaml`. See `sssf --help`.
<!-- /sssf -->
"""

GITIGNORE_ENTRIES = [
    "adws/adw_data/sessions/",
    "adws/adw_data/sssf.db",
    # The WAL sidecars are runtime state too — if they ever get tracked, agents
    # see them as clutter and may git-checkout them over a live db, which breaks
    # open reader connections (SQLITE_IOERR_VNODE). Keep them untracked.
    "adws/adw_data/sssf.db-wal",
    "adws/adw_data/sssf.db-shm",
]


def _copy_tree(src, dest: Path, *, force: bool) -> list[str]:
    """Copy a template tree (Traversable or Path) into dest, skipping existing files unless forced."""
    copied = []

    def walk(trav, rel: Path) -> None:
        for item in trav.iterdir():
            if item.is_dir():
                walk(item, rel / item.name)
                continue
            target = dest / rel / item.name
            if target.exists() and not force:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.read_text())
            copied.append(str(rel / item.name))

    walk(src, Path())
    return copied


def run(root: Path, *, refresh: bool = False, force: bool = False) -> int:
    templates = resources.files("sssf.templates")
    root.mkdir(parents=True, exist_ok=True)

    _copy_tree(templates / "adws", root / "adws", force=force or refresh)
    config_dest = root / "adws" / "adw_sssf_config" / "sssf.config.yaml"
    if not config_dest.exists() or force:
        config_dest.parent.mkdir(parents=True, exist_ok=True)
        config_dest.write_text((templates / "sssf.config.yaml").read_text())
    ticket_dest = root / "adws" / "adw_sssf_config" / "ticketing.yaml"
    if not ticket_dest.exists() or force:
        ticket_dest.parent.mkdir(parents=True, exist_ok=True)
        ticket_dest.write_text((templates / "ticketing.yaml").read_text())
    _copy_tree(templates / "prompt_engineering", root / "adws" / "adw_data" / "prompt_engineering",
               force=force)
    _copy_tree(templates / "harness_engineering", root / "adws" / "adw_data" / "harness_engineering",
               force=force)

    env_dest = root / ".env.sample"
    if not env_dest.exists() or force:
        env_dest.write_text((templates / "env.sample").read_text())

    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        text = agents_md.read_text()
        if "<!-- sssf -->" not in text:
            agents_md.write_text(text.rstrip() + "\n" + AGENTS_BLOCK)
    else:
        agents_md.write_text("# Project\n" + AGENTS_BLOCK)

    gitignore = root / ".gitignore"
    if gitignore.exists():
        text = gitignore.read_text()
        missing = [line for line in GITIGNORE_ENTRIES if line not in text]
        if missing:
            gitignore.write_text(text.rstrip() + "\n" + "\n".join(missing) + "\n")
    else:
        gitignore.write_text("\n".join(GITIGNORE_ENTRIES) + "\n")

    registry.register_project(root, root / "adws" / "adw_data" / "sssf.db",
                              __version__, added=True)
    return 0
