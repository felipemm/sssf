# #93 — Ralph loop: `sssf flow implement afk`

Parent: #86. Blocked-by #92 (implement flow + ticket machine) — merged (PR
#111). `sssf flow implement <ticket>` already runs one ready-for-agent ticket
end-to-end and drives the machine (claim → settle). What remains is the
UNATTENDED DRIVER: iterate the ready-for-agent queue one ticket per run,
each run a fresh context window, until the queue is empty or a round cap is
hit.

## Design

`sssf flow implement afk [--cap N] [--wait-seconds S] [--project P]
[--no-sandbox]` — the `afk` keyword takes the `implement` subcommand's
positional slot (exactly the issue's surface).

Each round:

1. **Query the queue**: `ticketing.backlog_tickets(conn)` — the
   `ready-for-agent` queue, oldest first (the same source the backlog reads).
   Empty ⇒ print + return 0 (done).
2. **Run the head ticket**: delegate to the existing `implement()` — it
   claims the ticket, spawns the chain, and settles (no-sandbox: here, from
   the ADW's exit code; sandboxed: the detached monitor, after the run ends).
   A failed run requeues the ticket fix-forward, so the next round re-picks
   it — bounded by `--cap`.
3. **Wait for the round to settle (sandboxed only)**: sandboxed `implement()`
   returns the moment the run spawns, so the loop must not dispatch the next
   ticket until the current run's machine settle lands — otherwise sandboxed
   runs stack concurrently. Poll the project db until the ticket's linked
   session is no longer `running` AND the ticket has left `in-progress`
   (session-end alone can race the monitor's settle, which runs right after
   the final sync). `--wait-seconds` bounds one round's wait (default 7200);
   a timeout returns 1 with a message — never stack a second run on a live
   one.

### Fresh context window

Each round is a brand-new run by construction: `--no-sandbox` dispatches a
fresh ADW subprocess (new session rows + events file under the round's
pinned adw_id); sandboxed rounds get a fresh container + worktree. The loop
driver itself never accumulates context — its body is a queue query, one
`implement()` call, and a poll.

### Cap semantics

`--cap` bounds ROUNDS (default 30, per the issue), not tickets. A
fix-forward-retried ticket consumes a round each retry; an empty queue ends
the loop early with exit 0. Hitting the cap with work remaining is also exit
0 — the loop ran as far as allowed (the operator re-runs `afk` to continue).

## Scope

1. **`flow.py`** — `implement_afk(cwd, explicit_project, cap, wait_seconds,
   no_sandbox)` + `_wait_for_run_settle(root, ticket_id, wait_seconds,
   poll_seconds=15)`; route the `afk` keyword through `implement()`.
2. **`cli.py`** — `implement` positional becomes optional (`afk` keyword);
   `--cap` + `--wait-seconds` flags.
3. **Tests** (`tests/test_flow.py`, the flow-command pattern) —
   iterates the queue; empty-queue termination; cap stops the loop; a failed
   round requeues and is re-picked; sandboxed rounds wait for the settle;
   wait timeout returns 1.
4. **Docs** — site `cli.astro` implement row, `CHANGELOG.md`.
