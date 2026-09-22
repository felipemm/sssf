#!/usr/bin/env python3
"""Generate the schema contract's derived artifacts from src/sssf/db_schema.py:

- schema/schema.sql          — greppable DDL snapshot (derived, not authoritative)
- shared/rows.generated.ts   — TS row types + status unions, single-sourced

The pydantic Row models in db_schema.py are the source of truth; these
artifacts are emitted from them. `--check` exits non-zero on any drift (the
pytest gate tests/test_gen_viz_types.py enforces it in CI). No new toolchain:
JSON Schema inspection is enough — field annotations map 1:1 to TS types.

Run from the repo root: python scripts/gen_viz_types.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sssf import db_schema  # noqa: E402

SCHEMA_SQL_PATH = ROOT / "schema" / "schema.sql"
ROWS_TS_PATH = ROOT / "src" / "sssf" / "apps" / "visualizer" / "shared" / "rows.generated.ts"

# Named TS unions emitted for these (model, field) literal columns, with the
# docs shared/types.ts used to carry.
UNION_NAMES: dict[tuple[str, str], str] = {
    ("sessions", "status"): "SessionStatus",
    ("phases", "status"): "PhaseStatus",
    ("phases", "kind"): "PhaseKind",
    ("events", "type"): "EventType",
}
UNION_DOCS: dict[str, str] = {
    "SessionStatus": "sessions.status — a run is running until it earns success.",
    "PhaseStatus": "phases.status — queued only for manifest-declared phases not yet entered.",
    "PhaseKind": "phases.kind — decides which lane a block renders in.",
    "EventType": "events.type — every type the engine emits (the viz union was missing `integration`; this generated union fixed it).",
}

# Field JSDoc carried over from the hand-written types, so the generated file
# stays the doc home for row fields.
FIELD_DOCS: dict[str, str] = {
    "SessionsRow.adw_name": 'ADW script(s) that ran this session, e.g. "adw_plan + adw_build_test".',
    "SessionsRow.archived": "1 once archived out of the review list. Review state, not run state. Null on dbs predating the column.",
    "EnvelopesRow.output_type": "Name of the data_types model the response was parsed against.",
    "EnvelopesRow.valid": "SQLite integer boolean.",
    "GateResultsRow.passed": "SQLite integer boolean.",
    "GateResultsRow.checks_json": "JSON array of GateCheck — the per-item evidence behind the verdict. Null on dbs predating the column.",
    "AgentSessionsRow.color": 'The agent\'s lane color from sssf.config.yaml, e.g. "#a78bfa". Null on dbs predating the column.',
    "AgentSessionsRow.context_tokens": "Window occupancy after the agent's last turn. Null on dbs predating the column.",
    "AgentSessionsRow.context_window": "The model's context ceiling; 0/NULL = unknown. Null on dbs predating the column.",
    "EventsRow.rowid": "SQLite rowid — the stable ordering key the reader selects.",
    "TicketsRow.tracked": "Permanent origin-of-creation flag: plan-created tickets are tracked; synced tickets are not.",
    "TicketsRow.origin": "internal | jira | gitlab | github.",
    "TicketsRow.kind": "idea | implementation.",
    "SandboxRunRow.status": "'up' | 'stopped'.",
}


# ── TS rendering ────────────────────────────────────────────────────────────

def _literal_members(ann: Any) -> list[str] | None:
    """Literal members, unwrapping Optional (Literal[...] | None)."""
    origin = get_origin(ann)
    if origin is not None and origin in (Union, UnionType):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        ann = non_none[0] if len(non_none) == 1 else ann
    if get_origin(ann) is Literal:
        return [str(a) for a in get_args(ann)]
    return None


def ts_type(table: str, field_name: str, annotation: Any, field: Any) -> str:
    """The field's TS type. Nullable for Optional fields AND for version-gated
    columns the reader degrades (VERSION_GATED_COLUMNS)."""
    meta = field.json_schema_extra or {}
    origin = get_origin(annotation)
    nullable = origin is not None and type(None) in get_args(annotation)
    base = annotation
    if origin is not None and origin in (Union, UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        base = non_none[0] if len(non_none) == 1 else annotation

    union_name = UNION_NAMES.get((table, field_name))
    if union_name:
        t = union_name
    elif (members := _literal_members(base)) is not None:
        t = " | ".join(f'"{m}"' for m in members)
    elif base is str:
        t = "string"
    elif base is int or base is float or base is bool:
        t = "number"
    else:
        raise TypeError(f"gen_viz_types: no TS type for {base!r}")

    # autoincrement pk: sqlite always fills it — never null in a read row.
    if meta.get("pk") and meta.get("autoincrement"):
        return "number"
    if meta.get("virtual"):
        return "number"
    # read-guaranteed: the reader always populates it despite a nullable column.
    if meta.get("read"):
        return t
    if nullable or f"{table}.{field_name}" in db_schema.VERSION_GATED_COLUMNS:
        return f"{t} | null"
    return t


def render_ts() -> str:
    out: list[str] = [
        "// GENERATED by scripts/gen_viz_types.py — do not edit.",
        "// Row types + status unions, single-sourced from src/sssf/db_schema.py.",
        "// shared/types.ts re-exports these under the UI-facing names.",
        "// Drift fails CI (tests/test_gen_viz_types.py regenerates and diffs).",
        "",
    ]
    # unions first (interfaces reference them)
    for (table, field_name), union in UNION_NAMES.items():
        model = db_schema.TABLES[table]
        ann = get_type_hints(model)[field_name]
        members = _literal_members(ann) or []
        doc = UNION_DOCS.get(union, "")
        out.append(f"/** {doc} */")
        out.append(f'export type {union} = {" | ".join(f"{m!r}" for m in members)};')
        out.append("")
    for table, model in db_schema.TABLES.items():
        name = model.__name__
        out.append(f"/** `{table}` table — generated from db_schema.{name}. */")
        out.append(f"export interface {name} {{")
        for field_name, field in model.model_fields.items():
            ann = get_type_hints(model)[field_name]
            doc = FIELD_DOCS.get(f"{name}.{field_name}")
            if doc:
                out.append(f"  /** {doc} */")
            out.append(f"  {field_name}: {ts_type(table, field_name, ann, field)};")
        out.append("}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _table_for(model_name: str) -> str:
    for table, model in db_schema.TABLES.items():
        if model.__name__ == model_name:
            return table
    raise KeyError(model_name)

def render_schema_sql() -> str:
    out = [
        "-- GENERATED by scripts/gen_viz_types.py — do not edit.",
        "-- Derived snapshot of the schema contract (src/sssf/db_schema.py).",
        "-- The pydantic models are authoritative; this file is for humans and",
        "-- DB tools. Additive changes land in db_schema.MIGRATIONS, not here.",
        "",
    ]
    for table, model in db_schema.TABLES.items():
        out.append(db_schema.create_table(table, model))
        out.append("")
    for name, table, columns in db_schema.INDEXES:
        out.append(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({', '.join(columns)});")
    return "\n".join(out).rstrip() + "\n"


# ── write / check ───────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if any artifact drifts")
    args = parser.parse_args()

    artifacts = {
        SCHEMA_SQL_PATH: render_schema_sql(),
        ROWS_TS_PATH: render_ts(),
    }
    drifted: list[str] = []
    for path, content in artifacts.items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                drifted.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            print(f"generated {path}")
    if drifted:
        print("drift:", *drifted, sep="\n  ")
        print("Run scripts/gen_viz_types.py to regenerate.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
