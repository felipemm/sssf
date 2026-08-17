# Dev Tooling & CI Hardening — Design

Date: 2026-08-17
Status: Draft for review

## Goal

Bring the sssf repo to standard Python project hygiene: **ruff**, **mypy**,
**snyk**, **pre-commit**, plus `.editorconfig` / `.gitattributes`, with the new
checks wired into the CI `aggregate` gate so branch protection enforces them.
Motivated by the codebase audit (2026-08-17): 181 ruff findings, no type
checker, no lint/security CI gates — bugs have escaped for months through
paths no test or lint covered.

## Current state (measured)

- `pyproject.toml`: dev group = `pytest` only; no ruff/mypy/pre-commit config.
- `ruff check src/sssf tests`: **181 findings** (44 unsorted imports, 25 unused
  imports, 18 subprocess-without-check, 17 timezone-utc, 17 non-pep604
  annotations, 13 shebang issues, 7 blind-excepts, 5 unused vars, …) — **115
  auto-fixable**.
- `mypy src/sssf --ignore-missing-imports`: **6 errors in 3 files** (48 files
  checked) — close to clean.
- CI: `python` (pytest), `visualizer` (bun test), `site` (astro + impeccable),
  aggregated under the required `CI` check. No lint/typecheck/security jobs.
- No `.pre-commit-config.yaml`, `.editorconfig`, `.gitattributes`.

## 1. Ruff

`[tool.ruff]` in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
ignore = [
  "B008",          # function call in default arg (pydantic Field factories are idiomatic)
  "E501",          # line length is enforced by ruff-format, not lint
]
```

- **Run `ruff check --fix`** on the safe subset (unused imports, import sorting,
  timezone-utc, pep604 annotations where mechanical).
- **Intentional patterns stay, documented** — not muzzled:
  - `subprocess.run` without `check` (PLW1510): the engine checks returncodes
    explicitly; add a targeted `noqa: PLW1510` where a returncode check follows,
    or a module-level `# ruff: noqa: PLW1510` in the subprocess-heavy modules.
  - blind-excepts (BLE001) that are deliberate (daemon-never-dies, poll loops):
    narrow where cheap, add `noqa` + comment where intentional.
  - shebang-not-executable (EXE001) on the `#!/usr/bin/env -S uv run` ADW
    templates: mark executable or ignore — decide in the implementation (the
    `uv run` shebang is intentional; set EXE001 to warn-only or noqa the files).
- **CI job** `lint`: `uv sync --group dev && uv run ruff check src/sssf tests`.

## 2. Mypy

`[tool.mypy]` in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
check_untyped_defs = true
warn_unused_ignores = true
```

- Fix the **6 known errors** (3 files): `sandbox.py:152` (variadic argument),
  `commands/viz.py:23` (Traversable → PathLike), + 4 more (enumerate during
  implementation; the first pass reported exactly 6).
- **CI job** `typecheck`: `uv run mypy src/sssf`.

## 3. Snyk (repo-level security gate)

- **CI job** `security`: `uv run snyk test` (python deps) and
  `npx --yes snyk test` in `site/` (npm deps). Requires the `SNYK_TOKEN` repo
  secret (the runner image already uses `SNYK_TOKEN` for the per-project
  quality gate; add it to the GitHub repo secrets if absent).
- `snyk code test` (static analysis) is a follow-up if the SAST license permits
  — the CLI test (SCA) ships first.

## 4. Pre-commit

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: <pinned>
    hooks: [ruff, ruff-format]
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: <pinned>
    hooks: [trailing-whitespace, end-of-file-fixer, check-yaml, check-json,
            check-merge-conflict, mixed-line-ending]
```

- `pre-commit` added to `[dependency-groups].dev`.
- CONTRIBUTING gains a "local checks" section: `pre-commit install`,
  `uv run ruff check`, `uv run mypy`, `uv run pytest`.
- Note: pre-commit is a local developer convenience — CI enforces the same
  checks directly (ruff/mypy jobs), so a missing `pre-commit install` never
  bypasses the gates.

## 5. Other best practices

- `.editorconfig` — indent 4/2, utf-8, LF (matches the codebase).
- `.gitattributes` — `* text=auto`, `*.py text eol=lf`, binary overrides
  (`*.png`, `*.db` etc. as binary), linguist overrides for vendored dirs
  (`docker/impeccable-pi/`).

## 6. CI wiring

- New jobs `lint`, `typecheck`, `security` added to `ci.yml` and to the
  `aggregate` job's `needs` — branch protection's required `CI` check then
  enforces all of them.

## Verification

1. `uv run ruff check src/sssf tests` → clean (0 findings).
2. `uv run mypy src/sssf` → clean (0 errors).
3. `uv run snyk test` runs (token present) — findings (if any) are triaged in
   the PR, not silently ignored.
4. `pre-commit run --all-files` passes on the touched files.
5. CI: the new jobs go green and are wired into `aggregate`.

## Out of scope

- Fixing every historical ruff finding in unrelated code — the pass fixes what
  `--fix` + the config's ignores resolve; anything remaining in untouched code
  is tracked via the audit spec's next steps.
- `snyk code test` (SAST) — follow-up.
- ADW chain consolidation (audit next-step 7) — separate project.
