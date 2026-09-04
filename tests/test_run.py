from pathlib import Path

from sssf import registry
from sssf.commands import run


def _setup_project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    # minimal v2 project (init itself is covered in test_init.py)
    (root / "adws" / "modules").mkdir(parents=True)
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "config" / "sssf.config.yaml").write_text(
        "defaults:\n  coding_agent: pi\n  model: openai/gpt-4o-mini\n"
        "sandbox:\n  enabled: false\n"
        "observability:\n  db: adws/data/sssf.db\n"
    )
    registry.register_project(root, root / "adws" / "data" / "sssf.db", "1.0.0")
    # a stub ADW that proves the engine import works end-to-end
    stub = root / "adws" / "modules" / "adw_stub_check.py"
    stub.write_text(
        "import sssf.adw_modules\n"
        "from sssf.adw_modules.data_types import EnvelopeBase\n"
        "print('STUB_OK')\n"
    )
    return root


def test_run_with_prefix(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "stub_check", [], None, no_sandbox=True) == 0


def test_run_without_prefix(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "stub_check", [], None, no_sandbox=True) == 0


def test_run_missing_adw_fails(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    assert run.run(root, "does_not_exist", [], None) == 1


def test_run_updates_last_run(tmp_path, monkeypatch):
    root = _setup_project(tmp_path, monkeypatch)
    run.run(root, "stub_check", [], None)
    entry = registry.list_projects()[0]
    assert entry["last_run"] is not None


def test_run_warns_on_legacy_layout(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / "adws" / "adw_data").mkdir(parents=True)
    monkeypatch.setattr(registry, "registry_path", lambda: tmp_path / ".sssf" / "projects.json")
    assert run.run(root, "scout", [], None, no_sandbox=True) != 0
    assert "legacy adws layout" in capsys.readouterr().err


def test_sandbox_enabled_defaults_true_on_v2_project(tmp_path):
    """No sandbox key in the config → sandboxed by default. Regression: the
    v2 refactor left _sandbox_enabled using paths.config_file without the
    import — the NameError was swallowed and every run silently went local."""
    (tmp_path / "adws" / "config").mkdir(parents=True)
    (tmp_path / "adws" / "config" / "sssf.config.yaml").write_text(
        "defaults:\n  model: openai/gpt-4o-mini\n"
    )
    from sssf.commands import run

    assert run._sandbox_enabled(tmp_path) is True


def test_sandbox_enabled_failure_is_loud(tmp_path, capsys):
    from sssf.commands import run

    assert run._sandbox_enabled(tmp_path) is False
    assert "sandbox decision failed" in capsys.readouterr().err


def _seed_session(root: Path, adw_id: str, adw_name: str, request: str) -> None:
    """A minimal sessions table with one row, shaped like tracer's."""
    import sqlite3

    from sssf import sandbox
    from sssf.sandbox import project_db_path

    db = project_db_path(sandbox.sandbox_env(root)[0])
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT,"
        " status TEXT, engineer TEXT, started_at TEXT, ended_at TEXT,"
        " total_tokens INTEGER DEFAULT 0, total_cost REAL DEFAULT 0,"
        " archived INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,'success','Felipe',"
        " '2026-09-04T11:17:48',NULL,0,0,0)",
        (adw_id, adw_name, request),
    )
    conn.commit()
    conn.close()


def test_restart_reruns_the_original_adw(tmp_path, monkeypatch):
    """`sssf run restart` re-runs the ADW that ORIGINALLY ran the session, not
    a hardcoded simple_sdlc. (Field case, session 36bbd3b3: a build_review
    session restarted as simple_sdlc — different roster, different chain — and
    died validating agents the original run never touched.)"""
    from sssf.commands import run as run_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "adw_build_review", "bound the page to the viewport")

    captured: dict = {}

    def fake_run_sandboxed(root_, adw_file, args, adw_id=None, attach=False):
        captured.update(adw_file=str(adw_file.name), args=args, adw_id=adw_id, attach=attach)
        return 0

    monkeypatch.setattr(run_cmd, "_run_sandboxed", fake_run_sandboxed)
    assert run_cmd._restart(root, ["abc123"], None) == 0
    assert captured == {
        "adw_file": "adw_build_review.py",
        "args": ["bound the page to the viewport"],
        "adw_id": "abc123",
        "attach": True,
    }


def test_restart_uses_first_name_when_adws_joined(tmp_path, monkeypatch):
    """A session joined by a second ADW records 'original + joiner' — the
    restart must still run the ORIGINAL (first) ADW."""
    from sssf.commands import run as run_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "adw_build_review + adw_simple_sdlc", "bound the page")

    captured: dict = {}
    monkeypatch.setattr(
        run_cmd,
        "_run_sandboxed",
        lambda root_, adw_file, args, adw_id=None, attach=False: captured.update(
            adw_file=adw_file.name
        )
        or 0,
    )
    assert run_cmd._restart(root, ["abc123"], None) == 0
    assert captured["adw_file"] == "adw_build_review.py"


def test_restart_unprefixed_name_resolves(tmp_path, monkeypatch):
    """Legacy/unprefixed adw names ('build_review') resolve the same way the
    run command normalizes them."""
    from sssf.commands import run as run_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "build_review", "bound the page")

    captured: dict = {}
    monkeypatch.setattr(
        run_cmd,
        "_run_sandboxed",
        lambda root_, adw_file, args, adw_id=None, attach=False: captured.update(
            adw_file=adw_file.name
        )
        or 0,
    )
    assert run_cmd._restart(root, ["abc123"], None) == 0
    assert captured["adw_file"] == "adw_build_review.py"


def test_restart_of_vanished_adw_is_loud(tmp_path, monkeypatch, capsys):
    """No silent fallback: if the original ADW's module is gone, say so."""
    from sssf.commands import run as run_cmd

    root = _setup_project(tmp_path, monkeypatch)
    _seed_session(root, "abc123", "adw_vanished_adw", "bound the page")
    assert run_cmd._restart(root, ["abc123"], None) == 1
    assert "adw_vanished_adw" in capsys.readouterr().err
