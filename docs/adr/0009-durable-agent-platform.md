---
status: accepted
---

# Durable agent services beneath deterministic workflows

SSSF will become a local-first durable agent platform, with Software Factory as
its first workflow application. Agents are independently callable services, not
just factory roster roles. We select EVE as the target runtime for new agents
because its integrated channels, durable waits, approvals, delegation, and evals
match this expanded scope; extending pi would leave SSSF responsible for much of
that application runtime.

This is an accepted target, not a claim of production readiness. Executable
prototype results are recorded in
`src/sssf/apps/eve-viability-prototype/VIABILITY.md`; crash recovery and
pre-persistence secret protection currently block migration. Migration is gated
by a pinned-version spike covering crash recovery,
repeated external effects, approval resumption, workspace loss, and self-host
upgrades. Preserve the existing pi execution path during migration; do not default
to layering every EVE invocation over pi. Experimental pi-durable remains an
alternative if EVE cannot satisfy the validated requirements.

## Platform and factory responsibilities

- Always-on means continuously callable, recoverable services, not continuously
  running model inference or a mandatory dedicated process per agent.
- Initial deployment is one trusted operator on one machine. Server deployment
  follows later; hostile multi-tenant hosting is out of scope.
- Agent definitions use reviewed, registered capabilities. Custom executable
  tools are not arbitrary code pasted into the visualizer.
- Initial workflows are versioned, code-backed definitions. The visualizer
  configures, launches, and inspects them; a visual programming language is not
  required.
- SQLite owns SSSF application data. EVE's supported persistent local Workflow
  storage owns its execution state. A custom SQLite Workflow backend is not an
  initial requirement.
- Agents have private credentials, memory, and workspace access by default.
  Delegation does not implicitly transfer authority.
- Deterministic factory code retains sequencing, acceptance, ticket transitions,
  integration, commits, and PR/MR creation. Agents propose changes and descriptions.
- Preserve ticket-linked commit sets on dev, batch signoff, and release PR/MRs
  from dev to main (ADR-0005), with GitHub and GitLab provider support.
- Local tickets remain authoritative (ADR-0002). Tracker sync is explicitly
  invoked initially; pending or failed remote operations remain visible and
  recoverable. Adapt planning skills that currently publish directly.
- Ticket-first (ADR-0001) applies to factory runs, not every general agent
  invocation. The factory Kanban is separate from agent/conversation/execution
  views, and moving a card cannot bypass policy.

## Isolation and acceptance

Docker is the initial execution provider, selected explicitly rather than using
an availability-based fallback. Agent availability is separate from environment,
workspace, and sandbox lifetimes. Each execution owns an isolated workspace;
parallel writers use separate workspaces and exchange patches or snapshots.
Deterministic integration serializes shared-branch updates. Preserve the separate
human workbench concept from ADR-0004, but its per-run-container model is not the
target lifecycle for durable agents.

Project code must not receive the operator's writable checkout, broad credential
mounts, or Docker socket. Trusted runtime/tool implementations and workload
containers have different privileges; the spike must validate Docker authority
and provisioning. Workspace recovery requires checkpoints independently of
conversation recovery. Do not silently resume lost work in an empty workspace.

Required checks fail closed when unconfigured and bind evidence to the exact
candidate, environment, and acceptance policy. Relevant changes invalidate old
evidence. Missing credentials, configuration, or other requirements can park work
for human intervention; they must not produce false green results or unbounded
agent repair loops.

Add a black-box tester that uses browser, API, or CLI interfaces without access to
the implementation repository, and collect its evidence for the PR/MR. Add an
interactive planning agent supporting brainstorming, grilling, specification,
and ticket breakdown through adapted project skills. Detailed tester gate
placement, evidence publication, and planning checkpoints are still being designed.

## Credentials and leakage controls

Use Bitwarden Secrets Manager through the `bws` CLI rather than storing service
secrets in SSSF's database or operator credential mounts. Store references and
capability grants in SSSF; trusted code resolves only authorized secrets. Agents
and ordinary workloads must not receive the BWS machine-account access token.
Sandbox credentials are explicit, narrowly scoped development/test exceptions.
Bootstrap-token provisioning and caching/revocation policy remain open decisions.

Add secret-detection/redaction guardrails and agent hooks, but do not treat hooks
or redaction as a security boundary. EVE stream-event hooks observe events after
channel delivery and durable recording, so pre-storage/pre-delivery sanitization
must be validated at the relevant input, tool, model, and artifact interfaces.
Unexpected suspected leakage must not be published merely because a post-event
hook detected it. Exact quarantine, incident response, and evidence review policy
remain open.

## Consequences and validation references

This deliberately revisits the roster-only agent definition and broad ticket-first
scope. It preserves factory policy while adopting a different execution runtime;
it is not a Next.js rewrite of the current Vue visualizer. EVE is beta, its
self-host upgrades have compatibility constraints, and durable replay does not
make external effects exactly-once. Integration operations need stable identities,
reconciliation, and operation-specific idempotency.

Primary references inspected during design:

- [EVE source snapshot a2c6d64ed3c390eb0f04e21e06f24bf30eb55a81](https://github.com/vercel/eve/tree/a2c6d64ed3c390eb0f04e21e06f24bf30eb55a81)
- [EVE self-hosting](https://github.com/vercel/eve/blob/a2c6d64ed3c390eb0f04e21e06f24bf30eb55a81/docs/guides/deployment/self-hosting.md)
- [EVE Docker provider](https://github.com/vercel/eve/blob/a2c6d64ed3c390eb0f04e21e06f24bf30eb55a81/docs/sandbox/docker.mdx)
- [EVE hook ordering](https://github.com/vercel/eve/blob/a2c6d64ed3c390eb0f04e21e06f24bf30eb55a81/docs/guides/hooks.md#execution-order)
- [EVE security model](https://github.com/vercel/eve/blob/a2c6d64ed3c390eb0f04e21e06f24bf30eb55a81/docs/concepts/security-model.md)
- [Bitwarden Secrets Manager CLI](https://bitwarden.com/help/secrets-manager-cli/)

The SSSF/pi investigations completed; the EVE research child failed its tool
configuration check but left a source-cited report. These references describe
static source/documentation findings, not completed runtime or security tests.
