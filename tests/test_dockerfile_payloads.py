from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO / "docker" / "sssf-runner.Dockerfile"
ENTRYPOINT = REPO / "docker" / "entrypoint.sh"


def _copy_sources(dockerfile_text: str) -> list[str]:
    """Every local path a COPY line names — the payloads the image promises."""
    sources = []
    for line in dockerfile_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("COPY "):
            sources.append(stripped.split()[1])
    return sources


def test_every_dockerfile_copy_source_exists():
    text = DOCKERFILE.read_text()
    for src in _copy_sources(text):
        assert (REPO / src).exists(), f"COPY source {src} missing"


def test_impeccable_skill_vendored():
    skill = REPO / "docker" / "impeccable-pi" / "skills" / "impeccable" / "SKILL.md"
    assert skill.exists(), "impeccable skill must be vendored (docker/impeccable-pi)"
    text = skill.read_text()
    assert "impeccable" in text.lower()


def test_impeccable_cli_pin_is_latest_3_6_x():
    """The deterministic gate runs the npm CLI (`impeccable detect`). The pin
    tracks the 3.6.x line that ships `detect`; the vendored skill (4.1.1, the
    pi-skill version) calls detect/doctor/ignores on it."""
    text = DOCKERFILE.read_text()
    for pin in ("impeccable@3.6.1", "impeccable@3.6.0"):
        if pin in text:
            assert pin == "impeccable@3.6.1", f"stale impeccable pin {pin} in Dockerfile"
            return
    raise AssertionError("no impeccable npm pin found in the Dockerfile")


def test_dockerfile_references_impeccable_payload():
    text = DOCKERFILE.read_text()
    assert "COPY docker/impeccable-pi /opt/impeccable-pi" in text
    assert "npm install -g impeccable" in text


def test_entrypoint_copies_impeccable_skill():
    text = ENTRYPOINT.read_text()
    assert "/opt/impeccable-pi" in text
    assert 'mkdir -p "$HOME/.pi/agent/skills"' in text
    assert "cp -r /opt/impeccable-pi/skills/impeccable" in text
