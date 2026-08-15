from importlib import resources
from pathlib import Path

from sssf import adw_modules


def test_skill_exists_and_ships():
    skill = Path(resources.files("sssf") / "SKILL.md")
    assert skill.exists()
    text = skill.read_text()
    assert "Agent proposes, code disposes" in text
    assert "hard rule" in text.lower() or "Hard rules" in text


def test_agents_points_at_package_skill():
    import inspect
    from sssf.adw_modules import agents
    src = inspect.getsource(agents)
    assert "SKILL.md" in src
