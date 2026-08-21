# `sssf spec create` — Interview-Driven Ticket Creation — Design

Date: 2026-08-21
Status: Draft for review

## Goal

A new `sssf spec create` command that spawns an **interactive pi session** —
customized with an interviewer persona and the grilling/brainstorming skills —
to interview the user and turn the answers into a **robust, runnable ticket**:
a structured spec written to `adws/prompts/NN-<slug>.md` and an internal ticket
created via the ticketing system, runnable from the visualizer's kanban.

The interview validates what the user is asking for (consequences, effort,
complexity, UX, perceived value) instead of just recording it — the difference
between a story ("I want X") and a well-formed spec.

## Current state (measured)

- `sssf ticket add <title>` creates an internal backlog ticket with an **empty
  description** and no prompt link; the description field is written as `''`.
- `sssf ticket run <id>` generates its own prompt (`next_prompt_name` →
  `adws/prompts/NN-<slug>.md` from title + description) and links
  `prompt_file` at run time — a pre-existing spec file is not honored.
- pi supports exactly what this needs: interactive mode (default),
  `--append-system-prompt <file>` (custom context), `--session`/`--fork`,
  skills auto-loaded from `~/.pi/agent/skills/`.
- Skills on this machine: **superpowers** (grilling, brainstorming, …) up to
  date (v6.3.0); **mattpocock skills** (`grill-me`, `grill-with-docs`, …)
  local clone behind `origin/main` by ~10 commits (grilling HR separator,
  new `implement-spec` skill, YAML front-matter fixes).

## 1. CLI command: `sssf spec create`

```
sssf spec create [--mode idea|bug|feature] [--title "<title>"]
```

Behavior (a new `spec` subcommand group under the sssf CLI):

1. Resolve the project (`find_project`); warn on legacy layout; require the
   internal ticketing provider enabled (same guard as `ticket add`).
2. Derive a slug from `--title` or prompt for one (the ticketing slug rule).
3. Write the per-run interviewer context file to
   `adws/adw_data/spec_interview/<mode>-<adw-id-or-slug>.md` (runtime dir,
   gitignored) — rendered from the mode template + project context (config
   roster summary, data-dir paths).
4. Spawn **interactive pi** in the project root:
   `pi --append-system-prompt <context-file>` (cwd = project; skills loaded
   from the user's pi home). The command blocks until the session ends.
5. Exit with the pi session's exit code; print a summary.

The command is a thin launcher — the interview itself is the pi session.

## 2. Interviewer context templates (shipped, mode-specific)

`src/sssf/templates/spec_interviewer/idea.md`, `bug.md`, `feature.md` — the
`--append-system-prompt` payload, each with:

- **Persona**: a "spec interviewer" who validates before recording — hard
  questions, no rubber-stamping.
- **Mode-specific skill routing + question focus**:
  - `idea`: use superpowers **`/grilling`** (relentless probing) then
    **`/brainstorming`** — consequences, effort, complexity, UX improvement,
    perceived value, risks, alternatives; end with a value/effort verdict.
  - `bug`: use mattpocock **`/grill-me`** and **`/grill-with-docs`** — error
    text, logs, displayed messages, error codes, repro steps, environment,
    expected vs actual; produce a proposed-fix hypothesis.
  - `feature`: focused implementation questions — **story vs spec**
    distinction (a story gets pushed into concrete requirements); scope,
    acceptance criteria, edge cases, out-of-scope.
- **Project context block**: the agent reads `adws/config/sssf.config.yaml`
  (roster summary) and the existing `adws/prompts/` numbering so the spec fits
  the project's conventions.
- **Output contract** (the hard gate): the agent must, before finishing,
  (a) write the spec to `adws/prompts/NN-<slug>.md`, and (b) create the ticket
  (section 4). The context defines the spec's required sections.

## 3. Skills provisioning

- **superpowers**: already up to date — no action.
- **mattpocock**: update the local clone
  (`~/dev/ai/resources/github-projects/mattpocock-skills`, `git pull`) and
  refresh the installed skills under `~/.pi/agent/skills/` so the interactive
  session has the latest `grill-me`/`grill-with-docs` (and the new
  `implement-spec` if useful).
- No new standalone skill for v1 — the mode templates carry the interview
  contract. (A dedicated `sssf-spec` skill is a possible follow-up.)

## 4. Output → runnable ticket (small ticketing enhancements)

1. **The agent writes the spec** to `adws/prompts/NN-<slug>.md` using the
   ticketing numbering (`next_prompt_name` semantics — no collision).
2. **`sssf ticket add` gains `--description "<text>"`** (currently dropped):
   the agent creates the ticket with the spec's summary as the description.
3. **`sssf ticket add` gains `--prompt-file <path>`**: links the spec file to
   the ticket at creation.
4. **`sssf ticket run` honors an existing `prompt_file`**: when the ticket
   already has a prompt file (the interview's spec), the run uses IT as the
   prompt instead of regenerating a thin title+description prompt. No
   prompt_file → current behavior unchanged.

The visualizer already renders the ticket + prompt file; the kanban **Run**
button then executes the interview's spec verbatim.

## 5. Error handling

- No project / legacy layout / internal provider disabled → clear message +
  exit 1 (same guards as `ticket add`).
- pi binary missing (`misc.which("pi")`) → clear message + exit 1.
- Session interrupted (Ctrl-C) → the context file stays (gitignored runtime
  dir); no ticket is created unless the agent wrote one — the agent is
  instructed to create the ticket ONLY after the spec is written, so an
  interrupted interview leaves at most a spec file, never an empty ticket.
- A ticket created without a spec (agent error) → the existing
  title+description run path still works (back-compat).

## 6. Tests

- **CLI unit**: `spec create` resolves the project, renders the mode context
  (mode-specific content present), spawns `pi` with `--append-system-prompt
  <path>` (mocked spawn), respects `--mode`/`--title`, guards
  (no project / legacy / provider disabled / pi missing).
- **Ticketing**: `ticket add --description` persists the description;
  `--prompt-file` links it; `ticket run` with a pre-set `prompt_file` uses
  that file's contents as the prompt and skips regeneration; no prompt_file →
  unchanged behavior.
- **Template**: the three context templates exist under
  `src/sssf/templates/spec_interviewer/` and each contains the output
  contract marker.
- **Manual e2e**: one live `sssf spec create --mode idea` interview producing
  a spec + ticket, then Run from the visualizer.

## 7. Verification

1. `uv run pytest` + `cd src/sssf/apps/visualizer && bun test` green.
2. `sssf spec create --mode bug --title "..."` opens the interactive session
   (verified in a terminal), the agent interviews, writes the spec, creates
   the ticket.
3. The ticket appears on the kanban; Run executes with the interview's spec.
4. ruff + mypy clean (CI gates).

## Out of scope

- A standalone `sssf-spec` skill (follow-up if the templates prove too thin).
- Non-interactive/silent spec generation (the interview is the point).
- External ticket providers (jira/linear) for `spec create` — internal only
  for v1, same as the current runnable flow.
- Changing the ADW chain semantics — only the prompt file the run uses.
