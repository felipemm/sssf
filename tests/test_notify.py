import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sssf import notify
from sssf.commands import notify_cmd


def _project(tmp_path, notify_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "data").mkdir(parents=True)
    if notify_yaml is not None:
        (root / "adws" / "config" / "notify.yaml").write_text(notify_yaml)
    return root


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "data" / "sssf.db")
    conn.row_factory = sqlite3.Row
    notify.ensure_schema(conn)
    return conn


CONFIG = "channel: '#sssf-alerts'\ntoken_env: SLACK_BOT_TOKEN\n"


# ── config ────────────────────────────────────────────────────────────────


def test_missing_config_is_none(tmp_path):
    assert notify.load_config(_project(tmp_path)) is None


def test_commented_config_is_none(tmp_path):
    assert notify.load_config(_project(tmp_path, "# channel: '#x'\n")) is None


def test_config_parses(tmp_path):
    cfg = notify.load_config(_project(tmp_path, CONFIG))
    assert cfg is not None
    assert cfg.channel == "#sssf-alerts"
    assert cfg.token_env == "SLACK_BOT_TOKEN"
    assert cfg.retries == 5


def test_invalid_yaml_raises(tmp_path):
    with pytest.raises(RuntimeError, match="invalid"):
        notify.load_config(_project(tmp_path, "channel: [unclosed\n"))


# ── payload ───────────────────────────────────────────────────────────────


def test_payload_plain_without_urls():
    assert notify._payload("hello") == "hello"


def test_payload_carries_action_urls():
    payload = notify._payload(
        "test now",
        {"workbench": "http://wb", "mr": "http://mr", "release": "http://rel", "tompero": "http://tp"},
    )
    assert "test now" in payload
    assert "http://wb" in payload
    assert "http://mr" in payload
    assert "http://rel" in payload
    assert "http://tp" in payload


def test_payload_skips_empty_urls():
    payload = notify._payload("x", {"workbench": "", "mr": None})
    assert "workbench" not in payload


# ── SlackTransport ────────────────────────────────────────────────────────


class _FakeHTTP:
    """A fake Slack API: records requests, answers with canned payloads."""

    def __init__(self, responses, monkeypatch):
        self.responses = list(responses)
        self.requests = []
        self.urlopen = MagicMock(side_effect=self._call)
        monkeypatch.setattr(notify.urllib.request, "urlopen", self.urlopen)

    def _call(self, req, **kwargs):
        self.requests.append(json.loads(req.data))
        if not self.responses:
            raise AssertionError("no canned response left")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        out = MagicMock()
        out.read.return_value = json.dumps(resp).encode()
        out.__enter__.return_value = out
        return out


def test_slack_post_success(monkeypatch):
    api = _FakeHTTP([{"ok": True, "ts": "123.456"}], monkeypatch)
    tx = notify.SlackTransport("xoxb-test", sleep=lambda _s: None)
    result = tx.post(channel="#c", text="hi")
    assert result.ok and result.ts == "123.456" and result.attempts == 1
    req = api.requests[0]
    assert req["channel"] == "#c" and req["text"] == "hi"
    assert "thread_ts" not in req
    assert api.urlopen.call_args.args[0].headers["Authorization"] == "Bearer xoxb-test"


def test_slack_reply_passes_thread_ts(monkeypatch):
    api = _FakeHTTP([{"ok": True, "ts": "123.456"}], monkeypatch)
    tx = notify.SlackTransport("t", sleep=lambda _s: None)
    tx.post(channel="#c", text="hi", thread_ts="111.222")
    assert api.requests[0]["thread_ts"] == "111.222"


def test_slack_retries_on_ok_false_then_succeeds(monkeypatch):
    sleeps = []
    _FakeHTTP(
        [{"ok": False, "error": "rate_limited"}, {"ok": True, "ts": "9.9"}], monkeypatch
    )
    tx = notify.SlackTransport("t", sleep=sleeps.append)
    result = tx.post(channel="#c", text="hi")
    assert result.ok and result.attempts == 2
    assert sleeps == [1.0]  # backoff_base * 2 ** (attempt - 1)


def test_slack_retries_on_network_error(monkeypatch):
    from urllib.error import URLError

    sleeps = []
    _FakeHTTP([URLError("boom"), {"ok": True, "ts": "1.1"}], monkeypatch)
    tx = notify.SlackTransport("t", sleep=sleeps.append)
    result = tx.post(channel="#c", text="hi")
    assert result.ok and result.attempts == 2


def test_slack_gives_up_after_five_retries(monkeypatch):
    sleeps = []
    from urllib.error import URLError

    _FakeHTTP([URLError("boom")] * 10, monkeypatch)
    tx = notify.SlackTransport("t", retries=5, sleep=sleeps.append)
    result = tx.post(channel="#c", text="hi")
    assert not result.ok
    assert result.attempts == 6
    assert len(sleeps) == 5  # one backoff per retry, exponential


