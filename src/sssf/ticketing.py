"""Ticketing adapters: fetch backlog tickets from configured providers.

Providers are a set (any subset of jira | linear | internal). External sync is
read-only: Jira goes through the user-authenticated `acli` CLI, Linear through
its GraphQL API with a token from the project .env. All tickets land in the
trace db's `tickets` table; the kanban reads that.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

TICKETING_FILE = "adws/config/ticketing.yaml"
LINEAR_API = "https://api.linear.app/graphql"

TICKETS_DDL = """
CREATE TABLE IF NOT EXISTS tickets (
  id          TEXT PRIMARY KEY,
  provider    TEXT NOT NULL,
  external_id TEXT,
  title       TEXT NOT NULL,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'backlog',
  prompt_file TEXT,
  adw_id      TEXT,
  source_url  TEXT,
  created_at  TEXT, updated_at TEXT
);
"""

# One row per run of a ticket — history survives retries. `tickets.adw_id`
# stays the LATEST run; this table keeps every earlier one so a retried
# ticket shows its full run list instead of losing the failed attempt.
TICKET_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS ticket_runs (
  ticket_id  TEXT NOT NULL,
  adw_id     TEXT NOT NULL,
  created_at TEXT,
  PRIMARY KEY (ticket_id, adw_id)
);
"""


@dataclass
class TicketRecord:
    provider: str
    external_id: str
    title: str
    description: str
    source_url: str


@dataclass
class TicketingConfig:
    providers: list[str]
    jira: dict = field(default_factory=dict)
    linear: dict = field(default_factory=dict)


@dataclass
class ProviderSyncResult:
    provider: str
    tickets: int = 0
    error: str | None = None


def load_config(root: Path) -> TicketingConfig | None:
    """Parse ticketing.yaml; None when missing or no providers enabled."""
    path = root / TICKETING_FILE
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        raise RuntimeError(f"invalid {path}: {error}") from error
    providers = data.get("providers") or []
    if not providers:
        return None
    return TicketingConfig(providers=list(providers),
                           jira=data.get("jira") or {},
                           linear=data.get("linear") or {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _run_acli(args: list[str]) -> dict:
    if shutil.which("acli") is None:
        raise RuntimeError(
            "the Jira provider needs the acli CLI — install it and configure auth: "
            "https://github.com/zdharma-continuum/acli")
    result = subprocess.run(["acli", *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"acli failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}\n"
            "Is acli authenticated? Run its login/credentials setup first.")
    return json.loads(result.stdout or "[]")


def fetch_jira(cfg: TicketingConfig) -> list[TicketRecord]:
    jql = cfg.jira.get("jql")
    if not jql:
        raise RuntimeError("the jira provider needs a `jql` in ticketing.yaml")
    # acli >= 1.3 moved work items under `jira workitem search` (old
    # `acli issue list` was removed). Default fields exclude description,
    # so request it explicitly.
    data = _run_acli([
        "jira", "workitem", "search",
        "--jql", jql,
        "--fields", "summary,description",
        "--limit", "100",
        "--json",
    ])
    issues = data if isinstance(data, list) else (data.get("issues") or data.get("data") or [])
    base_host = (cfg.jira.get("base_url") or "").rstrip("/").removeprefix("https://")
    records = []
    for issue in issues:
        fields = issue.get("fields") or {}
        key = str(issue.get("key") or "")
        self_url = issue.get("self") or ""
        host = self_url.split("/")[2] if self_url.startswith("http") else ""
        # Internal Atlassian `self` hosts (jira-prod-us-*.prod.atl-paas.net)
        # aren't browsable; prefer the configured base_url when present.
        if base_host and (not host or "atl-paas.net" in host):
            host = base_host
        records.append(TicketRecord(
            provider="jira", external_id=key,
            title=str(fields.get("summary") or key),
            description=str(fields.get("description") or ""),
            source_url=f"https://{host}/browse/{key}" if host else "",
        ))
    return records


def fetch_linear(cfg: TicketingConfig) -> list[TicketRecord]:
    token_env = cfg.linear.get("token_env") or "LINEAR_TOKEN"
    token = os.environ.get(token_env, "")
    if not token:
        raise RuntimeError(f"the linear provider needs {token_env} set in the project .env")
    team = cfg.linear.get("team")
    if not team:
        raise RuntimeError("the linear provider needs a `team` key in ticketing.yaml")
    query = (
        'query { issues(filter: {team: {key: {eq: "%s"}}}, first: 100) '
        '{ nodes { id identifier title description url state { name } } } }' % team)
    req = urllib.request.Request(
        LINEAR_API, data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    nodes = (payload.get("data") or {}).get("issues", {}).get("nodes", [])
    wanted = {str(s).strip().lower() for s in (cfg.linear.get("states") or [])}
    records = []
    for node in nodes:
        state = ((node.get("state") or {}).get("name") or "").strip().lower()
        if wanted and state not in wanted:
            continue
        records.append(TicketRecord(
            provider="linear", external_id=str(node.get("identifier") or node.get("id")),
            title=str(node.get("title") or ""),
            description=str(node.get("description") or ""),
            source_url=str(node.get("url") or ""),
        ))
    return records


def upsert_tickets(db_path: Path, records: list[TicketRecord]) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(TICKETS_DDL)
        count = 0
        for r in records:
            now = _now()
            cur = conn.execute(
                "INSERT INTO tickets (id, provider, external_id, title, description, status,"
                " source_url, created_at, updated_at) VALUES (?,?,?,?,?,'backlog',?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                " description=excluded.description, source_url=excluded.source_url,"
                " updated_at=excluded.updated_at",
                (f"{r.provider}:{r.external_id}", r.provider, r.external_id,
                 r.title, r.description, r.source_url, now, now))
            count += cur.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def sync_tickets(root: Path, cfg: TicketingConfig) -> list[ProviderSyncResult]:
    """Load .env, fetch every enabled provider, upsert; one result per provider."""
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except ImportError:
        pass
    from sssf.adw_modules import paths
    db_path = paths.data_dir(root) / "sssf.db"
    results: list[ProviderSyncResult] = []
    for provider in cfg.providers:
        try:
            if provider == "jira":
                records = fetch_jira(cfg)
            elif provider == "linear":
                records = fetch_linear(cfg)
            elif provider == "internal":
                continue            # internal tickets already live in the db
            else:
                results.append(ProviderSyncResult(provider, error=f"unknown provider {provider!r}"))
                continue
            results.append(ProviderSyncResult(provider, tickets=upsert_tickets(db_path, records)))
        except (RuntimeError, OSError, sqlite3.Error) as error:
            results.append(ProviderSyncResult(provider, error=str(error)))
    return results


def next_prompt_name(root: Path, slug: str) -> Path:
    """The next enumerated prompt path: adws/prompts/NN-<slug>.md (collision suffix)."""
    prompts = root / "adws" / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    numbers = [int(p.stem.split("-")[0]) for p in prompts.glob("*.md")
               if p.stem.split("-")[0].isdigit()]
    n = (max(numbers, default=0) + 1)
    candidate = prompts / f"{n:02d}-{slug}.md"
    i = 1
    while candidate.exists():
        i += 1
        candidate = prompts / f"{n:02d}-{slug}-{i}.md"
    return candidate
