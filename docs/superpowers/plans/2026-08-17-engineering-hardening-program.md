# Engineering Hardening Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the engineering hardening program — dev tooling (ruff/mypy/snyk/pre-commit/hygiene) wired into CI, the audit's bug fixes, consolidations, test gaps, and the ADW chain consolidation — on `feat/hardening-program`.

**Architecture:** Five work packages in order. WP1 lands the gates (ruff → 0, mypy → 0, snyk, pre-commit, CI wiring). WP2 fixes the audit's silent-swallow bugs. WP3 consolidates duplicated logic. WP4 closes the test gaps. WP5 collapses the 13 near-identical ADW chains onto a shared chain-builder.

**Tech Stack:** Python 3.11 (ruff, mypy, pytest, pre-commit), TypeScript/Vue (visualizer, bun test), GitHub Actions, YAML.

**Spec:** `docs/superpowers/specs/2026-08-17-engineering-hardening-program-design.md` (and the audit it derives from: `docs/superpowers/specs/2026-08-17-codebase-audit.md`)

## Global Constraints

- Work on `feat/hardening-program` (the single consolidated branch; main checkout is on it — work in place, commit per task, push at phase ends). PR #30 carries the docs; implementation lands as follow-up commits/PRs.
- Every phase ends with: `uv run pytest` green AND `cd src/sssf/apps/visualizer && bun test` green.
- `ruff check src/sssf tests` and `mypy src/sssf` are clean at the end of WP1 and stay clean for the rest of the plan.
- Intentional patterns get `noqa` + a comment, never a blanket muzzle (no `# noqa` without a reason).
- Do not change engine/ADW phase semantics in WP1–WP4; WP5 preserves each chain's behavior exactly (the 13-chain import test + phase-assertion tests are the safety net).
- snyk findings are triaged in the PR, not silently ignored.
- Conventional commit messages.

---

# WP1 — Dev tooling

### Task 1.1: Tool configs in pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1:** Add to `[dependency-groups].dev`:
```toml
dev = ["pytest", "ruff", "mypy", "pre-commit"]
```

- [ ] **Step 2:** Add the tool configs:
```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
ignore = [
  "B008",   # function call in default arg (pydantic Field factories are idiomatic)
  "E501",   # line length is enforced by ruff-format, not lint
]

[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
check_untyped_defs = true
warn_unused_ignores = true
```

- [ ] **Step 3:** Verify the tools resolve:
Run: `uv sync --group dev && uv run ruff --version && uv run mypy --version`
Expected: versions print

- [ ] **Step 4: Commit**
```bash
git add pyproject.toml uv.lock
git commit -m "chore(tooling): ruff + mypy + pre-commit configs and dev deps"
```

### Task 1.2: Ruff cleanup — 181 → 0

**Files:** repo-wide (src/sssf, tests, site)

- [ ] **Step 1:** Auto-fix the safe subset:
```bash
uv run ruff check src/sssf tests --fix
```
This resolves unused imports, import sorting, timezone-utc, pep604 annotations.

- [ ] **Step 2:** Review the remaining findings (`uv run ruff check src/sssf tests`):
  - `subprocess.run` without `check` (PLW1510): where a returncode check follows, add `  # noqa: PLW1510 — returncode checked below` on the line; where genuinely unchecked, add `check=True` or an explicit `check=False` + comment.
  - blind-excepts (BLE001) that are deliberate (daemon loops, health polls): narrow to the expected exception type where cheap; otherwise `except Exception:  # noqa: BLE001 — daemon must never die`.
  - shebang-not-executable (EXE001) on `#!/usr/bin/env -S uv run` ADW templates: these are templates executed via `uv run`, not directly — add `# ruff: noqa: EXE001` at the top of the 13 ADW files with a comment.
  - shebang-not-first-line (EXE005): fix by moving the shebang to line 1 if it's a template we control.
  - any remaining: fix by hand or `noqa`+comment.

