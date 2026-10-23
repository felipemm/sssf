# EVE viability prototype (throwaway)

**The narrow smoke passes. The latest expanded suite has six passing gates and
one failing gate: hard-crash recovery. Synthetic pre-persistence secret
protection passes on the tested interfaces, but adoption remains blocked.** This
is not a factory migration.

Pinned dependencies: `eve@0.82.0`, `ai@7.0.137`, `zod@4.3.6`. The model is a
scripted AI SDK `LanguageModelV3` fixture: no external model calls, API spend,
real credentials, or claims about reasoning quality.

## Run

Requires Node 24+, a running local Docker daemon on a Unix socket, Docker Buildx,
and the existing `sssf-runner:latest` image. The prototype does not build that
base image or install Docker for you.

```bash
cd src/sssf/apps/eve-viability-prototype
npm ci --ignore-scripts
npm test
npm run probe:smoke
```

The smoke builds EVE, starts it on `127.0.0.1:3217` with a fresh in-memory Basic
credential, verifies missing/wrong credentials are rejected, submits one request,
and validates a successful real Docker tool result and observed workspace file.
It persists a result artifact and reopens SQLite to verify the completed
execution/session identity. It then stops its service and removes its recorded
session container. Shared runner/template images remain intact.

The smoke is bounded to 240 seconds including cleanup, with shorter per-phase
deadlines. It atomically journals metadata after each phase under the ignored
`.prototype/smoke-evidence.json`. Inspect `VIABILITY.md` for measured outcomes.
The journal omits conversation bodies, tool arguments, credentials, and headers;
the EVE runtime still owns its separate durable history.

## Isolation and Docker configuration

The trusted EVE process receives an allowlisted environment and isolated HOME.
A separate `.prototype/docker-config/config.json` contains only installed CLI
plugin search directories. Host Docker auth/config is **not** copied. BuildKit
is explicitly enabled; hiding Buildx had selected the slow legacy image builder.
If Buildx is not installed, the build fails rather than silently reverting.

The authored `agent/sandbox/sandbox.ts` explicitly selects Docker with `deny-all`
networking. Do not move it to a sibling `agent/sandbox.ts` while the sandbox
directory exists: EVE selects the directory and ignores that sibling. The build
checks the compiled manifest and refuses fallback to EVE's default sandbox. The
workload receives neither the host checkout nor Docker socket/route credentials.
The trusted provider does have Docker authority; this is a trusted single-operator
prototype, not a hostile multi-tenant security design. Only the smoke's file
operation and its observed result are validated by the narrow command; the
broader cross-workspace isolation gate is separate.

## Acceptance contract and limitations

A complete viability verdict requires all of these, not merely smoke success:

1. **Narrow smoke:** callable authenticated EVE → Docker operation → structured
   artifact → matching durable SQLite execution. **PASS**.
2. Approval survives a same-version process restart and resumes the same session.
3. Crash after an external effect is reconciled using a stable operation identity.
4. Cross-workspace isolation and absence of host credential/socket exposure.
5. Stop/resume and native-container loss restore a checkpoint or explicitly block.
6. Synthetic-secret prevention across ingress, tool results, split model output,
   durable storage, and artifacts.

`npm run probe` exercises the expanded suite and writes current evidence to
`.prototype/evidence.json`, independently of smoke evidence. Authentication,
callable execution, Docker isolation, stop/resume-or-block, parked approval
restart, and the synthetic security gate pass. Hard-crash recovery remains
blocked: the latest full run observed no replay for **55,104 ms** after restart
health, with the same Workflow `turnStep` still running at attempt 1 and one
effect/one invocation. The killed server PID was confirmed gone. The pinned
local Workflow World defaults active-run recovery on, but no supported
correction or root cause is established; see `VIABILITY.md`. The suite exits
nonzero because that recovery gate remains FAIL; adoption stays BLOCKED.

The security gate uses a custom authenticated HTTP `AuthFn` to inspect bounded
JSON request bodies before EVE dispatch, including nested `clientContext` and
`inputResponses`; unknown attachments fail closed. The selected model is wrapped
with AI SDK middleware that scans prompt input and buffers/scans complete
streaming or generated outputs (bounded to 1 MiB and 16,384 stream parts) before
EVE can persist or deliver them. Generate results and stream-result metadata
(request, response, and other plain data) are snapshotted and scanned before
return; SDK `Date` response timestamps retain their type. Stream chunks are
snapshotted before scanning and buffering. Enumerable
accessors, nested `Error` values, cycles, binary/custom objects, and other
unsupported provider shapes fail closed with a fresh static boundary error.
Provider exceptions at generate, stream startup, reader acquisition, or stream
read are also replaced with that error; original causes/custom diagnostics are
never forwarded. This conservatively blocks some otherwise benign provider
results and discards provider diagnostics. Reviewed tool results are scanned before return, and
reviewed tool exceptions are replaced with static errors while preserving the
typed secret-boundary signal.
Optional default tools and root-agent self-delegation are disabled, and the
build rejects any compiled tool beyond the five reviewed prototype tools or any
connections/subagents. Live EVE security-gate sessions test a canary created only
inside trusted tool code, a distinct provider-only canary split across output
chunks, and provider-error cases; those canaries are not sent in the sessions'
user messages. Unit regressions separately cover returned stream/generate
metadata canaries, accessor-thrown errors, changing accessors, post-snapshot
mutation, nested custom-property `Error` payloads, and safe `Date` metadata.
Those adjacent cases are not all exercised by the live runtime sessions.
Required durable EVE,
`.prototype` coordinator data, artifact, and `.output` build file scans stream
complete files in 64 KiB chunks with 4,096-character overlap. The gate fails on
any unreadable, unsupported, or unscannable scope. This file scanning does not raise the 1 MiB model/payload
guard. Historical direct-EVE leakage is retained as a record of the former
unguarded baseline, not as a live bypass or the current protected path. See
`VIABILITY.md` for measured coverage and limits.

Each independent probe/smoke run archives previous disposable Workflow state to
`.prototype/workflow-history/` before starting. Restarts *within* a run preserve
its Workflow state. This prevents older interrupted probes from being replayed
in later tests; archived data can contain synthetic canaries and must not be
published as evidence.

EVE event hooks still run after channel delivery and durable stream recording;
`toModelOutput` still changes only the model projection. Neither is treated as
a security boundary. The registered `agent/hooks/security_audit.ts` records
metadata only and can cancel after a sanitized guard error event; the actual
prevention seams are the authenticated request guard, reviewed tool wrappers,
and AI SDK model middleware. Coordinator artifact quarantine remains a final
independent safeguard. Pattern candidates in dependencies are reported
separately from exact synthetic-canary/auth matches; a pattern match alone is
not proof of a real key. This synthetic result is not universal secret safety:
only the selected HTTP channel and explicitly curated tools/model path are
validated. Unknown attachments fail closed, but arbitrary malicious trusted
Node code, other channel types, alternate model/provider integrations, real
provider behavior, and secret-pattern variants are not established.

No BWS integration, black-box tester/browser verification, PR/MR publication,
tracker mutations, real model/provider behavior, upgrades, or factory migration
is validated here. All `.prototype/`, `.eve/`, and `.output/` data is disposable.
Never use real credentials with these synthetic security fixtures.
