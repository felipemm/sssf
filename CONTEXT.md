# Super Simple Software Factory (sssf)

A factory that stamps repeatable agents-plus-code workflows (ADWs) into a
codebase: deterministic code owns sequencing and acceptance, coding agents do
bounded work, and everything traces to SQLite for a visualizer. Agent proposes,
code disposes.

## Language

**Ticket**:
The first-class unit of work. Every piece of sssf work is a ticket; a ticket is
mandatory to start any run. A ticket owns a state in the ticket machine and
accumulates one or more runs over its life.
_Avoid_: backlog item, issue (the external tracker's record is an issue; the
ticket is sssf's).

**Tracked ticket**:
A ticket created by the plan flow (root ticket plus its plan-published
children). Tracked status is permanent — it records origin of creation, never
changes.
_Avoid_: treating synced tickets as tracked

**Untracked ticket**:
A ticket synced from an external tracker (jira, gitlab, github) that the plan
flow did not create. Untracked tickets never appear in the backlog; they live on
the issue-tracker page until marked `ready-for-agent`, and they follow exactly
the same rules and workflows as tracked tickets.
_Avoid_: "external" (a tracked ticket can also exist on a tracker)

**Idea ticket**:
A title-only shell with a blank spec, born `needs-triage`. Created by
`sssf ticket new` for quick capture, or by `sssf flow plan` once a brief is
landed. The plan flow turns it into a spec plus implementation children.
_Avoid_: treating idea tickets as implementable (they are not `ready-for-agent`
by construction)

**Run**:
One execution of a chain against a ticket. A ticket may have many runs (initial
attempts, rework after rejection); every run has its own trace.
_Avoid_: session (a session is the traced record of a run, not the run itself)

**Chain**:
A deterministic Python ADW script: an ordered list of phases for one run.
_Avoid_: workflow (that's the whole factory, not one script)

**Phase**:
One bounded step inside a chain — either an agent call (typed envelope in,
typed envelope out) or a deterministic code command. Nothing between phases is
decided by an agent.
_Avoid_: step (their workflow's "STEP"s become phases once realized)

**Agent**:
A roster entry in `sssf.config.yaml` (planner, builder, reviewer, scout,
designer, documenter). Each agent is its own role; there is no separate role
layer.
_Avoid_: developer (that role is the **builder**)

**Envelope**:
The typed JSON contract passed between phases. Every agent call pairs with a
concrete envelope type; parse failures re-prompt the same session.

**Gate**:
A deterministic check that validates an envelope's claims (not guesses).
Failures return the same session for corrections.

**Flow**:
A phase orchestrator invoked by the human, mapping one of the factory's phases
of work to chains: `plan` (exploration → spec → tickets, one ticket at a time),
`implement` (triage → build → review, unattended; `implement afk` is the ralph
loop), `deploy` (sandbox → signoff → bump → MR → e2e → release — the QA stage
is embedded in the deployment workflow).
_Avoid_: running chains ad hoc from the terminal

**Plan flow**:
`sssf flow plan <ticket-id>` — each step is its own fresh agent session,
gated by deterministic code asking the human: optional exploration
(grill-me/wayfinder, skippable), then grill-with-docs (ADRs + CONTEXT.md), then
to-spec (spec.md), then to-tickets (child tickets, `ready-for-agent`).
`sssf flow plan` with no arguments runs the same chain from scratch: exploration
lands a brief, an idea ticket is created, then spec → slices. Plan runs only on
idea tickets and untracked tickets — never on implementation tickets (they are
already specified).
_Avoid_: one long session covering the whole plan

**Ralph loop**:
`sssf flow implement afk` — iterates `ready-for-agent` tickets, one per run,
each run a fresh context window, until the queue is empty or the cap is hit
(default 30 rounds, configurable). Each run runs the implement chain, which
includes the builder's self-review (`/code-review`) before the reviewer gate.
_Avoid_: long-lived agent sessions that carry context across tickets

**Runner sandbox**:
The ephemeral, isolated environment in which every run executes. Work never
happens directly on the host machine.
_Avoid_: running on the user machine

**Hermetic skills**:
The sssf-curated skill set, vendored with the codebase and stamped into each
project at init. Agent sessions never load global skills (`pi --no-skills`);
they load only the project's stamped skills.

**Dev branch**:
The integration branch where all ticket work lands after review, and the base
for the QA sandbox. Release candidates branch from dev into main.
_Avoid_: merging ticket work straight to main

**Ticket machine**:
The canonical lifecycle of a ticket:
`needs-triage → ready-for-agent → in-progress → ready-for-signoff →
ready-to-deploy → done`. Rejection edges re-enter the implement flow; canary
failure parks the ticket in `blocked`. Signoff is batch-level on the dev
snapshot (fix-forward, never revert), with a revert escape hatch for genuinely
unwanted tickets before the MR.
_Avoid_: triage's `ready-for-human` (that means a human must implement it
themselves — opposite of `ready-for-signoff`)

**Backlog**:
The `ready-for-agent` queue only. Untracked tickets never appear here until
marked `ready-for-agent` from the issue-tracker page.
_Avoid_: a backlog full of un-triaged synced issues

**Issue-tracker page**:
The viz view (per project) showing every ticket sssf knows: plan-created
(tracked) plus everything fetched from the configured trackers (untracked),
with their origin and state. The user marks untracked tickets
`ready-for-agent` from here.
_Avoid_: hiding untracked tickets until they are in the backlog

**Commit by code**:
Commits are performed by the deterministic layer (`CommitPhase`), never by the
agent. Agents stop at "done, uncommitted". Each ticket's work lands on dev as
its own commit-set, so a rejected ticket can be reverted by its own commits.
_Avoid_: letting agents run `git commit`

**Planner-in-implement**:
The planner agent is not replaced by the plan flow — during implementation it
still analyzes the chosen ticket and breaks it into super-laser-focused
changes before the builder works.
_Avoid_: sending tickets straight to the builder unsliced

**Notify**:
The Slack notification path, driven by a bot token (`chat.postMessage`): one
thread per ticket, five retries with backoff, and every payload carries the
URLs needed to act (workbench, MR, release, tompero). Notifications are
alerting only — human checkpoints stay in the tools.
_Avoid_: routing human decisions through Slack (buttons arrive in a later
phase)

**Monitor**:
The MR watcher that lives inside the viz service — when viz runs, it scans
tickets in `ready-to-deploy` with an open MR every 10 minutes and notifies on
pipeline green/failed and on merge. Stop viz, stop monitoring.
_Avoid_: a separate daemon that outlives the viz

**Promote**:
The canary promotion step: sssf executes the per-project tompero command
(`tompero deployment canary promote …`) only after the human confirms it at
the terminal, then polls `tompero deployment get` until the deployment is
fully promoted.
_Avoid_: promoting without human confirmation
