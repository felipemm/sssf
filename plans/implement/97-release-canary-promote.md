# 97 — Release: canary, blocked, promote, close-by-commits

Extends the #96 deploy flow (batch-to-dev release train) with the release
mechanics: the canary step runs only after the operator's terminal
confirmation; a canary failure parks the batch's tickets in `blocked`
(visible and actionable, never silently retried); promote executes the
per-project tompero canary-promote command after confirmation and polls
deployment status until fully promoted; the release closes every ticket
parsed from the MR's commit set (commits since the last tag) — implementation
tickets and their features close together.

## Where it lives

Human checkpoints stay host-side (ADR-0006, #96 precedent): the gates run in
`sssf flow deploy` after `_run_release_train` approves the batch
(ready-to-deploy). The adw_deploy chain stays deterministic (bump → MR → e2e →
release-candidate); the per-project canary/promote commands and poll settings
come from a new optional `release:` block in `adws/config/deploy.yaml`
(tompero is iFood-specific — absent = gates skipped with a note, release
train + close-by-commits still run).

## Sequence in deploy()

1. signoff (existing) → release train (existing) → approve → ready-to-deploy
2. canary gate: confirm at terminal (unless `--yes`) → run
   `release.canary.command` → non-zero exit → park batch in `blocked`
   (feedback = command stderr), return 1
3. promote gate: confirm (unless `--yes`) → run `release.promote.command` →
   poll `release.promote.status_command` every `poll_interval_s` until
   `promoted_match` in output or `poll_timeout_s` → failure/timeout → park
   batch in `blocked`, return 1
4. close-by-commits: commits since the last tag on the dev snapshot
   (fallback: the batch, `origin/main..dev`, when no tag exists); every
   ready-to-deploy ticket whose id (`#<tid>` or `sssf(<adw_id>)`) appears in
   the commit set moves ready-to-deploy → done

Declining a gate = pause: tickets stay ready-to-deploy (MR registered, monitor
watches), return 0.

## Machine edges (ticketing.py, host-side)

- `block_deploy_tickets(conn, ids, actor, feedback)` — ready-to-deploy →
  blocked with failure feedback (legal edge); only ready-to-deploy moves
- `close_release_tickets(conn, ids, actor)` — ready-to-deploy → done (legal
  edge); only ready-to-deploy moves (blocked/done stay)

Human unblock is the existing `sssf ticket backlog <id>` (blocked →
ready-for-agent is already legal).

## Files

- src/sssf/ticketing.py (+block_deploy_tickets, close_release_tickets)
- src/sssf/commands/flow.py (release gates + close-by-commits in deploy())
- src/sssf/templates/adws/config/deploy.yaml (documented release block)
- templates/adws/modules/adw_deploy.py (release-phase note)
- site docs cli.astro, CONTEXT.md, CHANGELOG.md
- tests: test_ticketing.py (3), test_flow.py (6 new + 2 updated)
