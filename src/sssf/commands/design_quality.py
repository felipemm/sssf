"""`sssf init --design-quality <surface>` — configure impeccable + the design
gate on a stamped project.

Per-project design quality has three consumers of the SAME declared surface:
the deterministic gate (the `design` quality check -> `impeccable detect
<surface>`), the designer agent's `writes:` scope, and the DESIGN.md/PRODUCT.md
design-context flow. This module wires all three in the project's commented
sssf.config.yaml with surgical, marker-anchored edits — never a yaml round-trip
(the config is comment-rich and deliberately has no editor) and never touching
the project's other checks/agents.

Idempotent: re-running with a new surface moves the gate + designer scope.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DESIGN_BLOCK = """\
    - name: design
      area: frontend
      operation: lint
      surface: {surface}
      timeout_seconds: 300
"""

IMPERCEPTIBLE_CONFIG: dict[str, dict[str, list[str]]] = {
    "detector": {
        "ignoreRules": [],
        "ignoreFiles": [],
        "ignoreValues": [],
    }
}

_ERR = "sssf: --design-quality needs adws/config/sssf.config.yaml — run `sssf init` first"


def config_file(root: Path) -> Path:
    return root / "adws" / "config" / "sssf.config.yaml"


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """Index of the first line after `start` that dedents past `indent` or
    starts a sibling at the same indent — the block's exclusive end."""
    i = start + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if not lines[i][:1].isspace():  # top-level key: block ended
            return i
        if not lines[i][:indent].strip() and lines[i][indent : indent + 1] not in ("", " "):
            # sibling at the same indent (e.g. another "- name:" list item)
            return i
        i += 1
    return len(lines)


def _find_list_item(lines: list[str], indent: int, name: str, value: str) -> int | None:
    """Index of a `- name: <value>` list item whose dash is at `indent`."""
    prefix = " " * indent + "- name:"
    for i, line in enumerate(lines):
        if line.startswith(prefix) and value in line[len(prefix) :].strip():
            return i
    return None


def _replace_design_block(text: str, surface: str) -> str:
    """Turn the quality.checks `design` entry into the canonical surface form
    (replace an argv/requires-style entry, insert one if absent)."""
    lines = text.splitlines(keepends=True)
    idx = _find_list_item(lines, 4, "design", "design")
    block = DESIGN_BLOCK.format(surface=surface)
    if idx is None:
        # Insert before the top-level `agents:` section.
        anchor = next(
            (i for i, line in enumerate(lines) if line.startswith("agents:")),
            len(lines),
        )
        # Preserve the blank line before agents: (insert the block + a blank
        # after it, matching the template's check-list formatting).
        insert_at = anchor
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
        return (
            "".join(lines[:insert_at])
            + block
            + "\n"
            + "".join(lines[anchor:])
        )
    end = _block_end(lines, idx, 4)
    kept: list[str] = []
    for line in lines[idx:end]:
        stripped = line.strip()
        if stripped.startswith("surface:") or stripped.startswith("argv:") or stripped.startswith("requires:"):
            continue  # replaced by the canonical surface form
        kept.append(line)
    head = "".join(kept[:1])  # the "- name: design" line (kept[0])
    rest = kept[1:]
    # Insert `surface` right after the last field line that precedes it in the
    # template ordering (area/operation); simplest: after the operation line,
    # else at the head.
    surface_line = f"      surface: {surface}\n"
    for j, line in enumerate(rest):
        if line.strip().startswith("operation:"):
            rest = [*rest[: j + 1], surface_line, *rest[j + 1 :]]
            break
    else:
        rest = [surface_line, *rest]
    return "".join(lines[:idx]) + head + "".join(rest) + "".join(lines[end:])


def _replace_designer_writes(text: str, surface: str) -> str:
    """Point the designer agent's `writes:` at the declared surface."""
    lines = text.splitlines(keepends=True)
    idx = _find_list_item(lines, 2, "designer", "designer")
    if idx is None:
        return text  # no designer roster entry — nothing to scope
    end = _block_end(lines, idx, 2)
    # find the writes: key inside the block, then its first list item
    for j in range(idx, end):
        if lines[j].strip() == "writes:" or lines[j].strip().startswith("writes:"):
            for k in range(j + 1, end):
                stripped = lines[k].strip()
                if not stripped:
                    continue
                if stripped.startswith("- "):
                    lines[k] = "      - " + surface + "\n"
                    return "".join(lines)
                break
            break
    return text


def configure(root: Path, surface: str) -> int:
    """Wire impeccable + the design gate into a stamped project. Returns 0."""
    cfg = config_file(root)
    if not cfg.exists():
        print(_ERR, file=sys.stderr)
        return 1
    surface = surface.rstrip("/")
    text = cfg.read_text()
    text = _replace_design_block(text, surface)
    text = _replace_designer_writes(text, surface)
    cfg.write_text(text)

    # Per-project rule control (.impeccable/config.json) — committed so the
    # sandbox (a fresh origin/main worktree) sees it. Empty ignores by default;
    # `impeccable ignores` or a hand edit fills it in.
    impeccable = root / ".impeccable"
    impeccable.mkdir(exist_ok=True)
    rc = impeccable / "config.json"
    if not rc.exists():
        rc.write_text(json.dumps(IMPERCEPTIBLE_CONFIG, indent=2) + "\n")
    print(f"sssf: design gate configured — surface {surface} (quality.checks `design`)")
    print(f"sssf: designer scope set to {surface}; .impeccable/config.json ready")
    print(
        "sssf: commit adws/config/sssf.config.yaml + .impeccable/ — sandboxed "
        "runs read the committed state. Run the full flow with `sssf run sdlc_full \"...\"`"
    )
    return 0
