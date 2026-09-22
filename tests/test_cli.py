from sssf.cli import main


def test_version_flag():
    assert main(["--version"]) == 0


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
