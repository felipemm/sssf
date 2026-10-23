# EVE viability: measured results

## Current verdict

**Narrow smoke: PASS. Latest expanded suite: 6 PASS / 1 FAIL. Platform adoption: BLOCKED.**

A measured run on 2026-10-22 completed in **15,519 ms** with `eve@0.82.0`,
`ai@7.0.137`, and Node `v26.12.0`. The model was a deterministic scripted AI SDK
fixture; EVE, its HTTP service, Docker workspace, and SQLite were real. Later
runs replace the local smoke journal; timings below identify this measured run,
not a guarantee for every environment.

| Phase | Result | Measured duration |
| --- | --- | --- |
| Preflight | PASS | 394 ms |
| EVE build | PASS | 6,781 ms |
| HTTP startup | PASS | 4,007 ms |
| Authentication | PASS: authenticated access; missing/wrong credentials 401 | 30 ms |
| Request submission | PASS | 26 ms |
| Tool execution and turn settlement | PASS | 3,343 ms |
| Result artifact and reopened SQLite record | PASS | 62 ms |
| Service/container cleanup | PASS | 654 ms |

The tool returned the expected execution and stable operation identity, one
invocation, and the exact observed file contents. The accepted artifact matched
that result, and SQLite retained the same execution/session in completed state.
No success inference was made from the scripted model's reply alone.

Run `npm test` and `npm run probe:smoke` from this package. Detailed current
metadata is in ignored `.prototype/smoke-evidence.json`; result artifacts and
scratch databases are under `.prototype/`. The historical full-suite
`.prototype/evidence.json` was not overwritten by smoke runs.

## Defects resolved directly

### Isolated HOME hid Buildx and selected the legacy builder

The original smoke expired after 120 seconds while EVE prepared its root Docker
sandbox template. Its context contained only a 106-byte Dockerfile, not the
repository or node_modules:

```dockerfile
FROM sssf-runner:latest
USER root
RUN mkdir -p /workspace && chown -R 1000:1000 /workspace
USER 1000:1000
```

An earlier uncached legacy-build reproduction remained at the RUN instruction
for 60 seconds. That did **not** establish that recursive chown itself was slow:
the legacy builder also snapshots the layer before printing the next instruction.

Direct measurements distinguished those operations:

- A disposable container found an empty `/workspace` (one directory, 4 KiB).
  Starting the container and executing mkdir/chown/measurement completed in
  **0.622 seconds**.
- A separate `docker commit` exceeded a **30-second** bound.
- With Buildx visible through a credential-free Docker configuration, a native
  BuildKit build completed in **3.642 seconds**, including the RUN instruction
  in **0.3 seconds** and image export in **0.7 seconds**.
- The next EVE build completed in **43.041 seconds**; a subsequent smoke build
  completed in **6.781 seconds** with prepared/cached resources.

Correction: `cleanRuntimeEnvironment` now creates a private Docker config
containing only known installed CLI-plugin directories and sets
`DOCKER_BUILDKIT=1`. It never copies the operator's Docker auth/config. A
regression test verifies those properties and absence of inherited BWS tokens.
No Docker daemon restart, global prune, image replacement, or EVE vendor patch
was needed. This identifies a practical legacy-builder bottleneck in this local
setup, not a universal EVE or Docker performance claim.

### Imported sandbox environment identity was rejected

Once the build completed, the first real tool result failed with:

> The requested sandbox environment is not active for the current session.

The tools passed an imported/aliased environment object to
`ctx.getSandbox(environment)`. They need only the common sandbox interface;
using `ctx.getSandbox()` selects the session's already-configured sandbox and
resolved that tool failure. The later expanded isolation gate independently
found the sibling sandbox definition was shadowed by the sandbox directory;
that separate configuration problem is corrected below.

### Evidence-driver timeout and privacy defects

The verification phase referenced `timeoutMs` without receiving it in its
callback; that parameter is now bound correctly. Session-settlement phase
journaling now records event shape/metadata rather than the full conversation
and tool arguments. The passing smoke checks EVE action status, error markers,
actual structured output and workspace contents before accepting the result.

