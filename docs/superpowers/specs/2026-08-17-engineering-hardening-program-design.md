# Engineering Hardening Program — Design

Date: 2026-08-17
Status: Draft for review
Supersedes: `2026-08-17-dev-tooling-design.md` (scope merged with the codebase audit's next steps)

## Goal

Bring the sssf repo to standard engineering hygiene and close every finding of
the codebase audit (`docs/superpowers/specs/2026-08-17-codebase-audit.md`): dev
tooling (ruff, mypy, snyk, pre-commit, editorconfig, gitattributes) wired into
CI, the audit's bug fixes, consolidation work, the test gaps, snyk SAST, and the
ADW chain consolidation. The recurring failure this program kills: **bugs
escaping because a path had no test or no lint/type check**.

## Current state (measured, 2026-08-17)

- `ruff check src/sssf tests`: **181 findings** (115 auto-fixable).
- `mypy src/sssf --ignore-missing-imports`: **6 errors in 3 files** (48 files).
- CI has pytest + bun + site jobs; **no lint/typecheck/security gates**.
- No `.pre-commit-config.yaml`, `.editorconfig`, `.gitattributes`.
- Audit findings A1–A3 (silent swallows), B1–B5 (test gaps), C1–C5
  (duplication/clean code) are open.

---

## Work packages (each is its own reviewable unit)

### WP1 — Dev tooling (ruff, mypy, snyk, pre-commit, hygiene)

- `[tool.ruff]` (line-length 100, select E/F/I/UP/B/SIM/RUF; targeted ignores for
  the intentional patterns, documented not muzzled); `ruff check --fix` the safe
  subset; hand-fix or `noqa`+comment the rest — the **full cleanup** (previously
  "out of scope" — now in).
- `[tool.mypy]` (py311, ignore_missing_imports, check_untyped_defs,
  warn_unused_ignores) + fix the **6 known errors**.
- Snyk: CI `snyk test` on python + site deps (`SNYK_TOKEN` secret), **plus
  `snyk code test` (SAST)** — previously out of scope, now in.
- `.pre-commit-config.yaml` (ruff + ruff-format + hygiene hooks), `pre-commit`
  in the dev group, CONTRIBUTING "local checks" section.
- `.editorconfig`, `.gitattributes` (line endings, binary overrides, linguist
  overrides for vendored `docker/impeccable-pi/`).
- CI: `lint`, `typecheck`, `security` jobs wired into the `aggregate` gate.

### WP2 — Audit bug fixes (A1–A3) + their tests

- **A1** `ticket.py _sandbox_enabled`: the silent-swallow sandbox decision
  (same class as the run.py bug fixed in PR #20) → loud message + consolidate
  with run.py's copy (C2). Test the loud path (B4).
- **A2** `sandbox.py` teardown poll: `except Exception: break` treats docker
  errors as run-complete → tighten (distinguish container-gone from docker
  error; log the error).
- **A3** `viz.py` healer-start `except Exception: pass` → log/surface.

### WP3 — Consolidations (C1–C3)

- **C1** `tickets.ts isEnabled` + `status.ts ticketingEnabled` (identical) →
  one shared module; test both consumers (B3).
- **C2** `_sandbox_enabled` (run.py + ticket.py) → one implementation in
  `sandbox.py`, loud on failure.
- **C3** `paths.config_file(Path.cwd())` repeated in all 13 ADW templates →
  `agents.default_config_path()` helper.

### WP4 — Test gaps (B1, B2, B5)

- **B1** `sandbox_cmd.build` config-resolution test (the PR #28 FileNotFoundError
  class).
- **B2** Visualizer ticket route handlers (index.ts run/sync/backlog) — extract
  the handlers for unit-testability, then test: ok path, failure path, stderr
  surfacing.
- **B5** Integration-style test that an env-failure quality gate skips the
  builder (issue #16 behavior) — beyond the existing template assertions.

### WP5 — ADW chain consolidation (C4, architectural)

The 13 near-identical ~100-line chains (request→plan→build→verify→fix→commit)
collapse onto a shared chain-builder (a `chains.py` module with a declarative
phase list; each ADW becomes a short config). Removes ~1k lines of drift — the
structural cause of "one ADW fixed, nine stale". Risks: phase semantics must be
preserved exactly (each chain's quirks: simple_sdlc's review/retest loop, the
design variant's init/document phases); the engine's phase/tracer contract is
the API — no engine changes. Rolled out chain-by-chain with the existing
template tests as the safety net (13-chain import test, phase-assertion tests).

## Execution order

WP1 → WP2 → WP3 → WP4 → WP5. WP1 lands the gates that make the rest safe to
touch (lint/typecheck catch regressions); WP5 last, alone, with its own review.

## Verification

1. `uv run ruff check src/sssf tests` → 0 findings.
2. `uv run mypy src/sssf` → 0 errors.
3. `uv run snyk test` + `uv run snyk code test` run (token present); findings
   triaged in the PRs, not silently ignored.
4. `pre-commit run --all-files` passes.
5. CI aggregate gates on lint + typecheck + security.
6. Audit items A1–A3, B1–B5, C1–C3 closed; WP5 chains behave identically
   (13-chain import + phase tests + one e2e run).
7. Full python suite + visualizer suite green throughout.

## Out of scope (explicitly deferred)

- None from the audit or the tooling spec — everything listed is in. Only
  speculative items (e.g. dependabot, coverage gates) remain as possible
  follow-ups, not part of this program.
