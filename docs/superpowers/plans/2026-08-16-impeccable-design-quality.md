# Impeccable Design Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Impeccable into sssf as a deterministic design gate (shipped configured by default alongside snyk), an agentic design pass in a new opt-in ADW variant, and full site documentation.

**Architecture:** (1) `impeccable detect` runs as a config-driven `quality.checks` entry with the CLI baked into the runner image (bare binary, snyk pattern) plus CI gates on the site; (2) a new `designer` roster agent + `adw_plan_build_test_quality_design.py` variant runs `/impeccable audit → critique → polish → optimize` (PRODUCT.md via `init` early, DESIGN.md via `document` at the end); (3) the impeccable pi skill is vendored under `docker/impeccable-pi/` and copied into the sandbox pi home by the entrypoint.

**Tech Stack:** Python (sssf engine, pytest), YAML config, Docker, bash entrypoint, GitHub Actions, Astro (site), npm.

**Spec:** `docs/superpowers/specs/2026-08-16-impeccable-design-quality-design.md`

## Global Constraints

- Work in the isolated worktree (`.worktrees/<name>` on branch `feat/impeccable-design-quality`) — `main` stays untouched; PR at the end.
- Impeccable pinned to `3.6.0` everywhere (Dockerfile, site devDependency).
- `quality.checks` argv must be a LIST and use bare binary names (`impeccable`, `snyk`) — never `npx`, never absolute paths.
- Template config keeps `test`/`lint`/`typecheck`/`build` as placeholders; only `design` + `snyk` ship configured by default.
- Every roster agent requires `prompt_engineering.system` and `.user` files on disk (`agents.validate` enforces `Path(ref).is_file()`).
- The impeccable skill is vendored (committed) — image builds must stay deterministic and offline.
- Do not modify the standard (non-variant) ADWs.
- After editing `docker/sssf-runner.Dockerfile`, the image MUST be rebuilt (`docker build -t sssf-runner -f docker/sssf-runner.Dockerfile .` from the repo root) — stale image reuse is the bug class fixed by PR #15.
- Commit per task with conventional messages (`feat:`/`test:`/`docs:`/`chore:`).

---

### Task 1: Vendor the impeccable pi skill + payload regression test

**Files:**
- Create: `docker/impeccable-pi/skills/impeccable/…` (vendored from npm — see steps)
- Create: `docker/impeccable-pi/README.md`
- Create: `tests/test_dockerfile_payloads.py`

**Interfaces:**
- Produces: vendored skill tree at `docker/impeccable-pi/skills/impeccable/` (with `SKILL.md` at its root) — Task 2's Dockerfile `COPY` and entrypoint `cp -r` depend on this exact path. Test helper `_copy_sources(dockerfile_text) -> list[str]` is used by later tasks' tests.

- [ ] **Step 1: Write the failing test**

`tests/test_dockerfile_payloads.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dockerfile_payloads.py::test_impeccable_skill_vendored -v`
Expected: FAIL with `impeccable skill must be vendored`

- [ ] **Step 3: Vendor the skill**

```bash
# Fetch the pi skill payload into a temp dir (do NOT run inside the repo)
rm -rf /tmp/impeccable-vendor && mkdir -p /tmp/impeccable-vendor && cd /tmp/impeccable-vendor
npx --yes impeccable install --providers=pi --scope=project
# Verify shape: .pi/skills/impeccable/SKILL.md must exist
test -f .pi/skills/impeccable/SKILL.md && echo OK
```

Then, from the worktree root:

```bash
mkdir -p docker/impeccable-pi/skills
cp -R /tmp/impeccable-vendor/.pi/skills/impeccable docker/impeccable-pi/skills/
```

Create `docker/impeccable-pi/README.md`:

```markdown
# impeccable pi skill (vendored)

Source: https://github.com/pbakaus/impeccable (npm: impeccable@3.6.0)
Vendored: 2026-08-16 via `npx impeccable install --providers=pi --scope=project`
Refresh: rerun that command in a temp dir, then copy `.pi/skills/impeccable` here
and bump the version above.

Why vendored: image builds stay deterministic and offline. The sandbox
entrypoint copies this into the container's pi home — skills are otherwise
deliberately excluded from the sandbox (they would distract ADW agents).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_dockerfile_payloads.py -v`
Expected: PASS (both tests — `test_every_dockerfile_copy_source_exists` guards existing COPY sources and passes already)

- [ ] **Step 5: Commit**

