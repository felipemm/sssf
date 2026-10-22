import pytest

from sssf.adw_modules import paths

V2 = {
    "modules_dir": ".adws/modules",
    "config_dir": ".adws/config",
    "config_file": ".adws/config/sssf.config.yaml",
    "ticketing_file": ".adws/config/ticketing.yaml",
    "deploy_config_file": ".adws/config/deploy.yaml",
    "data_dir": ".adws/data",
    "kb_dir": ".adws/kb",
    "prompts_dir": ".adws/prompts",
    "specs_dir": ".adws/specs",
}


@pytest.mark.parametrize("fn,rel", V2.items())
def test_v2_paths(tmp_path, fn, rel):
    assert getattr(paths, fn)(tmp_path) == tmp_path / rel


@pytest.mark.parametrize(
    "marker",
    [
        "adws/adw_sssf_config/sssf.config.yaml",
        "adws/adw_data",
        "adws/app_docs",
        "adws/adw_simple_sdlc.py",
    ],
)
def test_legacy_detected(tmp_path, marker):
    target = tmp_path / marker
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    assert paths.is_legacy_layout(tmp_path) is True


def test_v2_layout_not_legacy(tmp_path):
    (tmp_path / ".adws" / "modules").mkdir(parents=True)
    (tmp_path / ".adws" / "config").mkdir()
    (tmp_path / ".adws" / "config" / "sssf.config.yaml").write_text("defaults: {}\n")
    assert paths.is_legacy_layout(tmp_path) is False


def test_warn_if_legacy_prints_and_returns(capsys, tmp_path):
    (tmp_path / "adws" / "adw_data").mkdir(parents=True)
    assert paths.warn_if_legacy(tmp_path, command="flow") is True
    captured = capsys.readouterr()
    assert "legacy adws layout" in captured.err and "sssf init" in captured.err


def test_flow_and_workbench_read_deploy_config_through_shared_path_helper(
    tmp_path, monkeypatch
):
    from sssf import workbench
    from sssf.commands import flow

    config = tmp_path / "selected-deploy.yaml"
    config.write_text(
        "release:\n  canary_command: [deploy, canary]\n"
        "workbench:\n  command: [bun, run, dev]\n  container_port: 3000\n"
    )
    monkeypatch.setattr(paths, "deploy_config_file", lambda root: config)

    assert flow._release_config(tmp_path) == {"canary_command": ["deploy", "canary"]}
    assert workbench.workbench_config(tmp_path) == {
        "command": ["bun", "run", "dev"],
        "container_port": 3000,
    }


def test_warn_if_legacy_silent_on_v2(capsys, tmp_path):
    (tmp_path / ".adws" / "modules").mkdir(parents=True)
    (tmp_path / ".adws" / "config").mkdir()
    (tmp_path / ".adws" / "config" / "sssf.config.yaml").write_text("defaults: {}\n")
    assert paths.warn_if_legacy(tmp_path, command="flow") is False
    assert capsys.readouterr().out == ""
