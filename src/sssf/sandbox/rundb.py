"""Project db + per-run sync: the shared sssf.db bookkeeping
(sandbox_run records, session status reads) and the forward-only merge
of a run's per-run db into the project db.
"""

import contextlib
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from sssf import db_schema
from sssf.sandbox.docker import _docker, container_name


def project_db_path(data_dir: Path) -> Path:
    """The shared project db (bind-mounted into the container at
    /work/adws/data/sssf.db)."""
    return data_dir / "sssf.db"


def sandbox_run_db(data_dir: Path) -> sqlite3.Connection:
    """A connection to the host project db for sandbox_run bookkeeping (the
    table is created by the tracer SCHEMA; create it defensively for dbs the
    tracer has not opened yet)."""
    conn = sqlite3.connect(str(project_db_path(data_dir)), isolation_level=None)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sandbox_run ("
        "  adw_id TEXT PRIMARY KEY, container TEXT NOT NULL,"
        "  container_port INTEGER, host_port INTEGER, review_url TEXT,"
        "  review_command TEXT, instructions TEXT DEFAULT '',"
        "  status TEXT, updated_at TEXT)"
    )
    return conn


def _record_sandbox_run(data_dir: Path, adw_id: str, review: dict) -> None:
    """Record the live container + review mapping (host project db). Resolves
    the random host port docker assigned for the review app's container port.
    Best-effort: the run proceeds even if the record write fails."""
    import datetime
    import json

    name = container_name(adw_id)
    cp = review.get("container_port")
    host_port = None
    if cp:
        r = _docker("port", name, f"{cp}/tcp")
        if r.returncode == 0 and r.stdout.strip():
            try:
                host_port = int(r.stdout.strip().split(":")[-1])
            except ValueError:
                host_port = None
    now = datetime.datetime.now(datetime.UTC).isoformat()
    try:
        conn = sandbox_run_db(data_dir)
        conn.execute(
            "INSERT INTO sandbox_run (adw_id, container, container_port, host_port,"
            " review_url, review_command, instructions, status, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(adw_id) DO UPDATE SET container=excluded.container,"
            " container_port=excluded.container_port, host_port=excluded.host_port,"
            " review_url=excluded.review_url, review_command=excluded.review_command,"
            " instructions=excluded.instructions, status='up', updated_at=excluded.updated_at",
            (
                adw_id,
                name,
                cp,
                host_port,
                f"http://127.0.0.1:{host_port}" if host_port else None,
                json.dumps(review.get("command") or []),
                review.get("instructions") or "",
                "up",
                now,
            ),
        )
        conn.close()
    except sqlite3.Error:
        pass


def _forward_merge(
    conn: sqlite3.Connection, src: sqlite3.Connection, table: str, adw_id: str
) -> None:
    """Merge one row-table (sessions/phases) forward-only: INSERT rows the
    project lacks, and UPDATE a row's status only while the project row is
    still un-ended (ended_at IS NULL). A stale mid-run copy can never
    downgrade a terminal status."""
    try:
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        if not cols or "adw_id" not in cols:
            return
        pk = "adw_id" if table == "sessions" else "phase_id"
        if pk not in cols:
            return
        rows = src.execute(f"SELECT * FROM {table} WHERE adw_id=?", (adw_id,)).fetchall()
        if not rows:
            return
        q = ",".join("?" * len(cols))
        # insert missing rows (by PK)
        existing = {
            r[cols.index(pk)]
            for r in conn.execute(f"SELECT {pk} FROM {table} WHERE adw_id=?", (adw_id,)).fetchall()
        }
        insert_cols = ",".join(cols)
        for row in rows:
            if row[cols.index(pk)] not in existing:
                conn.execute(f"INSERT INTO {table} ({insert_cols}) VALUES ({q})", row)
        # forward update: fill un-ended rows with the source's terminal state
        if "ended_at" in cols and "status" in cols:
            for row in rows:
                pk_val = row[cols.index(pk)]
                if row[cols.index("ended_at")] is not None:
                    sets = [f"{c}=?" for c in cols if c not in (pk, "adw_id")]
                    if table == "sessions":
                        # A restart is meant to reopen the host row first
                        # (reopen_session), but a restart route that misses it
                        # leaves the host row ENDED at the previous run's
                        # terminal state — then this ended-source merge can
                        # never record the new run's outcome and the UI shows
                        # the old failure forever (f9e445e9: run C succeeded
                        # 01:11:35, host row frozen at run B's fail 00:29:50).
                        # Let a strictly NEWER ended source row supersede an
                        # ended host row; a stale/torn copy (older ended_at, or
                        # NULL) can still never downgrade a newer terminal
                        # state.
                        #
                        # Generation guard: the merge must also never let an
                        # OLDER-run copy (source.started_at < host.started_at)
                        # overwrite a freshly reopened host row. reopen_session
                        # stamps started_at=now; a sync racing the re-run's
                        # first ADW write still sees the PREVIOUS attempt's
                        # terminal row and would otherwise revert the reopen,
                        # freezing the card in the old failure for the whole
                        # re-run (session 2e3d7693: attempt 2 built while the
                        # kanban card sat in Blocked).
                        started = row[cols.index("started_at")] if "started_at" in cols else None
                        if started is not None:
                            conn.execute(
                                f"UPDATE {table} SET {','.join(sets)} "
                                f"WHERE {pk}=? AND (ended_at IS NULL OR ended_at < ?) "
                                f"AND (started_at IS NULL OR ? >= started_at)",
                                [row[cols.index(c)] for c in cols if c not in (pk, "adw_id")]
                                + [pk_val, row[cols.index("ended_at")], started],
                            )
                        else:
                            conn.execute(
                                f"UPDATE {table} SET {','.join(sets)} "
                                f"WHERE {pk}=? AND (ended_at IS NULL OR ended_at < ?)",
                                [row[cols.index(c)] for c in cols if c not in (pk, "adw_id")]
                                + [pk_val, row[cols.index("ended_at")]],
                            )
                    else:
                        conn.execute(
                            f"UPDATE {table} SET {','.join(sets)} WHERE {pk}=? AND ended_at IS NULL",
                            [row[cols.index(c)] for c in cols if c not in (pk, "adw_id")]
                            + [pk_val],
                        )
        # live totals: tokens/cost only accumulate, so a max-merge on every
        # sync never regresses — a torn mid-run copy carries fewer tokens than
        # the previous sync, and MAX is safe in both directions. This is what
        # makes card tokens/costs update in-flight instead of only at teardown.
        for col in ("total_tokens", "total_cost"):
            if col in cols:
                for row in rows:
                    pk_val = row[cols.index(pk)]
                    conn.execute(
                        f"UPDATE {table} SET {col}=MAX(COALESCE({col},0), COALESCE(?,0)) "
                        f"WHERE {pk}=?",
                        (row[cols.index(col)], pk_val),
                    )
        # request (sessions only): the request phase writes it ONCE and it is
        # immutable for the run, but the project row is inserted at the FIRST
        # sync — usually BEFORE the request phase logs — and the ended-row
        # forward update above only fires at the final merge (never once the
        # healer has finalized the host row first). A mid-run copy must carry
        # the request too, or `sssf sandbox restart` on the host reads an empty
        # request and bails ('no request to re-run'): the healer's restarts of
        # a hung sandboxed run then burn the whole budget doing nothing and
        # the run is finalized unrecoverably. Copy only into an empty host
        # slot — a torn source copy (NULL request) never regresses one the
        # host already merged.
        if table == "sessions" and "request" in cols:
            for row in rows:
                req = row[cols.index("request")]
                if req:
                    conn.execute(
                        f"UPDATE {table} SET request=? WHERE {pk}=? "
                        "AND (request IS NULL OR request='')",
                        (req, row[cols.index(pk)]),
                    )
    except sqlite3.Error:
        pass


