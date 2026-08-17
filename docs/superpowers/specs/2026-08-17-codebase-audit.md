# Codebase Audit — Findings & Next Steps

Date: 2026-08-17
Status: Draft — audit findings, not yet a design

## Context

Full-repo audit of `felipemm/sssf` after a long feature run (layout v2, impeccable
design quality, ticketing, sandbox hardening). Several bugs this month escaped
because the code path had no test; this audit is the systematic pass to close the
pattern.

## A. Real bugs / risky patterns (fix first)

| # | Where | Issue |
|---|---|---|
| A1 | `src/sssf/commands/ticket.py:230` `_sandbox_enabled` | **Silent swallow** — `except Exception: return False` with no message. The identical bug in `run.py` was fixed in PR #20 (now loud); ticket.py's copy still silently degrades ticket runs to unsandboxed on any config error. |
| A2 | `src/sssf/sandbox.py:398` (teardown poll loop) | `except Exception: break` treats ANY docker error as "container gone" → possible premature teardown/sync on a docker hiccup. |
| A3 | `src/sssf/commands/viz.py:112` | `except Exception: pass` swallows healer-start failures before opening the browser. |

## B. Test gaps (the recurring escape hatch)

Every bug below shipped because the path had **no test**. The pattern: new behavior added without a regression test → months later a refactor breaks it silently.

| # | Gap | Bug it would have caught |
|---|---|---|
| B1 | `sandbox_cmd.build` config resolution — no test | PR #28's `FileNotFoundError` on v2 projects (v1 path leftover) |
| B2 | Visualizer server route handlers (`index.ts` run/sync/backlog) — no tests | PR #27/28's stderr-swallow (empty `run failed`) |
| B3 | `status.ts` `ticketingEnabled` — no test | Same v1-path class as `tickets.ts isEnabled` (PR #26) |
| B4 | `ticket.py _sandbox_enabled` failure path — no test | The silent-unsandboxed bug (A1) |
| B5 | No integration test that an env-failure quality gate skips the builder | Issue #16 fix (templates assert the break exists; nothing proves the run actually skips the builder) |

## C. Duplication / clean code

| # | Where | Finding |
|---|---|---|
| C1 | `tickets.ts isEnabled` vs `status.ts ticketingEnabled` | Identical ticketing-enabled check, two copies — consolidate into one shared module |
| C2 | `run.py _sandbox_enabled` vs `ticket.py _sandbox_enabled` | Duplicate sandbox decision — consolidate (into `sandbox.py`), keep the loud failure |
| C3 | All 13 ADW templates | `config or str(paths.config_file(Path.cwd()))` repeated verbatim — extract `agents.default_config_path()` |
| C4 | ADW chains overall | 13 near-identical ~100-line chains (request→plan→build→verify→fix→commit); a shared chain-builder would remove ~1k lines of drift — **big refactor, separate project** |
| C5 | `ruff check` — **181 findings** | 44 unsorted imports, 25 unused imports (incl. stale `healer.py` import of `sandbox._session_status`, unused `sqlite3` in `sandbox.py:385`, unused `registry` in `cli.py`/`sandbox_cmd.py`), 18 `subprocess.run` w/o `check` (mostly intentional — returncode checked), 17 `datetime.utcnow`-era timezone patterns, 13 shebang issues, 7 blind-excepts, 5 unused vars — a lint-cleanup pass, then make ruff a CI gate |

## D. Stale / dead code

- `src/sssf/adw_modules/agent_cc.py` — intentional v1 stub (`claude_code` → NotImplementedError, documented); fine, but note the `coding_agent` config surface implies a v2 promise.
- The honest placeholders in `quality.py` are intentional (by design).

## E. Ops notes

- The stale-image guard + toasts now surface engine/image drift visibly. Consider a `post-merge` checklist item or CI hint: "engine changed → `sssf sandbox build`" (the guard fails loudly, but a proactive nudge saves a failed run).

## Next steps (prioritized)

1. **A1 + B4** — fix `ticket.py _sandbox_enabled` (loud, consolidate with run.py's) + regression test.
2. **C1 + B3** — consolidate the ticketing-enabled check (TS) + test both consumers.
3. **B1 + B2** — tests for `sandbox_cmd.build` config resolution; extract the ticket route handlers in `index.ts` so they're unit-testable, then test them (stderr surfacing, ok/error paths).
4. **C5** — lint cleanup: `ruff check --fix` the safe subset (unused imports, import sorting, `datetime.now(timezone.utc)`), fix stale imports by hand, then add a ruff CI job.
5. **C3** — ADW config-resolution helper (13 files → 1 helper + 13 one-liners).
6. **A2, A3** — targeted fixes for the teardown poll + viz healer swallow (tighten excepts, log).
7. **C4** — ADW chain consolidation as its own spec/plan (largest structural win; needs care).
8. **B5** — an integration-style test for the env-failure builder skip.

Each of 1–6 is a bounded change; 7 is architectural.
