"""Notify adapter: Slack, one thread per ticket, alerting only.

A small transport behind the flows and the monitor. It posts to
`chat.postMessage` with a bot token, keeps one thread per ticket across its
lifecycle (the first post's `ts` becomes the thread root; later posts reply
in-thread), carries the action URLs needed to act (workbench, MR, release,
tompero), retries five times with exponential backoff, and records every
attempt in the trace db — a dropped alert is visible, never silent.

Alerting only by design: human checkpoints (signoff, MR approval, promote
confirmation) stay in the terminal and the tracker. Call sites depend on the
`Transport` protocol, so swapping Slack for another transport does not touch
them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import yaml

from sssf import db_schema
from sssf.adw_modules import paths

NOTIFY_FILE = "adws/config/notify.yaml"
SLACK_API = "https://slack.com/api/chat.postMessage"



class NotifyError(RuntimeError):
    """Configuration or transport problem that could not be retried away."""


@dataclass
class NotifyConfig:
    channel: str
    token_env: str = "SLACK_BOT_TOKEN"
    retries: int = 5
    backoff_base: float = 1.0
    timeout: float = 30.0


@dataclass
class PostResult:
    ok: bool
    ts: str | None = None
    error: str | None = None
    attempts: int = 0


class Transport(Protocol):
    """What call sites know: post text, get a result. Swap freely."""

    def post(self, *, channel: str, text: str, thread_ts: str | None = None) -> PostResult: ...


def load_config(root: Path) -> NotifyConfig | None:
    """Parse notify.yaml; None when missing or no channel configured."""
    path = root / NOTIFY_FILE
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        raise RuntimeError(f"invalid {path}: {error}") from error
    channel = str(data.get("channel") or "").strip()
    if not channel:
        return None
    return NotifyConfig(
        channel=channel,
        token_env=str(data.get("token_env") or "SLACK_BOT_TOKEN"),
        retries=int(data.get("retries") or 5),
        backoff_base=float(data.get("backoff_base") or 1.0),
        timeout=float(data.get("timeout") or 30.0),
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The schema contract: notify tables come from the db_schema models."""
    db_schema.apply_schema(conn)

def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class SlackTransport:
    """chat.postMessage via bot token, five retries with exponential backoff.

    Retries cover network errors, HTTP 5xx, and `ok:false` responses alike —
    Slack itself can return 200 with `ok:false` for rate limits and transient
    backend issues. Every attempt is recorded by the caller, so an exhausted
    retry loop is visible, never silently dropped.
    """

    def __init__(
        self,
        token: str,
        *,
        retries: int = 5,
        backoff_base: float = 1.0,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token
        self.retries = retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self._sleep = sleep

    def post(self, *, channel: str, text: str, thread_ts: str | None = None) -> PostResult:
        last_error: str | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                # backoff_base * 2 ** (attempt - 1): 1s, 2s, 4s, 8s, 16s
                self._sleep(self.backoff_base * 2 ** (attempt - 1))
            try:
                return self._post_once(channel, text, thread_ts, attempts=attempt + 1)
            except urllib.error.HTTPError as error:
                last_error = f"http {error.code}"
                if error.code < 500 and error.code != 429:
                    return PostResult(ok=False, error=last_error, attempts=attempt + 1)
            except urllib.error.URLError as error:
                last_error = f"network error: {error.reason}"
            except (json.JSONDecodeError, TimeoutError, _SlackError) as error:
                last_error = f"bad response: {error}"
        return PostResult(ok=False, error=last_error, attempts=self.retries + 1)

    def _post_once(
        self, channel: str, text: str, thread_ts: str | None, *, attempts: int
    ) -> PostResult:
        body: dict[str, str] = {"channel": channel, "text": text}
        if thread_ts:
            body["thread_ts"] = thread_ts
        req = urllib.request.Request(
            SLACK_API,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read())
        if payload.get("ok"):
            return PostResult(ok=True, ts=payload.get("ts"), attempts=attempts)
        error = str(payload.get("error") or "unknown error")
        # ok:false with a non-recoverable auth error — retrying won't help.
        if error in ("invalid_auth", "account_inactive", "not_authed"):
            return PostResult(ok=False, error=error, attempts=attempts)
        raise _SlackError(error)


class _SlackError(Exception):
    """A retryable `ok:false` from Slack (rate limited, transient backend)."""


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(paths.data_dir(root) / "sssf.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _payload(text: str, urls: dict[str, str] | None = None) -> str:
    """The alert body: message plus the action URLs (Slack mrkdwn links)."""
    if not urls:
        return text
    lines = [text, ""]
    for label, url in urls.items():
        if url:
            lines.append(f"• <{url}|{label}>")
    return "\n".join(lines)


def send(
    root: Path,
    ticket_id: str,
    text: str,
    *,
    urls: dict[str, str] | None = None,
    transport: Transport | None = None,
    config: NotifyConfig | None = None,
) -> PostResult:
    """Post an alert to a ticket's thread (creating it on first contact).

    Threads are per-ticket and permanent: the first post creates the root
    message, its `ts` is stored in the trace db, and every later post for that
    ticket replies in-thread. Each attempt is recorded in `notify_events`.
    """
    cfg = config or load_config(root)
    if cfg is None:
        raise NotifyError("notify is not configured (adws/config/notify.yaml)")
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    token = os.environ.get(cfg.token_env, "")
    if not token:
        raise NotifyError(f"{cfg.token_env} is not set (add it to the project .env)")

    conn = _db(root)
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT channel, thread_ts FROM notify_threads WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        thread_ts = None
        if row is not None and row["channel"] == cfg.channel:
            thread_ts = row["thread_ts"]

        tx = transport or SlackTransport(
            token, retries=cfg.retries, backoff_base=cfg.backoff_base, timeout=cfg.timeout
        )
        result = tx.post(channel=cfg.channel, text=_payload(text, urls), thread_ts=thread_ts)

        now = _now()
        if result.ok and thread_ts is None and result.ts:
            conn.execute(
                "INSERT OR REPLACE INTO notify_threads (ticket_id, channel, thread_ts, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (ticket_id, cfg.channel, result.ts, now),
            )
        conn.execute(
            "INSERT INTO notify_events (ticket_id, ts, ok, error, attempts, thread_ts, channel)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, now, 1 if result.ok else 0, result.error, result.attempts, thread_ts, cfg.channel),
        )
        conn.commit()
        return result
    finally:
        conn.close()