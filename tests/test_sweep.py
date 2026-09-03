import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sssf import registry
from sssf.commands import sweep


def _make_db(path: Path) -> None:
    """A sessions table with one old and one recent session, plus a running one.

    `recent` sits comfortably INSIDE the sweep window (1 hour old, not exactly
    one day): sweep compares with second precision
    (`datetime(ended_at) < datetime('now', '-N days')`), so a value set to
    exactly N days ago flips across the boundary whenever the wall clock
    crosses a second between insert and sweep — a classic date flake.
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT,"
        " archived INTEGER DEFAULT 0, request TEXT);"
    )
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="milliseconds")
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="milliseconds")
    conn.execute(
        "INSERT INTO sessions (adw_id, status, ended_at, archived) VALUES"
        " ('old-success', 'success', ?, 0), ('old-fail', 'fail', ?, 0),"
        " ('recent', 'success', ?, 0), ('running', 'running', NULL, 0)",
        (old, old, recent),
    )
    conn.commit()
    conn.close()


def _register(tmp_path: Path, monkeypatch, root: Path | None = None) -> None:
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    if root is not None:
        registry.register_project(root, root / "adws" / "data" / "sssf.db", "0.1.0")


def test_sweep_archives_old_finished_only(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/data").mkdir(parents=True)
    db = root / "adws" / "data" / "sssf.db"
    _make_db(db)
    _register(tmp_path, monkeypatch, root)

    assert sweep.run(None) == 0
    out = capsys.readouterr().out
    assert "archived 2 session(s)" in out

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT adw_id, archived FROM sessions").fetchall())
    conn.close()
    assert rows["old-success"] == 1
    assert rows["old-fail"] == 1
    assert rows["recent"] == 0
    assert rows["running"] == 0


def test_sweep_project_flag(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/data").mkdir(parents=True)
    db = root / "adws" / "data" / "sssf.db"
    _make_db(db)
    # not registered — --project sweeps it directly
    assert sweep.run(str(root)) == 0
    assert "archived 2 session(s)" in capsys.readouterr().out


def test_sweep_empty_registry_is_friendly(tmp_path, monkeypatch, capsys):
    _register(tmp_path, monkeypatch)
    assert sweep.run(None) == 0
    assert "no projects registered" in capsys.readouterr().out


def test_sweep_clears_sandbox_resources(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/data").mkdir(parents=True)
    db = root / "adws" / "data" / "sssf.db"
    _make_db(db)
    cleared = []
    monkeypatch.setattr(sweep, "_clear_sandbox", lambda r, a: cleared.append((r, a)))
    assert sweep.run(str(root)) == 0
    assert sorted(a for _, a in cleared) == ["old-fail", "old-success"]


def test_clear_sandbox_removes_container_and_worktree(tmp_path, monkeypatch):
    from sssf import sandbox

    root = tmp_path / "proj"
    (root / ".worktrees").mkdir(parents=True)
    stopped = []
    monkeypatch.setattr(sandbox, "stop_remove", lambda name: stopped.append(name))
    monkeypatch.setattr(sandbox, "remove_worktree", lambda wt: wt)
    sweep._clear_sandbox(root, "abc123")
    assert stopped == ["sssf-abc123"]


def test_sweep_days_flag(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws/data").mkdir(parents=True)
    db = root / "adws" / "data" / "sssf.db"
    _make_db(db)
    _register(tmp_path, monkeypatch, root)
    # 1-day window: only the 40-day-old sessions qualify
    assert sweep.run(None, days=1) == 0
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE archived = 1").fetchone()[0]
    conn.close()
    assert n == 2


def test_sweep_clears_sandbox_run_row(tmp_path, monkeypatch):
    """Sweep is the sole deleter — it also removes the session's sandbox_run
    record so the viz review panel goes away with the run."""
    import subprocess

    import sssf.sandbox as sb
    from sssf.commands import sweep as sweep_mod

    root = tmp_path / "proj"
    data = root / "adws" / "data"
    data.mkdir(parents=True)
    db_path = sb.project_db_path(data)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT,"
        " archived INTEGER DEFAULT 0);"
        "CREATE TABLE sandbox_run (adw_id TEXT PRIMARY KEY, container TEXT, status TEXT);"
    )
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="milliseconds")
    conn.execute(
        "INSERT INTO sessions (adw_id, status, ended_at) VALUES ('old1','success',?)",
        (old,),
    )
    conn.execute("INSERT INTO sandbox_run VALUES ('old1','sssf-old1','up')")
    conn.commit()
    conn.close()

    calls: list[list[str]] = []
    monkeypatch.setattr(
        sb, "_docker",
        lambda *a, timeout_s=30: calls.append(list(a))
        or subprocess.CompletedProcess(list(a), 0, "", ""),
    )
    ids = sweep_mod.sweep_db(db_path, "-30 days")
    assert "old1" in ids
    sweep_mod._clear_sandbox(root, "old1")
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT COUNT(*) FROM sandbox_run WHERE adw_id='old1'").fetchone()
    conn.close()
    assert row[0] == 0  # the review record is deleted with the run


def test_sweep_removes_orphan_containers(tmp_path, monkeypatch):
    """Containers that match NO session (spawn leftovers) are swept too —
    the only cleanup path for orphans now that the healer never deletes."""
    import subprocess

    import sssf.sandbox as sb
    from sssf.commands import sweep as sweep_mod

    root = tmp_path / "proj"
    data = root / "adws" / "data"
    data.mkdir(parents=True)
    db_path = sb.project_db_path(data)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT,"
        " archived INTEGER DEFAULT 0);"
    )
    conn.execute("INSERT INTO sessions (adw_id, status, ended_at) VALUES ('live1','success','2020-01-01T00:00:00')")
    conn.commit()
    conn.close()

    ps = "sssf-orphanx\nsssf-live1\n"
    calls: list[list[str]] = []

    def fake_docker(*a, timeout_s=30):
        calls.append(list(a))
        if a[0] == "ps":
            return subprocess.CompletedProcess(list(a), 0, ps, "")
        return subprocess.CompletedProcess(list(a), 0, "", "")

    monkeypatch.setattr(sb, "_docker", fake_docker)
    removed = sweep_mod._clean_orphan_containers(root, db_path)
    assert removed == ["sssf-orphanx"]  # live1 has a session → kept
    assert ["rm", "-f", "sssf-orphanx"] in calls
    assert not any("sssf-live1" in a for a in calls if a and a[0] == "rm")
