# Two sandbox concepts: runner and workbench

Work never happens directly on the host machine. Every unattended run executes
in an ephemeral **runner**: one reusable image, `docker run --rm` with the
worktree and `data_dir` bind-mounted, the ADW runs to completion inside and the
container exits. The interactive QA environment is a separate **workbench**: a
container from the `dev` branch with mocks and a published port, brought up by
`sssf flow deploy` and torn down by the human.

## Considered Options

- **Today's model (worktree-in-container + in-container supervisor + review
  block + host monitor + per-run db sync, ~1,400 lines)**: the supervisor
  idling after the run to keep the app up and the `review.command` config were
  the workbench's job, fused into every run. Rejected: it made every run carry
  interactive-preview machinery and a monitor daemon; the runner/workbench
  split lets each be simple and disposable.

## Consequences

- `review.command` is dropped from the config; the workbench replaces it.
- The runner is cheap enough for the ralph loop to spawn one per ticket.
