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
