# #93 — Ralph loop: `sssf flow implement afk`

Parent: #86. Blocked-by (#92 implement flow machine) merged (PR #111): the
implement flow already claims a `ready-for-agent` ticket, runs the implement
chain unattended, and settles the machine (success → ready-for-signoff,
failure → requeue fix-forward). What remains is the LOOP that works a whole
queue: `sssf flow implement afk` iterates the `ready-for-agent` queue one
ticket per run — each run a fresh context window — until the queue is empty
or the cap is hit (default 30 rounds, configurable).

## The constraint that shapes the design

`tickets` is **project-owned** (the machine is driven host-side only, per
#92). The loop therefore lives in `flow.py`, not in the ADW chain: each round
re-reads the host queue, hands the head ticket to the existing
`flow.implement()` (which claims → runs → settles), and repeats. The chain
itself is untouched — one run per ticket, exactly as `sssf flow implement
<ticket>` already behaves.

- **Fresh context window per run** comes free: `implement()` spawns a new
  process (or sandbox) per ticket with a freshly minted `adw_id`, and the
  agent session id derives from that `adw_id` (`sssf-<adw_id>-<agent>-…`,
  agents.py `_agent_session_id`). Nothing carries across rounds.
- **Termination**: the queue read is oldest-first (`backlog_tickets`). An
  empty queue ends the loop with 0. A failing run is requeued fix-forward by
  `implement()` — the ticket stays `ready-for-agent` (and at the head of the
  queue, since it is the oldest), so the loop keeps working it until it
  passes or the cap is reached. The cap is the only bound on an unpassable
  ticket — that is the documented ralph-loop contract.

## Scope

1. **`flow.implement_afk(cwd, explicit_project, cap, no_sandbox)`** — the
   loop:
   - `cap` validated (>= 1), default `DEFAULT_AFK_CAP = 30` (CONTEXT.md's
     documented default);
   - each round: fresh connection → `backlog_tickets()` → empty? print "queue
     empty after N run(s)" and return 0; else print the run header and call
     `implement(cwd, ticket_id, explicit_project, no_sandbox)`;
   - the connection is closed per round (the previous round's `implement()`
     writes on its own connection — no stale read snapshot, no lock held
     across a spawn);
   - exit code: 0 when the queue emptied; 1 when the cap was hit with
     tickets still `ready-for-agent` (work outstanding — a script re-invokes
     afk until 0).
2. **`cli.py`** — `sssf flow implement afk [--cap N] [--project P]
   [--no-sandbox]`: the `afk` sentinel in the `ticket_id` slot routes to
   `implement_afk` (mirrors CONTEXT.md's documented `sssf flow implement
   afk`); `--cap` defaults to 30 and is otherwise unused. Ticket ids are
   always `provider:xxx` shaped, so `afk` cannot collide with a real ticket.
3. **Docs** — cli.astro (afk row + `--cap`), quickstart.astro (mention the
   unattended queue loop), CONTEXT.md already documents the ralph loop
   (verify cap wording), CHANGELOG.md.

## Tests (TDD, flow-command seam in tests/test_flow.py)

- `implement_afk` iterates the queue one ticket per run: N ready tickets,
  simulated success sessions per dispatch → N calls (distinct adw-ids), all
  tickets end `ready-for-signoff`, return 0.
- Empty queue terminates immediately: no dispatch, return 0.
- Cap stops the loop: N > cap tickets → exactly cap calls, cap tickets
  signed off, the rest still `ready-for-agent`, return 1 (work remains).
- A failing ticket is retried until the cap (it stays at the head of the
  queue): one stubborn ticket, failing runs (no session row → `implement()`
  requeues fix-forward) → cap calls for the same ticket, return 1.
- Bad cap is refused loudly.

## Blockers / notes

- No live agent run is executed here — the loop is exercised against a real
  tmp-project db with the dispatch seam faked (same as the #92 machine
  tests).
- A failing ticket that never passes burns the whole cap on itself — that is
  the documented contract (the operator re-runs or intervenes); a future
  iteration could rotate the queue or park repeated failures in `blocked`.
