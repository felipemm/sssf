"""The v2 stamped .adws/ layout — the single source of truth for its paths.

Legacy projects have an ``adws/`` tree but no complete ``.adws/`` footprint.
They must run ``sssf init`` before a flow can use the new paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

ADWS = ".adws"
LEGACY_ADWS = "adws"


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


def has_current_layout(root: Path) -> bool:
    """Whether the project has the footprint required by flow commands."""
    return modules_dir(root).is_dir() and config_file(root).is_file()


def is_legacy_layout(root: Path) -> bool:
    """True when the old visible footprint exists without a complete v2 stamp."""
    return (root / LEGACY_ADWS).is_dir() and not has_current_layout(root)


def require_current_layout(root: Path) -> bool:
    """Reject flows until init has stamped the current hidden footprint."""
    if has_current_layout(root):
        return True
    print(
        f"sssf flow: project at {root} does not have the .adws/ layout — "
        "run `sssf init` to upgrade",
        file=sys.stderr,
    )
    return False


def warn_if_legacy(root: Path, *, command: str) -> bool:
    if not is_legacy_layout(root):
        return False
    print(
        f"sssf: legacy adws layout detected in {root}; run `sssf init` to upgrade to .adws/.",
        file=sys.stderr,
    )
    return True
