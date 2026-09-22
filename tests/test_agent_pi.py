"""The pi argv builder — hermetic skill flags (#88) ride the command line:
`--no-skills` disables discovery, and every per-agent subset path is an
additive `--skill` (pi's `--skill` loads even with `--no-skills`)."""

from __future__ import annotations

from sssf.adw_modules import agent_pi
from sssf.adw_modules.data_types import PiRequest


def _request(**overrides) -> PiRequest:
    base = dict(
        prompt="do the thing",
        system_prompt="system",
        model="openai/gpt-4o-mini",
        session_id="sssf-abc",
        session_dir="/tmp/sessions",
        raw_output_path="/tmp/raw.jsonl",
    )
    base.update(overrides)
    return PiRequest(**base)


def test_build_command_baseline(monkeypatch):
    monkeypatch.setattr(agent_pi, "resolve_model", lambda pattern: ("openai", "gpt-4o-mini"))
    cmd = agent_pi.build_command(_request(skill_path="/pkg/SKILL.md"))
    assert cmd[0] == "pi"
    assert "--skill" in cmd
    assert cmd[cmd.index("--skill") + 1] == "/pkg/SKILL.md"
    assert "--no-skills" not in cmd
    assert cmd[-1] == "do the thing"


def test_build_command_hermetic_flags(monkeypatch):
    monkeypatch.setattr(agent_pi, "resolve_model", lambda pattern: ("openai", "gpt-4o-mini"))
    cmd = agent_pi.build_command(
        _request(
            no_skills=True,
            skill_path="/pkg/SKILL.md",
            skill_paths=[
                "/proj/.pi/skills/grilling/SKILL.md",
                "/proj/.pi/skills/to-spec/SKILL.md",
            ],
        )
    )
    assert "--no-skills" in cmd
    skills = [cmd[i + 1] for i, token in enumerate(cmd) if token == "--skill"]
    assert skills == [
        "/pkg/SKILL.md",
        "/proj/.pi/skills/grilling/SKILL.md",
        "/proj/.pi/skills/to-spec/SKILL.md",
    ]


def test_build_command_no_subset_without_hermetic(monkeypatch):
    monkeypatch.setattr(agent_pi, "resolve_model", lambda pattern: ("openai", "gpt-4o-mini"))
    cmd = agent_pi.build_command(_request(skill_paths=["/x/SKILL.md"], no_skills=False))
    assert "--no-skills" not in cmd
    assert "--skill" in cmd  # explicit paths are additive either way
