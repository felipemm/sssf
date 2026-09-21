# Internal database is the truth; trackers are sync endpoints

The local `tickets` table is always authoritative. Tickets arrive from any
source — `internal | jira | gitlab | github` — via CLI sync (`acli`, `glab`,
`gh`), and every ticket records its `origin` and `external_id`. State
transitions, labels, and comments are written back to the origin tracker
best-effort; write-back failures are recorded as events, never blocking the
flow. A `ticket_events` table keeps the full audit trail (transitions,
comments, labels, actor, timestamps) — like any ticket system.

## Considered Options

- **Tracker authoritative**: sssf would need write-compatible semantics across
  trackers and would block on tracker availability. Rejected because a single
  project mixes internal, jira, gitlab, and github tickets — no one tracker
  can be the source of truth for all of them.

## Consequences

- **Tracked vs untracked is permanent.** Plan-created tickets are *tracked*;
  synced tickets are *untracked* — the flag records origin of creation and
  never changes. Both follow identical rules and workflows.
- Untracked tickets never appear in the backlog (which shows only
  `ready-for-agent`); they live on the viz issue-tracker page until marked
  `ready-for-agent`, and can be enriched via `sssf flow plan <ticket-id>`.
