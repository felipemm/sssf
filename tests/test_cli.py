from sssf.cli import main


def test_version_flag():
    assert main(["--version"]) == 0


def test_flow_deploy_default_dispatches_deploy(monkeypatch):
    """`sssf flow deploy` (no flags) → flow.deploy; `--down` → deploy_down;
    `--revert <id>` → deploy_revert (#96's new surface)."""
    from pathlib import Path

    from sssf.commands import flow as flow_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        flow_mod,
        "deploy",
        lambda cwd, project, yes: calls.append(("deploy", cwd, project, yes)) or 0,
    )
    monkeypatch.setattr(
        flow_mod,
        "deploy_down",
        lambda cwd, project: calls.append(("down", cwd, project)) or 0,
    )
    monkeypatch.setattr(
        flow_mod,
        "deploy_revert",
        lambda cwd, ticket, project: calls.append(("revert", cwd, ticket, project)) or 0,
    )
    assert main(["flow", "deploy"]) == 0
    assert main(["flow", "deploy", "--yes"]) == 0
    assert calls[0][0] == "deploy" and calls[0][3] is False
    assert calls[1][0] == "deploy" and calls[1][3] is True
    assert main(["flow", "deploy", "--down"]) == 0
    assert calls[2][0] == "down"
    assert calls[2][1] == Path.cwd()
    assert main(["flow", "deploy", "--revert", "internal:abc"]) == 0
    assert calls[3][0] == "revert"
    assert calls[3][2] == "internal:abc"
