"""Design quality: the per-project surface knob, config patcher, and the
sdlc_full template wiring.

Covers: QualityCheckSpec.surface expansion (argv/requires derivation),
the `sssf init --design-quality` surgical config patcher (insert / convert /
idempotent), the .impeccable/config.json stamp, and the agent-phase
user_directive plumbing used by sdlc_full's design-context phases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sssf.adw_modules.agents import load_config
from sssf.adw_modules.data_types import AgentCall, PlanOutput, QualityCheckSpec
from sssf.commands import design_quality as dq

TEMPLATE_CONFIG = (
    Path(__file__).resolve().parents[1] / "src" / "sssf" / "templates" / "adws" / "config"
    / "sssf.config.yaml"
)


# ── surface expansion ─────────────────────────────────────────────────────────


def test_surface_expands_to_detect_argv_and_requires():
    spec = QualityCheckSpec(name="design", area="frontend", operation="lint", surface="src/public")
    assert spec.argv == ["impeccable", "detect", "src/public"]
    assert spec.requires == "src/public"


def test_explicit_argv_wins_over_surface():
    spec = QualityCheckSpec(
        name="design",
        area="frontend",
        operation="lint",
        argv=["impeccable", "detect", "site/dist"],
        surface="src/public",
    )
    assert spec.argv == ["impeccable", "detect", "site/dist"]


def test_check_requires_argv_or_surface():
    with pytest.raises(ValueError):
        QualityCheckSpec(name="nope", area="backend", operation="build")


# ── config patcher ────────────────────────────────────────────────────────────


def _write(root: Path, text: str) -> Path:
    cfg = root / "adws" / "config" / "sssf.config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(text)
    return cfg


def test_configure_template_style_moves_surface(tmp_path):
    root = tmp_path / "proj"
    cfg = _write(root, TEMPLATE_CONFIG.read_text())
    assert dq.configure(root, "src/public") == 0
    c = load_config(str(cfg))
    design = next(x for x in c.quality.checks if x.name == "design")
    assert design.surface == "src/public"
    assert design.argv == ["impeccable", "detect", "src/public"]
    assert design.requires == "src/public"
    designer = next(a for a in c.agents if a.name == "designer")
    assert designer.writes == ["src/public"]
    assert (root / ".impeccable" / "config.json").exists()


def test_configure_inserts_design_block_when_absent(tmp_path):
    """inkwell-style config: no design entry -> inserted before agents:, and
    the project's other checks are untouched."""
    text = TEMPLATE_CONFIG.read_text()
    text = text.replace(
        "    - name: design\n      area: frontend\n      operation: lint\n"
        "      surface: site/dist\n      timeout_seconds: 300\n",
        "",
    )
    root = tmp_path / "proj"
    cfg = _write(root, text)
    assert dq.configure(root, "src/public") == 0
    c = load_config(str(cfg))
    names = [x.name for x in c.quality.checks]
    assert "design" in names
    design = next(x for x in c.quality.checks if x.name == "design")
    assert design.argv == ["impeccable", "detect", "src/public"]
    # the snyk + test entries survive untouched
    assert "snyk" in names and "test" in names


def test_configure_converts_legacy_argv_requires_block(tmp_path):
    """Pre-surface configs (explicit argv + requires) are converted to the
    surface form, not duplicated."""
    text = TEMPLATE_CONFIG.read_text().replace(
        "      surface: site/dist\n",
        '      argv: ["impeccable", "detect", "site/dist"]\n      requires: site/dist\n',
    )
    root = tmp_path / "proj"
    cfg = _write(root, text)
    assert "surface:" not in text  # sanity: the legacy form has no surface
    assert dq.configure(root, "web") == 0
    raw = cfg.read_text()
    assert raw.count("    - name: design") == 1  # the check entry, not the designer agent
    c = load_config(str(cfg))
    design = next(x for x in c.quality.checks if x.name == "design")
    assert design.surface == "web"
    assert design.argv == ["impeccable", "detect", "web"]


def test_configure_is_idempotent_and_moves_surface(tmp_path):
    root = tmp_path / "proj"
    cfg = _write(root, TEMPLATE_CONFIG.read_text())
    dq.configure(root, "src/public")
    before = cfg.read_text()
    assert dq.configure(root, "src/public") == 0  # same surface — no churn
    assert cfg.read_text() == before
    # a NEW surface moves the gate
    assert dq.configure(root, "web") == 0
    design = next(x for x in load_config(str(cfg)).quality.checks if x.name == "design")
    assert design.surface == "web"
    assert cfg.read_text().count("    - name: design") == 1


def test_configure_without_config_is_friendly(tmp_path, capsys):
    root = tmp_path / "bare"
    root.mkdir()
    assert dq.configure(root, "site/dist") == 1
    assert "run `sssf init` first" in capsys.readouterr().err


def test_stamped_impeccable_config_is_valid_and_committable(tmp_path):
    root = tmp_path / "proj"
    _write(root, TEMPLATE_CONFIG.read_text())
    dq.configure(root, "src/public")
    rc = root / ".impeccable" / "config.json"
    assert json.loads(rc.read_text())["detector"] == {
        "ignoreRules": [],
        "ignoreFiles": [],
        "ignoreValues": [],
    }


# ── template: sdlc_full ships the surface-form design check ──────────────────


def test_template_config_ships_surface_form_design_check():
    c = load_config(str(TEMPLATE_CONFIG))
    design = next(x for x in c.quality.checks if x.name == "design")
    assert design.surface == "site/dist"
    assert design.argv == ["impeccable", "detect", "site/dist"]
    assert design.requires == "site/dist"


# ── user_directive plumbing ───────────────────────────────────────────────────


def test_agent_call_carries_user_directive():
    call = AgentCall(output_type=PlanOutput, prompt="p", user_directive="Do the other thing")
    assert call.user_directive == "Do the other thing"
