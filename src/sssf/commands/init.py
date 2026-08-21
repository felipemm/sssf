"""`sssf init` — stamp the customization surface into a project and register it.

v2 layout: adws/modules, adws/config, adws/data, adws/prompts, adws/specs,
adws/kb. A project still on the v1 layout (adw_sssf_config/adw_data/app_docs,
chains at adws root) must run `sssf init --refresh` to migrate: it warns,
backs up adws/, and moves the v1 items to v2 in place.
"""

from __future__ import annotations

import shutil
import sys
import time
from importlib import resources
from pathlib import Path

from sssf import __version__, registry
from sssf.adw_modules import paths

AGENTS_BLOCK = """
<!-- sssf -->
This repo runs the **sssf** software factory (global CLI). Run `sssf` commands
to operate it: `sssf run <adw> "<prompt>"`, `sssf sessions`, `sssf viz`.
Edit your chains in `adws/modules/adw_*.py` and your roster in
`adws/config/sssf.config.yaml`. See `sssf --help`.
<!-- /sssf -->
"""

GITIGNORE_ENTRIES = [
    "adws/data/sessions/",
    "adws/data/sssf.db",
    # The WAL sidecars are runtime state too — if they ever get tracked, agents
    # see them as clutter and may git-checkout them over a live db, which breaks
    # open reader connections (SQLITE_IOERR_VNODE). Keep them untracked.
    "adws/data/sssf.db-wal",
    "adws/data/sssf.db-shm",
]

_BACKUP_PREFIX = "adws.backup."
# (legacy relpath under adws/, v2 relpath under adws/)
_LEGACY_MOVES = (
    ("adw_sssf_config", "config"),
    ("adw_data", "data"),
    ("app_docs", "kb"),
)
_LITERAL_REWRITES = (  # applied to moved chain files
    ("adws/adw_sssf_config/", "adws/config/"),
    ("adws/adw_data", "adws/data"),
    ("adws/app_docs", "adws/kb"),
)


def _copy_tree(
    src,
    dest: Path,
    *,
    force: bool = False,
    confirm: bool = False,
    auto: bool = False,
    label: str = "",
) -> list[str]:
    """Copy a template tree into dest.

    Existing files are skipped unless forced; with ``confirm`` the user is asked
    per file first (y/N/a — a = yes to all), default no, so a --refresh can
    never silently clobber an edited chain. ``auto`` answers yes to every
    prompt without reading stdin (--refresh --auto: the accept-all scripting
    mode the cockpit refresh button uses).
    """
    copied = []
    state = {"all": False}

    def ask(rel: Path) -> bool:
        if state["all"]:
            return True
        prefix = f"{label}/" if label else ""
        try:
            answer = input(f"overwrite {prefix}{rel}? [y/N/a] ").strip().lower()
        except EOFError:
            return False  # non-interactive: skip, never clobber
        if answer in ("a", "all"):
            state["all"] = True
            return True
        return answer in ("y", "yes")

    def walk(trav, rel: Path) -> None:
        for item in trav.iterdir():
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


def _backup_adws(root: Path) -> Path | None:
    adws = root / "adws"
    if not adws.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = root / f"{_BACKUP_PREFIX}{stamp}"
    shutil.copytree(adws, dest)
    return dest


def _migrate_legacy(root: Path) -> None:
    """Move v1 items to v2 in place; never overwrite an existing v2 target."""
    adws = root / "adws"
    for legacy, v2 in _LEGACY_MOVES:
        src = adws / legacy
        dst = adws / v2
        if src.exists() and not dst.exists():
            src.rename(dst)
    # root-level chains → modules/
    modules = paths.modules_dir(root)
    modules.mkdir(parents=True, exist_ok=True)
    for chain in adws.glob("adw_*.py"):
        target = modules / chain.name
        if not target.exists():
            chain.rename(target)
    # rewrite layout literals inside moved chains AND the moved config files
    for chain in modules.glob("adw_*.py"):
        text = chain.read_text()
        for old, new in _LITERAL_REWRITES:
            text = text.replace(old, new)
        chain.write_text(text)
    for cfg_file in [adws / "config" / "sssf.config.yaml", adws / "config" / "ticketing.yaml"]:
        if cfg_file.exists():
            text = cfg_file.read_text()
            for old, new in _LITERAL_REWRITES:
                text = text.replace(old, new)
            cfg_file.write_text(text)
    # gitignore the backup
    gitignore = root / ".gitignore"
    entry = f"{_BACKUP_PREFIX}*/"
    if gitignore.exists():
        text = gitignore.read_text()
        if entry not in text:
            gitignore.write_text(text.rstrip() + "\n" + entry + "\n")
    else:
        gitignore.write_text(entry + "\n")


def run(root: Path, *, refresh: bool = False, force: bool = False, auto: bool = False) -> int:
    templates = resources.files("sssf.templates")
    root.mkdir(parents=True, exist_ok=True)

    if refresh and paths.is_legacy_layout(root):
        print(
            f"sssf: legacy adws layout detected in {root} — migrating to v2 "
            "(backup of adws/ first, then move).",
            file=sys.stderr,
        )
        backup = _backup_adws(root)
        if backup is not None:
            print(f"sssf: backed up adws/ -> {backup.relative_to(root)}", file=sys.stderr)
        _migrate_legacy(root)

    # Stamp the whole v2 tree (templates/adws mirrors the stamped layout)
    _copy_tree(
        templates / "adws", root / "adws", force=force, confirm=refresh, auto=auto, label="adws"
    )

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

    registry.register_project(root, paths.data_dir(root) / "sssf.db", __version__, added=True)

    # Interview skills — project-local (.pi/skills/), never global. A fetch
    # failure doesn't fail init; `sssf doctor` reports it.
    try:
        from sssf.adw_modules import skills_install
        skills_install.install_skills(root, refresh=refresh)
    except Exception as error:
        print(f"sssf: skills install skipped ({error}) — run `sssf doctor`", file=sys.stderr)
    return 0
