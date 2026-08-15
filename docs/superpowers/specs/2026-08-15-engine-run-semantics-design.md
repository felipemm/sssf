# Engine Run Semantics & Commit Discipline — Design

**Status:** implemented 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` (engine internals, CLI surface unchanged) · **Plan:** `2026-08-15-engine-run-semantics-plan.md`
**Scope record:** captured post-implementation from the approved chat designs and field incidents; the aggregate change record is `2026-08-15-sssf-global-cli-revisions.md`.

This spec fixes how the factory behaves when you run an ADW: what a re-run does to a stalled previous run, what happens when code never lands or lands through the wrong hands, how a no-op run resolves, and where artifacts live.

## 1. Re-running an adw_id reaps the previous run

`session.ensure(cfg, adw_id)` now, before starting:

1. Reads live process rows (`ended_at IS NULL`) for the adw_id.
2. Terminates each — SIGTERM, then SIGKILL after a 0.5 s grace — **only when the pid's current command line matches the first token of the recorded command** (a recycled pid must never kill an innocent process).
3. Marks open phases `running`/`queued` → `fail` (`reaped: superseded by a re-run`) and a `running` session → `fail`.
4. Closes the process rows.

Fresh adw_ids are unaffected (empty tables → no-op). A Ctrl+C'd, killed, or stalled run no longer lingers as `running`; the next re-run owns the id and the trace shows one finished run.

## 2. Failsafe: uncaught exceptions never leave a `running` session

`ensure()` installs a `sys.excepthook` that writes `session_finish(ok=False)` before the original hook runs. The phase manager already handles exceptions inside phases; the hook covers everything else after the session row exists (between phases, `run.finish()`, console). Idempotent — an already-failed session is written again harmlessly.

## 3. The factory owns commits

- **The builder never runs `git commit`.** It provides `commit_message` on its envelope; the factory's commit code phase performs the write. Enforced in the builder prompt (`builder/user.md`) and regression-guarded.
- `commit_all(message, allow_empty=False)` returns `None` instead of raising when the tree is clean and `allow_empty` is set; strict call sites (`commit_plan`, `commit_docs`) keep raising.

## 4. `commit_build` resolves an empty commit three ways

At `commit_build` (green suite + approved review required to get here):

- **Already implemented** — builder reported no changes (`changed_files: []`), tree clean, HEAD still at the spec commit → the run is a no-op re-run → **success with a note**.
- **Builder committed its own work** — tree clean, `changed_files` non-empty, HEAD moved past the spec commit (`plan_sha`) → **hard fail** with a precise message (field incident: inkwell `7f914799`; the code had landed, but before review, through the wrong actor).
- **Changes never landed** — tree clean, `changed_files` non-empty, HEAD unmoved → **hard fail** (rolled back or never made).

`plan_sha` is captured immediately after `commit_plan` (the spec commit is the only commit the run owns up to that point). `git_helper.diff_files_between(a, b)` supports commit-to-commit checks.

## 5. A no-op re-run still walks the doc chain

A verified no-op run (already implemented) no longer skips documentation:

- The `changes` phase always runs for a verified run (diff vs the pinned baseline; raises on an empty changeset).
- If `adws/app_docs/<adw_id>_*.md` already exists → a code phase logs **"documentation already exists — success run, no updated doc"**; no agent spawned.
- If the write-up is missing (e.g. an earlier run failed before documenting) → the documenter **produces the missing write-up** from the diff and `commit_docs` lands it.

## 6. The standalone `adw_document` chain ends in a commit

`adw_document` (write up a diff against `--base`) previously wrote `adws/app_docs/<id>.md` and stopped — the write-up sat uncommitted. It now ends in a `commit_docs` git phase using the documenter's own `commit_message`, mirroring `adw_simple_sdlc`.

## 7. Folder convention: factory artifacts live under `adws/`

Nothing the factory produces touches the repo root:

- planner writes `adws/specs/<adw_id>_<slug>.md` (never overwrites; `_v2`/`_v3` on collision)
- documenter writes `adws/app_docs/<adw_id>_<slug>.md`
- prompt files live at `adws/prompts/` (`sssf run <adw> "run prompt adws/prompts/x.md"`)
- config `writes:` entries (`adws/specs/`, `adws/app_docs/`) enforce it via `permissions.py` prefix matching
- `sssf init` gitignores `adws/adw_data/sssf.db-wal`/`-shm` — if the WAL sidecars ever get committed, agents treat them as clutter and git-checkout them over a live db, breaking open reader connections (`SQLITE_IOERR_SHMOPEN`, errno 6922)
- regression guard `test_artifact_folders_live_under_adws` rejects any template reference to bare `specs/` or `app_docs/`

## What did NOT change

CLI surface, registry schema, envelope contracts (types/prompts/call-sites stay a synced triad), the visualizer's query code, the hard rules in `SKILL.md`.

## Verification

- 46 pytest tests (reap kills/marks/spares, failsafe, `commit_all` allow_empty, `diff_files_between`, template guards for commit discipline / doc chain / artifact folders), 2 bun tests, typecheck, lint.
- Field e2e: no-op re-run green, missing write-up produced, builder-commits-itself correctly diagnosed.
