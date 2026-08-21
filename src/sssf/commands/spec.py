"""`sssf spec create` — interactive product-manager interview.

Spawns an interactive pi session with a mode-specific product-manager context
(idea | bug | feature) appended to the system prompt. The skills are installed
project-locally by `sssf init`; the agent interviews the user, writes the spec
to `adws/prompts/NN-<slug>.md`, and creates the ticket — runnable from the
visualizer.
"""

from __future__ import annotations

import subprocess
import sys
from importlib import resources
from pathlib import Path

from sssf import ticketing
from sssf.adw_modules import paths
from sssf.commands import misc
from sssf.project import find_project

MODES = ("idea", "bug", "feature")


def create(mode: str, title: str | None, cwd: Path, explicit_project: str | None = None) -> int:
    if mode not in MODES:
        print(f"sssf spec: unknown mode {mode!r} (choose {', '.join(MODES)})", file=sys.stderr)
        return 1
    root = find_project(cwd, explicit_project)
    if root is None:
        print("sssf spec: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="spec")
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print("sssf spec: the internal provider is not enabled in adws/config/ticketing.yaml",
              file=sys.stderr)
        return 1
    if misc.which("pi") is None:
        print("sssf spec: pi binary not found on PATH", file=sys.stderr)
        return 1

    slug = _slug(title)
    context_dir = paths.data_dir(root) / "spec_interview"
    context_dir.mkdir(parents=True, exist_ok=True)
    context_path = context_dir / f"{mode}-{slug}.md"
    template = (resources.files("sssf.templates") / "spec_interviewer" / f"{mode}.md").read_text()
    context_path.write_text(
        template
        + _project_context_block(root)
    )
    print(f"sssf spec: {mode} interview — context {context_path.relative_to(root)}")
    print("sssf spec: starting interactive pi session (product manager). "
          "Answer the interview; the spec + ticket are created for you.")
    return subprocess.call(["pi", "--append-system-prompt", str(context_path)], cwd=root)


def _slug(title: str | None) -> str:
    if title:
        s = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40]
        if s:
            return s
    return "spec"


def _project_context_block(root: Path) -> str:
    cfg_path = paths.config_file(root)
    roster = "?"
    if cfg_path.exists():
        try:
            from sssf.adw_modules.agents import load_config
            roster = ", ".join(a.name for a in load_config(str(cfg_path)).agents)
        except Exception:
            roster = "(unreadable)"
    prompts = sorted(p.name for p in paths.prompts_dir(root).glob("*.md"))
    return (
        "\n\n## Project context\n"
        f"- project root: {root}\n"
        f"- roster: {roster}\n"
        f"- existing prompts: {', '.join(prompts) if prompts else 'none'}\n"
        "- write the spec to adws/prompts/ with the next NN- numbering\n"
    )