```bash
git add docker/impeccable-pi tests/test_dockerfile_payloads.py
git commit -m "chore(sandbox): vendor the impeccable pi skill under docker/impeccable-pi"
```

---

### Task 2: Bake impeccable into the runner image + entrypoint copy

**Files:**
- Modify: `docker/sssf-runner.Dockerfile` (after the snyk block, before `# sssf itself`)
- Modify: `docker/entrypoint.sh` (after the settings copy block)
- Modify: `tests/test_dockerfile_payloads.py` (add two tests)

**Interfaces:**
- Consumes: `docker/impeccable-pi/skills/impeccable` (Task 1), `_copy_sources` (Task 1).
- Produces: `impeccable` on PATH in the runner image (npm global → `/usr/local/bin`); the skill at `$HOME/.pi/agent/skills/impeccable` inside the container at runtime.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dockerfile_payloads.py`:

```python
def test_dockerfile_references_impeccable_payload():
    text = DOCKERFILE.read_text()
    assert "COPY docker/impeccable-pi /opt/impeccable-pi" in text
    assert "npm install -g impeccable" in text


def test_entrypoint_copies_impeccable_skill():
    text = ENTRYPOINT.read_text()
    assert "/opt/impeccable-pi" in text
    assert 'mkdir -p "$HOME/.pi/agent/skills"' in text
    assert "cp -r /opt/impeccable-pi/skills/impeccable" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dockerfile_payloads.py -k "impeccable_payload or entrypoint" -v`
Expected: FAIL — Dockerfile/entrypoint lack the references

- [ ] **Step 3: Modify the Dockerfile**

In `docker/sssf-runner.Dockerfile`, after the snyk block (line ~24) and before `# sssf itself (the build context is the sssf repo root)`:

```dockerfile
# impeccable — design quality: CLI for the deterministic gate (detect) and the
# pi skill the designer agent runs (/impeccable audit|critique|polish|optimize,
# init, document). npm lands in /usr/local (world-readable for the runtime uid,
# like bun). The skill is vendored under docker/impeccable-pi/ and copied into
# the pi home by entrypoint.sh — skills are otherwise excluded from the sandbox.
RUN npm install -g impeccable@3.6.0
COPY docker/impeccable-pi /opt/impeccable-pi
```

- [ ] **Step 4: Modify the entrypoint**

In `docker/entrypoint.sh`, after the settings copy block (`[ -d /opt/pi-agent-host/settings ] && cp -r ...`) and before `exec "$@"`:

```sh
# impeccable pi skill — vendored in the image; copied into the pi home here
# because skills are otherwise excluded from the sandbox.
mkdir -p "$HOME/.pi/agent/skills"
cp -r /opt/impeccable-pi/skills/impeccable "$HOME/.pi/agent/skills/"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_dockerfile_payloads.py -v`
Expected: PASS (all four tests)

- [ ] **Step 6: Commit**

```bash
git add docker/sssf-runner.Dockerfile docker/entrypoint.sh tests/test_dockerfile_payloads.py
git commit -m "feat(sandbox): bake impeccable CLI + pi skill into the runner image"
```

---

### Task 3: Template config — designer agent + shipped default checks

**Files:**
- Create: `src/sssf/templates/prompt_engineering/designer/system.md`
- Create: `src/sssf/templates/prompt_engineering/designer/user.md`
- Modify: `src/sssf/templates/sssf.config.yaml` (quality.checks + agents roster)
- Modify: `tests/test_templates.py`

**Interfaces:**
- Consumes: existing `agents.validate` contract (every roster agent needs prompt files on disk).
- Produces: `designer` roster agent (writes `site/`, tools read/grep/find/ls/bash/edit/write); `design` + `snyk` checks in `quality.checks` that `quality.run_quality()` picks up via `_specs()` — consumed by Task 4's ADW variant. `test_starter_config_validates` exercises the full roster.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_templates.py`:

```python
def test_template_ships_default_checks():
    cfg = (TEMPLATES / "sssf.config.yaml").read_text()
    assert '- name: design' in cfg
    assert '"impeccable", "detect", "site/dist"' in cfg
    assert '- name: snyk' in cfg
    assert '"snyk", "test"' in cfg
    # runners stay honest placeholders — never defaulted
    assert "PLACEHOLDER test" in cfg


def test_designer_prompt_files_exist():
    for label in ("system", "user"):
        path = TEMPLATES / "prompt_engineering" / "designer" / f"{label}.md"
        assert path.is_file(), f"designer {label} prompt missing"
```

