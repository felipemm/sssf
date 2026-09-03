# Review Containers & Run Lifecycle v2 — Design

Date: 2026-09-03
Status: Draft for review
Spec: `docs/superpowers/specs/2026-09-03-review-containers-design.md`
Repo: `~/dev/lab/mvp/sssf` (worktree `.worktrees/review-containers`, branch `feat/review-containers`)

## Goal

After a sandboxed run finishes, keep the run's container **up** so the engineer
can open the produced app and review the change; never delete run artifacts
(worktrees, containers, partial work) except through `sssf sweep`; and give the
visualizer the prompt, logs, and access instructions for every run.

This supersedes the "teardown leaves everything for debugging but stop/healer
delete it" contradiction: the engine now never deletes anything itself.

## Background / why

Session 9701903a (2026-09-02) surfaced the failures this design fixes:

1. The healer's budget-exhausted finalize called `stop_run`, which **removed the
   container and the worktree** — the builder's ~40 uncommitted files (tsc/test/
   build green) were destroyed and unrecoverable. The code's own
   `teardown_sandbox` documents "NEITHER the container nor the worktree is
   deleted — they are the debugging surface", but `stop_run`/healer-finalize
   contradicted it.
2. A restart therefore could only replay from the committed spec — the partial
   work was gone — and the replayed run re-planned from scratch.
3. There was no way to open the produced app: the container exited when the ADW
   (its PID 1) exited, and no ports were published.

## Decisions (confirmed with the engineer)

- **Restart replays the whole chain from `request`** (no mid-chain resume). It
  *reuses* everything the previous run did because nothing is deleted: the
  branch holds committed state; the preserved worktree holds uncommitted
  partial work and the per-run DB; the replay runs on top of all of it. The
  chain's `commit_all` model absorbs preserved uncommitted changes into the
  replay's own commits.
- **Only `sssf sweep` deletes run artifacts.** One carve-out: a re-run of the
  same session replaces its own container (same name `sssf-<adw_id>`, `docker
  rm -f` before `docker run` — the existing reuse mechanism the engineer chose
  to keep). Nothing else removes containers, worktrees, or branches.
- **The container is kept running after the run finishes** (review mode) so the
  engineer can open the app. Restart/stop/finalize stop the container but never
  remove it or its worktree.
- **Container identity stays deterministic** (`sssf-<adw_id>`) — no per-attempt
  names, no labels indirection.

## Architecture

A **supervisor** runs as the container's command instead of the ADW directly:
it runs the ADW command, then (regardless of ADW exit code) starts the
project's configured **review command** in the same container, then idles so
the container never exits on its own. The monitor (`monitor_run`) now ends when
the *run* has ended — signalled by the supervisor's exit marker or a terminal
session row in the per-run DB — instead of when the container disappears, and
performs its final merge without stopping the container.

Ports: at `docker run`, publish the review app's container port loopback-only on
a **random host port** (`-p 127.0.0.1::<container_port>`), so concurrent runs
never collide. The resolved host port is recorded in a new host-owned
`sandbox_run` table alongside the review command, instructions, and container
state; the visualizer reads it to offer prompt / logs / review-access.

Data safety: `stop_run` (engineer stop and healer finalize) becomes
stop-container-only; `abort_sandbox` (failed spawn) stops rather than removes;
`sandbox prune` is deprecated in favor of `sweep`; the healer's orphan
auto-cleanup no longer deletes. `sweep` (already the sanctioned bulk cleanup:
archive + remove container + worktree) additionally deletes the `sandbox_run`
row and is the only deleter left.

## Requirements

### R1 — Only `sssf sweep` deletes run artifacts

- [ ] `stop_run(root, adw_id, data_dir, reason)` stops the container
      (`docker stop`, keep it) and does **not** remove the worktree. Used by
      `sssf run stop` and by healer finalize paths (dead run, restart budget
      exhausted) — those keep the debugging surface too.
- [ ] `abort_sandbox` stops the (possibly 'Created'-stuck) container, never
      removes it; the worktree stays (already true).
- [ ] `sssf sandbox prune` prints "cleanup is `sssf sweep` only" and does
      nothing (deprecated; `prune_sandbox`/`delete_branch` stop being called by
      commands).
- [ ] Healer `_clean_orphans` stops deleting orphaned containers/worktrees; it
      only reports them (`sweep` is the cleanup). Nothing else in `healer.py`
      deletes: finalize and budget-exhausted go through the new `stop_run`.
- [ ] `run_sandbox` keeps its pre-run `docker rm -f <name>` — the sanctioned
      same-session container reuse on re-runs.
- [ ] `sweep` remains the only bulk deleter: removes the session's container +
      worktree + `sandbox_run` row (+ branch? — see Open Questions).

### R2 — The container stays running after the run (review mode)

- [ ] New `sssf/adw_modules/supervise.py`: `python -m sssf.adw_modules.supervise
      -- <adw cmd...>`.
      Behaviour (inside the container, cwd = the worktree):
      1. run `<adw cmd...>` as a subprocess, stream/ignore output (docker logs
         captures it);
      2. after it exits (any code), if the project's `sandbox.review.command`
         is configured (read from `adws/config/sssf.config.yaml`), run it
         (foreground child of the supervisor);
      3. if the review command exits or is not configured, idle forever
         (`while True: time.sleep(3600)`) — the container never exits on its
         own;
      4. after the ADW exits, write the exit marker
         `adws/data/sessions/<adw_id>.supervisor-exit` (bind-mounted worktree)
         containing the ADW exit code — the monitor's end-of-run signal.
