# Engine Run Semantics & Commit Discipline — Implementation (as-executed)

> **Status:** DONE — every change landed, tested, and verified in the field.
> Retrospective record written 2026-08-15 from the approved chat designs,
> the SDD-style deviation log, and the field incidents.
>
> **Spec:** `docs/superpowers/specs/2026-08-15-engine-run-semantics-design.md`
> **Aggregate record:** `2026-08-15-sssf-global-cli-revisions.md` (spec + plan)
> **Implementation repo:** `~/dev/lab/mvp/sssf` · **Demo repo:** `~/dev/lab/demos/inkwell`

## Commit map

| Commit (sssf) | What landed |
|---|---|
| `e5a92da` | gitignore WAL sidecars in `sssf init`; viz recovers from `SQLITE_IOERR` |
| `1693e1e` | `commit_all(allow_empty)`; `commit_build` tolerates no-op re-runs |
| `388288d` | reap stale runs on re-run; failsafe excepthook; already-implemented detection |
| `a18113f` | builder never commits (prompt + guard); `commit_build` three-way diagnosis; `git_helper.diff_files_between` |
| `a92b3be` | no-op re-runs walk the doc chain (confirm existing write-up or produce the missing one) |
| `eb4291d` | `adw_document` ends in a `commit_docs` git phase |
| `1f61c1a` | folder convention: planner/documenter artifacts + prompts under `adws/`; config `writes:` updated; regression guard |

| Commit (inkwell) | What landed |
|---|---|
| `b2f7b51` | untrack `sssf.db-wal`/`-shm` + gitignore entries |
| `701318b`, `54483f6`, `930fa39`, `983efcf` | re-sync `adw_simple_sdlc.py` / `adw_document.py` / prompts as the templates evolved |
| `b6cfbde` | refactor: `specs/`/`app_docs/`/`prompts/` → `adws/`; app code → `src/` |

## Field incidents that drove the design

1. **Inkwell `7f914799` (FTS5) — builder committed its own work.** The builder ran `git add && git commit` mid-phase (twice), so `commit_build` found a clean tree and the original claim-mismatch check reported *"changes never landed"* — wrong; they had landed via the builder. Fix: `HEAD != plan_sha` ⇒ builder-committed (precise message + prompt-level prohibition). The FTS5 work itself was already committed and review-approved; a re-run went green as a no-op.
2. **Inkwell WAL sidecar incident** — tracked `sssf.db-wal`/`-shm` led the reviewer to flag them and the builder to `git checkout` them over a live db → `SQLITE_IOERR_SHMOPEN` on every viz poll. Fix: untrack + gitignore (repo) and gitignore in `sssf init` (prevention).
3. **FTS5 write-up never produced** — the failed run never reached the document phase, and the no-op re-run skipped docs entirely. Fix: the no-op path now confirms or produces the write-up (§5 of the spec).

## Verification

```bash
cd ~/dev/lab/mvp/sssf && uv run pytest -q        # 46 passed
# targeted: tests/test_session.py (reap), tests/test_git_helper.py (allow_empty,
# diff_files_between), tests/test_templates.py (commit discipline, doc chain,
# artifact folders), tests/test_engine_port.py
```

Field checks: re-run of the FTS5 prompt (already implemented → green no-op); re-run after the fix produced `adws/app_docs/7f914799_fts5-search.md` and committed it; `sssf run document --base <ref>` lands the write-up in its own commit.