- [ ] **Step 3:** Verify clean:
Run: `uv run ruff check src/sssf tests`
Expected: 0 findings

- [ ] **Step 4:** Run the suite:
Run: `uv run pytest -q && cd src/sssf/apps/visualizer && bun test`
Expected: green (the --fix pass must not change behavior — tests are the proof)

- [ ] **Step 5: Commit**
```bash
git add -A
git commit -m "chore(tooling): ruff clean — 181 findings to 0 (fix + documented noqas)"
```

### Task 1.3: Mypy cleanup — 6 → 0

**Files:** `src/sssf/sandbox.py`, `src/sssf/commands/viz.py`, + any surfaced by re-running

- [ ] **Step 1:** Run and enumerate:
Run: `uv run mypy src/sssf`
Expected: the known 6 errors in 3 files (sandbox.py:152 variadic arg; viz.py:23 Traversable→PathLike; + 4 more)

- [ ] **Step 2:** Fix each error with a real type fix (no `type: ignore` unless a third-party stub is wrong — then `# type: ignore[code]` + comment).
  - `sandbox.py:152`: `_docker(*args)` receiving a list — unpack correctly or type the helper to accept `list[str]`.
  - `viz.py:23`: `Path(traversable)` → coerce via `str(...)` or `os.fspath`.
  - Enumerate the remaining 4 during implementation and fix the same way.

- [ ] **Step 3:** Verify clean:
Run: `uv run mypy src/sssf`
Expected: 0 errors

- [ ] **Step 4: Commit**
```bash
git add src/sssf
git commit -m "chore(tooling): mypy clean — 6 errors to 0"
```

### Task 1.4: pre-commit, .editorconfig, .gitattributes

**Files:**
- Create: `.pre-commit-config.yaml`, `.editorconfig`, `.gitattributes`

- [ ] **Step 1:** `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json
      - id: check-merge-conflict
      - id: mixed-line-ending
        args: [--fix=lf]
```

- [ ] **Step 2:** `.editorconfig`:
```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space

[*.py]
indent_size = 4

[*.{ts,vue,js,mjs,json,astro}]
indent_size = 2

[*.{yaml,yml}]
indent_size = 2
```

- [ ] **Step 3:** `.gitattributes`:
```gitattributes
* text=auto
*.py text eol=lf
*.ts text eol=lf
*.vue text eol=lf
*.sh text eol=lf
*.png binary
*.jpg binary
*.db binary
*.db-wal binary
*.db-shm binary
docker/impeccable-pi/** linguist-vendored
```

- [ ] **Step 4:** Run pre-commit once to validate:
Run: `uv run pre-commit run --all-files`
Expected: hooks run; any findings (trailing whitespace, EOF newline) fixed or listed

- [ ] **Step 5:** CONTRIBUTING.md — add a "Local checks" section:
```markdown
## Local checks

Before pushing, run the same gates CI enforces:

- `uv run ruff check src/sssf tests`
- `uv run mypy src/sssf`
- `uv run pytest`
- `cd src/sssf/apps/visualizer && bun test`

Install the git hooks once: `uv run pre-commit install` (ruff + hygiene run on every commit).
```

- [ ] **Step 6: Commit**
```bash
git add .pre-commit-config.yaml .editorconfig .gitattributes CONTRIBUTING.md
git commit -m "chore(tooling): pre-commit hooks, editorconfig, gitattributes, local-checks docs"
```

### Task 1.5: CI — lint/typecheck/security jobs wired into the aggregate

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 1:** Add three jobs after the `site` job:

```yaml
  lint:
    name: lint (ruff)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - run: uv run ruff check src/sssf tests

  typecheck:
    name: typecheck (mypy)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - run: uv run mypy src/sssf

  security:
    name: security (snyk)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --group dev
      - name: Install snyk
        run: npm install -g snyk
      - name: Snyk python deps
        env:
          SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}
        run: uv run snyk test --fail-on=upgradable
      - name: Snyk npm deps (site)
        working-directory: site
        env:
          SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}
        run: npx --yes snyk test --fail-on=upgradable
      - name: Snyk code test (SAST)
        env:
          SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}
        run: uv run snyk code test --severity-threshold=high
```