def _row_obeys(model: type[db_schema.Row], row: sqlite3.Row) -> bool:
    """True when a copied row validates against the contract. Extra columns
    from a newer source db are tolerated (the host may lag one image); a row
    the models reject is drift and is never copied."""
    try:
        model.model_validate(dict(zip(row.keys(), row, strict=True)))
        return True
    except ValidationError:
        return False


def sync_run_db(conn: sqlite3.Connection, per_run_db: Path, adw_id: str) -> None:
    """Merge a run's per-run db (written by the ADW inside the container) into
    the project db via the given connection. The per-run db is COPIED first —
    a plain file read never takes sqlite locks on the live db, so the ADW's
    own writes are never disturbed (a concurrent sqlite reader through the
    bind mount caused 'disk I/O error' in the ADW). A torn copy (mid-commit)
    is skipped; the next sync catches up. DELETE the run's previous rows then
    INSERT the current ones, per table, so repeated syncs never duplicate."""
    import shutil

    if not per_run_db.exists():
        return
    tmp = per_run_db.with_suffix(".sync-copy.db")
    try:
        shutil.copy2(per_run_db, tmp)
    except OSError:
        return
    try:
        src = sqlite3.connect(str(tmp), isolation_level=None)
        src.row_factory = sqlite3.Row
        try:
            # tickets is PROJECT-owned (the host's ticket commands write it; the
            # per-run db never contains tickets) — syncing it would DELETE the
            # run's ticket row and insert nothing.
            # sessions/phases merge FORWARD-ONLY: INSERT missing rows, and
            # update a status only when the project row is still un-ended. A
            # torn mid-run copy (status 'running') can therefore never
            # downgrade a terminal state the project already recorded.
            _forward_merge(conn, src, "sessions", adw_id)
            _forward_merge(conn, src, "phases", adw_id)
            for table in ("events", "envelopes", "gate_results", "processes", "agent_sessions"):
                try:
                    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                    if not cols or "adw_id" not in cols:
                        continue
                    conn.execute(f"DELETE FROM {table} WHERE adw_id=?", (adw_id,))
                    rows = src.execute(
                        f"SELECT * FROM {table} WHERE adw_id=?", (adw_id,)
                    ).fetchall()
                    if rows:
                        model = db_schema.TABLES.get(table)
                        if model is not None:
                            rows = [r for r in rows if _row_obeys(model, r)]
                        q = ",".join("?" * len(cols))
                        conn.executemany(
                            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({q})", rows
                        )
                except sqlite3.Error:
                    continue  # a table missing in one of the dbs — skip
        finally:
            src.close()
    except sqlite3.Error:
        pass  # torn copy — the next sync catches up
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _session_status(data_dir: Path, adw_id: str) -> str | None:
    import sqlite3

    db_path = project_db_path(data_dir)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        row = conn.execute("SELECT status FROM sessions WHERE adw_id=?", (adw_id,)).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None
