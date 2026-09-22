# 91 — Plan flow: idea → spec → implementation tickets

## Goal

`sssf flow plan` turns a vague idea into a spec plus implementation-ticket
children in one human-invoked pass, and can never be re-planned by accident.
This lands the db-level transform (#91) on top of #89's chain skeleton:

- `sssf flow plan <ticket-id>` transforms an idea ticket into a spec
  reference plus implementation children (born `ready-for-agent`, linked to
  the parent); the parent records the lineage.
- `sssf flow plan` (no args) runs exploration → brief → idea ticket → spec →
  slices in one pass — the idea ticket is created from the landed spec.
- Each plan step runs in its own fresh agent session (no context bleed
  between explore / grill / spec / tickets).
- Implementation tickets are terminal plan inputs (never planned); an
  already-planned idea ticket needs `--revise` — the only re-plan escape.
- Plan-once recursion guard: nothing the plan flow emits (implementation
  children, the planned parent) is a valid plan input without `--revise`.

## Machine wiring (mirrors #92's implement-flow pattern)

`tickets` is PROJECT-owned (the sandbox per-run db never carries tickets), so
the transform runs host-side only: in the sandbox monitor after a sandboxed
run ends, and in `flow.plan` from the ADW's exit code for `--no-sandbox`.

- **Dispatch (ticket path):** guards (`plan_guard`) then link the run —
  `tickets.adw_id = <adw_id>` + a `ticket_runs` row, BEFORE the spawn (the
  live-run guard and the settle both read the link). The parent stays
  `needs-triage` — the machine has no plan-claim edge (needs-triage →
  ready-for-agent only), so the transform is the settle, not a transition.
- **Settle (`finish_plan_run`):** only a SUCCESSFUL `adw_plan` session lands.
  Finds the linked idea ticket (`tickets.adw_id = run AND kind='idea'`);
  missing link = no-args mode → create the idea ticket first (title = the
  spec's first `# ` heading). Reads the spec + tickets artifacts from the
  run's root (sandbox worktree when `sandbox_run` has a row, else the
  project), sets `parent.spec` = committed relative path, and inserts one
  implementation child per `## ` heading of the breakdown (title = heading,
  description = body). Lineage: `planned` event on the parent + `created`
  event per child.
- **Revise:** `--revise` relaxes the dispatch guard. At settle time, stale
  children still `ready-for-agent` are commented "superseded by re-plan"
  (audited, never deleted — the machine has no ready → blocked edge to park
  them, and deletion is destructive); claimed children are left alone.
- **Failure:** a failed plan run lands nothing (parent untouched, the failed
  run stays linked in the trace). A sandboxed spawn failure leaves the
  parent `needs-triage` (nothing to requeue) — the operator re-runs.

## Fresh session per step

`AgentCall.fresh_session` (+ `AgentPhase.fresh_session`) makes
`agents._agent_session_id` mint a new pi session id even when the run's
agent_map has a live session for that agent. The plan chain sets it on all
four agent phases — grill/spec/tickets previously reused ONE planner session
(context bleed); now each step starts clean and only the envelope hands off.

## Files

- `src/sssf/ticketing.py` — `PLAN_ADW`, `plan_guard`, `parse_ticket_breakdown`,
  `_spec_title`, `_run_root`, `_plan_artifacts`, `finish_plan_run`, `_apply_plan`
- `src/sssf/commands/flow.py` — `plan()` guards + link + no-sandbox settle
- `src/sssf/cli.py` — `--revise` flag on `flow plan`
- `src/sssf/sandbox/orchestrator.py` — monitor lands plan tickets best-effort
- `src/sssf/adw_modules/data_types.py` — `AgentCall.fresh_session`
- `src/sssf/adw_modules/chains.py` — `AgentPhase.fresh_session` → AgentCall
- `src/sssf/adw_modules/agents.py` — `_agent_session_id(run, agent, fresh)`
- `src/sssf/templates/adws/modules/adw_plan.py` — fresh_session on phases;
  strengthened spec/tickets directives (spec starts with `# ` title; one
  `## ` heading per ticket)
- tests: `test_ticketing.py`, `test_flow.py`, `test_sandbox_orchestrator.py`,
  `test_flow_chains.py`, agents/runner session test
- docs: `CHANGELOG.md`, `site/src/pages/docs/cli.astro`,
  `site/src/pages/docs/quickstart.astro`, this plan

## Testing (TDD)

- ticketing: guard matrix (missing / implementation / planned / revise);
  breakdown parser (H2 split, preamble drop, nested headings, empty);
  `finish_plan_run` matrix (non-plan session, failed run, no session, ticket
  path, no-args path, no-H2 raises, revise stacking + supersede comments)
- flow: guard refusals spawn nothing; link before spawn; no-sandbox success
  lands children; no-sandbox failure lands nothing; spawn failure keeps
  needs-triage; no-args success creates the idea ticket + children
- orchestrator: monitor lands plan tickets after a successful sandboxed run;
  failed run untouched
- chains: plan phases declare fresh_session; fresh mints a new agent session

## Out of scope

- Workbench / signoff / batch (deploy flow #96) and release close-by-commits
  (#97) — the parent's terminal state stays a later issue.
- The machine's ready → blocked edge (stale revise children are commented,
  not parked).
