# Ticketing Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project enable one or more ticketing providers (Jira via `acli`, Linear, or internal) so their backlog tickets populate the kanban Backlog stage, and each ticket can be promoted into a spawned `adw_simple_sdlc` run.

**Architecture:** Python engine owns everything ticket-shaped — config (`ticketing.yaml`), provider adapters, the `tickets` table in the trace db, and the CLI (`sssf ticket add/sync/list/run`). The viz server is a thin shell: it reads the tickets table for the board, and shells to the CLI for sync and run. The kanban Backlog column renders ticket cards (only when ticketing is enabled) with a content modal (Run | Close). The session (ADW id) is the first-class kanban citizen; a ticket leaves the board the moment it is run.

**Tech Stack:** Python 3.11+ (argparse, sqlite3, subprocess, urllib, python-dotenv, pyyaml), the `acli` CLI for Jira, bun + Vue 3 for the visualizer, pytest + bun test.

**Spec:** `docs/superpowers/specs/2026-08-15-ticketing-design.md`

## Global Constraints

- Ticketing is **opt-in**: missing or effectively empty `adws/adw_sssf_config/ticketing.yaml` ⇒ feature off ⇒ the kanban hides the Backlog stage and `sssf ticket` commands answer "ticketing not configured".
- **Providers are a set** — any subset of `jira | linear | internal`; a broken provider block is skipped with a clear error, the rest still sync.
- **Jira has no token in sssf**: the adapter shells to the pre-installed, user-authenticated `acli` CLI. Linear's token comes from `token_env` loaded via the project `.env` (python-dotenv).
- The `tickets` table lives in the project's existing `adws/adw_data/sssf.db`; upsert on `provider + external_id` (the `id` column is `provider:external_id`), never duplicate.
- **Session is first-class**: tickets are backlog cards only while `status='backlog'`; the moment a run spawns the ticket leaves the board and exists purely as the session's origin. Nothing is in two kanban stages at once.
- Prompt files are **enumerated** in `adws/prompts/` (`NN-<slug>.md`, next number by scanning existing files, collision suffix `-2`, `-3`…).
- External sync is **read-only** (no write-back to Jira/Linear).
- The viz server shells to the CLI (`sssf ticket sync/run`) rather than reimplementing the logic.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/sssf/adw_modules/tracer.py` | + `tickets` table in SCHEMA (idempotent) |
| `src/sssf/ticketing.py` (new) | config load, `TicketRecord`, adapters (`fetch_jira`/`fetch_linear`), `sync_tickets`, `upsert_tickets`, `next_prompt_name`, `TICKETS_DDL` |
| `src/sssf/commands/ticket.py` (new) | `sssf ticket add/sync/list/run` |
| `src/sssf/cli.py` | register the `ticket` subparser |
| `src/sssf/templates/ticketing.yaml` (new) | commented-out template |
| `src/sssf/commands/init.py` | stamp the template |
| `src/sssf/apps/visualizer/server/index.ts` | 3 routes: GET tickets, POST sync, POST run |
| `src/sssf/apps/visualizer/server/tickets.ts` (new) | read + reconcile tickets from the db |
| `src/sssf/apps/visualizer/src/lib/api.ts` | `fetchTickets`/`runTicket`/`syncTickets` |
| `src/sssf/apps/visualizer/src/components/KanbanBoard.vue` | dynamic Backlog column + ticket cards + sync button |
| `src/sssf/apps/visualizer/src/components/TicketCard.vue` (new) | ticket card |
| `src/sssf/apps/visualizer/src/components/TicketModal.vue` (new) | content modal with Run | Close |
| `tests/test_ticketing.py` (new) | config + adapters + sync + prompt enumeration |
| `tests/test_ticket_cli.py` (new) | CLI commands |
| `src/sssf/apps/visualizer/server/tickets.test.ts` (new) | tickets read/reconcile + routes with CLI stubbed |

---

### Task 1: `tickets` table in the tracer schema

**Files:**
- Modify: `src/sssf/adw_modules/tracer.py` (SCHEMA)
- Test: `tests/test_engine_port.py` (append)

**Interfaces:**
- Produces: `tickets` table in every trace db, columns exactly:
  `id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT, title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog', prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine_port.py`:

```python
def test_tracer_creates_tickets_table(tmp_path):
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    cols = {row[1] for row in t.conn.execute("PRAGMA table_info(tickets)")}
    assert {"id", "provider", "external_id", "title", "description", "status",
            "prompt_file", "adw_id", "source_url", "created_at", "updated_at"} <= cols
    t.conn.close()
```

