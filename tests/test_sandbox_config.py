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


def test_review_block_is_dropped_and_ignored(tmp_path):
    """ADR-0004: `review.command` is dropped — the config no longer models
    it, and a legacy project whose stamped yaml still carries the block loads
    cleanly (the workbench in deploy.yaml replaces it)."""
    from sssf.adw_modules.agents import load_config

    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text(
        "sandbox:\n"
        "  enabled: true\n"
        "  review:\n"
        "    command: [\"npm\", \"run\", \"dev\"]\n"
        "    container_port: 3000\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.sandbox.enabled is True
    assert not hasattr(cfg.sandbox, "review")  # the model no longer declares it


def test_review_block_absent_by_default():
    # default factory — no config file needed
    from sssf.adw_modules.data_types import SSSFConfig

    cfg = SSSFConfig()
    assert cfg.sandbox.enabled is True
    assert not hasattr(cfg.sandbox, "review")
