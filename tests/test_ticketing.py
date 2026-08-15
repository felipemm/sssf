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