(check the module import at the top of the test file — `from sssf.adw_modules import tracer as tracer_mod` — and add it if missing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine_port.py::test_tracer_creates_tickets_table -v`
Expected: FAIL (`no such table: tickets`)

- [ ] **Step 3: Add the table to SCHEMA**

In `src/sssf/adw_modules/tracer.py`, append to the `SCHEMA` string (after the `agent_sessions` table):

```sql
CREATE TABLE IF NOT EXISTS tickets (
  id          TEXT PRIMARY KEY,   -- 'jira:<key>' | 'linear:<id>' | 'internal:<uuid>'
  provider    TEXT NOT NULL,      -- jira | linear | internal
  external_id TEXT,               -- NULL for internal
  title       TEXT NOT NULL,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'backlog',   -- backlog | running | done | failed
  prompt_file TEXT,               -- adws/prompts/NN-<slug>.md once run
  adw_id      TEXT,               -- the run spawned for this ticket
  source_url  TEXT,               -- the ticket's origin link ('' for internal)
  created_at  TEXT, updated_at TEXT
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine_port.py::test_tracer_creates_tickets_table -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/adw_modules/tracer.py tests/test_engine_port.py
git commit -m "feat: tickets table in the tracer schema"
```

---

### Task 2: Ticketing config load (`sssf/ticketing.py`)

**Files:**
- Create: `src/sssf/ticketing.py`
- Create: `tests/test_ticketing.py`

**Interfaces:**
- Produces:
  - `TICKETING_FILE = "adws/adw_sssf_config/ticketing.yaml"`
  - `@dataclass TicketingConfig: providers: list[str]; jira: dict; linear: dict`
  - `load_config(root: Path) -> TicketingConfig | None` — `None` when the file is missing or has no `providers`; raises `RuntimeError("invalid {path}: …")` on YAML parse errors; returns a config whose `providers` is the `providers:` list (uncommented entries only, as parsed by yaml).

- [ ] **Step 1: Write the failing test**

`tests/test_ticketing.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ticketing.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.ticketing`)

- [ ] **Step 3: Write the minimal implementation**

`src/sssf/ticketing.py`:

```python
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

TICKETING_FILE = "adws/adw_sssf_config/ticketing.yaml"
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ticketing.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/ticketing.py tests/test_ticketing.py
git commit -m "feat: ticketing config load (opt-in, multi-provider)"
```

---

### Task 3: Provider adapters + sync + upsert

**Files:**
- Modify: `src/sssf/ticketing.py`
- Modify: `tests/test_ticketing.py`

**Interfaces:**
- Consumes: `TicketingConfig`, `TicketRecord`, `TICKETS_DDL` (Task 2).
- Produces:
  - `fetch_jira(cfg: TicketingConfig) -> list[TicketRecord]` — shells to `acli issue list --jql "<jql>" -f json`; raises `RuntimeError` with install/auth guidance when acli is missing or fails.
  - `fetch_linear(cfg: TicketingConfig) -> list[TicketRecord]` — reads `token_env` (default `LINEAR_TOKEN`) from env, POSTs a GraphQL query for the team's issues, applies the optional `states` list client-side.
  - `upsert_tickets(db_path: Path, records: list[TicketRecord]) -> int`
  - `sync_tickets(root: Path, cfg: TicketingConfig) -> list[ProviderSyncResult]` — loads `root/.env` (python-dotenv) first; iterates `cfg.providers`; a failing provider yields `ProviderSyncResult(provider, error=…)`, others still run.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticketing.py`:

```python
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
    assert calls[0][0] == ["acli", "issue", "list", "--jql", "project = ACME", "-f", "json"]
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
    monkeypatch.setattr(os, "environ", {"LINEAR_TOKEN": "tok"}, raising=False)
    records = ticketing.fetch_linear(_cfg(tmp_path))
    assert sent["auth"] == "Bearer tok"
    assert "team" in sent["body"]["query"] and "ENG" in sent["body"]["query"]
    assert [r.external_id for r in records] == ["ENG-3"]   # state filter keeps Backlog only


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticketing.py -v`
Expected: FAIL (`AttributeError: module 'sssf.ticketing' has no attribute 'fetch_jira'` etc.)

- [ ] **Step 3: Write the implementation**

Append to `src/sssf/ticketing.py`:

```python
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
    data = _run_acli(["issue", "list", "--jql", jql, "-f", "json"])
    issues = data if isinstance(data, list) else (data.get("issues") or data.get("data") or [])
    records = []
    for issue in issues:
        fields = issue.get("fields") or {}
        key = str(issue.get("key") or "")
        self_url = issue.get("self") or ""
        host = self_url.split("/")[2] if self_url.startswith("http") else ""
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
    db_path = root / "adws" / "adw_data" / "sssf.db"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticketing.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sssf/ticketing.py tests/test_ticketing.py
git commit -m "feat: ticketing adapters (acli jira, linear) + sync/upsert"
```

---

### Task 4: `sssf ticket` CLI

**Files:**
- Create: `src/sssf/commands/ticket.py`
- Modify: `src/sssf/cli.py`
- Create: `tests/test_ticket_cli.py`

**Interfaces:**
- Consumes: `ticketing.load_config/sync_tickets/upsert_tickets/TICKETS_DDL`, `project.find_project` (Task 2 of the original plan: `find_project(cwd, explicit) -> Path | None`).
- Produces:
  - `ticket.add(title: str, project: str | None) -> int`
  - `ticket.sync(project: str | None) -> int`
  - `ticket.list_tickets(project: str | None) -> int`
  - `ticket.run(ticket_id: str, project: str | None) -> int` — enumerates `adws/prompts/NN-<slug>.md`, writes the ticket content, spawns `adw_simple_sdlc` detached with a minted `--adw-id`, updates the ticket row to `running`.
  - `ticketing.next_prompt_name(root: Path, slug: str) -> Path` (in `ticketing.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_ticket_cli.py`:

```python
import sqlite3
import subprocess
from pathlib import Path

import pytest

from sssf import ticketing
from sssf.commands import ticket


def _project(tmp_path, monkeypatch, ticketing_yaml: str | None = None) -> Path:
    root = tmp_path / "proj"
    (root / "adws" / "adw_sssf_config").mkdir(parents=True)
    (root / "adws" / "adw_data").mkdir(parents=True)
    (root / "adws" / "prompts").mkdir(parents=True)
    if ticketing_yaml is not None:
        (root / "adws" / "adw_sssf_config" / "ticketing.yaml").write_text(ticketing_yaml)
    monkeypatch.chdir(root)
    return root


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "adw_data" / "sssf.db")
    conn.execute(ticketing.TICKETS_DDL)
    return conn


CONFIG = "providers:\n  - internal\n  - jira\njira:\n  jql: 'project = ACME'\n"


def test_add_internal_ticket(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    assert ticket.add("Ship dark mode", None) == 0
    conn = _db(root)
    row = conn.execute("SELECT provider, title, status FROM tickets").fetchone()
    conn.close()
    assert row == ("internal", "Ship dark mode", "backlog")
    assert "added" in capsys.readouterr().out


def test_add_requires_internal_provider(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, "providers:\n  - jira\njira:\n  jql: 'x'\n")
    assert ticket.add("nope", None) == 1
    assert "internal provider not enabled" in capsys.readouterr().err


def test_not_configured_is_friendly(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)   # no ticketing.yaml
    assert ticket.sync(None) == 1
    assert "not configured" in capsys.readouterr().err


def test_run_creates_prompt_and_spawns(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, description, status)"
                 " VALUES ('internal:abc', 'internal', '', 'Dark mode', 'Make it dark', 'backlog')")
    conn.commit()
    conn.close()
    (root / "adws" / "adw_simple_sdlc.py").write_text("print('adw stub')\n")
    spawned = {}
    def fake_popen(argv, **kw):
        spawned["argv"] = argv
        class P:
            pid = 12345
        return P()
    monkeypatch.setattr(ticket.subprocess, "Popen", fake_popen)
    assert ticket.run("internal:abc", None) == 0
    assert spawned["argv"][0] == ticket.sys.executable
    prompt = sorted((root / "adws" / "prompts").glob("*.md"))
    assert len(prompt) == 1
    text = prompt[0].read_text()
    assert "Dark mode" in text and "Make it dark" in text
    assert "--adw-id" in spawned["argv"]
    conn = _db(root)
    row = conn.execute("SELECT status, adw_id, prompt_file FROM tickets WHERE id='internal:abc'").fetchone()
    conn.close()
    assert row[0] == "running" and row[1] and row[2]


def test_run_rejects_already_running(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, CONFIG)
    conn = _db(root)
    conn.execute("INSERT INTO tickets (id, provider, external_id, title, status)"
                 " VALUES ('internal:abc', 'internal', '', 'X', 'running')")
    conn.commit()
    conn.close()
    assert ticket.run("internal:abc", None) == 1
    assert "already running" in capsys.readouterr().err


def test_next_prompt_name_enumerates(tmp_path):
    root = tmp_path / "proj"
    (root / "adws" / "prompts").mkdir(parents=True)
    for name in ("01-foo.md", "02-bar.md", "08-last.md"):
        (root / "adws" / "prompts" / name).write_text("x")
    assert ticketing.next_prompt_name(root, "baz").name == "09-baz.md"
    assert ticketing.next_prompt_name(root, "baz").exists() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticket_cli.py -v`
Expected: FAIL (`ModuleNotFoundError: sssf.commands.ticket`, `next_prompt_name` missing)

- [ ] **Step 3: Implement `next_prompt_name`**

Append to `src/sssf/ticketing.py`:

```python
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
```

- [ ] **Step 4: Implement `src/sssf/commands/ticket.py`**

```python
"""`sssf ticket` — ticketing integration (add / sync / list / run)."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sssf import ticketing
from sssf.project import find_project


def _root(explicit: str | None) -> Path | None:
    return find_project(Path.cwd(), explicit)


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "adws" / "adw_data" / "sssf.db")
    conn.execute(ticketing.TICKETS_DDL)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def add(title: str, project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None or "internal" not in cfg.providers:
        print("sssf ticket: the internal provider is not enabled in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    ticket_id = f"internal:{uuid.uuid4().hex[:12]}"
    now = _now()
    conn = _db(root)
    conn.execute(
        "INSERT INTO tickets (id, provider, external_id, title, description, status,"
        " source_url, created_at, updated_at) VALUES (?,?,'',?,'','backlog','',?,?)",
        (ticket_id, "internal", title, now, now))
    conn.commit()
    conn.close()
    print(f"sssf ticket: added internal ticket {title!r} ({ticket_id})")
    return 0


def sync(project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    results = ticketing.sync_tickets(root, cfg)
    for r in results:
        if r.error:
            print(f"sssf ticket: {r.provider}: {r.error}")
        else:
            print(f"sssf ticket: {r.provider}: {r.tickets} ticket(s) synced")
    return 0


def list_tickets(project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    conn = _db(root)
    rows = conn.execute(
        "SELECT id, provider, title, status, adw_id FROM tickets ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    for row in rows:
        print(f"{row[0]:20} {row[1]:8} {row[2][:50]:50} {row[3]:8} {row[4] or ''}")
    print(f"sssf ticket: {len(rows)} ticket(s)")
    return 0


def run(ticket_id: str, project: str | None = None) -> int:
    root = _root(project)
    if root is None:
        print("sssf: no project here (no adws/). Run `sssf init` first.", file=sys.stderr)
        return 1
    cfg = ticketing.load_config(root)
    if cfg is None:
        print("sssf ticket: ticketing not configured — enable it in "
              "adws/adw_sssf_config/ticketing.yaml", file=sys.stderr)
        return 1
    conn = _db(root)
    row = conn.execute(
        "SELECT id, title, description, status, provider, external_id, source_url"
        " FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        conn.close()
        print(f"sssf ticket: no ticket {ticket_id}", file=sys.stderr)
        return 1
    tid, title, description, status, provider, external_id, source_url = row
    if status == "running":
        conn.close()
        print(f"sssf ticket: {ticket_id} is already running", file=sys.stderr)
        return 1
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40] or "ticket"
    prompt_path = ticketing.next_prompt_name(root, slug)
    prompt_path.write_text(
        f"# {title}\n\n{description}\n\n---\n"
        f"Generated from {provider} ticket {external_id or ''} ({source_url})\n")
    adw_id = uuid.uuid4().hex[:8]
    adw_file = root / "adws" / "adw_simple_sdlc.py"
    if not adw_file.exists():
        conn.close()
        print(f"sssf ticket: no adws/adw_simple_sdlc.py in {root}", file=sys.stderr)
        return 1
    rel_prompt = prompt_path.relative_to(root)
    subprocess.Popen(
        [sys.executable, str(adw_file), f"run prompt {rel_prompt}", "--adw-id", adw_id],
        cwd=root, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    conn.execute("UPDATE tickets SET status='running', adw_id=?, prompt_file=? WHERE id=?",
                 (adw_id, str(rel_prompt), tid))
    conn.commit()
    conn.close()
    print(f"sssf ticket: run spawned for {ticket_id} — adw_id {adw_id}, prompt {rel_prompt}")
    return 0
```

- [ ] **Step 5: Register the subcommand in `src/sssf/cli.py`**

In `cli.py`, import and add after the `sweep` parser:

```python
from sssf.commands import init, misc, obs_cmds, run, sweep, ticket, viz
```

```python
    p_ticket = sub.add_parser("ticket", help="ticketing integration (add / sync / list / run)")
    tsub = p_ticket.add_subparsers(dest="ticket_action", required=True)
    p_add = tsub.add_parser("add", help="create an internal ticket")
    p_add.add_argument("title")
    p_add.add_argument("--project", default=None)
    p_sync = tsub.add_parser("sync", help="fetch external tickets into the backlog")
    p_sync.add_argument("--project", default=None)
    p_list = tsub.add_parser("list", help="list tickets")
    p_list.add_argument("--project", default=None)
    p_run = tsub.add_parser("run", help="spawn simple_sdlc for a ticket")
    p_run.add_argument("ticket_id")
    p_run.add_argument("--project", default=None)
    p_ticket.set_defaults(func=lambda a: _dispatch_ticket(a))
```

and at module level:

```python
def _dispatch_ticket(a) -> int:
    action = a.ticket_action
    if action == "add":
        return ticket.add(a.title, a.project)
    if action == "sync":
        return ticket.sync(a.project)
    if action == "list":
        return ticket.list_tickets(a.project)
    if action == "run":
        return ticket.run(a.ticket_id, a.project)
    return 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticket_cli.py -v`
Expected: 6 PASS

- [ ] **Step 7: Manual smoke (no external provider needed)**

```bash
cd /tmp && rm -rf tkt-smoke && mkdir tkt-smoke && cd tkt-smoke && git init -q
sssf init
# enable internal provider:
printf 'providers:\n  - internal\n' > adws/adw_sssf_config/ticketing.yaml
sssf ticket add "ship dark mode"
sssf ticket list
```

Expected: ticket added and listed with status `backlog`.

- [ ] **Step 8: Commit**

```bash
git add src/sssf/ticketing.py src/sssf/commands/ticket.py src/sssf/cli.py tests/test_ticket_cli.py
git commit -m "feat: sssf ticket add/sync/list/run"
```

---

### Task 5: `sssf init` stamps the commented template

**Files:**
- Create: `src/sssf/templates/ticketing.yaml`
- Modify: `src/sssf/commands/init.py`
- Modify: `tests/test_init.py`

**Interfaces:**
- Consumes: `init.run(root, refresh=, force=)` (existing).
- Produces: `adws/adw_sssf_config/ticketing.yaml` stamped (only when missing, or with `--force`), fully commented so it parses as "not configured".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_init.py`:

```python
def test_init_stamps_commented_ticketing_template(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    assert _run_init(root, monkeypatch) == 0
    cfg = root / "adws/adw_sssf_config/ticketing.yaml"
    assert cfg.exists()
    text = cfg.read_text()
    assert text.lstrip().startswith("#")          # fully commented
    assert "providers" in text
    # and it parses as "not configured":
    from sssf import ticketing
    assert ticketing.load_config(root) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_init.py::test_init_stamps_commented_ticketing_template -v`
Expected: FAIL (`assert cfg.exists()`)

- [ ] **Step 3: Create the template**

`src/sssf/templates/ticketing.yaml`:

```yaml
# Ticketing integration (optional). Uncomment to enable — the kanban Backlog
# stage stays hidden until this file configures at least one provider.
#
# providers:            # any subset of jira | linear | internal
#   - internal
#   # - jira
#   # - linear
#
# jira:                 # via the acli CLI (install and authenticate it first)
#   jql: 'project = ACME AND status in (Backlog, "To Do")'
#
# linear:               # token from the project .env (token_env)
#   team: ENG
#   token_env: LINEAR_TOKEN
#   states: [Backlog, "To Do"]
```

- [ ] **Step 4: Stamp it in `init.py`**

In `src/sssf/commands/init.py` `run()`, after the `config_dest` block:

```python
    ticket_dest = root / "adws" / "adw_sssf_config" / "ticketing.yaml"
    if not ticket_dest.exists() or force:
        ticket_dest.parent.mkdir(parents=True, exist_ok=True)
        ticket_dest.write_text((templates / "ticketing.yaml").read_text())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_init.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/sssf/templates/ticketing.yaml src/sssf/commands/init.py tests/test_init.py
git commit -m "feat: sssf init stamps the commented ticketing template"
```

---

### Task 6: Viz server tickets API

**Files:**
- Create: `src/sssf/apps/visualizer/server/tickets.ts`
- Modify: `src/sssf/apps/visualizer/server/index.ts`
- Create: `src/sssf/apps/visualizer/server/tickets.test.ts`

**Interfaces:**
- Consumes: `ProjectRegistry` (`list()`, `pathFor(name)`), `openReadonly` (from `db.ts`), the `tickets` table (Task 1).
- Produces:
  - `tickets.ts`: `isEnabled(root: string) -> boolean` (ticketing.yaml exists with an uncommented `providers:` line); `readTickets(dbPath: string) -> Ticket[]` (ensures the table, reconciles `running/done/failed` from the linked session); `Ticket` shape `{id, provider, external_id, title, description, status, prompt_file, adw_id, source_url}`.
  - Routes: `GET /api/projects/:project/tickets` → `{enabled, tickets}`; `POST …/tickets/sync` → shells `sssf ticket sync --project <root>`, returns `{ok, output}`; `POST …/tickets/:id/run` → shells `sssf ticket run <id> --project <root>`, returns `{ok, adwId, output}`.

- [ ] **Step 1: Write the failing bun test**

`src/sssf/apps/visualizer/server/tickets.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { readTickets } from "./tickets";

function makeDb(path: string): Database {
  const db = new Database(path);
  db.run(`CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
    title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
    prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`);
  db.run(`CREATE TABLE IF NOT EXISTS sessions (
    adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT)`);
  return db;
}

describe("readTickets", () => {
  test("reconciles status from the linked session", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:a", "internal", "", "running ticket", "running", "sess1");
    db.query("INSERT INTO sessions (adw_id, status) VALUES (?,?)").run("sess1", "success");
    db.close();

    const tickets = readTickets(dbPath);
    expect(tickets).toHaveLength(1);
    expect(tickets[0]!.status).toBe("done");          // session success => done
    expect(tickets[0]!.adw_id).toBe("sess1");
  });

  test("backlog tickets stay backlog", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-tickets-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status) VALUES (?,?,?,?,?)")
      .run("internal:b", "internal", "", "unrun", "backlog");
    db.close();
    expect(readTickets(dbPath)[0]!.status).toBe("backlog");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/sssf/apps/visualizer && bun test server/tickets.test.ts`
Expected: FAIL (`Cannot find module './tickets'`)

- [ ] **Step 3: Implement `server/tickets.ts`**

```ts
/** Ticketing: enabled check + backlog reads over a project's trace db. */
import { Database } from "bun:sqlite";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export interface Ticket {
  id: string;
  provider: string;
  external_id: string | null;
  title: string;
  description: string;
  status: string;
  prompt_file: string | null;
  adw_id: string | null;
  source_url: string;
}

const TICKETS_DDL = `CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
  title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
  prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`;

/** The feature is on when ticketing.yaml exists with an uncommented providers: line. */
export function isEnabled(root: string): boolean {
  const path = resolve(root, "adws", "adw_sssf_config", "ticketing.yaml");
  if (!existsSync(path)) return false;
  try {
    return readFileSync(path, "utf8")
      .split("\n")
      .some((line) => /^\s*providers\s*:/.test(line));
  } catch {
    return false;
  }
}

export function readTickets(dbPath: string): Ticket[] {
  const db = new Database(dbPath);
  try {
    db.run(TICKETS_DDL);
    const rows = db.query<any[], []>(
      "SELECT id, provider, external_id, title, description, status, prompt_file, adw_id, source_url"
      + " FROM tickets ORDER BY created_at DESC, rowid DESC",
    ).all();
    return rows.map((row) => {
      let status = row.status as string;
      if (row.adw_id) {
        const s = db.query<{ status: string }, [string]>(
          "SELECT status FROM sessions WHERE adw_id = ?",
        ).get(row.adw_id);
        if (s) status = s.status === "success" ? "done" : s.status === "fail" ? "failed" : "running";
      }
      return { ...row, status, source_url: row.source_url ?? "" };
    });
  } finally {
    db.close();
  }
}
```

- [ ] **Step 4: Wire the routes in `server/index.ts`**

Add to the imports:

```ts
import { isEnabled, readTickets } from "./tickets.ts";
```

Add a project-root resolver next to `dbForProject`:

```ts
function projectRoot(name: string): string | null {
  return projects.list().find((p) => p.name === name)?.root ?? null;
}
```

Register (before the static fallthrough, inside `routes`):

```ts
    "/api/projects/:project/tickets": scoped((req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root) return notFound(`no project ${name}`);
      const db = dbForProject(name);
      if (!db) return notFound("no trace db for project");
      return json({ enabled: isEnabled(root), tickets: readTickets(db.path) });
    }),
    "/api/projects/:project/tickets/sync": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      if (!root || !isEnabled(root)) return json({ error: "ticketing not configured" }, 400);
      const proc = Bun.spawn(["sssf", "ticket", "sync", "--project", root],
        { stdout: "pipe", stderr: "pipe" });
      const output = await new Response(proc.stdout).text();
      await proc.exited;
      return json({ ok: proc.exitCode === 0, output });
    }),
    "/api/projects/:project/tickets/:id/run": scoped(async (req) => {
      const name = param(req, "project");
      const root = projectRoot(name);
      const id = param(req, "id");
      if (!root || !isEnabled(root)) return json({ error: "ticketing not configured" }, 400);
      const proc = Bun.spawn(["sssf", "ticket", "run", id, "--project", root],
        { stdout: "pipe", stderr: "pipe" });
      const output = await new Response(proc.stdout).text();
      await proc.exited;
      if (proc.exitCode !== 0) return json({ ok: false, output }, 409);
      const adwId = output.match(/adw_id ([a-f0-9]+)/)?.[1] ?? null;
      return json({ ok: true, adwId, output });
    }),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/sssf/apps/visualizer && bun test server/tickets.test.ts`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
cd ~/dev/lab/mvp/sssf && git add -A
git commit -m "feat: viz tickets api (read/reconcile, sync + run shell to the CLI)"
```

---

### Task 7: Kanban backlog — ticket cards + modal

**Files:**
- Modify: `src/sssf/apps/visualizer/src/lib/api.ts`
- Modify: `src/sssf/apps/visualizer/src/components/KanbanBoard.vue`
- Create: `src/sssf/apps/visualizer/src/components/TicketCard.vue`
- Create: `src/sssf/apps/visualizer/src/components/TicketModal.vue`

**Interfaces:**
- Consumes: `base()` from `api.ts` (project-scoped prefix), `Ticket` shape (Task 6).
- Produces: `api.fetchTickets() -> Promise<{enabled, tickets}>`; `api.runTicket(id) -> Promise<{ok, adwId?, output?}>`; `api.syncTickets() -> Promise<{ok, output?}>`. `KanbanBoard` renders the Backlog column only when `enabled`, listing `TicketCard`s; clicking a card opens `TicketModal` (Run | Close); Run calls `runTicket` then refetches; a sync (refresh) button sits in the Backlog header.

- [ ] **Step 1: Add the API client functions**

In `src/sssf/apps/visualizer/src/lib/api.ts`, after `runSweep`:

```ts
export interface Ticket {
  id: string
  provider: string
  external_id: string | null
  title: string
  description: string
  status: string
  prompt_file: string | null
  adw_id: string | null
  source_url: string
}

export interface TicketsResponse {
  enabled: boolean
  tickets: Ticket[]
}

export async function fetchTickets(): Promise<TicketsResponse> {
  return getJson(`${base()}/tickets`) as Promise<TicketsResponse>
}

export async function runTicket(id: string): Promise<{ ok: boolean; adwId?: string; output?: string }> {
  const res = await fetch(`${base()}/tickets/${encodeURIComponent(id)}/run`, { method: 'POST' })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; adwId?: string; output?: string }
  return { ok: data.ok ?? res.ok, adwId: data.adwId, output: data.output }
}

export async function syncTickets(): Promise<{ ok: boolean; output?: string }> {
  const res = await fetch(`${base()}/tickets/sync`, { method: 'POST' })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; output?: string }
  return { ok: data.ok ?? res.ok, output: data.output }
}
```

- [ ] **Step 2: Create `TicketCard.vue`**

```vue
<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import type { Ticket } from '../lib/api'

defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ open: [ticket: Ticket] }>()

const BADGE: Record<string, string> = { jira: 'J', linear: 'L', internal: '⚙' }
</script>

<template>
  <button class="ticket" type="button" @click="emit('open', ticket)">
    <span class="badge">{{ BADGE[ticket.provider] ?? '?' }}</span>
    <span class="t-title">{{ ticket.title }}</span>
    <span class="t-meta dim">
      {{ ticket.external_id || ticket.id }} · {{ ticket.status }}
    </span>
  </button>
</template>

<style scoped>
.ticket {
  display: block;
  width: 100%;
  text-align: left;
  background: rgba(11, 15, 24, 0.66);
  border: 1px dashed rgba(232, 182, 74, 0.5);   /* distinct from session cards */
  border-radius: 12px;
  padding: 12px 14px;
  color: var(--text);
  cursor: pointer;
}
.ticket:hover { border-color: rgba(232, 182, 74, 0.9); }
.badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 6px; margin-right: 8px;
  background: rgba(232, 182, 74, 0.18); color: #e8b64a; font-weight: 700; font-size: 13px;
}
.t-title { font-weight: 700; font-size: 14px; }
.t-meta { display: block; margin-top: 4px; font-size: 12px; }
</style>
```

- [ ] **Step 3: Create `TicketModal.vue`**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { Archive, ExternalLink, Play, X } from 'lucide-vue-next'
import type { Ticket } from '../lib/api'
import { runTicket } from '../lib/api'

const props = defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ close: []; ran: [adwId: string | null] }>()

const running = ref(false)
const error = ref('')

async function run() {
  running.value = true
  error.value = ''
  try {
    const res = await runTicket(props.ticket.id)
    if (!res.ok) {
      error.value = res.output ?? 'run failed'
    } else {
      emit('ran', res.adwId ?? null)
      emit('close')
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true" aria-label="ticket">
      <header class="m-head">
        <span class="badge">{{ { jira: 'J', linear: 'L', internal: '⚙' }[ticket.provider] ?? '?' }}</span>
        <span class="m-title">{{ ticket.title }}</span>
        <button class="icon" type="button" aria-label="Close" @click="emit('close')"><X :size="18" /></button>
      </header>

      <p class="m-origin dim">
        {{ ticket.external_id || ticket.id }} · {{ ticket.status }}
        <a v-if="ticket.source_url" :href="ticket.source_url" target="_blank" rel="noreferrer">
          <ExternalLink :size="13" /> source
        </a>
      </p>

      <div class="m-body">{{ ticket.description || 'no description' }}</div>

      <p v-if="ticket.adw_id" class="m-link dim">run: <code>{{ ticket.adw_id }}</code></p>
      <p v-if="error" class="m-error">{{ error }}</p>

      <footer class="m-foot">
        <button class="btn ghost" type="button" @click="emit('close')">Close</button>
        <button class="btn primary" type="button" :disabled="running" @click="run">
          <Play :size="15" /> {{ running ? 'Starting…' : 'Run' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed; inset: 0; z-index: 70;
  background: rgba(0, 0, 0, 0.55);
  display: flex; align-items: center; justify-content: center;
}
.modal {
  width: min(560px, 92vw); max-height: 80vh; overflow: auto;
  background: #0b0f18; border: 1px solid var(--border); border-radius: 14px;
  padding: 18px 20px;
}
.m-head { display: flex; align-items: center; gap: 10px; }
.badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border-radius: 6px; flex: none;
  background: rgba(232, 182, 74, 0.18); color: #e8b64a; font-weight: 700;
}
.m-title { font-weight: 700; font-size: 17px; flex: 1; }
.icon { background: none; border: none; color: var(--faint); cursor: pointer; }
.m-origin { display: flex; align-items: center; gap: 10px; margin: 8px 0 0; font-size: 13px; }
.m-origin a { display: inline-flex; align-items: center; gap: 4px; color: var(--purple); }
.m-body { margin-top: 14px; font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.m-link code { background: rgba(255,255,255,0.06); padding: 1px 6px; border-radius: 4px; }
.m-error { color: var(--red); font-size: 13px; }
.m-foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border);
  background: rgba(255,255,255,0.04); color: var(--text); cursor: pointer;
}
.btn.primary { background: rgba(200, 155, 255, 0.2); border-color: rgba(200, 155, 255, 0.5); }
.btn:disabled { opacity: 0.5; cursor: default; }
</style>
```

- [ ] **Step 4: Wire the backlog column into `KanbanBoard.vue`**

Script additions:

```ts
import { fetchTickets, runTicket, syncTickets, type Ticket, type TicketsResponse } from '../lib/api'
import TicketCard from './TicketCard.vue'
import TicketModal from './TicketModal.vue'

const tickets = ref<TicketsResponse>({ enabled: false, tickets: [] })
const activeTicket = ref<Ticket | null>(null)
const syncing = ref(false)

async function pullTickets() {
  if (!selectedProject.value) return      // import { useProjects } — adhoc mode has no backlog
  try {
    tickets.value = await fetchTickets()
  } catch {
    tickets.value = { enabled: false, tickets: [] }
  }
}

async function onSync() {
  syncing.value = true
  try { await syncTickets() } finally { syncing.value = false }
  void pullTickets()
}
```

(`selectedProject` comes from `useProjects()` — import it; in adhoc mode there is no project scope, so the backlog stays hidden.)

Make `COLUMNS` dynamic — a computed:

```ts
const columns = computed(() =>
  tickets.value.enabled
    ? COLUMNS
    : COLUMNS.filter((c) => c.key !== 'backlog'),
)
```

Replace `v-for="col in COLUMNS"` with `v-for="col in columns"`, and render the backlog body from tickets when `col.key === 'backlog'`:

```html
<div v-if="!collapsed[col.key]" class="cards">
  <template v-if="col.key === 'backlog'">
    <TicketCard
      v-for="t in tickets.tickets.filter((x) => x.status === 'backlog')"
      :key="t.id"
      :ticket="t"
      @open="activeTicket = $event"
    />
    <button v-if="tickets.tickets.length" class="sync-link" type="button" :disabled="syncing" @click="onSync">
      <RefreshCw :size="13" /> {{ syncing ? 'syncing…' : 'refresh' }}
    </button>
  </template>
  <template v-else>
    ...existing session card markup...
  </template>
</div>
```

(import `RefreshCw` from `lucide-vue-next`; add a `.sync-link` style — small, dim, right-aligned.)

Call `pullTickets()` in `onMounted` and inside `tick()` (after `fetchSessions` succeeds, fire-and-forget), and add the modal at the end of the template:

```html
<TicketModal
  v-if="activeTicket"
  :ticket="activeTicket"
  @close="activeTicket = null"
  @ran="void pullTickets()"
/>
```

- [ ] **Step 5: Verify — typecheck + build**

Run: `cd src/sssf/apps/visualizer && bun run typecheck && bun run lint && bun run build`
Expected: clean, bundle built

- [ ] **Step 6: Manual smoke — full loop**

```bash
cd /tmp/tkt-smoke && sssf viz start --port 4607
# board shows NO backlog column (ticketing off)
# enable internal + add a ticket:
printf 'providers:\n  - internal\n' > adws/adw_sssf_config/ticketing.yaml
sssf ticket add "ship dark mode"
# reload board: Backlog column appears with the ticket; open it, click Run
curl -s localhost:4607/api/projects/tkt-smoke/tickets
```

Expected: `{enabled: true, tickets: [...]}` with the internal ticket; the run spawns `adw_simple_sdlc`, the ticket leaves the backlog, and the session appears in Planning. Then `sssf viz stop`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: kanban backlog — ticket cards, content modal (Run | Close), refresh"
```

---

### Task 8: End-to-end verification + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md` (summary row + index)
- Modify: `README.md` (commands table row for `sssf ticket`)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q` then `cd src/sssf/apps/visualizer && bun test && bun run typecheck && bun run build`
Expected: all green (previous totals + the new tests)

- [ ] **Step 2: Update README commands table**

Add a row:

```markdown
| `sssf ticket add/sync/list/run [--project]` | ticketing integration (internal add, external sync, backlog run) — optional, per-project `adws/adw_sssf_config/ticketing.yaml` |
```

- [ ] **Step 3: Update the revisions docs**

Add to `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`:
- summary-table row for the ticketing subsystem (with the commit hash from Task 7)
- the per-feature index gains `2026-08-15-ticketing-{design,plan}.md`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: ticketing in README + revisions record"
```

---

## Self-Review

**Spec coverage:**
- §1 config (opt-in, multi-provider, acli jira, linear token) → Tasks 2, 3, 5.
- §2 tickets table + origin + lifecycle + one-stage invariant → Tasks 1, 3, 4 (run leaves backlog), 6 (reconcile).
- §3 adapters (acli wrapper, linear, internal) → Task 3.
- §4 CLI (sync/add/list) → Task 4; run is Task 4 as well.
- §5 kanban (enabled-gated backlog, ticket cards, modal Run|Close, refresh) → Task 7.
- §6 run flow (enumerated prompt, spawn, leave backlog) → Tasks 4 (CLI) + 6 (route shells).
- §7 routes (GET enabled+tickets, sync, run) → Task 6.
- §8 errors (not-configured, acli missing/auth, per-provider skip, already-running 409) → Tasks 3, 4, 6.
- §9 cut list (no write-back, no polling, read-only) → respected throughout.
- §10 verification → Tasks 3–7 tests + Task 8.

**Placeholder scan:** every step carries real code; no TBD/TODO/"similar to".

**Type consistency:** `TicketRecord(provider, external_id, title, description, source_url)` (Task 3) ↔ `Ticket` TS shape (Task 6, extra fields `status/prompt_file/adw_id`) — the CLI writes those extra columns; `upsert_tickets(db, records) -> int` (Task 3) used in `sync_tickets` and tested directly; `next_prompt_name(root, slug) -> Path` (Task 4) used by `ticket.run`; `load_config(root) -> TicketingConfig | None` used by all CLI commands and tested; `isEnabled(root)`/`readTickets(dbPath)` (Task 6) used by the GET route; `fetchTickets/runTicket/syncTickets` (Task 7) match the route shapes.
