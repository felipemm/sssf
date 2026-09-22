# #92 — Implement flow: triage → build → review, unattended

Parent: #86. Blocked-by (#01 ticket machine core, #03 flows entry point) both
merged (PRs #100, #89). The implement chain already runs end-to-end
(triage → build → quality → builder self-review → review → commit, #89); what
remains is the MACHINE wiring: the flow must claim a ready-for-agent ticket,
move it to ready-for-signoff on success, requeue it fix-forward with the
reviewer's feedback on rejection, and keep every attempt in the ticket's run
history.

## The constraint that shapes the design

`tickets` is **project-owned**: the sandbox's per-run db never contains
tickets and `sync_run_db` never merges them (rundb.py). So the ticket machine
can only be driven from the HOST:

- **Start** (`ready-for-agent → in-progress` + `ticket_runs` row +
  `tickets.adw_id` link): in `flow.py implement()` at spawn time — mirrors the
  legacy `ticket run` path (ticket.py).
- **Finish** (`in-progress → ready-for-signoff` on success /
  `in-progress → ready-for-agent` + feedback on failure): host-side, after the
  run's outcome is known —
  - sandboxed runs: the monitor (`orchestrator.monitor_run`), which is the
    only host process that observes the sandboxed run's end;
  - `--no-sandbox` runs: `flow.py implement()` right after the blocking
    subprocess call returns.
  Both call one new helper: `ticketing.finish_implement_run(conn, adw_id)`.

## Scope

1. **`ticketing.finish_implement_run(conn, adw_id, *, actor)`** — settle a
   finished implement run's ticket:
   - resolves the outcome from the `sessions` row (missing row ⇒ fail — a run
     that never wrote a session is a failed run, same rule as
     `record_never_started`);
   - refuses to touch runs that are not implement-flow runs (session
     `adw_name` other than `adw_implement` ⇒ no-op — legacy `adw_simple_sdlc`
     runs settle by hand);
   - refuses to touch tickets it does not own (`WHERE adw_id=? AND
     status='in-progress'` — a ticket the operator already requeued, signed
     off, or handed to another run is never yanked);
   - success ⇒ machine transition to `ready-for-signoff` (audited);
   - failure ⇒ machine transition back to `ready-for-agent` with feedback
     attached (fix-forward). Feedback precedence: the reviewer's `ReviewOutput`
     envelope (blocking list, else unmet requirements), then the run's last
     `error` event (reason/error), then a generic line.
   - returns the outcome string (`"signoff"` / `"requeued"`) or `None`.
2. **`flow.py implement()`** — claim the ticket before the run spawns
   (transition + `ticket_runs` + `adw_id`, all audited); on a sandbox spawn
   failure, requeue via the machine (in-progress → ready-for-agent, feedback
   "sandbox spawn failed") instead of leaving the ticket stuck; in
   `--no-sandbox` mode, settle the ticket from the ADW's exit code via
   `finish_implement_run` after the blocking call.
3. **`_dispatch_chain`** — gains an optional `adw_id` so the no-sandbox path
   forwards `--adw-id <id>` to the ADW (today it mints its own — the ticket
   link would point at a run that never existed).
4. **`orchestrator.monitor_run`** — after the final merge, best-effort
   `finish_implement_run(tracer.conn, adw_id)` (guarded: a ticket-write
   hiccup must never crash the monitor).
5. Docs: flow.py + adw_implement.py module docstrings (machine transitions no
   longer "land with #92"), CHANGELOG entry.

## Seams

- Seam 1 (CLI): `tests/test_flow.py` — implement() claims the ticket, settles
  it on no-sandbox success/failure, requeues on sandbox spawn failure, and
  `--adw-id` forwarding in the no-sandbox dispatch argv.
- `tests/test_ticketing.py` — `finish_implement_run` unit tests: success →
  signoff, failure → requeued with reviewer feedback, non-implement runs
  untouched, unowned tickets never yanked, missing session ⇒ fail.
- `tests/test_sandbox_orchestrator.py` — the monitor settles an implement
  ticket after the run ends (extended monitor test).

## Out of scope

- Human signoff UI / workbench gate (the signoff step itself, #96/#97).
- The plan flow's ticket transforms (#91).
- Canary/promote/close-by-commits (#97).
