# Batch-to-dev release train, with per-ticket revert escape hatch

All ticket work lands on the `dev` integration branch as its own commit-set
(code-driven commits, `{ticket}: {title}`), forming a release train. Signoff is
batch-level on the dev snapshot: one workbench, one verdict. Rejection is
fix-forward — the feedback re-queues the failing tickets to `ready-for-agent`,
a new run stacks the fix on dev, and the workbench is rebuilt. A genuinely
unwanted ticket is reverted from dev by its own commits before the MR (rare,
human-driven). The MR dev→main carries the clean snapshot.

## Considered Options

- **Per-ticket branches merged to dev only when green**: clean rejection, but
  features rot on long-lived branches, integration bugs hide until composition,
  and the human pays two test rounds (each feature, then the batch). Rejected:
  batch catches integration problems at signoff — one round, on the real
  composition — and the revert escape hatch preserves the one real advantage of
  per-ticket green (cheap rejection) without the divergence cost.

## Consequences

- The MR pipeline is watched by a **monitor that lives inside the viz
  service** — 10-minute cadence, notify-only (green → "test now", failed →
  detail, merged → release flow). No separate daemon: stop viz, stop
  monitoring.
- Release closes **every ticket in its commit set** (parsed from commits since
  the last tag), not just the idea ticket.