## Expanded runtime gates (2026-10-22)

The latest isolated full run (`npm run probe`, 2026-10-22, EVE 0.82.0 / AI SDK
7.0.137 / Node 26.12.0) exits **1** and records six passing gates and one
failure in `.prototype/evidence.json`. Crash recovery remains an adoption
blocker; the protected synthetic-secret gate passes its measured scope.

| Gate | Result | Observation |
| --- | --- | --- |
| Loopback authentication | PASS | Authenticated calls work; absent/wrong credentials return 401. |
| EVE → Docker → SQLite | PASS | Structured result/artifact and completed execution/session identity. |
| Docker workspace isolation | PASS | Distinct owned containers, network `none`, no checkout/socket/credential mounts or credential-like environment names. |
| Stop/resume and workspace loss | PASS | Original container/file resumes; deletion leads to a trusted human-blocked result, not an assistant-text success claim. |
| Approval across process restart | PASS | Same pending request/session resumes; operation executes once and SQLite reaches completed. |
| Hard crash before tool checkpoint | FAIL | In the latest full run, the server was confirmed dead after SIGKILL; the durable local effect exists once, but no replay invocation was observed during 55,104 ms after restart health. |
| Synthetic pre-persistence secret protection | PASS (tested prototype scope) | Authenticated ingress, tool/provider outputs, stream-result metadata, provider accessors, and nested error payloads were guarded; required scans completed with zero exact canary matches. |

### Sandbox discovery must fail closed

EVE 0.82.0 selects `sandbox/` whenever that directory exists, rather than falling
back to a sibling `sandbox.ts`. Our Dockerfile directory therefore shadowed the
original authored module and EVE selected `eve:defaults:sandbox.ts`. Container
inspection exposed bridge networking despite the tool's then-hardcoded policy
claim. This was a prototype layout error revealed by independent verification.

The module now lives at `agent/sandbox/sandbox.ts`. After every build the helper
requires the compiled manifest to select that exact authored source and Docker
provider; a fallback is rejected. Container inspection confirms network `none`.
The tool's policy field is named `requestedNetworkPolicy` so it cannot masquerade
as an independently observed Docker setting.

### Hard-crash recovery remains unresolved

The fault injector writes a metadata-only marker immediately before SIGKILL,
after its disposable workspace effect and SQLite operation record but before
returning the tool result. EVE's launcher returns code 1 when its child is killed;
the probe now verifies the killed PID is gone rather than assuming the launcher's
signal represents the child. The effect count remains one and invocation count
remains one after the 55-second recovery window. The coordinator persists a
human-blocked execution. This demonstrates **no observed automatic recovery
within that window**, not that EVE can never resume or that duplicate effects
occurred. Root cause and longer-window behavior are not established.

Each independent test archives prior prototype Workflow data while no service
is running. Restarts inside the same test do not archive anything. This prevents
stale interrupted runs from resuming during later tests. The hard-crash failure was reproduced with fresh initial state, so it cannot be dismissed merely as cross-test contamination. The focused recovery-only probe (`PROBE_GATE=stable-idempotent-replay npm run probe`) reproduces it without running unrelated gates. On the latest measured run (2026-10-22, EVE 0.82.0 / AI SDK 7.0.137 / Node 26.12.0), the gate took **58,153 ms** total and observed **55,014 ms** after the restarted service became healthy. It SIGKILLed server PID **96469** after the effect; the launcher exited code **1**, and the killed PID was independently confirmed gone. Immediately before restart and after the full recovery window, the Workflow run remained `running`; its `turnStep` remained `running`, `attempt: 1`, with no failure event. The event tail remained `session.started`, `turn.started`, `message.received`, `step.started`, `actions.requested`. Stable operation `replay_49c1f12076:workspace-effect` remained completed with `invocation_count: 1` and `effect_count: 1`. The recovery startup log contained only the HTTP listening and schedules-disabled lines; it reported no queue/recovery error.

