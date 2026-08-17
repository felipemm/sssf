# GitHub + GitLab Ticketing for sssf — Design

**Status:** proposed · **Date:** 2026-08-17 · **Amends:** `2026-08-15-ticketing-design.md` (adds `github`/`gitlab` providers, origin resolution, PR/MR management, and a provider picker on the kanban refresh button) · **Plan:** written after this spec is approved (writing-plans)

GitHub and GitLab both ship a robust issue tracker, and sssf already shells to user-authenticated CLIs for its ticketing adapters (`acli` for Jira). This design adds two hosted-git providers that follow that exact pattern — `gh` for GitHub, `glab` for GitLab — with the active tracker chosen by the project's git remote origin, plus a manual PR/MR creation command and a multi-provider refresh picker in the kanban.

> Path note: this spec uses the v2 stamped layout (`adws/config/`, `adws/data/`, `adws/modules/`, `adws/prompts/`). Where the 2026-08-15 spec names v1 paths, this one supersedes them.

## 1. Configuration — per-project `adws/config/ticketing.yaml`

```yaml
providers:            # any subset of jira | linear | github | gitlab | internal
  - internal
  # - github
  # - gitlab

# GitHub goes through the gh CLI (pre-installed and authenticated by the
# user — gh owns auth + host; sssf only passes the query). The repo is parsed
# from the git remote origin unless overridden.
github:
  labels: []          # optional label filter; repeat entries = OR
  self_hosted: false  # true for GitHub Enterprise — origin must match custom_url
  # custom_url: https://github.company.com
  # repo: owner/repo # optional override; default = parsed from origin
  pr:
    auto: false       # RESERVED — auto PR-on-success is deferred (see §6)

gitlab:               # via the glab CLI (glab owns per-host auth + GITLAB_HOST)
  labels: []
  self_hosted: false  # true for self-hosted GitLab (e.g. git.ifoodcorp.com.br)
  # custom_url: https://git.ifoodcorp.com.br
  # repo: group/project
  pr:
    auto: false
```

