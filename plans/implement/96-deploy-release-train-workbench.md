# #96 — Deploy flow: batch-to-dev release train + workbench signoff

Parent: #86. Blocked-by (#03 flows entry point, #06 implement flow) both
merged (PRs #89, #92). The deploy chain already runs a deterministic skeleton
(sandbox → signoff → bump → MR → e2e → release, #89); what remains is the
BATCH mechanics per ADR-0005: signoff is batch-level on the dev snapshot —
one workbench, one verdict; rejection re-queues the failing tickets
fix-forward; a revert escape hatch removes a genuinely unwanted ticket from
dev by its own commits before the MR; the MR dev→main carries the clean
snapshot; and `review.command` is dropped — the workbench replaces the fused
interactive-preview machinery (ADR-0004).

## The constraint that shapes the design

`tickets` is **project-owned** (the sandbox per-run db never carries tickets
and `sync_run_db` never merges them). So every deploy machine edge is driven
from the HOST, in `flow.py` — exactly like the implement (#92) and plan (#91)
settles. Two more constraints:

- **Signoff cannot run inside a runner container**: the operator's terminal
  is on the host (a detached container's stdin is /dev/null — the old
  sandboxed signoff always rejected unless `--yes`). Deploy therefore stops
  dispatching a sandboxed chain; it is a host-side orchestration whose only
  container is the workbench.
- **The release train's git steps must land on `dev`** (bump commit, MR
  source), never on the operator's checkout. The chain runs in a release
  worktree checked out at the `dev` branch (`.worktrees/deploy-<adw_id>`);
  its per-run db (the worktree's `adws/data/sssf.db`) is merged into the
  project db with the existing `sync_run_db`, so the trace lands like any
  sandboxed run's.

## Scope

1. **Batch machine edges (`ticketing.py`)** — the deploy settle:
   - the batch = the dev snapshot's tickets: every ticket in
     `ready-for-signoff` (implement success lands its commits on dev via the
     integration merge, so `ready-for-signoff` IS "work is on dev awaiting the
     batch verdict"). Commit-message `#<id>` parsing stays a fallback for the
     MR title/body only — internal ticket ids (`internal:…`) never appear in
     commit messages reliably, so batch membership never depends on them.
   - `reject_deploy_batch(conn, ticket_ids, actor, feedback)` —
     `ready-for-signoff → ready-for-agent` (a legal machine edge) with the
     batch verdict as fix-forward feedback, audited per ticket.
   - `approve_deploy_batch(conn, ticket_ids, actor, mr_url, mr_iid)` —
     `ready-for-signoff → ready-to-deploy` (legal edge) once the MR exists,
     and registers the MR against each ticket (`ticket_mrs` upsert) so the
     #98 monitor watches them.
   - `revert_deploy_ticket(conn, ticket_id, actor, feedback)` — the escape
     hatch's edge: `ready-for-signoff → ready-for-agent`; only touches
     ready-for-signoff tickets (a ticket already past the MR is #97's
     blocked/operator territory — never yanked).
2. **`workbench.py` (new)** — the QA surface per ADR-0004: a detached
   worktree at the dev tip + a `docker run -d` container (the runner image,
   workbench `command` from `adws/config/deploy.yaml`, `container_port`
   published on a random host port) + a `workbench_runs` record + the URL.
   `--down` tears it down (stop+remove the container, remove the worktree);
   rejection tears it down ("the workbench is rebuilt"); approval leaves it
   up for the human while the MR/pipeline runs.
3. **`flow.py deploy()` rewrite** — host-side orchestration:
   verify `dev`; compute the batch; bring up the workbench; host-terminal
   signoff (one verdict, `--yes` is the explicit escape; rejection prompts
   "which tickets failed?" — blank = the whole batch — then re-queues them
   fix-forward and tears down); on approval, create the release worktree at
   `dev`, dispatch `adw_deploy` (cwd = worktree, adw-id pinned), sync its
   per-run db, read the MR record, then settle — chain ok ⇒
   `approve_deploy_batch` + MR registration; chain failed ⇒
   `reject_deploy_batch` with the run's feedback.
   New sub-commands: `--down` (human teardown of the workbench) and
   `--revert <ticket-id>` (revert the ticket's own commits from dev: commits
   whose message references `#<id>` or any of the ticket's run adw_ids, then
   the machine edge + push).
4. **`adw_deploy.py` chain restructure** — the workbench + signoff phases
   move OUT of the chain (host-side now); the chain is the deterministic
   release train: `bump → mr → e2e → release`. `_deploy_mr` also writes a
   machine-readable record (`adws/data/deploy/<adw_id>-mr.json`: title, url,
   iid) the flow reads for registration; the human-readable payload md stays
   for the no-glab case. `--yes` leaves the chain (it gated a phase that no
   longer exists).
5. **`review.command` drop (ADR-0004)** — `ReviewConfig` and
   `SandboxConfig.review` removed from `data_types.py`; the template
   `sssf.config.yaml` review block removed; `supervise.py` no longer launches
   a review command or idles (the runner runs the ADW, writes the exit
   marker, and EXITS — the container is cheap and disposable); `docker.py
   run_sandbox` drops `publish_port`; `orchestrator.spawn_sandbox` drops the
   `review` param; `rundb._record_sandbox_run` stops resolving a review URL.
   The `sandbox_run` schema columns stay (compat with existing dbs; the viz
   reader renders the "no review URL" fallback — panel removal is #94's viz
   work).
6. Docs: flow.py + adw_deploy.py docstrings, CONTEXT.md deploy description,
   CHANGELOG entry, site docs (cli.astro, quickstart.astro), and the
   `deploy.yaml` template gains the `workbench:` block.

## Seams

- Seam 1 (CLI): `tests/test_flow.py` — deploy() no longer dispatches a
  sandboxed chain; it brings up the workbench, signs off at the host
  terminal (monkeypatched input), re-queues on rejection, approves into the
  chain on `y`/`--yes`, and `--down`/`--revert` drive their own paths.
- `tests/test_ticketing.py` — the batch edges: rejection re-queues only
  ready-for-signoff tickets with the verdict feedback; approval moves them to
  ready-to-deploy + registers MRs; revert only touches ready-for-signoff.
- `tests/test_flow_chains.py` — the chain is now `bump → mr → e2e → release`;
  the executor test runs it on a real repo and the MR record json lands.
- `tests/test_sandbox_config.py` / `test_sandbox_orchestrator.py` —
  no `review` anywhere; spawn_sandbox signature.
- `tests/test_db_schema.py` — `workbench_runs` table; `scripts/gen_viz_types.py`
  regenerated (drift fails CI).

## Out of scope

- Canary / promote / close-by-commits (release semantics, #97 — the deploy
  chain's release phase still only computes the candidate).
- The viz workbench/issue-tracker UI (#94) — the workbench is surfaced in the
  terminal (URL + teardown hint); the vestigial viz review panel stays until
  #94.
- Multi-source sync / untracked tickets (#90), hermetic skills (#88).
