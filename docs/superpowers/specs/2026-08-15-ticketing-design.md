# Ticketing Integration for sssf — Design

**Status:** proposed · **Date:** 2026-08-15 · **Amends:** `2026-08-14-sssf-global-cli-design.md` (new subsystem) · **Plan:** written after this spec is approved (writing-plans)

Backlog tickets drive the factory: a per-project ticketing source (Jira, Linear, or sssf's own internal tickets) populates the **Backlog** stage of the kanban board, and each ticket can be promoted into a run of `adw_simple_sdlc`. The backlog lives in the project's existing SQLite trace db, so the board, the CLI, and the trace all read one source.

## 1. Configuration — per-project `adws/adw_sssf_config/ticketing.yaml`

```yaml
# Any subset of providers can be enabled at once; the backlog aggregates
# tickets from every enabled provider.
providers:
  - internal
  - jira
  # - linear

# Jira goes through the acli CLI (pre-installed and authenticated by the
# user — acli owns base_url + auth; sssf only passes the JQL).
jira:
  jql: 'project = ACME AND status in (Backlog, "To Do")'

linear:
  team: ENG
  token_env: LINEAR_TOKEN
  filter: 'team:ENG state:Backlog'
```

- **Ticketing is opt-in.** `sssf init` stamps a **commented-out template** —
  every key commented, so the file parses as "not configured". A missing or
  effectively empty `ticketing.yaml` means the feature is **off**: the kanban
  **hides the Backlog stage entirely**, and the `sssf ticket` commands answer
  with a friendly "ticketing not configured" instead of failing.
- **Providers are a set**: enable any subset of `jira | linear | internal`.
  A missing/invalid block for one provider skips that provider with a clear
  error; the others still sync.
- **Secrets live in env** — only Linear carries a token (`token_env`, loaded
  from the project's `.env` via python-dotenv like the roster keys). Jira has
  **no token in sssf at all**: auth is acli's own, configured globally by the
  user. The YAML is committable; the `.env` is not.

## 2. Backlog storage — the `tickets` table (same SQLite db)

Added to the tracer schema (idempotent `CREATE TABLE IF NOT EXISTS`, additive like the existing tables):

```sql
CREATE TABLE IF NOT EXISTS tickets (
  id          TEXT PRIMARY KEY,   -- 'jira:<key>' | 'linear:<id>' | 'internal:<uuid>'
  provider    TEXT NOT NULL,      -- jira | linear | internal
  external_id TEXT,               -- NULL for internal
  title       TEXT NOT NULL,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'backlog',   -- backlog | running | done | failed
  prompt_file TEXT,               -- adws/prompts/NN-<slug>.md once run
  adw_id      TEXT,               -- the run spawned for this ticket
  source_url  TEXT,               -- the ticket's origin link ('' for internal)
  created_at  TEXT, updated_at TEXT
);
```

- **Origin is fully stored**: every ticket persists its provenance — the
  `provider`, the `external_id` in that system, and the `source_url` back to it
  (populated for Jira/Linear; empty for internal). The modal and the ticket
  card render the origin, and the row can always be traced to where it came
  from.
- **Dedupe**: upsert on `provider + external_id`; re-syncing never duplicates.
- **Lifecycle & first-class citizens**: the **session (ADW id) is the
  first-class citizen of the kanban**, not the ticket. A ticket's lifecycle is
  `backlog → running → done|failed` as **bookkeeping**: `running` is set when
  the run spawns (linked `adw_id`); `done|failed` is reconciled lazily from the
  linked session when tickets are read (session `success` ⇒ done, `fail` ⇒
  failed).
- **One stage at a time, always**: a session maps to exactly one kanban stage
  (the deterministic stage rule) and a ticket is in the Backlog stage **only
  while `status='backlog'`** — the moment a run spawns, the ticket leaves the
  board and exists purely as the session's source/origin reference. Nothing is
  ever in two stages simultaneously.

## 3. Provider adapters — Python engine (`sssf/ticketing.py`)

| Provider | Fetch | Auth |
|---|---|---|
| `jira` | **acli wrapper** — shells to the pre-installed, user-authenticated [`acli`](https://github.com/zdharma-continuum/acli) CLI (e.g. `acli issue list --jql "…" -f json`), parses its JSON | acli's own global auth; sssf holds no Jira token |
| `linear` | GraphQL `issues` query filtered by team + filter | Bearer token from `token_env` |
| `internal` | rows already in the `tickets` table (created via CLI) | — |

Each adapter returns normalized records (external id, title, description, source url); `sync()` iterates **every enabled provider** and upserts into the db. Read-only — **no write-back** to Jira/Linear in v1.

## 4. CLI — `sssf ticket`

| Command | What it does |
|---|---|
| `sssf ticket sync [--project]` | fetch external tickets → db (loads `.env`, calls the provider, upserts) |
| `sssf ticket add "<title>" [--project]` | create an internal ticket |
| `sssf ticket list [--project]` | read-only terminal listing |

## 5. Kanban — Backlog stage + ticket modal

- The **Backlog** column is rendered **only when ticketing is enabled** for the
  project (at least one provider configured in `ticketing.yaml`). Disabled →
  the column is hidden entirely and the board shows Planning → … → Blocked as
  before. Enabled → the column aggregates **backlog tickets only** (unrun,
  `status='backlog'`) from **all enabled providers**, each with its provider
  badge (J / L / ⚙), title, status chip, and the origin (external id / link).
  Session cards and ticket cards are visually distinct; a run ticket is not a
  card anywhere — its session is.
- **Clicking a ticket card opens a modal** (`TicketModal.vue`): full title, provider + source link, description, current status, and the prompt file / `adw_id` when the ticket was run. Buttons: **Run** (primary) and **Close**.
  - **Run** → creates the enumerated prompt file and spawns the ADW (below), sets the ticket to `running` with the `adw_id`, closes the modal.
  - **Close** → dismisses; nothing changes.
- A **refresh** button in the Backlog header triggers `POST …/tickets/sync` (shells to the CLI), so the board can pull new external tickets on demand. No background polling in v1.
- The Backlog header shows a "stub → live" state once a provider is configured; with `provider: internal` and no tickets, it reads "no tickets".

## 6. The run flow

1. Click **Run** in the modal → `POST /api/projects/:p/tickets/:id/run` on the viz server.
2. The server scans `adws/prompts/` for the **next enumerated name** (`01-…` through `08-…` → `09-<slug>.md`; slug = kebab-case of the title, collision-safe suffix).
3. The server writes the prompt file: `# <title>`, the ticket description as the task, the source link, and a line that it was generated from ticket `<external_id>`.
4. The server spawns, detached: `sssf run simple_sdlc "run prompt <prompt_file>" --adw-id <generated_id>` (cwd = project root; `--adw-id` is minted by the server so the ticket can be linked immediately).
5. The ticket row is updated to `status=running, adw_id=<id>, prompt_file=<file>`
   — and, per §2, the ticket **leaves the Backlog** immediately (the board
   queries `status='backlog'`); it now exists only as the session's origin.
6. The spawned run appears in the board's Planning → Building → Reviewing →
   Done/Blocked columns as a normal session (the only first-class kanban
   citizen); the ticket's `done|failed` state reconciles from that session.

## 7. Viz server routes (new)

| Route | Behavior |
|---|---|
| `GET /api/projects/:project/tickets` | `{enabled, tickets}` — enabled = at least one provider configured; tickets from all enabled providers, reconciled against linked sessions |
| `POST /api/projects/:project/tickets/sync` | shells to `sssf ticket sync --project <root>`; returns per-provider counts; 400 when ticketing is not configured |
| `POST /api/projects/:project/tickets/:id/run` | prompt file + spawn (above); 409 if the ticket is already running |

The server shells to the Python CLI (`sssf`) for sync and run — the CLI is installed globally (uv tool), so `Bun.spawn(["sssf", …])` from the project root works; the SQLite db remains the single shared source.

## 8. Error handling

- Missing/invalid `ticketing.yaml` → board shows a hint ("configure ticketing in adws/adw_sssf_config/ticketing.yaml"), sync/run return a clear error, never a crash.
- **acli missing or unauthenticated** → the Jira adapter fails with actionable instructions: install acli and configure its auth (login/credentials) globally; Jira is skipped, other providers still sync.
- Linear provider unreachable / bad token → sync reports the error per provider and leaves existing rows untouched.
- Ticket already `running` → Run returns 409; the modal shows the linked session instead.
- Prompt file collision → the enumerator appends a suffix (`09-<slug>-2.md`).

## 9. Deliberately cut (v1)

- **No write-back** to Jira/Linear (no transitions, comments, or assignment).
- No webhooks or background polling — sync is on demand (CLI or the board refresh button).
- No board "add internal ticket" button (CLI `ticket add` covers creation).
- No attachments, comments, or rich field sync — title, description, and origin only.
- Ticketing is off by default; no auto-enable heuristics.

## 10. Verification

- pytest: config load (missing/invalid, multiple providers), adapter parsing (Jira via a mocked `acli` subprocess, Linear via mocked GraphQL), multi-provider sync upsert idempotency, `ticket add`, prompt enumeration (`09` after `01-08`), the run command's argv, and the "one stage at a time" invariant (run ticket leaves the backlog query).
- bun test: tickets read + reconciliation from a temp db, run/sync route behavior with the CLI stubbed.
- Field: configure `provider: internal`, add a ticket, see it in the Backlog, open the modal, Run → watch it move through Planning → Done, reconcile to `done`; the prompt file lands in `adws/prompts/`.
