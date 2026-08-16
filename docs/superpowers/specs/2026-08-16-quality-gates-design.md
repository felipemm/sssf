# Quality gates — real, per-project, visible

2026-08-16 — feature spec

## Problem

The quality-gate mechanism exists end-to-end but runs fake commands:

- `adw_modules/quality.py` ships `test`/`lint`/`typecheck`/`build` blocks whose
  argv are placeholder echoes — they exit 0 and say out loud that they are
  fake. A stamped repo cannot guess the project's test runner, so a
  wrong-but-plausible command that silently passes is worse than one that says
  so.
- Quality results record as `tool_call` events only — never as `gate_results`
  rows. The status dashboard's **Quality KPI** ("quality-gate pass rate",
  checks passed / checks total across `gate_results`) therefore counts only the
  agents' claim gates (`artifacts_exist`, `diff_matches_claims`,
  `verdict_consistent`, …), never the tests/lint/typecheck/build runs. The
  gates a factory should actually care about are invisible to the dashboard.

## Approach (agreed)

Three moves, all engine-level + one demo wiring:

1. **Per-project commands via config** — a `quality.checks` section in
   `adws/adw_sssf_config/sssf.config.yaml` (the roster file already declares
   agents there). `quality.py` builds its blocks from config; a name the
   project did not wire keeps its honest placeholder. No import/shadowing
   hacks; works identically in sandboxed runs (commands run with
   `cwd=run.repo_root` inside the container, binaries by bare name via the
   operator's environment).
2. **Quality results become gates** — every check also writes a `gate_results`
   row (`gate = "quality:<name>"`, one `GateCheck` per run: item = the
   command, ok = exit 0, note = `exit N, Ds — see <artifact>` on failure).
   The existing KPI aggregation (`status.ts` parses `checks_json` from every
   `gate_results` row) then counts the real commands with zero dashboard
   changes.
3. **Wire the demo** — inkwell's config gets its real command
   (`bun test`; it has no lint/typecheck/build scripts), so the next run's
   `test_N` phase verifies the actual suite.

Placeholders stay honest: the shipped template config carries the four
placeholder checks with guidance comments, so a stamped project sees the
mechanism and knows exactly what to replace.

## Changes

- `data_types.py` — `QualityConfig(checks: list[QualityCheckSpec])`; `SSSFConfig`
  gains `quality: QualityConfig` (default empty → package defaults apply).
- `quality.py` — `_default_spec()`/`_specs(run)` (configured checks in config
  order + placeholders for unwired names); `_block(run, name)` resolves each
  block's spec from config; `run_quality(run)` iterates `_specs(run)`; `_run()`
  records the `gate_results` row; banner/placeholder text points at
  `sssf.config.yaml`.
- `src/sssf/templates/sssf.config.yaml` — `quality.checks` section with the
  four placeholders + guidance (argv list, bare-name binaries, delete unused).
- `~/dev/lab/demos/inkwell/adws/adw_sssf_config/sssf.config.yaml` — real
  `test` command (`bun test`).
- `src/sssf/docs/customizing.md` — "your quality commands" guidance updated:
  per-project config, not engine-level module edits.

## Not in scope

- New dashboard UI (the KPI already aggregates `gate_results`).
- Lint/typecheck/build for inkwell (its stack defines no such scripts).
- Enforcement semantics changes: a failing quality block still fails the phase
  result and reaches the builder as an envelope; the repair loop is unchanged.

## Tests

`tests/test_quality.py` (4): unconfigured → honest placeholders run and record
gate rows; configured command replaces the placeholder (failure carries the
verbatim tail + red gate row); partial config falls back per-name; `run_quality`
runs every check and records one gate row each.
