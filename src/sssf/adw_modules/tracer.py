"""Tracer: every event lands in JSONL and SQLite AS IT HAPPENS.

Files are the raw record; sssf.db is the queryable mirror the UI polls.
No push transport — the flow is always: agents -> sqlite -> web ui.
WAL mode so the UI can read while ADW processes write.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from sssf import db_schema

from .data_types import AgentConfig, EventRecord, GateReport, Phase
from .utils import ensure_dir, new_id, now_iso


def _write(
    conn: sqlite3.Connection,
    table: str,
    model: type[db_schema.Row],
    row: dict,
    on_conflict: str = "",
) -> None:
    """Validate a row against the contract, then INSERT it with the column
    list taken from the validated model — the write cannot invent columns
    or drift from the declared shape."""
    validated = model.model_validate(row)
    cols = list(validated.model_fields_set)
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) {on_conflict}",
        tuple(getattr(validated, c) for c in cols),
    )


def _validate(model: type[db_schema.Row], values: dict) -> None:
    """Validate a partial UPDATE's SET values against the contract model."""
    model.model_validate(values)





class Tracer:
    def __init__(self, db_path: str | Path, events_jsonl: str | Path):
        ensure_dir(Path(db_path).parent)
        self.db_path = str(db_path)
        self.events_jsonl = Path(events_jsonl)
        ensure_dir(self.events_jsonl.parent)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        # WAL normally lets the UI read while ADW processes write. Inside a
        # sandbox (docker bind mount) the WAL file does not propagate to the
        # host promptly, so use rollback journal mode there — every commit
        # rewrites the main file, which the host sees immediately. The
        # busy_timeout (set below) serializes concurrent sandbox writers.
        mode = "DELETE" if os.environ.get("SSSF_IN_SANDBOX") else "WAL"
        self.conn.execute(f"PRAGMA journal_mode={mode};")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        db_schema.apply_schema(self.conn)

    # ── events ──────────────────────────────────────────────────────────────
    def event(self, record: EventRecord) -> str:
        event_id = f"evt_{new_id(12)}"
        ts = now_iso()
        line = {"event_id": event_id, "ts": ts, **record.model_dump()}
        with self.events_jsonl.open("a") as f:
            f.write(json.dumps(line) + "\n")
        _write(
            self.conn,
            "events",
            db_schema.EventsRow,
            {
                "event_id": event_id,
                "adw_id": record.adw_id,
                "phase_id": record.phase_id,
                "parent_id": record.parent_id,
                "type": record.type,
                "name": record.name,
                "payload_json": json.dumps(record.payload),
                "tokens": record.tokens,
                "started_at": record.started_at or ts,
                "ended_at": record.ended_at,
            },
        )
        return event_id

    # ── sessions ────────────────────────────────────────────────────────────
    def session_start(self, adw_id: str, engineer: str, adw_name: str | None = None) -> None:
        # A re-run of the same adw_id is a NEW generation of the session: the
        # previous attempt's terminal state must not linger on the row. Leaving
        # the old ended_at set while flipping status to 'running' produced a
        # contradictory per-run row, and the monitor's forward-merge (which
        # lets only a STRICTLY NEWER ended_at supersede) then froze the host
        # row at the previous attempt's failure for the whole re-run — the
        # kanban card sat in Blocked while the new run progressed (session
        # 2e3d7693). Clear ended_at and re-stamp started_at: a fresh process is
        # a fresh run generation.
        _write(
            self.conn,
            "sessions",
            db_schema.SessionsRow,
            {"adw_id": adw_id, "status": "running", "engineer": engineer, "started_at": now_iso()},
            on_conflict=(
                "ON CONFLICT(adw_id) DO UPDATE SET status=excluded.status,"
                " started_at=excluded.started_at, ended_at=NULL"
            ),
        )
        if not adw_name:
            return
        # A joined session chains ADWs — record each distinct one, in run order.
        row = self.conn.execute(
            "SELECT adw_name FROM sessions WHERE adw_id=?", (adw_id,)
        ).fetchone()
        names = row[0].split(" + ") if row and row[0] else []
        if adw_name not in names:
            names.append(adw_name)
            self.conn.execute(
                "UPDATE sessions SET adw_name=? WHERE adw_id=?", (" + ".join(names), adw_id)
            )

    def session_request(self, adw_id: str, request: str) -> None:
        # Full prompt, not truncated: `sssf sandbox restart` re-runs this exact ask.
        _validate(db_schema.SessionsRow, {"adw_id": adw_id, "request": request})
        self.conn.execute("UPDATE sessions SET request=? WHERE adw_id=?", (request, adw_id))

    def session_finish(self, adw_id: str, ok: bool) -> None:
        _validate(db_schema.SessionsRow, {"adw_id": adw_id, "status": "success" if ok else "fail"})
        self.conn.execute(
            "UPDATE sessions SET status=?, ended_at=? WHERE adw_id=?",
            ("success" if ok else "fail", now_iso(), adw_id),
        )
        self.processes_end_all(adw_id)  # nothing of this run is alive any more

    def session_add_usage(self, adw_id: str, tokens: int, cost: float) -> None:
        _validate(db_schema.SessionsRow, {"adw_id": adw_id, "total_tokens": tokens, "total_cost": cost})
        self.conn.execute(
            "UPDATE sessions SET total_tokens=total_tokens+?, total_cost=total_cost+? WHERE adw_id=?",
            (tokens, cost, adw_id),
        )

    # ── processes (adw_id → pid, so a hung run can be found and killed) ─────
    def process_start(self, adw_id: str, kind: str, name: str, pid: int, command: str) -> None:
        """Record a live process for this run.

        A coding agent that hangs produces no events at all, which is exactly
        when you need its pid — and `ps` cannot tell you which adw_id it
        belongs to. Writing it here makes the trace the answer to "what is this
        run running, and how do I stop it".
        """
        _write(
            self.conn,
            "processes",
            db_schema.ProcessesRow,
            {
                "adw_id": adw_id,
                "kind": kind,
                "name": name,
                "pid": pid,
                "command": command[:500],
                "started_at": now_iso(),
            },
        )

    def process_end(self, adw_id: str, pid: int) -> None:
        """Mark the newest live row for this pid as finished."""
        _validate(db_schema.ProcessesRow, {"adw_id": adw_id, "ended_at": now_iso()})
        self.conn.execute(
            "UPDATE processes SET ended_at=? WHERE id = ("
            "  SELECT id FROM processes WHERE adw_id=? AND pid=? AND ended_at IS NULL"
            "  ORDER BY id DESC LIMIT 1)",
            (now_iso(), adw_id, pid),
        )

    def processes_end_all(self, adw_id: str) -> None:
        """Close out every live row for a run — called when the session ends."""
        _validate(db_schema.ProcessesRow, {"adw_id": adw_id, "ended_at": now_iso()})
        self.conn.execute(
            "UPDATE processes SET ended_at=? WHERE adw_id=? AND ended_at IS NULL",
            (now_iso(), adw_id),
        )

    # ── phases ──────────────────────────────────────────────────────────────
    def max_phase_seq(self, adw_id: str) -> int:
        """Highest seq already recorded for this session; 0 when it is new.

        A joined run continues the sequence instead of restarting at 1 — which
        would collide with the first run's phases on both `seq` (breaking
        ordering) and `phase_id` (silently overwriting a row through the
        phase_upsert conflict clause).
        """
        row = self.conn.execute(
            "SELECT MAX(seq) FROM phases WHERE adw_id = ?", (adw_id,)
        ).fetchone()
        return row[0] if row and row[0] is not None else 0

    def phase_upsert(self, phase: Phase) -> None:
        p = phase.params
        _write(
            self.conn,
            "phases",
            db_schema.PhasesRow,
            {
                "phase_id": phase.phase_id,
                "adw_id": phase.adw_id,
                "seq": phase.seq,
                "name": p.name,
                "kind": p.kind,
                "owner": p.owner,
                "description": p.description,
                "status": phase.status,
                "attempt": phase.attempt,
                "retries": p.retries,
                "error": phase.error,
                "started_at": phase.started_at,
                "ended_at": phase.ended_at,
            },
            on_conflict=(
                "ON CONFLICT(phase_id) DO UPDATE SET status=excluded.status,"
                " attempt=excluded.attempt, error=excluded.error, ended_at=excluded.ended_at"
            ),
        )

    # ── envelopes / gates / agent sessions ──────────────────────────────────
    def envelope_row(
        self,
        phase: Phase,
        agent: str,
        output_type: str,
        payload_json: str,
        valid: bool,
        attempt: int,
    ) -> None:
        _write(
            self.conn,
            "envelopes",
            db_schema.EnvelopesRow,
            {
                "envelope_id": f"env_{new_id(12)}",
                "adw_id": phase.adw_id,
                "phase_id": phase.phase_id,
                "agent": agent,
                "output_type": output_type,
                "payload_json": payload_json,
                "valid": int(valid),
                "attempt": attempt,
                "created_at": now_iso(),
            },
        )

    def gate_row(self, phase: Phase, gate: str, report: GateReport, attempt: int) -> None:
        """The report carries both the verdict and the evidence behind it."""
        _write(
            self.conn,
            "gate_results",
            db_schema.GateResultsRow,
            {
                "adw_id": phase.adw_id,
                "phase_id": phase.phase_id,
                "attempt": attempt,
                "gate": gate,
                "passed": int(report.passed),
                "violations_json": json.dumps(report.violations),
                "checks_json": json.dumps([c.model_dump() for c in report.checks]),
                "created_at": now_iso(),
            },
        )

    def agent_session_row(
        self,
        adw_id: str,
        agent: AgentConfig,
        session_id: str,
        context_tokens: int = 0,
        context_window: int = 0,
    ) -> None:
        """The agent's config row is the source of truth for its label and color.

        Context is carried here rather than derived from events because the lane
        wants one number per agent — the latest — and a session that runs the
        same agent twice overwrites it, exactly like model and session_id.
        """
        ts = now_iso()
        _write(
            self.conn,
            "agent_sessions",
            db_schema.AgentSessionsRow,
            {
                "adw_id": adw_id,
                "agent": agent.name,
                "coding_agent": agent.coding_agent,
                "model": agent.model,
                "color": agent.color,
                "session_id": session_id,
                "context_tokens": context_tokens,
                "context_window": context_window,
                "created_at": ts,
                "last_used_at": ts,
            },
            on_conflict=(
                "ON CONFLICT(adw_id, agent) DO UPDATE SET model=excluded.model,"
                " color=excluded.color, session_id=excluded.session_id,"
                " context_tokens=excluded.context_tokens,"
                " context_window=excluded.context_window,"
                " last_used_at=excluded.last_used_at"
            ),
        )