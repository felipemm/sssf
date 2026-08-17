"""Project resolution: where the per-project stamp lives."""

from __future__ import annotations

from pathlib import Path


def find_project(cwd: Path, explicit: str | None = None) -> Path | None:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        return root if (root / "adws").is_dir() else None
    cur = cwd.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "adws").is_dir():
            return candidate
        if (candidate / ".git").exists():
            return None  # hit a repo root without adws/ — stop
    return None


def data_dir(root: Path) -> Path:
    from sssf.adw_modules import paths

    return paths.data_dir(root)