- [ ] **Step 2:** Wire into the aggregate:
```yaml
  aggregate:
    name: CI
    runs-on: ubuntu-latest
    needs: [python, visualizer, site, lint, typecheck, security]
    steps:
      - run: echo "python + visualizer + site + lint + typecheck + security green"
```

- [ ] **Step 3:** Note: `SNYK_TOKEN` must exist as a repo secret (it exists for the runner quality gate; confirm via `gh secret list --repo felipemm/sssf` — if absent, the security job is skipped-or-fails; document the requirement in the PR body).

- [ ] **Step 4: Commit**
```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint (ruff), typecheck (mypy), security (snyk) jobs wired into the CI aggregate"
```

**WP1 done — push and open a PR for review before continuing.** (The gates now protect the rest of the plan.)

---

# WP2 — Audit bug fixes

### Task 2.1: ticket.py `_sandbox_enabled` — loud + consolidated (A1 + C2)

**Files:**
- Modify: `src/sssf/commands/ticket.py`, `src/sssf/commands/run.py`, `src/sssf/sandbox.py`
- Test: `tests/test_ticket_cli.py`

**Interfaces:**
- Produces: `sandbox.enabled(root, *, command)` in `sandbox.py` — the single sandbox decision, loud on failure.

- [ ] **Step 1: Write the failing test** (in `tests/test_ticket_cli.py`):

```python
def test_ticket_sandbox_failure_is_loud(tmp_path, monkeypatch, capsys):
    """A config error in the sandbox decision must be visible — never silently
    unsandboxed (audit A1)."""
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)   # no sssf.config.yaml
    from sssf.commands import ticket
    assert ticket._sandbox_enabled(root) is False
    assert "sandbox decision failed" in capsys.readouterr().err
```

- [ ] **Step 2:** Run — Expected: FAIL (ticket.py swallows silently, no stderr message)

- [ ] **Step 3:** Implement `sandbox.enabled(root, *, command) -> bool` in `sandbox.py`:
```python
def enabled(root: Path, *, command: str) -> bool:
    """The single sandbox decision. NEVER silently degrades to a local run: a
    missing config or a bug here is printed, not swallowed (audit A1)."""
    try:
        from sssf.adw_modules.agents import load_config
        cfg = load_config(str(paths.config_file(root)))
        return cfg.sandbox.enabled
    except Exception as error:
        print(f"sssf: sandbox decision failed for {command} ({error}) — "
              f"running unsandboxed", file=sys.stderr)
        return False
```
(Note: `paths` is already a module import in sandbox.py — verify.)

- [ ] **Step 4:** `run.py` `_sandbox_enabled` → delegate: `return sandbox.enabled(root, command="run")` (import `sandbox` at module top — the file already imports from it inside functions; add the module import). Keep the message text stable (tests assert `"sandbox decision failed"`).

- [ ] **Step 5:** `ticket.py` `_sandbox_enabled` → `return sandbox.enabled(root, command="ticket")`.

- [ ] **Step 6:** Run the ticket + run tests:
Run: `uv run pytest tests/test_ticket_cli.py tests/test_run.py -q`
Expected: PASS (including the new loud test)

- [ ] **Step 7: Commit**
```bash
git add src/sssf/sandbox.py src/sssf/commands/run.py src/sssf/commands/ticket.py tests/test_ticket_cli.py
git commit -m "fix(sandbox): consolidated loud sandbox decision — ticket.py no longer silently unsandboxed (audit A1, C2)"
```

### Task 2.2: sandbox teardown poll — no premature teardown on docker errors (A2)

**Files:** `src/sssf/sandbox.py` (the `monitor_run` poll loop, ~line 392), `tests/test_sandbox_docker.py`

- [ ] **Step 1: Write the failing test** — a docker `ps` that raises should NOT break the loop as "container gone":

