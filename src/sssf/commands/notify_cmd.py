"""`sssf notify` — post an alert to a ticket's Slack thread (alerting only)."""

from __future__ import annotations

import sys
from pathlib import Path

from sssf import notify
from sssf.adw_modules import paths
from sssf.project import find_project


def run(
    ticket_id: str,
    text: str,
    project: str | None = None,
    *,
    workbench: str | None = None,
    mr: str | None = None,
    release: str | None = None,
    tompero: str | None = None,
) -> int:
    root = find_project(Path.cwd(), project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    paths.warn_if_legacy(root, command="notify")
    urls: dict[str, str] = {}
    for label, value in (
        ("workbench", workbench),
        ("mr", mr),
        ("release", release),
        ("tompero", tompero),
    ):
        if value:
            urls[label] = value
    try:
        result = notify.send(root, ticket_id, text, urls=urls)
    except notify.NotifyError as error:
        print(f"sssf notify: {error}", file=sys.stderr)
        return 1
    if not result.ok:
        print(f"sssf notify: delivery failed after {result.attempts} attempt(s): {result.error}",
              file=sys.stderr)
        return 1
    print(f"sssf notify: posted to thread {result.ts} for ticket {ticket_id}")
    return 0
