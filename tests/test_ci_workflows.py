from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_ci_has_site_design_job():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "  site:" in text
    assert "impeccable detect dist" in text
    assert "needs: [python, visualizer, site, lint, typecheck, security]" in text


def test_pages_deploy_has_design_gate():
    text = (REPO / ".github" / "workflows" / "pages.yml").read_text()
    assert "Impeccable design check" in text
    assert "impeccable detect dist" in text


def test_ci_has_lint_typecheck_security_jobs():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    for job in ("lint (ruff)", "typecheck (mypy)", "security (snyk)"):
        assert job in text, f"missing {job} job"
