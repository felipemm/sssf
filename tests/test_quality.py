"""Quality gates: config-driven deterministic commands that record gate rows.

The engine ships placeholder quality blocks (honest echoes); a project wires
real commands per-project in sssf.config.yaml under `quality.checks`. Every
check also writes a gate_results row so the dashboard's quality-gate KPI
counts the real runs, not just claim gates.
"""

from __future__ import annotations

from pathlib import Path

from sssf.adw_modules.data_types import (Phase, PhaseParams, QualityCheckSpec,
                                         QualityConfig, SSSFConfig)
from sssf.adw_modules import quality
from sssf.adw_modules.tracer import Tracer


class _Console:
    def note(self, *a, **k):  # noqa: D102
        pass


class _Run:
    """The minimal surface quality.py touches on a real Run."""

    def __init__(self, cfg: SSSFConfig, repo_root: Path, tracer: Tracer,
                 context_handoff_dir: Path):
        self.cfg = cfg
        self.adw_id = "adw_test"
        self.repo_root = repo_root
        self.tracer = tracer
        self.console = _Console()
        self.context_handoff_dir = context_handoff_dir
        self.phases = [Phase(
            phase_id="ph_test_1", adw_id="adw_test", seq=1,
            params=PhaseParams(name="test_1", kind="code", owner="quality",
                               description="Run the project's quality commands"))]


def _make_run(tmp: Path, checks: list[QualityCheckSpec] | None = None) -> _Run:
    cfg = SSSFConfig(quality=QualityConfig(checks=checks or []))
    tracer = Tracer(tmp / "sssf.db", tmp / "events.jsonl")
    return _Run(cfg, tmp, tracer, tmp / "context_handoff")


def _gate_rows(tmp: Path) -> list[tuple]:
    import sqlite3
    conn = sqlite3.connect(tmp / "sssf.db")
    try:
        return conn.execute(
            "SELECT gate, passed FROM gate_results ORDER BY gate").fetchall()
    finally:
        conn.close()


def test_unconfigured_runs_honest_placeholders(tmp_path):
    """No quality section → the package's placeholder commands run and pass
    loudly (they say out loud that they are fake), and each still records a
    gate row."""
    run = _make_run(tmp_path)
    result = quality.run_tests(run)
    assert result.passed is True
    assert "PLACEHOLDER" in result.checks[0].command

    rows = _gate_rows(tmp_path)
    assert rows == [("quality:test", 1)]


def test_configured_command_replaces_the_placeholder(tmp_path):
    """A project's real command runs instead of the placeholder, and a failing
    command fails the result with its verbatim tail."""
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="test", area="backend", operation="build",
        argv=["python3", "-c", "import sys; sys.exit(3)"])])
    result = quality.run_tests(run)
    assert result.passed is False
    assert "exited 3" in result.failures[0]
    assert result.checks[0].command == "python3 -c 'import sys; sys.exit(3)'"

    rows = _gate_rows(tmp_path)
    assert rows == [("quality:test", 0)]


def test_missing_names_fall_back_to_defaults(tmp_path):
    """A config that wires only `test` keeps honest placeholders for the
    other blocks — configured entries replace their names, never the rest."""
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="test", area="backend", operation="build", argv=["true"])])
    specs = quality._specs(run)
    by_name = {s.name: s for s in specs}
    assert by_name["test"].argv == ["true"]
    assert all(n in by_name for n in ("lint", "typecheck", "build"))
    assert all("PLACEHOLDER" in " ".join(s.argv)
               for n, s in by_name.items() if n != "test")


def test_run_quality_runs_every_configured_check(tmp_path):
    """run_quality() covers all checks (configured + defaults), and every
    check lands a gate row — the dashboard KPI counts real runs."""
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="test", area="backend", operation="build", argv=["true"]),
        QualityCheckSpec(name="typecheck", area="frontend",
                         operation="typecheck", argv=["false"])])
    result = quality.run_quality(run)
    names = [c.name for c in result.checks]
    assert names == ["test", "typecheck", "lint", "build"]
    assert result.passed is False
    assert result.checks[0].passed and not result.checks[1].passed

    rows = dict(_gate_rows(tmp_path))
    assert rows == {"quality:test": 1, "quality:typecheck": 0,
                    "quality:lint": 1, "quality:build": 1}


def test_security_operation_is_accepted(tmp_path):
    """A security scan (e.g. snyk test) is a valid operation — the literal
    covers lint | typecheck | build | security."""
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="snyk", area="backend", operation="security", argv=["true"])])
    result = quality.run_quality(run)
    names = [c.name for c in result.checks]
    assert names == ["snyk", "test", "lint", "typecheck", "build"]
    assert result.checks[0].operation == "security"
    assert result.checks[0].passed
    assert dict(_gate_rows(tmp_path))["quality:snyk"] == 1


def test_missing_requires_fails_fast_with_127(tmp_path):
    """A check whose declared target does not exist fails fast with 127 and a
    clear message — a green gate that scanned nothing is forbidden. This is
    what keeps the shipped `design` check honest on projects without a site."""
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="design", area="frontend", operation="lint",
        argv=["impeccable", "detect", "site/dist"], requires="site/dist")])
    result = quality.run_quality(run)
    check = result.checks[0]
    assert check.passed is False
    assert check.returncode == 127
    assert "requires site/dist" in check.output_tail

    rows = _gate_rows(tmp_path)
    assert ("quality:design", 0) in rows


def test_requires_present_runs_the_command(tmp_path):
    """A present requires target does not change behavior — the command runs."""
    target = tmp_path / "site"
    target.mkdir()
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="design", area="frontend", operation="lint",
        argv=["echo", "scanned"], requires="site")])
    result = quality.run_quality(run)
    check = result.checks[0]
    assert check.passed is True
    assert check.returncode == 0