The pinned EVE 0.82.0 generated server installs and starts its local Workflow World. Its bundled local World type exposes `recoverActiveRuns`; bundled implementation defaults it to true unless `WORKFLOW_LOCAL_RECOVER_ACTIVE_RUNS` is `0`/`false`, and `start()` submits pending/running runs to the queue. The clean prototype environment leaves that variable unset, so this is not explained by recovery being explicitly disabled. These source observations do not explain why this concrete run was not resumed, nor establish that the same behavior occurs for every crash. No supported opt-in correction was found, so the gate remains FAIL and no status/counter reset, prompt resend, vendor patch, or timeout enlargement was used to manufacture recovery. Root cause remains unresolved. Archives contain synthetic-only raw state and are not publishable artifacts.

In the latest full probe after the provider-result boundary fixes (2026-10-22, EVE 0.82.0 / AI SDK 7.0.137 / Node 26.12.0), server PID **70053** was confirmed gone after SIGKILL and the launcher exited **1**. The same Workflow run and `turnStep` remained `running` at attempt 1; after **55,104 ms** from restart health there was still no further event or invocation (1 invocation, 1 effect). The recovery gate took **57,694 ms** and the full suite took **74,997 ms**, recording six PASS gates and this one FAIL; adoption remains blocked. This reproduces the bounded recovery blocker, not a claim that recovery can never happen. The active run evidence and final event tail are in ignored `.prototype/evidence.json`.

### Synthetic pre-persistence security boundary (measured PASS)

The previous prototype experiment sent a canary directly through an unguarded
authenticated EVE route; it confirmed that raw EVE history, tool results, and
streamed output could retain that value despite `toModelOutput` projection and
post-event hooks. That unsafe route is **not** the current protected test path;
the old result is historical baseline evidence, not a live bypass.

Current guards cover the selected authenticated EVE HTTP channel and the
reviewed compiled tool/model surface:

- A standard EVE channel `AuthFn` first validates Basic auth, then inspects a
  clone of each bounded (at most 1 MiB) JSON POST body before dispatch. It scans
  recursively, including `message`, nested `clientContext`, and `inputResponses`;
  unparseable/oversized requests, attachment-like content, and unknown binary
  inputs fail closed with a static 403. `uploadPolicy: "disabled"` rejects
  attachments as a second explicit limit. A normalized-message `onMessage`
  guard is defense in depth, not the primary boundary.
- Reviewed tool results are scanned before the tool returns to EVE. The
  fixture's secret-producing tool creates the synthetic value inside trusted
  tool code; it is not supplied by a user. Its guard error contains no value.
  A metadata-only hook sees the sanitized `action.result` after persistence and
  cancels the turn there; it does not perform the secret prevention. All five
  reviewed tool `execute` paths also normalize unexpected exceptions to fresh
  static `ProtectedToolError` values; the typed secret-boundary failure remains
  a fresh static `SecretBoundaryError`.
- AI SDK `wrapLanguageModel` middleware inspects model prompt input before the
  provider call and scans generated results and fully buffered streaming output
  before EVE receives any chunks. Stream-result `request`/`response` metadata is
  copied from enumerable plain data and scanned; generated results (including
  standard SDK `Date` response timestamps) and each stream chunk are likewise
  snapshotted so checked provider objects are never returned by reference.
  Buffering remains capped at 1 MiB and 16,384 stream parts;
  bounded cross-chunk checks cover text, reasoning, and tool-call inputs. Provider
  accessors, nested `Error` payloads, cycles, binary/custom objects, and other
  unsupported shapes fail closed with a fresh static `SecretBoundaryError`.
  Errors from `doGenerate`, `doStream`, reader acquisition, or `reader.read` are
  also replaced without retaining causes/custom properties. This conservatively
  blocks some benign provider values and hides provider diagnostics rather than
  forwarding upstream data.