def test_slack_does_not_retry_auth_error(monkeypatch):
    _FakeHTTP([{"ok": False, "error": "invalid_auth"}], monkeypatch)
    tx = notify.SlackTransport("t", sleep=lambda _s: None)
    result = tx.post(channel="#c", text="hi")
    assert not result.ok and result.error == "invalid_auth" and result.attempts == 1


def test_slack_does_not_retry_client_http(monkeypatch):
    from urllib.error import HTTPError

    _FakeHTTP([HTTPError("slack", 400, "bad", None, None)], monkeypatch)
    tx = notify.SlackTransport("t", sleep=lambda _s: None)
    result = tx.post(channel="#c", text="hi")
    assert not result.ok and "400" in result.error and result.attempts == 1


def test_slack_retries_http_5xx(monkeypatch):
    from urllib.error import HTTPError

    _FakeHTTP([HTTPError("slack", 500, "boom", None, None), {"ok": True, "ts": "2.2"}], monkeypatch)
    tx = notify.SlackTransport("t", sleep=lambda _s: None)
    result = tx.post(channel="#c", text="hi")
    assert result.ok and result.attempts == 2


# ── notify() orchestration ────────────────────────────────────────────────


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.result = notify.PostResult(ok=True, ts="777.777", attempts=1)

    def post(self, *, channel, text, thread_ts=None):
        self.calls.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return self.result


def test_notify_requires_config(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    with pytest.raises(notify.NotifyError, match="not configured"):
        notify.send(root, "t1", "hi", transport=FakeTransport())


def test_notify_requires_token(tmp_path, monkeypatch):
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    with pytest.raises(notify.NotifyError, match="SLACK_BOT_TOKEN"):
        notify.send(root, "t1", "hi", transport=FakeTransport())


def test_notify_creates_thread_on_first_post(tmp_path, monkeypatch):
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    tx = FakeTransport()
    result = notify.send(root, "t1", "hello", urls={"mr": "http://mr"}, transport=tx)
    assert result.ok
    assert tx.calls[0]["thread_ts"] is None
    assert "hello" in tx.calls[0]["text"] and "http://mr" in tx.calls[0]["text"]
    conn = _db(root)
    row = conn.execute("SELECT thread_ts, channel FROM notify_threads WHERE ticket_id='t1'").fetchone()
    assert row["thread_ts"] == "777.777"
    event = conn.execute("SELECT ok, attempts FROM notify_events WHERE ticket_id='t1'").fetchone()
    assert event["ok"] == 1 and event["attempts"] == 1
    conn.close()


def test_notify_reuses_thread_on_later_posts(tmp_path, monkeypatch):
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    conn = _db(root)
    conn.execute(
        "INSERT INTO notify_threads (ticket_id, channel, thread_ts, updated_at)"
        " VALUES ('t1', '#sssf-alerts', '111.222', 'now')"
    )
    conn.commit()
    conn.close()
    tx = FakeTransport()
    notify.send(root, "t1", "again", transport=tx)
    assert tx.calls[0]["thread_ts"] == "111.222"


def test_notify_records_failure(tmp_path, monkeypatch):
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    tx = FakeTransport()
    tx.result = notify.PostResult(ok=False, error="rate_limited", attempts=6)
    result = notify.send(root, "t1", "hi", transport=tx)
    assert not result.ok
    conn = _db(root)
    event = conn.execute("SELECT ok, error FROM notify_events WHERE ticket_id='t1'").fetchone()
    assert event["ok"] == 0 and event["error"] == "rate_limited"
    assert conn.execute("SELECT count(*) FROM notify_threads WHERE ticket_id='t1'").fetchone()[0] == 0
    conn.close()


def test_notify_swaps_transport(tmp_path, monkeypatch):
    """Call sites depend on the Transport protocol — Slack never leaks."""
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    tx = FakeTransport()
    notify.send(root, "t1", "hi", transport=tx)
    assert tx.calls  # our fake got the call; no Slack-specific object used


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_notify_no_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert notify_cmd.run("t1", "hi") == 1
    assert "no project here" in capsys.readouterr().err


def test_cli_notify_missing_token(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert notify_cmd.run("t1", "hi") == 1
    assert "SLACK_BOT_TOKEN" in capsys.readouterr().err


def test_cli_notify_success(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, CONFIG)
    monkeypatch.chdir(root)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(
        notify,
        "SlackTransport",
        lambda *a, **k: FakeTransport(),
    )
    assert notify_cmd.run("t1", "hi", workbench="http://wb") == 0
    out = capsys.readouterr().out
    assert "posted" in out
    conn = _db(root)
    assert conn.execute("SELECT count(*) FROM notify_events").fetchone()[0] == 1
    conn.close()
