"""`sssf heal` — the self-healing monitor daemon (start / stop / status)."""
from __future__ import annotations

from sssf import healer


def main(action: str) -> int:
    if action == "start":
        return healer.start()
    if action == "stop":
        return healer.stop()
    return healer.status()