- Optional default shell/file/network tools and root-copy delegation are
  disabled. The build verifies the compiled tool allowlist is exactly the five
  reviewed prototype tools and there are no connections or subagents, closing
  the unguarded default/deferred/native capability path for this app.
- Coordinator ingress protects its supported submission helper; artifact
  persistence independently quarantines sensitive or unscannable outputs and
  leaves result JSON null/human-blocked. Required durable EVE, `.prototype`
  coordinator data, artifact, and `.output` build scans read every regular file
  in bounded 64 KiB chunks with 4,096-character overlap. Exact canaries crossing chunk boundaries are
  detected; unreadable files/directories, symlinks, unsupported entries, and
  missing required roots are counted as unscannable/read failures and make the
  security gate fail. The complete-file scanner is separate from the 1 MiB
  model/payload guard.

The security gate is exercised on actual EVE HTTP, tool, and AI SDK middleware
interfaces. Unit regressions also invoke the public middleware interface with a
stream-result response getter that throws a provider error containing a canary
only in a custom field, direct canaries in stream and generate metadata, a
changing metadata accessor, metadata mutated after its snapshot, and nested
custom-property `Error` values in generate and streamed error payloads. The
middleware rejects accessors without invoking them, returns fresh static errors,
and does not return the checked provider objects. A distinct ingress-only canary
is denied at nested `clientContext` and `inputResponses`; separate tool-only,
split-provider-output, and provider-error sessions contain no canary in their
user messages. The scripted provider error places a canary only in nested custom
error properties (including an `AggregateError`, cause, and cycle); the
middleware replaces it before EVE can persist the failure. The tool emits one
guarded error action and the turn is cancelled. All required runtime file scopes
were completely scanned in the latest full run: durable EVE **638/638 files,
437,378 bytes**; `.prototype` coordinator data **12,185/12,185 files,
8,655,847 bytes**; artifacts **54/54 files, 47,034 bytes**; compiled output
**62/62 files, 9,535,996 bytes**. Every scope reported zero unscannable entries
and zero read failures. Exact canary matches were **0** in all four file scopes,
events, and server logs. Durable EVE had zero pattern candidates; compiled
dependencies had five generic pattern candidates, not exact run-scoped values.
The metadata-only audit records one guard rejection without any canary bytes.

This PASS is bounded to the selected route, static wrapped model, five reviewed
tools, and synthetic registry/patterns. The current app exposes no other
channels, but future channels, direct model configurations, dynamic/provider
executed tools, arbitrary malicious Node code, provider-side logging/retention,
real BWS values, and every possible secret encoding/pattern are not covered.
Unknown attachments are denied rather than scanned. No universal secret-safety
or hostile multi-tenant guarantee is claimed. The former raw-EVE failure remains
historical evidence; no data was post-hoc scrubbed to make this protected gate
pass.

## Remaining scope and next decisions

Resolve the local-world crash-recovery behavior before considering platform
adoption. General exactly-once effects, upgrades, BWS integration, real
model/provider behavior, black-box browser testing, and PR/MR publication remain
unvalidated. A source-blind tester is not implemented by these runtime probes.

Validation for this security stage: `npm test` passes **12/12**; `node --check`
passes for `security-guard.mjs` and `security.test.mjs`; `npm run probe:smoke`
passes in **3,844 ms**. The focused
`PROBE_GATE=synthetic-secret-boundaries npm run probe` records security PASS and
exits **1** because the six unselected gates remain NOT_RUN. The full
`npm run probe` takes **74,997 ms**, exits **1**, and records six PASS / one
FAIL: hard-crash recovery. The security gate passes, with all four required scan
scopes complete and zero unreadable/unscannable counts. No real secrets/models,
external provider calls, Git commits/pushes, tracker writes, or production
changes occurred. The full probe stopped EVE and removed its owned session
containers; no listener remained on port 3217. Shared images remain untouched.
Probe evidence and synthetic-only disposable diagnostics are under ignored
`.prototype/` paths.