Also update the existing `test_starter_config_validates` roster list to include `designer`:

```python
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter", "designer"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_templates.py -k "default_checks or designer_prompt" -v`
Expected: FAIL — config/prompts missing

- [ ] **Step 3: Create the designer prompt files**

`src/sssf/templates/prompt_engineering/designer/system.md`:

```markdown
You are the design quality engineer for this project. Your job is to run the
impeccable design pass on the project's design surface and fix what it reports.

Rules:
- Read PRODUCT.md first — it is your design context (audience, product lane, voice).
- Run the impeccable skill commands in order: /impeccable audit, /impeccable
  critique, /impeccable polish, /impeccable optimize — against the design
  surface (site/ by default).
- Apply every actionable fix each command reports. Never skip a finding you
  can fix.
- You never commit; the factory commits. You only edit the design surface.
- Report every changed file in your envelope.
```

`src/sssf/templates/prompt_engineering/designer/user.md`:

```markdown
The work being built is described in the envelope: read it before starting.
Your design surface is the site directory (site/ unless the envelope says
otherwise). Run the full impeccable pass — /impeccable audit, /impeccable
critique, /impeccable polish, /impeccable optimize — on it, apply every
actionable fix, and report the files you changed.
```

- [ ] **Step 4: Modify the template config**

In `src/sssf/templates/sssf.config.yaml`, in `quality.checks`, after the `build` placeholder entry, add (with a comment noting they ship configured because the runner image provides both binaries):

```yaml
    # Shipped configured by default: the runner image provides both binaries
    # (snyk since PR #13, impeccable via the design-quality change). Projects
    # without a design surface or snyk setup remove the entry.
    - name: design
      area: frontend
      operation: lint
      argv: ["impeccable", "detect", "site/dist"]
      timeout_seconds: 300
    - name: snyk
      area: backend
      operation: security
      argv: ["snyk", "test"]
      timeout_seconds: 300
```

In the `agents:` roster, after the `reviewer` entry, add:

```yaml
  - name: designer
    color: "#f59e0b"
    purpose: Run the impeccable design pass (audit, critique, polish, optimize) on the project's design surface and apply the fixes it reports.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/designer/system.md
      user: adws/adw_data/prompt_engineering/designer/user.md
    writes:
      - site/                     # the design surface; adjust in project configs
    tools:
      - read
      - grep
      - find
      - ls
      - bash
      - edit
      - write
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_templates.py -v`
Expected: PASS — including the updated `test_starter_config_validates` (validates the full roster with the new designer)

- [ ] **Step 6: Commit**

```bash
git add src/sssf/templates/sssf.config.yaml \
        src/sssf/templates/prompt_engineering/designer \
        tests/test_templates.py
git commit -m "feat(templates): designer agent + design/snyk checks shipped configured by default"
```

---

### Task 4: New ADW variant `adw_plan_build_test_quality_design.py`

**Files:**
- Create: `src/sssf/templates/adws/adw_plan_build_test_quality_design.py`
- Modify: `tests/test_templates.py` (chain-count test + variant phase test)

**Interfaces:**
- Consumes: `planner`, `builder`, `designer`, `documenter` roster agents (Task 3); `quality.run_quality(run)` (existing — includes the `design` + `snyk` checks from config); `quality.as_envelope(result, what)`; `gates.artifacts_exist`, `gates.files_non_empty`, `gates.diff_matches_claims`; `git_helper.commit_all(message)`; `session.ensure(cfg, adw_id)`.
- Produces: runnable ADW `adw_plan_build_test_quality_design.py` — the opt-in chain. Phases: request → plan → build → init (PRODUCT.md) → design → verify loop → fix loop → document (DESIGN.md) → commit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_templates.py`:

```python
def test_thirteen_starter_chains():
    adws = sorted((TEMPLATES / "adws").glob("adw_*.py"))
    assert len(adws) == 13
    for adw in adws:
        spec = importlib.util.spec_from_file_location(adw.stem, adw)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[adw.stem] = mod
        spec.loader.exec_module(mod)   # imports sssf.adw_modules — proves engine link
```

(Replace the existing `test_twelve_starter_chains` with the above, renamed.)

```python
def test_quality_design_variant_has_impeccable_phases():
    text = (TEMPLATES / "adws" / "adw_plan_build_test_quality_design.py").read_text()
    for needle in ('name="init"', 'name="design"', 'owner="designer"',
                   'owner="documenter"', 'name="document"', 'impeccable'):
        assert needle in text, f"variant missing {needle}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_templates.py -k "starter_chains or quality_design_variant" -v`
