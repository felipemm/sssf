import json
import sqlite3
from pathlib import Path

import pytest

from sssf import ticketing


def _write(root: Path, text: str) -> Path:
    path = root / ticketing.TICKETING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_missing_config_is_none(tmp_path):
    assert ticketing.load_config(tmp_path) is None


def test_commented_template_is_none(tmp_path):
    _write(tmp_path, "# providers:\n#   - internal\n")
    assert ticketing.load_config(tmp_path) is None


def test_multi_provider_config_parses(tmp_path):
    _write(tmp_path, (
        "providers:\n  - internal\n  - jira\n"
        "jira:\n  jql: 'project = ACME AND status in (Backlog, \"To Do\")'\n"
        "linear:\n  team: ENG\n  token_env: LINEAR_TOKEN\n  states: [Backlog]\n"))
    cfg = ticketing.load_config(tmp_path)
    assert cfg is not None
    assert cfg.providers == ["internal", "jira"]
    assert cfg.jira["jql"].startswith("project = ACME")


def test_invalid_yaml_raises(tmp_path):
    _write(tmp_path, "providers: [unclosed\n")
    with pytest.raises(RuntimeError, match="invalid"):
        ticketing.load_config(tmp_path)


def _cfg(tmp_path, providers=("jira",), jira=None, linear=None):
    return ticketing.TicketingConfig(
        providers=list(providers),
        jira={"jql": "project = ACME"} if jira is None else jira,
        linear={"team": "ENG", "token_env": "LINEAR_TOKEN", "states": ["Backlog"]} if linear is None else linear,
    )


def test_fetch_jira_parses_acli_output(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)
        class R:
            returncode = 0
            stdout = json.dumps([{
                "key": "ACME-7",
                "self": "https://acme.atlassian.net/rest/api/3/issue/ACME-7",
                "fields": {"summary": "Add dark mode", "description": "The app needs a dark theme."},
            }])
            stderr = ""
        return R()

    monkeypatch.setattr(ticketing.subprocess, "run", fake_run)
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: "/usr/local/bin/acli")
    records = ticketing.fetch_jira(_cfg(tmp_path))
    assert calls[0] == ["acli", "issue", "list", "--jql", "project = ACME", "-f", "json"]
    assert records[0].external_id == "ACME-7"
    assert records[0].source_url == "https://acme.atlassian.net/browse/ACME-7"


def test_fetch_jira_missing_acli(tmp_path, monkeypatch):
    monkeypatch.setattr(ticketing.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="acli"):
        ticketing.fetch_jira(_cfg(tmp_path))


def test_fetch_linear_parses_graphql(tmp_path, monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout):
        sent["body"] = json.loads(request.data)
        sent["auth"] = request.headers.get("Authorization")
        class R:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def read(self):
                return json.dumps({"data": {"issues": {"nodes": [
                    {"id": "lin1", "identifier": "ENG-3", "title": "Linear ticket",
                     "description": "Do the thing", "url": "https://linear.app/acme/issue/ENG-3",
                     "state": {"name": "Backlog"}},
                    {"id": "lin2", "identifier": "ENG-4", "title": "Done one",
                     "description": "", "url": "https://linear.app/acme/issue/ENG-4",
                     "state": {"name": "Done"}},
                ]}}}).encode()
        return R()

    monkeypatch.setattr(ticketing.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ticketing.os, "environ", {"LINEAR_TOKEN": "tok"}, raising=False)
    records = ticketing.fetch_linear(_cfg(tmp_path))
    assert sent["auth"] == "Bearer tok"
    assert "team" in sent["body"]["query"] and "ENG" in sent["body"]["query"]
    assert [r.external_id for r in records] == ["ENG-3"]


def test_sync_upserts_without_duplicates(tmp_path):
    from sssf.adw_modules import tracer as tracer_mod
    db = tmp_path / "adws" / "adw_data" / "sssf.db"
    db.parent.mkdir(parents=True)
    tracer_mod.Tracer(db_path=db, events_jsonl=db.with_suffix(".jsonl")).conn.close()
    records = [ticketing.TicketRecord("jira", "ACME-1", "One", "d", "u")]
    assert ticketing.upsert_tickets(db, records) == 1
    assert ticketing.upsert_tickets(db, records) == 1   # same id -> update, no new row
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    assert n == 1
    conn.close()
