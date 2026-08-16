import pytest
from pydantic import ValidationError

from sssf.adw_modules.data_types import SandboxConfig, SSSFConfig


def test_defaults():
    cfg = SSSFConfig()
    assert cfg.sandbox.enabled is True
    assert cfg.sandbox.image == "sssf-runner"


def test_parses_yaml_sections(tmp_path):
    yaml_path = tmp_path / "sssf.config.yaml"
    yaml_path.write_text("sandbox:\n  enabled: false\n")
    from sssf.adw_modules.agents import load_config
    cfg = load_config(str(yaml_path))
    assert cfg.sandbox.enabled is False


def test_validation_errors():
    assert SandboxConfig().enabled is True
