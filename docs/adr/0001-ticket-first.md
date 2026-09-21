# Ticket-first: every run belongs to a ticket

Every piece of sssf work is a ticket and a ticket is mandatory to start any
run — there is no ad-hoc `sssf run "<prompt>"`. Tickets are the aggregate
root: they own the lifecycle state, accumulate one or more runs (`ticket_runs`),
and drive the flows. The motivation: a factory that ships work unattended needs
a durable, traceable unit of work that survives retries and rework — a backlog
row that a run consumes once cannot hold a history of attempts, rejection
feedback, and release closure.

## Considered Options

- **Backlog item → run session (today's model)**: tickets were a read-only
  backlog that a run transformed into a session. Rejected because a retried or
  reworked ticket lost its history and nothing external could observe its state.
- **Tracker as source of truth**: see ADR-0002 — the internal db is truth, not
  the tracker.

## Consequences

- **Two ticket kinds with a one-way transform.** Idea tickets (title-only,
  `needs-triage`, created by `sssf ticket new` or `sssf flow plan` with a brief)
  are turned by the plan flow into a spec plus **implementation tickets**
  (born `ready-for-agent`, terminal — never re-planned). This is what makes
  recursive planning impossible: nothing the plan flow emits is a valid plan
  input. Plan is human-invoked only, plan-once unless `--revise`, and the
  parent/child lineage is rendered in the viz.
- **Ticket machine**: `needs-triage → ready-for-agent → in-progress →
  ready-for-signoff → ready-to-deploy → done`, plus `blocked` for canary
  failures. Rejection is fix-forward: back to `ready-for-agent` with the
  human's feedback attached.
