import pytest
from pydantic import ValidationError

from sssf.adw_modules.data_types import ReviewConfig, SandboxConfig, SSSFConfig


def test_defaults():
    cfg = SSSFConfig()
    assert cfg.sandbox.enabled is True
    assert cfg.sandbox.image == "sssf-runner"
    assert cfg.sandbox.port_base == 3000
    assert cfg.review.command is None
    assert cfg.review.port == 3000
    assert cfg.review.poll_seconds == 3


def test_parses_yaml_sections(tmp_path):
    yaml_path = tmp_path / "sssf.config.yaml"
    yaml_path.write_text(
        "sandbox:\n  enabled: true\n  port_base: 4000\n"
        "review:\n  command: 'bun run dev'\n  port: 5173\n"
    )
    from sssf.adw_modules.agents import load_config
    cfg = load_config(str(yaml_path))
    assert cfg.sandbox.port_base == 4000
    assert cfg.review.command == "bun run dev"
    assert cfg.review.port == 5173


def test_validation_errors():
    with pytest.raises(ValidationError):
        ReviewConfig(port=0)          # must be a positive port
    with pytest.raises(ValidationError):
        SandboxConfig(port_base=-1)