- **Same opt-in rule as today**: a missing or effectively empty `ticketing.yaml` means the feature is off (Backlog stage hidden, `sssf ticket` answers "not configured").
- **Providers remain a set**; the valid set grows to `jira | linear | github | gitlab | internal`. A missing/invalid block for one provider skips that provider with a clear error; the rest still sync.
- **`repo:` is an optional override** for edge cases (no origin, a forge URL that doesn't parse, submodules). When present it wins over origin parsing entirely.
- **`pr.auto` is reserved, not functional.** The key parses and validates; setting `auto: true` makes sync/run print a "not implemented yet" warning. It ships inert so the file never needs to change when the follow-up lands.
- **Secrets live in the CLIs, never in sssf**: like `acli`, `gh` and `glab` hold their own global auth (`gh auth login`, `glab auth login`). The YAML is committable.

## 2. Origin resolution — which tracker does this repo use?

`detect_origin(root) -> (host, repo) | None` runs `git -C <root> config --get remote.origin.url` and parses both common forms:

- SSH: `git@github.com:owner/repo.git` → host `github.com`, repo `owner/repo`
- HTTPS: `https://github.com/owner/repo.git` → host `github.com`, repo `owner/repo`
- The `ssh://git@host/owner/repo.git` form parses identically; a trailing `.git` is stripped.

**Host matching is per-provider and explicit.** By default each provider matches only its cloud standard URL host; any other origin host must be configured (`self_hosted: true`, optionally pinned with `custom_url:`) or the provider is skipped with a config-issue warning.

| Provider config | Origin host must be | Notes |
|---|---|---|
| `github` (default) | `github.com` | the cloud standard URL |
| `github` + `self_hosted: true` + `custom_url:` | the host of `custom_url` | e.g. `github.company.com` |
| `github` + `self_hosted: true`, no `custom_url` | any host | `custom_url` recommended to pin the instance |
| `gitlab` (default) | `gitlab.com` | the cloud standard URL |
| `gitlab` + `self_hosted: true` + `custom_url:` | the host of `custom_url` | e.g. `git.ifoodcorp.com.br` |
| `gitlab` + `self_hosted: true`, no `custom_url` | any host | `custom_url` recommended |
| no origin (empty/missing remote) | — | no hosted-git provider applies |

`custom_url` is used **only for origin matching** — sssf never passes it to the CLI. gh/glab keep owning their own host + auth configuration (`gh auth login --hostname …`, `glab auth login --hostname …`, `GITLAB_HOST`).

**Per-provider resolution** inside `sync_tickets`:

1. `repo:` override in the yaml block → use it, no host matching.
2. Origin exists and its host is in the provider's expected hosts (table above) → use the parsed repo.
3. Otherwise the provider is **skipped with a warning message, never an error** — the config-issue warning surface:
   - Cloud provider, origin is a non-standard host (e.g. `git.ifoodcorp.com.br` with plain `gitlab:`) → "gitlab configured but origin is git.ifoodcorp.com.br — the cloud host is gitlab.com; is this a self-hosted instance? set `self_hosted: true` and `custom_url:` (or add a `repo:` override)".
   - `self_hosted: true` with `custom_url`, origin host ≠ custom host → "gitlab configured with custom_url <url> but origin is <host> — fix custom_url or add a `repo:` override".
   - No origin at all → "no git remote origin — add a `repo:` override in ticketing.yaml".

Jira, Linear, and internal are unaffected — they are explicit, not origin-keyed. This strict default replaces the earlier "any unknown host counts as gitlab" rule: a self-hosted origin now requires explicit `self_hosted: true`, which surfaces misconfiguration instead of silently aiming at the wrong forge.

## 3. Provider adapters — `sssf/ticketing.py`

| Provider | Fetch | Auth |
|---|---|---|
| `github` | shell to `gh issue list --repo <repo> --state open [--label L …] --json number,title,body,url,state,labels --limit 100`, parse JSON | gh's own global auth |
| `gitlab` | shell to `glab issue list --repo <repo> --state opened [--label L …] --output json`, parse JSON | glab's own per-host auth (`GITLAB_HOST` for self-hosted) |

- **Open issues only** (`--state open` / `--state opened`) — the backlog intent, matching Linear's state filter's default posture. `labels` is an optional OR filter from the yaml (repeat the flag per label).
- Normalized to the existing `TicketRecord` (`provider`, `external_id`, `title`, `description`, `source_url`):
  - github → `external_id = "<repo>#<number>"`, title/body/url straight from JSON.
  - gitlab → `external_id = "<repo>#<iid>"`, `web_url` → source_url; labels normalized defensively (string or `{name}`).
- **Ticket ids in the db**: `github:<repo>#<n>` and `gitlab:<repo>#<iid>` — same `provider:external_id` dedupe key as today; re-syncing never duplicates.
- **Error pattern identical to `fetch_jira`**: missing binary → actionable install+auth message; nonzero exit → per-provider `ProviderSyncResult(provider, error=…)`; the rest still sync.

## 4. CLI

### `sssf ticket sync [--provider github|gitlab|jira|linear]`

Sync one provider instead of all. Origin mismatch skips still apply. The viz refresh picker shells to this with `--provider`.

### `sssf ticket pr <ticket_id> [--no-comment]` (new)

Creates a **draft** PR/MR from the **current branch** of the project repo (cwd = project root, so `gh pr create` / `glab mr create` act on the working tree's branch):

1. Loads the ticket row; resolves the repo via origin (or yaml `repo:` override) — **if the origin host isn't in the provider's expected hosts (§2), this fails with the same config-issue warning as sync** (a PR cannot be aimed at a forge the repo doesn't live on).
2. Builds the PR/MR: title = ticket title; body = ticket description + issue link. The bare issue number / IID is the ticket's `external_id` **after the last `#`** (e.g. `owner/repo#12` → `12`) — uniform for both forges, since `external_id` is `<repo>#<number-or-iid>` (§3).
   - github → `Closes #<number>` (same-repo issue auto-close on merge).
   - gitlab → `Closes <issue_url>` (from the ticket's `source_url`; works for same-project and cross-project issues).
   - internal tickets → no link line (there is no external issue); the PR/MR is still created.
3. `gh pr create --draft --title <t> --body <b> --repo <repo>` / `glab mr create --draft --title <t> --description <b> --repo <repo>`.
4. Unless `--no-comment`: comment the PR/MR URL back on the issue — `gh issue comment <number> --repo <repo> --body "PR: <url>"` / `glab issue note <iid> -m "MR: <url>" --repo <repo>`. Internal tickets skip this step.
5. Prints the PR/MR URL.

**Branch requirement is the user's**: the branch must exist with commits ahead of its base (a plain `gh pr create`/`glab mr create` error surfaces as-is). sssf never pushes or creates branches — runs with `--no-sandbox` leave local commits behind, which is the supported way to reach this command with fresh work.

## 5. Kanban — refresh picker for multiple providers

- `GET /api/projects/:project/tickets` response gains `providers: string[]` — the **enabled external** providers (github/gitlab/jira/linear, from the yaml `providers:` list minus `internal`). `internal` is excluded: it has nothing to fetch (tickets are created in the db via CLI).
- `POST /api/projects/:project/tickets/sync` gains an optional `?provider=` that passes through to `sssf ticket sync --provider <p> --project <root>`.
- **KanbanBoard refresh button**: 0–1 external providers → syncs directly (today's behavior). **>1 → the button opens a small menu: "All" + each external provider.** Picking one syncs only that one (`syncTickets(provider)`); "All" behaves like today. No persistence of the last choice in v1.

## 6. Error handling

- Missing binary (`gh`/`glab`) → the adapter fails with install + auth instructions ("install gh and run `gh auth login`" / "install glab and run `glab auth login`"); the provider is skipped, others sync.
- Origin mismatch → skip with message, never a crash (per §2).
- `pr.auto: true` → warning "not implemented yet"; no behavior.
- `sssf ticket pr` on a ticket whose provider's forge doesn't match the origin → clear error naming the override (`repo:`) as the fix.
- gh/glab nonzero exit (e.g. branch not pushed) → the CLI's stderr surfaces verbatim as the provider error.

## 7. Deliberately cut (v1)

- **Auto PR/MR on run success** (`pr.auto` reserved + validated, inert). Requires branch pushing and run-lifecycle coupling — a follow-up design.
- Issue state write-back / transitions / comments-from-runs.
- Webhooks / background polling — sync stays on demand (CLI or the refresh button).
- Attachments, milestones, assignee sync — title, description, labels filter, and origin only.
- Bitbucket or other hosted trackers; pagination beyond the CLI's first page.

## 8. Verification

- **pytest** (`tests/test_ticketing.py`, `tests/test_ticket_cli.py`):
  - origin parsing: SSH, HTTPS, `ssh://` form, trailing `.git`, self-hosted hostname, no origin.
  - host matching + warnings: cloud hosts match by default (`github.com` ↔ github, `gitlab.com` ↔ gitlab); a non-standard origin host with a cloud provider → warning naming `self_hosted: true`/`custom_url:`; `self_hosted` + `custom_url` host mismatch → warning; `repo:` override bypasses host matching.
  - adapter parsing with mocked subprocess (same pattern as `fetch_jira`): github `--json` fields, gitlab `web_url`/`iid` mapping, label filter flags present in argv.
  - missing `gh`/`glab` → actionable RuntimeError.
  - `sync --provider` fetches only that provider.
  - `ticket pr`: argv shape for gh vs glab, `--draft`, issue-link line per forge, `--no-comment` skips the comment step, internal ticket has no link line, mismatch → error with `repo:` hint.
  - config: new keys parse, `pr.auto: true` warns, unknown provider still errors.
- **bun test** (`src/sssf/apps/visualizer/server/tickets.test.ts` + route tests): `providers` in the GET response (external only, internal excluded); sync route passes `?provider=` into the spawned argv.
- **Field**: a github-origin repo with cloud `gitlab:` configured → sync warns "gitlab configured but origin is … — is this a self-hosted instance? set `self_hosted: true` and `custom_url:`" and still lists github issues; a self-hosted origin syncs only with `self_hosted: true` (+ `custom_url`); two external providers → refresh shows the picker; `sssf ticket pr <id>` after a `--no-sandbox` run opens a draft PR linking the issue.