- [ ] `run_sandbox` takes the container command as
      `["python", "-m", "sssf.adw_modules.supervise", "--", *cmd]`.
- [ ] `monitor_run` ends when the run has ended: poll for the supervisor-exit
      marker (or a terminal session row in the per-run DB) as well as container
      death; on end, sleep briefly for last writes, run `record_never_started`
      (spawn-death: ADW never wrote a session row) + the final
      `sync_run_db`, then exit **without** stopping the container.
- [ ] Healer diagnosis unchanged: it only scans `status='running'` sessions, so
      an idling review container is never a "hung run". (A run whose ADW ended
      has a terminal host row after the final sync.)

### R3 — Configurable review command (how to run the app in the container)

- [ ] `SandboxConfig` (adw_modules/data_types.py) gains `review`:
      ```yaml
      sandbox:
        image: sssf-runner
        enabled: true
        review:
          command: ["npm", "run", "dev", "--workspace=web"]  # argv list; run in /work after the ADW exits
          container_port: 3000                                 # the app's port inside the container (published)
          instructions: "Open the URL and sign in as Local Dev via the mock IDP."  # shown in the viz
      ```
      pydantic model `ReviewConfig(command: list[str] | None = None,
      container_port: int | None = None, instructions: str = "")` with
      validation (port 1–65535 when set; command list non-empty strings).
      Update the config template `src/sssf/templates/adws/config/sssf.config.yaml`
      (commented example) and the sandbox docs page
      (`site/src/pages/docs/sandbox.astro`).
- [ ] Ports are published only when `container_port` is set.

### R4 — Random host port, recorded for the viz

- [ ] `run_sandbox` adds `-p 127.0.0.1::<container_port>` when the config sets
      it (docker picks a free host port).
- [ ] New host-owned table in the tracer SCHEMA (created in both dbs; merged
      never):
      ```sql
      CREATE TABLE IF NOT EXISTS sandbox_run (
        adw_id          TEXT PRIMARY KEY,
        container       TEXT NOT NULL,
        container_port  INTEGER,
        host_port       INTEGER,
        review_url      TEXT,
        review_command  TEXT,     -- json list, for the viz
        instructions    TEXT DEFAULT '',
        status          TEXT,     -- 'up' | 'stopped'
        updated_at      TEXT
      );
      ```
- [ ] `spawn_sandbox` records the row after the container starts: resolve
      `docker port <container> <container_port>/tcp` → host port; write the
      row (status `up`). No review config → still record container + status
      (logs button) with NULL ports.
- [ ] `stop_run` flips the row's status to `stopped` (URL kept for display).
- [ ] `sweep` deletes the row for each swept session.

### R5 — Visualizer: prompt, logs, and review access per run

- [ ] Server endpoints (scoped under `/api/projects/:project/sessions/:adw_id/`):
      - `GET .../review` → the `sandbox_run` row (+ live container state via
        `docker ps -a --filter name=sssf-<adw_id>` → `up`/`exited`/absent);
      - `GET .../logs?tail=N` → docker logs of `sssf-<adw_id>` (reuse the
        existing `containerLogs` helper); `""` when no container.
      - The prompt: the session detail already returns `session.request` —
        see R6 (no 500-char truncation) so it is the full prompt.
- [ ] `SessionTrace.vue` run-strip gains three actions (icon buttons + a small
      detail panel, shown when data exists):
      - **Prompt** — expandable block with the full `session.request`;
      - **Logs** — expandable docker-log tail (with refresh);
      - **Review** — when `review_url` exists: an `Open app ↗` link
        (http://127.0.0.1:<host_port>) plus the configured `instructions` and
        the review command; container state chip otherwise.
- [ ] Rebuild `dist` and restart the viz server to serve the new UI/code.

### R6 — Full prompt fidelity (restart replays the complete ask)

- [ ] `tracer.session_request` stores the **full** prompt (drop `[:500]`).
      Existing rows keep their truncated value; new runs are complete. Restart
      (`sssf run restart`) re-runs the full original ask.

### R7 — Restart semantics (replay from start, reuse everything)

- [ ] No change to the chain: restart replays `request → plan → …` from the
      start (as today, and as confirmed).
- [ ] `reopen_session` keeps flipping the host row (running / cleared ended_at)
      and clearing the previous run's host phases/events — with the per-run DB
      now preserved (R1), the first sync repopulates the full history
      (accumulated events/phases incl. prior attempts) so nothing is lost from
      the trace; numbering continues per attempt.
- [ ] Because nothing deletes the worktree, a restart's attach reuses it
      (already fixed) and the replay sees prior commits **and** surviving
      partial work.

## Non-goals / open questions

- Restart still runs the hardcoded `adw_simple_sdlc.py` even for
  `adw_sdlc_full` sessions (pre-existing; tracked separately).
- Should `sweep` delete the `sssf/<adw_id>` branch too? Branches carry merged
  deliverable history; default keeps them (manual `git branch -D`). Revisit if
  branches accumulate.
- The review app starts after **any** ADW exit (success or fail) — the engineer
  reviews whatever state exists. If a failed run's tree doesn't build, the
  container idles and logs explain why.
