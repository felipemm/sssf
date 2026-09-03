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


def test_review_config_parses(tmp_path):
    from sssf.adw_modules.agents import load_config

    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text(
        "sandbox:\n"
        "  review:\n"
        "    command: [\"npm\", \"run\", \"dev\", \"--workspace=web\"]\n"
        "    container_port: 3000\n"
        "    instructions: \"open the url\"\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.sandbox.review.command == ["npm", "run", "dev", "--workspace=web"]
    assert cfg.sandbox.review.container_port == 3000
    assert cfg.sandbox.review.instructions == "open the url"


def test_review_config_absent_by_default():
    # default factory — no config file needed
    from sssf.adw_modules.data_types import SSSFConfig

    cfg = SSSFConfig()
    assert cfg.sandbox.review.command is None
    assert cfg.sandbox.review.container_port is None
    assert cfg.sandbox.review.instructions == ""


def test_review_config_rejects_bad_port(tmp_path):
    import pydantic

    from sssf.adw_modules.agents import load_config

    cfg_file = tmp_path / "sssf.config.yaml"
    cfg_file.write_text("sandbox:\n  review:\n    container_port: 99999\n")
    try:
        load_config(str(cfg_file))
    except pydantic.ValidationError as exc:
        assert "container_port" in str(exc)
    else:
        raise AssertionError("expected ValidationError for container_port 99999")
