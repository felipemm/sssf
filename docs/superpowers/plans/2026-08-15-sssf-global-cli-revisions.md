# SSSF Global CLI — Revisions Implementation (as-executed)

> **Status:** DONE — every task landed and verified. This is the reference
> record for future sessions: what changed, in which commits, and how to apply
> the same fixes to a project stamped before the fix.
>
> **Spec:** `docs/superpowers/specs/2026-08-15-sssf-global-cli-revisions.md`
> **Implementation repo:** `~/dev/lab/mvp/sssf` (branch `main`)

## Commit map

| Commit | What landed |
|---|---|
| `4ebb094..d238518` | original tasks 1–4 (scaffold, registry, engine port, templates) — per the 2026-08-14 plan |
| `e37472b` | Task 5 `sssf init` (Traversable API, `--version` contract, sidecar gitignore entries) |
| `d843146` | Task 6 `sssf run` |
| `6a0fcfc` | Task 7 observability (`adw_name` added to sessions select) |
| `6c313dc` | Task 8 projects/doctor/upgrade |
| `43b3497` | Task 9 `sssf viz` (openReadonly, PORT env, hatchling exclude, mkdtempSync) |
| `93ddfd1` | Task 10 SKILL.md + docs + LICENSE + wheel payload |
| `553e96e` | **fix:** visualizer api double-prefix (`/api/projects/:p/api/sessions` → `/api/projects/:p/sessions`) |
| `e5a92da` | **fix:** gitignore WAL sidecars in init; viz recovers from `SQLITE_IOERR` |
| `1693e1e` | `commit_all(allow_empty)`; commit_build tolerates no-op re-runs |
| `388288d` | reap stale runs on re-run; failsafe excepthook; already-implemented vs claim-mismatch |
| `1f61c1a` | folder convention: planner/documenter artifacts + prompts under `adws/` (§2.4) + regression guard |
| `4fc95d5` | revisions spec/plan in-repo; README/customizing/contributing/SKILL updated |

Inkwell demo repo (`~/dev/lab/demos/inkwell`):

| Commit | What landed |
|---|---|
| `b2f7b51` | untrack `sssf.db-wal`/`sssf.db-shm` + gitignore entries |
| `701318b` | sync `adw_simple_sdlc.py` (allow_empty commit_build) |
| `54483f6` | sync `adw_simple_sdlc.py` (already-implemented detection, no-op skips doc chain) |
| `b6cfbde` | refactor: specs/app_docs/prompts → `adws/`; app code → `src/`; prompts + config re-synced |

## Applying the fixes to a project stamped before the fix

A project stamped with an older `sssf init` needs three things (inkwell did
all three):

1. **Untrack the WAL sidecars** if they ever got committed:

   ```bash
   git rm --cached adws/adw_data/sssf.db-wal adws/adw_data/sssf.db-shm
   printf 'adws/adw_data/sssf.db-wal\nadws/adw_data/sssf.db-shm\n' >> .gitignore
   git commit -m "chore: untrack sssf WAL sidecars — runtime state"
   ```

2. **Re-sync the `adw_simple_sdlc` chain** (and any other ADW you use that
   calls `commit_build`):

   ```bash
   cp ~/dev/lab/mvp/sssf/src/sssf/templates/adws/adw_simple_sdlc.py adws/
   ```

   Only safe if the project's copy is unmodified (check with `diff` first).

2b. **Re-sync the planner/documenter prompts** (folder convention §2.4):

   ```bash
   cp ~/dev/lab/mvp/sssf/src/sssf/templates/prompt_engineering/{planner,documenter}/{system,user}.md \
      adws/adw_data/prompt_engineering/{planner,documenter}/
   # and in adws/adw_sssf_config/sssf.config.yaml:
   #   planner writes:    - specs/      → - adws/specs/
   #   documenter writes: - app_docs/   → - adws/app_docs/
   ```

   Then move any existing artifacts: `git mv specs adws/specs`, `git mv
   app_docs adws/app_docs`, `git mv prompts adws/prompts` (if present).

3. **Upgrade the tool** so the engine picks up the reap/failsafe/no-op logic:

   ```bash
   cd ~/dev/lab/mvp/sssf && uv tool install --editable .
   ```

   `session.ensure()` is the engine, so reap + failsafe apply to every project
   automatically.

## Deviation log (from the SDD ledger, condensed)

| Deviation | Resolution |
|---|---|
| `action="version"` raises `SystemExit`; `main` must return int | explicit `--version` branch returning 0 |
| `Path(resources.files(...))` fails on Python 3.13 `MultiplexedPath` | Traversable API (`iterdir`/`read_text`) in `init.py`; `resources.files("sssf") / "apps" / "visualizer"` in `viz.py` |
| sessions query omitted `adw_name`, but the plan's test requires it | added `adw_name` to the select (rest verbatim) |
| `Bun.tmpdir` missing in bun 1.3.14 | `mkdtempSync` in `registry.test.ts` |
| cold WAL db unreadable readonly (`SQLITE_IOERR_SHMOPEN`) | `openReadonly()` probe + RW fallback in `db.ts` |
| `--port` argv ignored; server reads `PORT` env | `env["PORT"] = str(port)` in `viz.py` |
| hatchling auto-includes `apps/` → double-add with force-include | `exclude` the asset trees from auto-discovery |
| frontend fetched `/api/projects/:p/api/sessions` (double prefix) | `base()` drops the inner `/api/` |
| `apps/__init__.py` made `sssf.apps` a package → wheel collision | removed; resolve via `resources.files("sssf")` |
| `inspect.getsource(adw_modules.agents)` — package doesn't re-export submodules | import `agents` explicitly in the test |

## Verification (final state)

```bash
cd ~/dev/lab/mvp/sssf
uv run pytest -q              # 34 passed
cd src/sssf/apps/visualizer
bun test                      # 2 passed
bun run typecheck && bun run lint
uv build && unzip -l dist/*.whl | grep -E "SKILL.md|apps/visualizer/server/index.ts"
```

Field checks performed: real ADW run against a live provider, session
continuity across chained ADWs (`agent_map.json`), global visualizer serving
multiple registered projects, adhoc `--db` mode, UI built and served, IOERR
recovery simulated by replacing sidecars under an open connection.