Expected: FAIL — file missing, count 12 ≠ 13

- [ ] **Step 3: Create the ADW variant**

`src/sssf/templates/adws/adw_plan_build_test_quality_design.py`:

```python
#!/usr/bin/env -S uv run
"""ADW Plan Build Test Quality + Design — full agent chain with the
impeccable design pass and deterministic quality gates.

Usage:
    uv run adws/adw_plan_build_test_quality_design.py "<prompt or path/to/prompt.md>" [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> planner -> builder -> documenter(init) ->
designer(design) -> [code(verify) -> builder(fix)] bounded -> documenter(document)
-> git(commit)

The design pass is agentic and bounded: the designer runs /impeccable audit →
critique → polish → optimize on the design surface, and its work is then
verified by the deterministic quality gates (including the `design` detect
check). PRODUCT.md (via /impeccable init) is the designer's design context;
DESIGN.md (via /impeccable document) ships with the project. A failing gate
does not fail its phase — the failure becomes an envelope and flows back into
the builder, and only an exhausted repair loop fails the run.
"""

import argparse
import sys

from sssf.adw_modules import agents, gates, git_helper, quality, session, utils
from sssf.adw_modules.data_types import (AgentCall, BuildOutput, DocumentOutput,
                                    PhaseParams, PlanOutput)

REQUIRED_AGENTS = ["planner", "builder", "designer", "documenter"]
MAX_FIX_LOOPS = 3

DESIGN_SURFACE = "site/"   # the designer edits this; the `design` gate checks site/dist


def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml", adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt)

    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the request into an implementable plan")) as ph:
        plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                                 gates=[gates.artifacts_exist, gates.files_non_empty]))

    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the plan exactly")) as ph:
        build_out = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=plan,
                                      gates=[gates.diff_matches_claims]))

    with run.phase(PhaseParams(name="init", kind="agent", owner="documenter", retries=1,
                               description="Run /impeccable init to generate PRODUCT.md — the designer's design context")) as ph:
        init_out = ph.call(AgentCall(output_type=DocumentOutput, prompt=prompt, previous=plan,
                                     gates=[gates.artifacts_exist, gates.files_non_empty]))

    with run.phase(PhaseParams(name="design", kind="agent", owner="designer", retries=1,
                               description=f"Impeccable design pass (audit → critique → polish → optimize) on {DESIGN_SURFACE}")) as ph:
        design_out = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=build_out,
                                       gates=[gates.diff_matches_claims]))

    def record(ph, result) -> None:
        passed = sum(1 for check in result.checks if check.passed)
        ph.log(passed=result.passed, checks=f"{passed}/{len(result.checks)}",
               artifacts=", ".join(result.artifacts))

    quality_result = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(PhaseParams(name=f"verify_{i}", kind="code", owner="quality",
                                   description="Run every quality gate — tests, typecheck, build, design, snyk")) as ph:
            quality_result = quality.run_quality(run)
            record(ph, quality_result)

        if quality_result.passed:
            break
        if i == MAX_FIX_LOOPS:
            break

        with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                   description="Resolve the reported gate failures")) as ph:
            build_out = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                          previous=quality.as_envelope(quality_result, "quality gates"),
                                          gates=[gates.diff_matches_claims]))

    verified = quality_result is not None and quality_result.passed
    if verified:
        with run.phase(PhaseParams(name="document", kind="agent", owner="documenter", retries=1,
                                   description="Run /impeccable document to generate DESIGN.md from the built project")) as ph:
            document = ph.call(AgentCall(output_type=DocumentOutput, prompt=prompt,
                                         previous=init_out,
                                         gates=[gates.artifacts_exist, gates.files_non_empty]))

        with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                                   description="Commit the designed, tested, and quality-verified working tree")) as ph:
            message = document.commit_message or f"sssf({run.adw_id}): {build_out.summary}"
            ph.log(sha=git_helper.commit_all(message), message=message)

    return run.finish(accepted=verified,
                      reason=f"quality gates never came back clean after {MAX_FIX_LOOPS} fix attempt(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_templates.py -v`
Expected: PASS (13 chains import cleanly; variant phases present)

- [ ] **Step 5: Sanity-check the variant imports + config validate in a stamped layout**

