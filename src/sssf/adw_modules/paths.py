"""The v2 stamped adws/ layout — the single source of truth for its paths.

Strict, no runtime fallback: the engine resolves these paths only. A project
that still lives at the v1 layout must run `sssf init --refresh` to migrate
(which warns, backs up adws/, and moves it). Legacy detection exists solely
to warn and to drive that migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

ADWS = "adws"


def modules_dir(root: Path) -> Path:
    return root / ADWS / "modules"


def config_dir(root: Path) -> Path:
    return root / ADWS / "config"


def config_file(root: Path) -> Path:
    return root / ADWS / "config" / "sssf.config.yaml"


def ticketing_file(root: Path) -> Path:
    return root / ADWS / "config" / "ticketing.yaml"


def data_dir(root: Path) -> Path:
    return root / ADWS / "data"


def kb_dir(root: Path) -> Path:
    return root / ADWS / "kb"


def prompts_dir(root: Path) -> Path:
    return root / ADWS / "prompts"


def specs_dir(root: Path) -> Path:
    return root / ADWS / "specs"


# The v1 markers — any one present means the project predates v2.
_LEGACY_MARKERS = (
    "adws/adw_sssf_config",
    "adws/adw_data",
    "adws/app_docs",
)


def is_legacy_layout(root: Path) -> bool:
    for marker in _LEGACY_MARKERS:
        if (root / marker).exists():
            return True
    # v1 chains sat directly under adws/ — e.g. adws/adw_simple_sdlc.py
    return bool(any((root / ADWS).glob("adw_*.py")))


def warn_if_legacy(root: Path, *, command: str) -> bool:
    if not is_legacy_layout(root):
        return False
    print(
        f"sssf: legacy adws layout detected in {root} — chains/config/data live at "
        "the v1 paths. Run `sssf init --refresh` to migrate (it backs up adws/ "
        "first, then moves to the v2 layout: modules/, config/, data/, prompts/, "
        "specs/, kb/).",
        file=sys.stderr,
    )
    return True
