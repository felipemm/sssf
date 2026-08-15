from sssf.commands import misc, viz


def test_viz_rejects_missing_bun(monkeypatch):
    monkeypatch.setattr(misc, "which", lambda name: None)
    assert viz.run(4600, None, None) == 1
