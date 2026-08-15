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
