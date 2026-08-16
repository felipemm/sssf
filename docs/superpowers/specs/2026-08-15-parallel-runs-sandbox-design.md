# Parallel Runs & Sandboxed Isolation — Design Spec

**Date:** 2026-08-15 · **Branch:** `feat/parallel-runs` · **Status:** design (awaiting review)

sssf runs execute in isolated, per-run sandboxes so multiple runs can proceed
in parallel without touching each other or the project's working tree. Each
run ends in a **human review gate**: the changed app runs inside the sandbox,
the engineer tests it, and approves or rejects — which tears the sandbox down
and leaves the run's branch open for a PR.

## Why

Today `sssf run`/`sssf ticket run` execute the ADW with `cwd = project root` —
one shared working tree. Two runs in parallel commit over each other, collide
on enumerated prompt files, and leave the project mid-change. The data layer
is already per-session (`adw_id`-scoped rows in a shared WAL db), but the
**working tree** and the **environment** are not.

## Approach (agreed)

Port the proven patterns from [sandcastle](https://github.com/mattpocock/sandcastle)
(worktree-per-run branch strategy, container-isolated environment, sync/merge
back) natively in sssf's Python — **no new runtime dependencies**:

- **Working-tree isolation = git worktree per run** (branch `sssf/<adw_id>`)
- **Environment isolation = Docker container per run** with the worktree
  bind-mounted
- **Shared live data = the host's `adws/adw_data/`** bind-mounted read-write
  into the container (db + session events; board/trace/status stay live)
- **No auto-merge**: the branch is the deliverable; the engineer PRs it

Reference patterns (from research):
- disler/inkwell-agent-sandboxes: credential boundary (host keeps secrets,
  only per-run scoped values cross), orchestrator/sandbox split, harvest +
  teardown lifecycle.
- sandcastle: worktree branch strategy with collision-safe names (timestamp +
  random hex), bind-mount Docker provider, UID-matching container user,
  `safe.directory` + git identity set in the sandbox, two-phase sync-out with
  recovery artifacts, concurrency ADRs (`.git/config.lock`, fork races).

## Architecture

```
host repo (base branch)                       per-run sandbox
┌─────────────────────────────┐               ┌───────────────────────────────────┐
│ sssf run / ticket run       │               │ docker run sssf-runner            │
│  ├─ mint adw_id             │   worktree    │  ├─ python + git + node/pi + bun  │
│  ├─ worktree add -b         │──bind mount──▶│  ├─ cwd = worktree (own branch)   │
│  │    sssf/<adw_id>         │               │  ├─ runs the ADW (pi calls)       │
│  └─ docker run …            │               │  └─ review stage: dev server +    │
│        ▲                    │               │     wait for decision             │
│        │ shared data        │  bind mounts: │        │                          │
│  adws/adw_data/ (db,        │◀──────────────│        ▼                          │
│  sessions/, events)         │  adws/adw_data (rw)  shared db: review_status     │
│  ~/.pi/agent/, creds, .env  │──────────────▶│  (ro)                             │
└─────────────────────────────┘               └───────────────────────────────────┘
   approve/reject → teardown container + worktree → branch ref persists → engineer PRs
```

### The runner image (`docker/sssf-runner.Dockerfile` in the sssf repo)

- Base `python:3.11-slim` + apt `git`, `nodejs`/`npm` (pi runs on Node), `bun`
  (inkwell's app runtime — `bun run dev` runs inside the container).
- `sssf`: `COPY .` + `pip install .` (tracks the current sssf source; tag
  `sssf-runner:<sssf-version>`).
- `pi`: `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`.
- Entrypoint: `git config --global --add safe.directory /work`, set
  `user.name`/`user.email` (env), then `exec "$@"`.
- **No credentials and no project files in the image** (inkwell's credential
  boundary). At container start the host provides, read-only:
  `~/.pi/agent/` → the container's pi home, GenPlat token (env), project
  `.env` (`--env-file`). `adws/adw_data/` and the worktree are read-write.
- Container runs as the **host uid:gid** (bind-mounted file ownership).

### Run lifecycle (rev 3 — human review gate)

```
1. sssf run / sssf ticket run
     ├─ mint adw_id (uuid)
     ├─ git worktree add ~/.sssf/sandboxes/<project>/<adw_id> -b sssf/<adw_id>
     │    (created from the current base branch; cwd untouched)
     ├─ write the prompt into the worktree's adws/prompts/ (per-run → no NN race)
     └─ docker run sssf-runner (worktree + adws/adw_data rw; creds/env ro)
2. ADW pipeline runs inside the container (plan/build/test/reviewer/document…)
3. NEW engineer-review stage (adw_simple_sdlc)
     ├─ starts the changed app inside the container:
     │    config review.command (e.g. "bun run dev") + review.port (e.g. 3000),
     │    port forwarded to the host (docker -p)
     ├─ creates the run_reviews record (status pending) in the SHARED db
     ├─ logs a phase event with the URL (http://localhost:<port>) → viz shows it
     └─ ADW polls the shared db for a decision
4. Engineer opens the forwarded URL and tests the app
5. APPROVE (trace-page button or `sssf run approve <adw_id>`)
6. REJECT  (trace-page button or `sssf run reject <adw_id>`)
     both: teardown container (stop+rm), `git worktree remove` the sandbox —
     the branch sssf/<adw_id> REMAINS as a ref (pushable/PR-able with no checkout)
     reject additionally marks the run failed (ADW exits non-zero → tracer)
7. Engineer creates the PR (push sssf/<adw_id> + MR via their tooling), then
   `sssf sandbox prune <adw_id>` (optional cleanup helper)
```

### Review signal (shared db)

New small table (tracer-owned, `CREATE TABLE IF NOT EXISTS`):

```
run_reviews (adw_id TEXT PRIMARY KEY,
             status TEXT NOT NULL,          -- pending | approved | rejected
             updated_at TEXT)
```

- The ADW's review phase inserts `pending`, then polls `status` every N
  seconds (no timeout — the engineer decides the pace; a cancel path exists
  via the sandbox lifecycle).
- `sssf run approve|reject <adw_id>` updates the row (and the CLI is what the
  viz shells to, same pattern as ticket run).
- The trace page reads the review status via the session detail API (extended)
  and renders Approve/Reject buttons when `pending`.

### Data layer changes

- Tracer sqlite connection gains a **`busy_timeout`** so two concurrent runs
  writing the shared db retry instead of erroring (WAL mode already allows
  concurrent readers + serialized writers).
- No schema changes to existing tables; the `run_reviews` table is additive.

## CLI surface

| Command | Purpose |
|---|---|
| `sssf run` / `sssf ticket run` | sandboxed by default; `--no-sandbox` runs in the cwd (today's behavior) for debugging |
| `sssf run approve <adw_id>` | approve the review → teardown, branch stays |
| `sssf run reject <adw_id>` | reject the review → teardown, run marked failed, branch stays |
| `sssf sandbox build` | build/refresh the `sssf-runner` image |
| `sssf sandbox list` | adw_id · status · branch · container · worktree (incl. finished runs awaiting resolution) |
| `sssf sandbox prune [<adw_id>|--all]` | tear down kept sandboxes (after the engineer merged/PR'd) |

## Viz changes

- New route `POST /api/projects/:p/sessions/:adw_id/review {decision}` →
  shells `sssf run approve|reject` (409 when the run isn't pending).
- **Trace page only**: Approve/Reject buttons in the run-strip when the run is
  in the review stage (status pending); the review URL shows in the phase
  event log. No board/status changes for the buttons.
- The kanban already lands the run in Reviewing during the review phase via
  the stage fallback (last agent phase = reviewer → reviewing).
- The ticket-run route is unchanged (already shells `sssf ticket run`).

## Config (`sssf.config.yaml`)

```yaml
sandbox:
  enabled: true            # false → behave like today (cwd)
  image: sssf-runner       # tag auto-appended: sssf-runner:<sssf-version>
review:
  command: "bun run dev"   # started inside the container at the review stage
  port: 3000               # forwarded to the host
```

## Error handling

- **docker unavailable / image missing** → `sssf sandbox build` instructions,
  fail loudly (no silent cwd fallback unless `sandbox.enabled: false`).
- **cwd never changes** — approval never checks out the branch anywhere; the
  branch persists as a ref. Parallel runs and the engineer's checkout are
  unaffected (worktrees are structurally isolated; a branch can be checked out
  in only one worktree).
- **Teardown failure** (docker rm / worktree remove fails) → the run is still
  marked decided; `sssf sandbox list`/`prune` surface leftovers.
- **ADW crashes during review** → failsafe marks the session failed; the
  sandbox stays (kept-on-failure) for inspection.

## Testing

- Python (pytest):
  - worktree create/commit/remove lifecycle on a temp repo — branch survives
    removal, pushable ref; cwd untouched
  - per-run prompt allocation (two concurrent allocations → distinct files)
  - `run_reviews` state machine (pending → approved/rejected, idempotent)
  - review-phase poll loop (temp db, decision arrives → exits 0/1)
  - config parsing (`sandbox`, `review` sections; defaults)
  - lifecycle orchestration with docker mocked (spawn/build/teardown calls)
- bun (visualizer):
  - the new review route (approve/reject shells the CLI; 409 on non-pending)
- Manual/optional integration: build the image, run a real end-to-end run in
  a throwaway project (cheap model), test the app at the forwarded port,
  approve via CLI, verify teardown + surviving branch ref.
- Gates stay green: `uv run pytest -q`, `bun test`, `vue-tsc`, `bun run build`,
  `bun run lint`.

## Out of scope (v1)

- No auto-merge / auto-PR (the engineer's tooling owns that).
- No sandbox reuse/caching, no multi-image per project, no IDE attach.
- No board/status approve buttons (trace only).
- No dev-server auto-restart on change (a plain `bun run dev`-style command).
