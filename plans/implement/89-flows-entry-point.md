# #89 — Flows as the only entry point + chain set reduction

Parent: #86. Blocked-by (#01 ticket machine core) merged via PR #100. The three
flow chains (plan/implement/deploy) are the new shipped template set; ad-hoc
`sssf run` is removed; run control moves to `sssf sandbox stop|restart`;
already-stamped projects keep legacy combos (refresh copies the new files in,
never clobbers; removal is manual).

## Scope

1. **Flow chains (templates)** — `adw_plan.py`, `adw_implement.py`,
   `adw_deploy.py` replace the 13 legacy combos as the shipped template set.
   Phase lists per the spec: plan (exploration → grill-with-docs → to-spec →
   to-tickets, exploration skippable), implement (triage → build → quality →
   builder self-review → review → commit), deploy (sandbox → signoff → bump →
   MR → e2e → release). Deep per-flow semantics (db ticket transforms,
   machine transitions, workbench, canary/promote/close-by-commits) land in
   #91/#92/#96/#97 — the chains here run end-to-end with real agent/code
   phases and honest deterministic behavior.
2. **`sssf flow` command** — `plan [ticket-id] [--skip-exploration]`,
   `implement <ticket-id>`, `deploy [--yes]` dispatch the flow chains through
   the existing chain runner (sandboxed by default, `--no-sandbox` honored).
   Read-only ticket validation (exists; implement requires ready-for-agent and
   no live run) — transitions are #92's seam.
3. **`sssf run` removed** — ad-hoc `sssf run <adw> "<prompt>"` is gone. Run
   control (`stop`/`restart`) moves to `sssf sandbox`; the viz cockpit shells
   the new form. Legacy ADWs in already-stamped projects stay runnable via
   `sssf ticket run` (unchanged) and `sandbox restart`.
4. **`init --refresh`** lands the three flow chains alongside legacy combos
   (copy-only-missing semantics already guarantee no clobbering).
5. Docs/AGENTS/CHANGELOG updated (no more `sssf run` references).

## Seams

- Seam 3 (chain declarations): `tests/test_flow_chains.py` loads the three
  template files and pins phase lists, ordering, owners, output types, gates,
  `when` conditions, required_agents.
- Seam 1 (CLI): `tests/test_flow.py` dispatch tests with monkeypatched spawns
  and a seeded ticket db (test_ticket_cli.py pattern).
- `tests/test_sandbox_cli.py` — stop/restart ported from test_run.py.
- `tests/test_templates.py` — 13 → 3 starter chains.

## Steps

1. RED: test_flow_chains.py (phase lists/gates/envelopes) → GREEN: templates.
2. RED: test_flow.py dispatch → GREEN: flow.py + cli.py wiring.
3. RED: sandbox stop/restart tests → GREEN: move _stop/_restart + register.
4. Templates count 13 → 3; remove legacy templates; update test_templates.py.
5. Viz cockpit sessionControl → `sandbox stop|restart` (+ tests, vue comment).
6. Docs/AGENTS/registry/README/CHANGELOG updates.
7. Full suite (pytest, ruff, mypy, bun test, npm build), code-review, commit, PR.
