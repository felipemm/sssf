# Multi-source sync + untracked tickets (#90) Implementation Plan

> **For agentic workers:** implement this plan task-by-task (superpowers:executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** sssf syncs tickets from all four origins (internal, jira, gitlab, github) into the internal db via CLIs, keeps synced tickets untracked and out of the backlog until marked, and writes state/label/comment back to origin trackers best-effort with failures recorded as events.

**Architecture:** Extend the existing sync machinery in `src/sssf/ticketing.py` (fetch_jira/fetch_linear/upsert_tickets/sync_tickets) with `gh`- and `glab`-based adapters behind origin resolution from the git remote (`detect_origin`), add a `--provider` filter to `sssf ticket sync`, and add a new `src/sssf/writeback.py` module with guarded per-origin writers that record failures in `ticket_events` and never raise. Untracked permanence is already enforced by `upsert_tickets`' content-only ON CONFLICT update — extend tests to cover all origins.

**Tech Stack:** Python (sqlite3, subprocess, shutil.which, yaml), `gh`/`glab`/`acli` CLIs, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-github-gitlab-ticketing-design.md` (fetch side: §1 config, §2 origin resolution, §3 adapters, §4 CLI, §8 verification) + `docs/adr/0002-internal-db-truth-multi-source.md` (write-backs best-effort, recorded as events) + issue #90 acceptance criteria.

## Global Constraints

- Provider set stays `jira | linear | github | gitlab | internal`; unknown provider → per-provider error result, never a crash.
- Synced tickets are born `needs-triage` + `untracked` (permanent); re-sync updates title/description/source_url ONLY — never kind/tracked/origin/status.
- Origin host matching is explicit: cloud providers match only their standard host unless `self_hosted: true` (+ optional `custom_url:`); `repo:` override bypasses host matching. Mismatch → skip with a warning naming `self_hosted`/`custom_url`, never an error.
- Secrets live in the CLIs' own auth (gh auth login / glab auth login / GITLAB_HOST); the YAML is committable.
- Write-backs are best-effort: a failure appends a `ticket_events` row and NEVER raises / never blocks the caller.
- `external_id` for github/gitlab = `<repo>#<number-or-iid>`; db id = `provider:external_id` (same dedupe key as today).
- Missing binary → actionable RuntimeError ("install gh and run `gh auth login`" / "install glab and run `glab auth login`").
- `sssf ticket sync` keeps its current argv compatibility with the viz server (`syncTickets` shells `ticket sync --project <root>`); `--provider` is additive.

## Review Focus

- A synced ticket whose origin forge doesn't match the git remote: provider must be SKIPPED with a warning that names `self_hosted: true` / `custom_url:` / `repo:` — never a crash and never a silent fetch against the wrong forge.
- Write-back to an origin that has no CLI installed / isn't authenticated / errors: recorded as a `writeback_failed` event, the command still exits 0, and the machine state change already applied is NOT rolled back.
- A `--provider` that isn't in the enabled list / is unknown: only that provider is attempted; unknown providers produce an error result, enabled ones still sync.
- Re-sync after a local edit: the synced ticket's kind/tracked/origin/status survive; only content refreshes.
- A synced (untracked) ticket must not appear in `backlog_tickets` until explicitly transitioned to ready-for-agent.

---

### Task 1: Origin detection + per-provider host matching (module seam)

**Files:**
- Modify: `src/sssf/ticketing.py` (after `load_config` / near fetch helpers)
- Test: `tests/test_ticketing.py`

**Interfaces:**
- Produces:
  - `detect_origin(root: Path) -> tuple[str, str] | None` — `(host, repo)` parsed from `git config --get remote.origin.url`; None when no origin or unparseable. Handles `git@host:owner/repo.git`, `https://host/owner/repo.git`, `ssh://git@host/owner/repo.git`; strips trailing `.git`.
  - `_origin_repo(cfg_block: dict, origin: tuple[str, str] | None, cloud_host: str, provider: str) -> tuple[str | None, str | None]` — returns `(repo, warning)`. `repo:` override wins without host matching. Cloud host match → repo. `self_hosted: true` + `custom_url:` → match custom host. `self_hosted: true` no custom_url → any host. No origin → warning "no git remote origin — add a `repo:` override in ticketing.yaml". Host mismatch → warning naming `self_hosted: true`/`custom_url:`.

- [ ] **Step 1: Write failing tests** for `detect_origin` (SSH/HTTPS/ssh:// forms, trailing `.git`, no origin, unparseable) and `_origin_repo` (override, cloud match, mismatch warning, self-hosted custom_url, self-hosted any-host, no origin warning).

- [ ] **Step 2: Run and confirm failures** — `uv run pytest tests/test_ticketing.py -k "origin or _origin_repo"`.

- [ ] **Step 3: Implement** `detect_origin` + `_origin_repo` in ticketing.py.

- [ ] **Step 4: Run the new tests to green.**

- [ ] **Step 5: Commit** — "test(origin): detect git remote origin + per-provider host matching for sync".

### Task 2: GitHub + GitLab fetch adapters

**Files:**
- Modify: `src/sssf/ticketing.py` (`fetch_github`, `fetch_gitlab`, `_run_gh`, `_run_glab`)
- Test: `tests/test_ticketing.py`

**Interfaces:**
- Consumes: `detect_origin`, `_origin_repo`, `TicketRecord`.
- Produces:
  - `fetch_github(cfg: TicketingConfig, root: Path) -> list[TicketRecord]` — `gh issue list --repo <repo> --state open [--label L …] --json number,title,body,url,state,labels --limit 100`; `external_id = "<repo>#<number>"`; missing gh → RuntimeError "install gh and run `gh auth login`"; nonzero exit → RuntimeError with stderr.
  - `fetch_gitlab(cfg: TicketingConfig, root: Path) -> list[TicketRecord]` — `glab issue list --repo <repo> --state opened [--label L …] --output json`; `external_id = "<repo>#<iid>"`; `web_url` → source_url; labels normalized (string or `{name}` dict).

- [ ] **Step 1: Write failing tests** — mocked subprocess asserting argv shape (repo, `--state open`/`opened`, repeated `--label` flags, `--json number,title,body,url,state,labels` / `--output json`), external_id format, missing-binary RuntimeError, nonzero-exit RuntimeError.

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Implement** `_run_gh` / `_run_glab` (which-guard + subprocess like `_run_acli`) and the two fetchers, wiring `_origin_repo` — repo None → raise RuntimeError(warning) so sync reports the skip.

- [ ] **Step 4: Green.**

- [ ] **Step 5: Commit** — "feat(sync): gh/glab fetch adapters with origin host matching".

### Task 3: Config + sync_tickets dispatch across all four origins

**Files:**
- Modify: `src/sssf/ticketing.py` (`TicketingConfig` gains `github`/`gitlab`; `ProviderSyncResult` gains `warning`; `sync_tickets(root, cfg, providers=None)`), `src/sssf/templates/adws/config/ticketing.yaml`
- Test: `tests/test_ticketing.py`

**Interfaces:**
- Consumes: `fetch_github`, `fetch_gitlab`, `fetch_jira`, `fetch_linear`, `upsert_tickets`.
- Produces: `sync_tickets(root, cfg, providers=None) -> list[ProviderSyncResult]` — providers subset filter; `internal` still a no-op; unknown provider → error result; warnings set `warning`, never `error`.
- `TicketingConfig(providers, jira, linear, github, gitlab)` — new fields default to `{}` (backward compatible).

- [ ] **Step 1: Failing tests** — config parses github/gitlab blocks; `sync_tickets` with a tmp project whose git origin resolves + mocked gh/glab upserts `github:owner/repo#12` and `gitlab:group/proj#5` rows (untracked, needs-triage, origin recorded); origin mismatch → result has warning and no tickets; `providers=["github"]` fetches only github; a failing provider doesn't stop the others.

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Implement** — extend dataclasses, dispatch in `sync_tickets`, `load_config` reads new blocks; template yaml documents github/gitlab.

- [ ] **Step 4: Green.**

- [ ] **Step 5: Commit** — "feat(sync): four-origin dispatch (internal/jira/gitlab/github) with per-provider skip warnings".

### Task 4: `sssf ticket sync --provider` + untracked backlog invisibility

**Files:**
- Modify: `src/sssf/cli.py` (`p_sync` gains `--provider`), `src/sssf/commands/ticket.py` (`sync()` passes subset)
- Test: `tests/test_ticket_cli.py`, `tests/test_ticketing.py`

**Interfaces:**
- Consumes: `sync_tickets(root, cfg, providers=...)`.
- Produces: `sssf ticket sync [--provider github|gitlab|jira|linear] [--project ROOT]` — exit 0 even when a provider warns/errors (per-provider messages printed); `--provider` syncs only that one.

- [ ] **Step 1: Failing tests** — CLI `sync --provider github` spawns only github (monkeypatch `sync_tickets` or real dispatch with mocked fetchers); `backlog_tickets` hides a synced untracked ticket until a `needs-triage → ready-for-agent` transition (then shows it).

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Implement** — cli flag + ticket.py sync subset; (backlog behavior already correct — the new test pins it).

- [ ] **Step 4: Green.**

- [ ] **Step 5: Commit** — "feat(cli): ticket sync --provider; pin untracked-out-of-backlog invariant".

### Task 5: Write-back layer (state/label/comment, best-effort, events on failure)

**Files:**
- Create: `src/sssf/writeback.py`
- Modify: `src/sssf/commands/ticket.py` (`backlog()` fires a state write-back; new `writeback` command), `src/sssf/cli.py` (`ticket writeback` subparser)
- Test: `tests/test_writeback.py`

**Interfaces:**
- Consumes: `ticketing.ticket_events`, `ticketing.add_ticket_event` (or direct SQL), `TicketingConfig`/origin row from `tickets` table.
- Produces:
  - `writeback_state(conn, ticket_id, state, *, actor="system") -> None`
  - `writeback_comment(conn, ticket_id, text, *, actor="system") -> None`
  - `writeback_label(conn, ticket_id, label, *, add=True, actor="system") -> None`
  - Dispatch by the ticket's `origin`: `internal` → no-op; `github` → `gh issue edit <n> --repo <repo> --state <s>` / `gh issue comment <n> --repo <repo> --body <text>` / `gh issue edit <n> --repo <repo> --add-label <l>` (or `--remove-label`); `gitlab` → `glab issue update <iid> --repo <repo> --state <s>` / `glab issue note <iid> --repo <repo> -m <text>` / `glab issue update <iid> --repo <repo> --label <l>` (or `--unlabel`); `jira`/`linear`/unknown → append a `writeback_skipped` event naming the origin.
  - Any exception → `ticket_events` row `writeback_failed` with payload `{origin, operation, error}`; NEVER raises.
  - `sssf ticket writeback <ticket-id> --state S [--comment TEXT] [--label L] [--remove-label L]` — manual/scripted write-back.

- [ ] **Step 1: Failing tests** — argv shapes per origin/operation; internal no-op (no events, no subprocess); failure (mocked nonzero exit / missing binary) → `writeback_failed` event with origin+operation+error, no exception; success appends NO failure event; jira/linear → `writeback_skipped`; CLI dispatches.

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Implement** `writeback.py` + wiring.

- [ ] **Step 4: Green.**

- [ ] **Step 5: Commit** — "feat(writeback): best-effort state/label/comment write-backs, failures as ticket_events".

### Task 6: Docs, template, full checks, review

**Files:**
- Modify: `src/sssf/templates/adws/config/ticketing.yaml` (github/gitlab blocks), `CHANGELOG.md`, site docs (`site/src/content/docs/cli.astro` — sync `--provider` + writeback rows; `quickstart.astro` if it mentions providers)

- [ ] **Step 1: Update** template yaml + CHANGELOG + site docs rows.
- [ ] **Step 2: Full local suite** — `uv run pytest`, `uv run ruff check src/sssf tests`, `uv run mypy src/sssf`, `bun test` (in `src/sssf/apps/visualizer`), `npm run build` (in `site`).
- [ ] **Step 3: Self-review** the diff (`/code-review`), fix findings.
- [ ] **Step 4: Commit** — "docs(sync): ticketing.yaml github/gitlab blocks, CHANGELOG, cli docs".