```python
def test_teardown_poll_treats_docker_error_as_retry_not_gone(tmp_path, monkeypatch):
    """A docker hiccup during the teardown poll must not be read as
    'container gone' — that tears the run down prematurely (audit A2)."""
    from sssf.sandbox import monitor_run
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("docker hiccup")
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox, "_docker", flaky)
    # the loop must survive the first two errors and only exit when the
    # container is actually gone (empty stdout)
    # — assert via a fake tracer/session that monitor_run's poll keeps running;
    #   if the loop is not directly testable, extract the poll predicate:
    from sssf.sandbox import _container_gone
    assert _container_gone(flaky, "sssf-x") is False  # survived the hiccup
```

(If the loop isn't structured for this, refactor the poll into a small pure function `_container_gone(docker_fn, name) -> bool` that returns False on docker error, True only on empty output — then test THAT.)

- [ ] **Step 2:** Run — Expected: FAIL (current code breaks on any exception)

- [ ] **Step 3:** Implement: extract the poll predicate and change the loop:
```python
def _container_gone(docker_fn, name: str) -> bool:
    try:
        r = docker_fn("ps", "--filter", f"name={name}", "--format", "{{.Status}}",
                      timeout_s=30)
        return not r.stdout.strip()
    except Exception as error:      # docker hiccup — NOT 'gone'; retry
        print(f"sssf: teardown poll docker error ({error}) — retrying", file=sys.stderr)
        return False
```
The loop calls `_container_gone(_docker, container_name(adw_id))`; only a real `True` breaks.

- [ ] **Step 4:** Run: `uv run pytest tests/test_sandbox_docker.py -q` — PASS

- [ ] **Step 5: Commit**
```bash
git add src/sssf/sandbox.py tests/test_sandbox_docker.py
git commit -m "fix(sandbox): teardown poll retries on docker errors — no premature teardown (audit A2)"
```

### Task 2.3: viz healer-start swallow (A3)

**Files:** `src/sssf/commands/viz.py`, `tests/test_misc.py`

- [ ] **Step 1:** In `viz.py` (~line 112), replace `except Exception: pass` with a logged surface:
```python
        except Exception as error:   # noqa: BLE001 — keep the browser opening
            print(f"sssf viz: healer start failed ({error})", file=sys.stderr)
```
(The browser still opens; the failure is visible.)

- [ ] **Step 2:** Add a test in `tests/test_misc.py` that the message prints (monkeypatch `healer.start` to raise and `webbrowser.open` to no-op; assert stderr contains "healer start failed").

- [ ] **Step 3:** Run: `uv run pytest tests/test_misc.py -q` — PASS

- [ ] **Step 4: Commit**
```bash
git add src/sssf/commands/viz.py tests/test_misc.py
git commit -m "fix(viz): healer-start failure is surfaced, not swallowed (audit A3)"
```

---

# WP3 — Consolidations

### Task 3.1: ticketing-enabled check — one shared function (C1 + B3)

**Files:** `src/sssf/apps/visualizer/server/tickets.ts`, `server/status.ts`, `server/tickets.test.ts`, `server/status.test.ts`

- [ ] **Step 1:** In `tickets.ts`, export a named function and keep `isEnabled` as the alias; move the body to a shared module:

`server/ticketing.ts` (new):
```ts
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";

/** ticketing.yaml with an uncommented providers line → the kanban/status
 *  stages are enabled. v2 layout: adws/config/ticketing.yaml. */
export function ticketingEnabled(root: string): boolean {
  const path = resolve(root, "adws", "config", "ticketing.yaml");
  if (!existsSync(path)) return false;
  try {
    return readFileSync(path, "utf8")
      .split("\n")
      .some((line) => /^\s*providers\s*:/.test(line));
  } catch {
    return false;
  }
}
```

- [ ] **Step 2:** `tickets.ts`: `export { ticketingEnabled as isEnabled } from "./ticketing";` (or import + re-export — keep the existing call sites working).
- [ ] **Step 3:** `status.ts`: delete the local `ticketingEnabled`; import from `./ticketing`.
- [ ] **Step 4:** Tests: the existing `isEnabled` test in `tickets.test.ts` stays; add `status.test.ts` coverage that the status page's stage uses the same function (assert `ticketingEnabled` behavior on a v2 layout with providers + without).
- [ ] **Step 5:** Run: `cd src/sssf/apps/visualizer && bun test` — PASS
- [ ] **Step 6: Commit**
```bash
git add src/sssf/apps/visualizer/server
git commit -m "refactor(viz): single ticketing-enabled check (audit C1, B3)"
```

### Task 3.2: ADW config resolution helper (C3)

**Files:** `src/sssf/adw_modules/agents.py`, all 13 `src/sssf/templates/adws/modules/adw_*.py`, `tests/test_templates.py`

- [ ] **Step 1:** Add to `agents.py`:
```python
def default_config_path() -> str:
    """The project's config at the v2 path, resolved from the caller's cwd
    (the ADW runs with cwd=project root). One place instead of 13 copies."""
    from sssf.adw_modules import paths
    return str(paths.config_file(Path.cwd()))
```
(`Path` is already imported in agents.py — verify.)

- [ ] **Step 2:** In each of the 13 ADWs, replace:
```python
    from sssf.adw_modules import paths
    cfg = agents.load_config(config or str(paths.config_file(Path.cwd())))
```
with:
```python
    cfg = agents.load_config(config or agents.default_config_path())
```
(Remove the now-unused `paths` import if it was only used there.)

- [ ] **Step 3:** Update `tests/test_templates.py::test_adws_resolve_config_at_runtime`: assert `agents.default_config_path` is referenced instead of `paths.config_file`.

- [ ] **Step 4:** Run: `uv run pytest tests/test_templates.py -q` — PASS (13 chains import cleanly)

- [ ] **Step 5: Commit**
```bash
git add src/sssf/adw_modules/agents.py src/sssf/templates/adws/modules tests/test_templates.py
git commit -m "refactor(adws): one config-resolution helper across 13 chains (audit C3)"
```

---

# WP4 — Test gaps

### Task 4.1: sandbox_cmd.build config-resolution test (B1)

**Files:** `tests/test_sandbox_config.py` (or `test_sandbox_cli.py`)

- [ ] **Step 1:** Write the test — `sssf sandbox build` resolves the config at the v2 path (regression for PR #28's FileNotFoundError):

```python
def test_sandbox_build_reads_v2_config(tmp_path, monkeypatch, fake_docker):
    """sandbox build must load the config from adws/config (v2) — the v1 path
    crash (audit B1)."""
    root = tmp_path / "proj"
    (root / "adws" / "config").mkdir(parents=True)
    (root / "adws" / "config" / "sssf.config.yaml").write_text(
        "sandbox:\n  image: sssf-runner\n")
    monkeypatch.chdir(root)
    from sssf.commands import sandbox_cmd
    assert sandbox_cmd.build(None) == 0
    # and a v1-only project is refused with the migration banner:
    (root / "adws" / "config").rename(root / "adws" / "adw_sssf_config")
    monkeypatch.chdir(root)
    assert sandbox_cmd.build(None) != 0
```
(Adjust to the actual `build()` signature/behavior — the point is the v2 path loads and the v1 path doesn't.)

- [ ] **Step 2:** Run — PASS after any fixture alignment; Commit:
```bash
git add tests
git commit -m "test(sandbox): sandbox build resolves the v2 config path (audit B1)"
```

### Task 4.2: visualizer ticket route handlers — extract + test (B2)

**Files:** `src/sssf/apps/visualizer/server/index.ts`, new `server/ticketRoutes.ts`, new `server/ticketRoutes.test.ts`

- [ ] **Step 1:** Extract the run/sync/backlog handlers from `index.ts` into `server/ticketRoutes.ts` as plain functions taking `(projectRoot, id, spawnFn)`:
```ts
export interface SpawnResult { exitCode: number; output: string }

export async function runTicket(root: string, id: string,
                                spawnFn = bunSpawn): Promise<{ ok: boolean; adwId?: string; output: string }> {
  const proc = spawnFn(["sssf", "ticket", "run", id, "--project", root]);
  const [output, errout] = await Promise.all(
    [new Response(proc.stdout).text(), new Response(proc.stderr).text()]);
  const combined = (output + (errout ? "\n" + errout : "")).trim();
  if (proc.exitCode !== 0) return { ok: false, output: combined };
  const adwId = output.match(/adw_id ([a-f0-9]+)/)?.[1] ?? null;
  return { ok: true, adwId: adwId ?? undefined, output: combined };
}
// syncTicket + backlogTicket with the same shape (stderr surfaced)
```
`bunSpawn` wraps `Bun.spawn` with `{ stdout: "pipe", stderr: "pipe" }` and returns a handle with `stdout`/`stderr`/`exitCode` (await `proc.exited` first).

- [ ] **Step 2:** `index.ts` imports and calls the extracted functions (the routes keep their `isEnabled` guard + `json()` wrapping).

- [ ] **Step 3:** `server/ticketRoutes.test.ts` with a fake spawnFn:
- run success → `{ok: true, adwId}` and the output carries the spawn line;
- run failure → `{ok: false}` with **stderr content surfaced** (the PR #27/28 class);
- backlog + sync same shape.

- [ ] **Step 4:** Run: `cd src/sssf/apps/visualizer && bun test` — PASS
- [ ] **Step 5: Commit**
```bash
git add src/sssf/apps/visualizer/server
git commit -m "test(viz): ticket route handlers extracted + tested incl. stderr surfacing (audit B2)"
```

### Task 4.3: env-failure skips the builder — integration test (B5)

**Files:** `tests/test_quality.py` (or a new `tests/test_adw_env_failure.py`)

- [ ] **Step 1:** Write the test — an env-failure result must NOT produce a builder envelope:
```python
def test_env_failure_is_not_handed_to_the_builder(tmp_path):
    """The fix-loop ADWs break on env failures — the builder is never called
    with an environment error (audit B5, issue #16 behavior)."""
    run = _make_run(tmp_path, checks=[QualityCheckSpec(
        name="test", area="backend", operation="build",
        argv=["definitely-not-a-binary-xyz"])])
    result = quality.run_quality(run)
    assert quality.env_failure(result) is not None
    # the envelope path for the builder must not fire: as_envelope is only
    # for code failures
    env_fail = [c for c in result.checks if c.env_error]
    assert env_fail and quality.as_envelope(result, "quality gates").passed is False
```
(The template assertions in `test_templates.py` already prove the ADWs reference the break; this pins the data-layer contract.)

- [ ] **Step 2:** Run: `uv run pytest tests/test_quality.py -q` — PASS
- [ ] **Step 3: Commit**
```bash
git add tests
git commit -m "test(quality): env-failure results are never handed to the builder (audit B5)"
```

**WP2–WP4 done — push + open a PR for review before WP5.**

---

# WP5 — ADW chain consolidation (architectural)

### Task 5.1: the chain-builder core

**Files:**
- Create: `src/sssf/adw_modules/chains.py`
- Test: `tests/test_chains.py`

**Interfaces:**
- Produces the shared builder every chain migrates to:
  `Chain(name, phases: list[PhaseSpec])` where `PhaseSpec` is a dataclass
  (`name`, `kind`, `owner`, `description`, optional `fn` for code phases, optional
  `gates`/`output_type`/`previous` for agent phases, optional `loop` for the
  verify/fix loops).

- [ ] **Step 1:** Design the DSL to cover ALL 13 chains' shapes (this is the hard
  part — enumerate each chain's phases first):
  - plain linear: request → plan → build → commit
  - verify/fix loop: request → plan → build → verify_i (code, run_quality) → [fix_i (agent, builder)] → commit
  - simple_sdlc: + plan-commit + review_i loop + retest + document (its own quirks)
  - design variant: + init (documenter) + design (designer) + document
  - scout/prompt/quality/document: single-purpose chains with no build loop
  The DSL must express each with data, not code forks — where a chain has a
  genuinely unique phase (review loop), keep it as a registered phase-builder.

- [ ] **Step 2:** Implement `chains.py`: `run_chain(cfg, run, prompt, chain)` executing the phase list, with the shared helpers: `agent_phase(...)`, `quality_loop(...)`, `commit_phase(...)`. The env-failure break from issue #16 lives in the shared `quality_loop`.

- [ ] **Step 3:** `tests/test_chains.py`: build one representative chain (plan→build→verify/fix→commit) via the DSL and run it against the `_Run`-style harness from test_quality; assert the phase sequence + env-failure break.

- [ ] **Step 4:** Run: `uv run pytest tests/test_chains.py tests/test_templates.py -q` — PASS (old chains still present and green — the safety net)
- [ ] **Step 5: Commit**
```bash
git add src/sssf/adw_modules/chains.py tests/test_chains.py
git commit -m "feat(chains): shared ADW chain-builder core (audit C4)"
```

### Task 5.2 … 5.14: migrate the 13 chains, one per task

For EACH chain `adw_<name>.py` (order: simplest first — scout, prompt, quality,
document, plan, build, build_review, plan_build, build_test, plan_build_test,
plan_build_test_quality, simple_sdlc, design_sdlc):

- [ ] **Step 1:** Rewrite `src/sssf/templates/adws/modules/adw_<name>.py` to declare its phases via `chains.run_chain(...)` — the file becomes a short config (~20 lines) plus the CLI entry (`main` → argparse → run).
- [ ] **Step 2:** Preserve the chain's exact semantics: same phase names, owners, kinds, descriptions, gates, and the commit/finish behavior (accepted/reason).
- [ ] **Step 3:** Run the safety net: `uv run pytest tests/test_templates.py -q` (13-chain import + phase assertions) — the phase-assertion tests may need updating to read the new declarative form (assert the phase names exist in the DSL declaration).
- [ ] **Step 4: Commit**
```bash
git add src/sssf/templates/adws/modules/adw_<name>.py tests/test_templates.py
git commit -m "refactor(adws): adw_<name> onto the shared chain-builder"
```

### Task 5.15: final e2e + cleanup

- [ ] **Step 1:** Run everything: `uv run pytest -q && cd src/sssf/apps/visualizer && bun test`
- [ ] **Step 2:** One e2e run in a stamped project (fresh `sssf init` + `sssf run scout` and `sssf run simple_sdlc` with a trivial prompt, `--no-sandbox`) to prove the migrated chains behave.
- [ ] **Step 3:** Confirm no behavioral drift in the 13-chain import test + phase tests.
- [ ] **Step 4: Commit** (if any final tidy):
```bash
git add -A
git commit -m "chore(adws): chain consolidation complete — e2e verified"
```

---

## Self-Review

**Spec coverage:** WP1 (tooling + CI gates + full ruff cleanup + snyk SAST) ✓ Tasks 1.1–1.5 · WP2 (A1/A2/A3 + tests) ✓ 2.1–2.3 · WP3 (C1/C2/C3) ✓ 3.1–3.2 (C2 folds into 2.1) · WP4 (B1/B2/B5) ✓ 4.1–4.3 · WP5 (C4) ✓ 5.1–5.15 · Verification (ruff/mypy 0, snyk runs, pre-commit passes, aggregate gates, suites green) ✓ per-task + final · Order (gates first, consolidation last) ✓.

**Placeholder scan:** the plan carries concrete configs/code for WP1–WP4; WP5's Tasks 5.2–5.14 are intentionally mechanical-per-chain (each has the same 4-step shape) — the DSL design in Task 5.1 is where the real design work happens. No TBD/TODO.