Run: `uv run pytest tests/test_templates.py::test_starter_config_validates tests/test_templates.py::test_thirteen_starter_chains -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sssf/templates/adws/adw_plan_build_test_quality_design.py tests/test_templates.py
git commit -m "feat(adws): adw_plan_build_test_quality_design — impeccable design pass variant"
```

---

### Task 5: CI gates — site job + deploy step + pinned devDependency

**Files:**
- Modify: `site/package.json` (+ `site/package-lock.json` via npm)
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/pages.yml`
- Create: `tests/test_ci_workflows.py`

**Interfaces:**
- Consumes: `site/dist` build output; `impeccable@3.6.0` devDependency.
- Produces: `site` job in CI wired into the aggregate `CI` job; `Impeccable design check` step in pages.yml that blocks deploys on exit 2.

- [ ] **Step 1: Write the failing tests**

`tests/test_ci_workflows.py`:

```python
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_ci_has_site_design_job():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "  site:" in text
    assert "impeccable detect dist" in text
    assert "needs: [python, visualizer, site]" in text


def test_pages_deploy_has_design_gate():
    text = (REPO / ".github" / "workflows" / "pages.yml").read_text()
    assert "Impeccable design check" in text
    assert "impeccable detect dist" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ci_workflows.py -v`
Expected: FAIL — workflows lack the steps

- [ ] **Step 3: Pin the devDependency**

```bash
cd site && npm install --save-dev impeccable@3.6.0
```

This adds `"impeccable": "3.6.0"` to `devDependencies` in `site/package.json` and updates `site/package-lock.json`.

- [ ] **Step 4: Modify ci.yml**

In `.github/workflows/ci.yml`, add a job after `visualizer`:

```yaml
  site:
    name: site (astro build + impeccable)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: site
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: site/package-lock.json
      - name: Install dependencies
        run: npm ci
      - name: Build the static site
        run: npm run build
      - name: Impeccable design check
        run: npx --yes impeccable detect dist
```

Update the aggregate job:

```yaml
  aggregate:
    name: CI
    runs-on: ubuntu-latest
    needs: [python, visualizer, site]
    steps:
      - run: echo "python (pytest) + visualizer (bun test) + site (astro + impeccable) green"
```

- [ ] **Step 5: Modify pages.yml**

In `.github/workflows/pages.yml`, between `Build the static site` and `Upload Pages artifact`:

```yaml
      - name: Impeccable design check
        run: npx --yes impeccable detect dist
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ci_workflows.py -v`
Expected: PASS

- [ ] **Step 7: Local gate sanity (same command CI runs, against the built dist)**

```bash
cd site && npm run build && npx --yes impeccable detect dist
```

Expected: exit 0 (only the advisory em-dash finding on docs/cli)

- [ ] **Step 8: Commit**

```bash
git add site/package.json site/package-lock.json .github/workflows/ci.yml .github/workflows/pages.yml tests/test_ci_workflows.py
git commit -m "feat(ci): impeccable design gate on the site — PR job + deploy step"
```

---

### Task 6: Site docs — quality-checks reference page

**Files:**
- Create: `site/src/pages/docs/quality-checks.astro`
- Modify: `site/src/components/DocsSidebar.astro`

**Interfaces:**
- Consumes: `DocsLayout` (title/description/current/prev/next props — see `configuration.astro`).
- Produces: rendered `/docs/quality-checks` page; sidebar entry in the Reference group.

- [ ] **Step 1: Create the page**

`site/src/pages/docs/quality-checks.astro` — model the frontmatter on `configuration.astro`. Content must document every `quality.checks` option:

- every field: `name`, `area`, `operation`, `argv`, `timeout_seconds` — semantics, allowed values, defaults;
- the two rules for a real check: `argv` as a LIST (never a shell string — no quoting bugs, no injection) and bare binary names (never absolute paths — the env inherits the operator's shell; the sandbox image provides `impeccable` + `snyk`);
- resolution: configured entries + honest placeholders for unwired defaults (via `_specs`); what a placeholder does and why a silent false-green is forbidden;
- the shipped-by-default checks (`design`, `snyk`) and how to opt out (remove the entry; adjust `site/dist` to the project's real surface);
- wiring examples for real runners: `["bun", "test"]`, `["uv", "run", "pytest", "-q"]`, `["npm", "run", "lint"]`;
- how a check surfaces: `gate_results` row (`gate: quality:<name>`), the failure envelope to the builder, the dashboard quality-gate KPI;
- `timeout_seconds` guidance (tests 600, build 300, typecheck/lint/design 120–300).

```astro
---
import DocsLayout from '../../layouts/DocsLayout.astro';

