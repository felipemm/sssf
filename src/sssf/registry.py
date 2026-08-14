"""Global project registry: ~/.sssf/projects.json.

Runtime state, not config. `sssf init` registers, `sssf run` refreshes
last_run, `sssf projects` lists/removes, `sssf viz` serves over it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def registry_path() -> Path:
    return Path(os.environ.get("SSSF_REGISTRY", str(Path.home() / ".sssf" / "projects.json")))


def load_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {"version": SCHEMA_VERSION, "projects": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": SCHEMA_VERSION, "projects": []}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return {"version": SCHEMA_VERSION, "projects": []}
    data.setdefault("version", SCHEMA_VERSION)
    return data


def save_registry(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _entry(projects: list[dict], name: str) -> dict | None:
    return next((p for p in projects if p.get("name") == name), None)


def register_project(root: Path, db_path: Path, tool_version: str, *, added: bool = False) -> dict:
    root = root.resolve()
    name = root.name
    data = load_registry()
    entry = _entry(data["projects"], name)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if entry is None:
        entry = {"name": name, "root": str(root), "db": str(db_path.resolve()),
                 "added": now, "last_run": None, "tool_version": tool_version}
        data["projects"].append(entry)
    else:
        entry["root"] = str(root)
        entry["db"] = str(db_path.resolve())
        entry["tool_version"] = tool_version
        if added and entry.get("added") is None:
            entry["added"] = now
    save_registry(data)
    return entry


def update_last_run(root: Path) -> None:
    root = root.resolve()
    data = load_registry()
    entry = _entry(data["projects"], root.name)
    if entry is None:
        return
    entry["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_registry(data)


def list_projects() -> list[dict]:
    return load_registry()["projects"]


def remove_project(name: str) -> bool:
    data = load_registry()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p.get("name") != name]
    if len(data["projects"]) == before:
        return False
    save_registry(data)
    return True
