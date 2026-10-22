import pytest

from sssf.cli import main


def test_version_flag(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "sssf 2.0.0"


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
def test_flow_implement_routes_ticket_vs_afk(monkeypatch, tmp_path):
    """`sssf flow implement` routes a ticket id to the single-ticket flow, the
    `afk` keyword (with --cap/--wait-seconds) to the unattended queue loop, and
    a missing id to a usage error (issue #93)."""
    from sssf.commands import flow as flow_mod

    routed: list[tuple[str, tuple, dict]] = []

    def _afk(*args, **kw):
        routed.append(("afk", args, kw))
        return 0

    def _implement(*args, **kw):
        routed.append(("implement", args, kw))
        return 0

    monkeypatch.setattr(flow_mod, "implement_afk", _afk)
    monkeypatch.setattr(flow_mod, "implement", _implement)
    monkeypatch.chdir(tmp_path)

    assert main(["flow", "implement", "afk", "--cap", "5", "--wait-seconds", "99"]) == 0
    assert main(["flow", "implement", "internal:abc"]) == 0
    assert main(["flow", "implement"]) == 2  # a ticket id or 'afk' is required

    kind, _args, kw = routed[0]
    assert kind == "afk"
    assert kw["cap"] == 5 and kw["wait_seconds"] == 99  # flags forwarded to the loop
    assert routed[1][0] == "implement"
    assert routed[1][1][1] == "internal:abc"  # the ticket id reaches the flow


def test_ticket_sync_is_the_only_tracker_write_cli_verb(capsys):
    with pytest.raises(SystemExit) as removed:
        main(["ticket", "writeback", "github:owner/repo#1"])
    assert removed.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

    with pytest.raises(SystemExit) as ticket_help:
        main(["ticket", "--help"])
    assert ticket_help.value.code == 0
    help_text = capsys.readouterr().out
    assert "sync" in help_text
    assert "writeback" not in help_text


def test_removed_legacy_verbs_are_not_in_the_cli(capsys):
    for argv in (
        ["spec", "create"],
        ["ticket", "add", "old path"],
        ["ticket", "run", "internal:old"],
        ["sessions"],
        ["phases", "old"],
        ["tail", "old"],
        ["procs", "old"],
        ["mr", "list"],
    ):
        with pytest.raises(SystemExit) as removed:
            main(argv)
        assert removed.value.code == 2
        assert "invalid choice" in capsys.readouterr().err


def test_flow_verbs_reject_legacy_project_layout(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "legacy"
    (legacy / "adws" / "modules").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    for argv in (
        ["flow", "plan", "--project", str(legacy)],
        ["flow", "implement", "internal:old", "--project", str(legacy)],
        ["flow", "implement", "afk", "--project", str(legacy)],
        ["flow", "deploy", "--project", str(legacy)],
        ["flow", "deploy", "--down", "--project", str(legacy)],
        ["flow", "deploy", "--revert", "internal:old", "--project", str(legacy)],
    ):
        assert main(argv) == 1
        assert "run `sssf init` to upgrade" in capsys.readouterr().err
