# Super Simple Software Factory (sssf)

A factory that stamps repeatable agents-plus-code workflows (ADWs) into a
codebase: deterministic code owns sequencing and acceptance, coding agents do
bounded work, and everything traces to SQLite for a visualizer. Agent proposes,
code disposes.

## Approved target model

SSSF is evolving into a local-first durable agent platform, with Software
Factory as its first deterministic workflow application. The target vocabulary
below distinguishes agents, conversations, agent executions, and factory runs.
This direction is approved but not yet implemented: the checked-out runtime
still uses roster-bound pi sessions. See `docs/adr/0009-durable-agent-platform.md`.
Other factory terms below retain their existing meaning unless explicitly
redefined; this document is not a runtime verification report.

## Language

**Ticket**:
The first-class unit of factory work. A ticket is mandatory to start a factory
run, but not a general agent conversation or agent execution. A ticket owns a
state in the ticket machine and accumulates one or more runs over its life.
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

**Sync**:
The bidirectional tracker mirror (`sssf ticket sync`): pulls external tickets
(born `needs-triage`, untracked) per each provider's configured filter — JQL
for jira, in `.adws/config/ticketing.yaml` — then pushes the machine's control
labels onto the origin (add/remove the state label — `needs-triage`,
`ready-for-agent`, `blocked`, … — never touching foreign labels), transitions
`done` tickets to closed, and appends the machine's comments (rejection
feedback, closing note). The CLI and the tracker page's sync button are the
only writers to trackers — nothing pushes on its own.
_Avoid_: writeback (that verb merged into sync)

**Idea ticket**:
A title-only shell with a blank spec, born `needs-triage`. Created by
`sssf ticket new` for quick capture, or by `sssf flow plan` once a brief is
landed. The plan flow turns it into a spec plus implementation children.
_Avoid_: treating idea tickets as implementable (they are not `ready-for-agent`
by construction)

**Run**:
One execution of a factory chain against a ticket. A ticket may have many runs
(initial attempts, rework after rejection); every run has its own trace.
_Avoid_: conversation, agent execution (neither is a factory run)

**Workflow**:
A user-declared, code-controlled coordination of agent executions and
non-agent actions, with explicit sequencing and acceptance rules. Software
Factory is one workflow application, not the whole agent platform.
_Avoid_: treating a workflow as an agent's free-form choice of next actions

**Chain**:
A deterministic Python ADW script: an ordered list of phases for one factory run.
_Avoid_: workflow as a synonym (a chain realizes part of a factory workflow)

**Phase**:
One bounded step inside a chain — either an agent call (typed envelope in,
typed envelope out) or a deterministic code command. Nothing between phases is
decided by an agent.
_Avoid_: step (their workflow's "STEP"s become phases once realized)

**Agent**:
A durable, independently addressable worker with its own declared capabilities
and conversations. It can accept work from workflows, people, or other tools
without owning the caller's sequencing or acceptance policy.
_Avoid_: roster entry, conversation, permanently running reasoning loop;
developer (the factory agent is the **builder**)

**Agent execution**:
One accepted request for an agent to do work, including any waits and
continuations needed to reach an outcome.
_Avoid_: factory run, conversation

**Conversation**:
An agent's durable interaction history within an explicit caller context. A
conversation can contain multiple agent executions and is not a shared memory
space for every caller of that agent.
_Avoid_: factory run, workspace

**Black-box tester**:
An agent that checks observable application behavior against acceptance
criteria through browser, API, or CLI interactions, without access to the
implementation repository.
_Avoid_: source reviewer, builder self-review

**Envelope**:
The typed JSON contract passed between phases. Every agent call pairs with a
concrete envelope type; parse failures re-prompt the same session.

**Gate**:
A deterministic acceptance check whose evidence is tied to the exact candidate
and verification environment. Missing requirements or invalidated evidence
cannot pass; failures lead to bounded correction or a visible human-blocked state.

**Flow**:
A phase orchestrator invoked by the human, mapping one of the factory's phases
of work to chains: `plan` (exploration → spec → tickets, one ticket at a time),
`implement` (triage → build → review, unattended; `implement afk` is the ralph
loop), `deploy` (workbench signoff on the dev snapshot → bump → MR → e2e →
release — one workbench, one batch verdict; the QA stage is embedded in the
deployment workflow, and the deterministic release train runs host-side in a
worktree at dev).
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

**Environment definition**:
A reusable, versioned description of the dependencies and restrictions under
which work executes.
_Avoid_: live sandbox, workspace

**Workspace**:
The isolated files associated with an execution, with explicit ownership and
recoverable checkpoints. A workspace has one writer; sharing changes requires
an explicit handoff.
_Avoid_: agent identity, shared writable checkout

**Runner sandbox**:
The replaceable, isolated environment that executes project code against a
workspace. Its lifetime is separate from the agent, conversation, and workspace.
_Avoid_: one permanent environment shared by all work handled by an agent

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
(`tompero deployment canary promote …`, configured under `release:` in
.adws/config/deploy.yaml) only after the human confirms it at the terminal,
then polls `tompero deployment get` until the deployment is fully promoted.
The canary step before it is equally confirmed; a canary/promote failure
parks the batch's tickets in `blocked` — visible and actionable, never
silently retried (the human unblocks with `sssf ticket backlog`). The
release closes every ticket parsed from the MR's commit set (commits since
the last tag) — implementation tickets and their features close together.
_Avoid_: promoting without human confirmation