const description =
  'Every option in quality.checks: fields, argv rules, placeholders, shipped defaults, and wiring examples.';
---
<DocsLayout
  title="Quality checks"
  description={description}
  current="/docs/quality-checks"
  prev={{ href: '/docs/design-quality', label: 'Design quality' }}
  next={{ href: '/docs/visualizer', label: 'Visualizer' }}
>
  <p>
    <code class="hl">quality.checks</code> turns deterministic commands into gates: known
    invocations that run as code, cost nothing, and return the same answer every time. Agents
    are for the parts that need reading and deciding — the commands themselves belong here.
  </p>

  <h2>The fields</h2>
  <pre><code>- name: test              # string, unique; used as gate:quality:&lt;name&gt;
  area: backend             # frontend | backend | security | data
  operation: build          # build | lint | typecheck | test | security
  argv: ["bun", "test"]     # LIST — never a shell string, never npx
  timeout_seconds: 600      # per-run cap; on expiry the check exits 124</code></pre>
  <ul>
    <li><code>name</code> — unique check id; recorded as <code>gate:quality:&lt;name&gt;</code> in gate_results.</li>
    <li><code>area</code> / <code>operation</code> — classification; <code>security</code> is the snyk lane.</li>
    <li><code>argv</code> — the command as a LIST (no quoting bugs, no shell injection). Call
        binaries by BARE NAME: the check inherits the operator's shell env (and the sandbox
        image provides <code>impeccable</code>, <code>snyk</code>, <code>bun</code>, <code>uv</code>,
        <code>node</code>). Never an absolute path — that bakes one machine into the trace.</li>
    <li><code>timeout_seconds</code> — tests ~600, builds ~300, lint/typecheck/design ~120–300.</li>
  </ul>

  <h2>Shipped by default</h2>
  <pre><code>- name: design     # impeccable detect site/dist — the 59-rule design gate
- name: snyk       # snyk test — security; needs SNYK_TOKEN (forwarded into the sandbox)</code></pre>
  <p>Both ship configured because the runner image provides the binaries. Projects without a
     design surface remove <code>design</code>; projects without snyk setup remove <code>snyk</code>.</p>

  <h2>Placeholders</h2>
  <p>Unwired defaults run an honest <code>echo PLACEHOLDER ...</code> that exits 0 — it never
     silently passes, it says out loud that it is fake. A wrong-but-plausible command that
     silently passes is worse than one that says so.</p>

  <h2>Wiring examples</h2>
  <pre><code>argv: ["bun", "test"]            # bun projects
