"""Spawn-death classification: turn a dead container's evidence into a
remediation hint. Pure functions only — no docker, no git, no io."""

from __future__ import annotations

import re


def classify_failure(log_tail: str, exit_code: str = "") -> str | None:
    """A remediation hint for a spawn-death, or None when there is zero
    evidence. Specific signatures first; unknown evidence passes through as
    its own hint (the tail IS the message)."""
    tail = (log_tail or "").strip()
    code = (exit_code or "").strip()
    if not tail and not code:
        return None
    low = tail.lower()
    if "can't open file" in low and "adws/" in low:
        return _layout_hint(_quoted_path(tail))
    if "no such file or directory" in low and "adws/" in low:
        return _layout_hint(_quoted_path(tail))
    if ("importerror" in low or "modulenotfounderror" in low) and "sssf.adw_modules" in low:
        return "runner image is stale or broken — rebuild it: `sssf sandbox build`"
    if "executable file not found" in low:
        return "a required binary is missing from the runner image — rebuild it or fix docker/sssf-runner.Dockerfile"
    if code == "127" and tail:
        return "a required binary is missing from the runner image (exit 127) — rebuild it or fix docker/sssf-runner.Dockerfile"
    if tail:
        return tail[:300]
    return f"container exited (exit {code}) with no output — inspect the image entrypoint (docker/entrypoint.sh) and the spawned command"


def _quoted_path(tail: str) -> str:
    m = re.search(r"'([^'\s]*/[^'\s]*)'", tail)
    return m.group(1) if m else "the entry file"


def _layout_hint(path: str) -> str:
    return (
        f"{path} is not in the worktree — the project layout is not committed;"
        " commit it (`git add -A && git commit`) or re-run `sssf init`"
    )