argv: ["uv", "run", "pytest", "-q"]   # python (uv)
argv: ["npm", "run", "lint"]          # node projects
argv: ["impeccable", "detect", "site/dist"]   # design surface (adjust the target)</code></pre>

  <h2>Where the result goes</h2>
  <p>A check writes its log to the session's <code>context_handoff/quality/&lt;seq&gt;_&lt;name&gt;/command.log</code>,
     records a <code>gate:quality:&lt;name&gt;</code> row (the dashboard's quality-gate KPI), and a failure
     rides back to the builder inside the envelope — verbatim output, no parser between the
     failure and the fix.</p>
</DocsLayout>
```

- [ ] **Step 2: Add the sidebar entry**

In `site/src/components/DocsSidebar.astro`, in the `Reference` group, before the Visualizer entry:

```ts
      { href: '/docs/quality-checks', label: 'Quality checks' },
```

- [ ] **Step 3: Verify the site builds**

Run: `cd site && npm run build`
Expected: build succeeds, `/docs/quality-checks` in `dist/docs/quality-checks/index.html`

- [ ] **Step 4: Commit**

```bash
git add site/src/pages/docs/quality-checks.astro site/src/components/DocsSidebar.astro
git commit -m "docs(site): quality-checks configuration reference page"
```

---

### Task 7: Site docs — design-quality page, demo callout, config pointer, README

**Files:**
- Create: `site/src/pages/docs/design-quality.astro`
- Modify: `site/src/components/DocsSidebar.astro`
- Modify: `site/src/pages/docs/index.astro` (inkwell demo callout)
- Modify: `site/src/pages/docs/quickstart.astro` (inkwell demo pointer)
- Modify: `site/src/pages/docs/configuration.astro` (pointer to quality-checks + designer)
- Modify: `README.md`

**Interfaces:**
- Consumes: `DocsLayout`; existing `docs/index.astro` toc array.
- Produces: `/docs/design-quality` page; visible demo-project callout linking `https://github.com/felipemm/inkwell`.

- [ ] **Step 1: Create the design-quality page**

`site/src/pages/docs/design-quality.astro` — model on `configuration.astro`. Content:

- the deterministic gate: `impeccable detect` — 59 rules, exit 0/2 semantics, advisory-only em-dash rule false positive on `--` CLI flags;
- the agentic pass: new `designer` agent + `adw_plan_build_test_quality_design` ADW — init (PRODUCT.md) → design (audit → critique → polish → optimize) → verify (incl. the detect gate) → document (DESIGN.md);
- how to opt in: choose the variant ADW, adjust `site/` target + the `design` check argv, ensure the runner image has impeccable;
- cross-link to `/docs/quality-checks` for full configuration.

```astro
---
import DocsLayout from '../../layouts/DocsLayout.astro';

const description =
  'Impeccable in sssf: the deterministic detect gate, the agentic design pass, and how to opt in.';
---
<DocsLayout
  title="Design quality"
  description={description}
  current="/docs/design-quality"
  prev={{ href: '/docs/sandbox', label: 'Sandboxed runs' }}
  next={{ href: '/docs/quality-checks', label: 'Quality checks' }}
>
  <p>
    Design quality is enforced twice: a deterministic gate (<code>impeccable detect</code>, 59
    rules, no LLM) and an agentic pass (a <code>designer</code> agent running the impeccable
    skill). The agent proposes, the gate disposes.
  </p>

  <h2>The deterministic gate</h2>
  <p>
    <code>impeccable detect site/dist</code> ships configured by default as the
    <code>design</code> quality check. It exits 0 on a clean scan and 2 on hard anti-patterns —
    so the gate is pass/fail exactly like every other check. One advisory is a known false
    positive: the em-dash saturation rule counts <code>--</code> in CLI flags (e.g.
    <code>--adw-id</code>); it is advisory-only and never fails the gate.
  </p>

  <h2>The agentic pass</h2>
  <p>
    <code>adws/adw_plan_build_test_quality_design.py</code> adds three phases to the standard
    build-test chain: <code>init</code> (documenter runs <code>/impeccable init</code> →
    PRODUCT.md), <code>design</code> (designer runs audit → critique → polish → optimize on
    <code>site/</code>), and <code>document</code> (documenter runs <code>/impeccable document</code>
    → DESIGN.md). The deterministic gates — including <code>design</code> — verify the agent's
    work before anything commits.
  </p>

  <h2>Opting in</h2>
  <ul>
    <li>Run the variant ADW instead of the standard one.</li>
    <li>Point <code>design</code>'s argv at your real surface (default <code>site/dist</code>).</li>
    <li>Rebuild the runner image after any Dockerfile change — stale images keep failing the
        gates with exit 127.</li>
    <li>Full field reference: <a href="/docs/quality-checks">Quality checks</a>.</li>
  </ul>
</DocsLayout>
```

- [ ] **Step 2: Sidebar entry**

In `site/src/components/DocsSidebar.astro`, in the `Guides` group (after `Configuration`):

```ts
      { href: '/docs/design-quality', label: 'Design quality' },
```

- [ ] **Step 3: Demo project callout — docs index**

In `site/src/pages/docs/index.astro`, add a short section after the intro paragraph:

```astro
  <h2>Demo project</h2>
  <p>
    <a href="https://github.com/felipemm/inkwell">inkwell</a> is provided as a demo repo for
    testing sssf end-to-end: factory runs, quality gates (tests, typecheck, build, snyk),
    sandboxed runs, and the visualizer. Clone it and <code>sssf run</code> to see a traced
    chain from a clean checkout.
  </p>
```

- [ ] **Step 4: Demo pointer — quickstart**

In `site/src/pages/docs/quickstart.astro`, add this sentence in a natural spot after the first run example (adapt the element to the page's existing markup style):

```astro
  <p>
    Want to see it before wiring your own project? The
    <a href="https://github.com/felipemm/inkwell">inkwell</a> demo repo is ready to run —
    clone it and run <code>sssf run</code> to watch a full traced chain.
  </p>
```

- [ ] **Step 5: Configuration page pointer**

In `site/src/pages/docs/configuration.astro`, add a short paragraph (e.g., at the top or in a new section):

```astro
  <h2>Quality checks</h2>
  <p>
    Deterministic gates live under <code>quality.checks</code> — see
    <a href="/docs/quality-checks">Quality checks</a> for every option, and
    <a href="/docs/design-quality">Design quality</a> for the impeccable pass. The roster also
    includes a <code>designer</code> agent for the agentic design pass.
  </p>
```

- [ ] **Step 6: README mention**

In `README.md`, add one line in the feature/overview area:

```markdown
- Design quality: deterministic `impeccable detect` gate (shipped configured) + an opt-in agentic design pass (`adw_plan_build_test_quality_design`) — see the site's Design quality docs.
```

- [ ] **Step 7: Verify the site builds**

Run: `cd site && npm run build`
Expected: build succeeds; `dist/docs/design-quality/index.html` exists

- [ ] **Step 8: Verify impeccable still passes on the built site**

Run: `npx --yes impeccable detect dist`
Expected: exit 0

- [ ] **Step 9: Commit**

```bash
git add site/src/pages/docs/design-quality.astro site/src/components/DocsSidebar.astro \
        site/src/pages/docs/index.astro site/src/pages/docs/quickstart.astro \
        site/src/pages/docs/configuration.astro README.md
git commit -m "docs(site): design-quality page, inkwell demo callout, config pointer"
```

---

### Task 8: Rebuild image + end-to-end verification + host skill install

**Files:**
- No source changes — verification only (plus optional host environment setup).

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: rebuilt `sssf-runner:latest` image; verified gates; host impeccable skill for `--no-sandbox` runs.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest`
Expected: all tests pass (including the new `test_dockerfile_payloads.py`, `test_ci_workflows.py`, updated `test_templates.py`)

- [ ] **Step 2: Rebuild the runner image from the worktree**

```bash
docker build -t sssf-runner -f docker/sssf-runner.Dockerfile .
```

Expected: build succeeds (this fetches + installs impeccable@3.6.0; may take minutes)

- [ ] **Step 3: Verify the CLI in the image**

```bash
docker run --rm sssf-runner:latest sh -c 'impeccable --version && which impeccable'
```

Expected: prints a version and `/usr/local/bin/impeccable`

- [ ] **Step 4: Verify the skill lands in the pi home**

```bash
docker run --rm sssf-runner:latest sh -c 'ls $HOME/.pi/agent/skills/impeccable/SKILL.md'
```

Expected: the vendored SKILL.md path exists (entrypoint copy works)

- [ ] **Step 5: Run the design gate in the container against the built site**

```bash
docker run --rm -v "$PWD/site/dist:/site/dist" -w /work sssf-runner:latest \
  sh -c 'cd /site && impeccable detect dist'
```

Expected: exit 0 (advisory-only em-dash finding permitted)

- [ ] **Step 6: Install the impeccable skill on the host (for --no-sandbox runs)**

```bash
npx --yes impeccable install --providers=pi
```

Expected: skill installed into the pi home (`~/.pi/agent/skills/impeccable` or as the installer reports). Note: this mutates the host pi config — operator-approved in the design.

- [ ] **Step 7: Optional end-to-end — one sandboxed run of the variant ADW**

```bash
cd <a project with a site, e.g. the sssf site as a stamped project>
sssf run adws/adw_plan_build_test_quality_design.py "<prompt>"
```

Expected: init (PRODUCT.md) → design → verify (incl. design + snyk) → document (DESIGN.md) → commit, all green. If the project lacks SNYK_TOKEN, remove the `snyk` check for the test run.

- [ ] **Step 8: Final commit (nothing to commit — confirm clean tree)**

```bash
git status --short
```

Expected: clean (unless Step 6 wrote into the worktree — it installs to the host pi home, not the repo).

---

## Self-Review

**Spec coverage:** Goal ✓ (Tasks 1–8) · Deterministic gate config ✓ (Task 3) · ADW variant ✓ (Task 4) · Designer agent + prompts ✓ (Task 3) · Provisioning vendored skill + image + entrypoint ✓ (Tasks 1–2, 8) · CI gates ✓ (Task 5) · Docs (a) quality-checks + design-quality + config + README ✓ (Tasks 6–7) · Docs (b) PRODUCT.md via init + DESIGN.md via document ✓ (Task 4) · Demo project callout ✓ (Task 7) · Verification ✓ (Task 8).
