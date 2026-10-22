import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import net from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { archivePrototypeWorkflowState, buildEve, cleanRuntimeEnvironment, prototypeRoot, startEve } from "./eve-process.mjs";

const appRoot = dirname(fileURLToPath(import.meta.url));
const dbPath = join(prototypeRoot, "smoke.sqlite");
const evidencePath = join(prototypeRoot, "smoke-evidence.json");
const port = Number(process.env.PROBE_SMOKE_PORT ?? 3217);
const host = `http://127.0.0.1:${port}`;
const executionId = `smoke_${randomUUID().replaceAll("-", "")}`;
const operationId = `${executionId}:workspace-effect`;
const auth = { username: `smoke-${randomUUID().slice(0, 8)}`, password: randomBytes(32).toString("base64url") };
const versions = {
  eve: JSON.parse(await readFile(join(appRoot, "node_modules/eve/package.json"), "utf8")).version,
  ai: JSON.parse(await readFile(join(appRoot, "node_modules/ai/package.json"), "utf8")).version,
  node: process.version,
};
const GLOBAL_BUDGET_MS = 240_000;
const CLEANUP_RESERVE_MS = 15_000;
const startedAt = Date.now();
const mainDeadline = startedAt + GLOBAL_BUDGET_MS - CLEANUP_RESERVE_MS;
let server;
let sessionId;
let dockerEnv;
let dockerDeadline;
let containerRemoved = false;
let activePhase = null;
let lastActionResultMetadata = null;
const evidence = {
  prototype: "sssf-eve-viability-prototype",
  scope: "one-authenticated request / one real Docker tool operation / one completed SQLite execution",
  createdAt: new Date(startedAt).toISOString(),
  versions,
  modelBoundary: "Scripted AI SDK LanguageModelV3 fixture; reasoning and real-provider behavior NOT_TESTED.",
  overallAdoption: "BLOCKED / NOT_EVALUATED by this scoped smoke pass.",
  outcome: "RUNNING",
  overallStatus: "NOT_EVALUATED",
  activePhase: null,
  phases: [],
  execution: { executionId, sessionId: null },
  credentialStoredInEvidence: false,
};

process.env.PROTOTYPE_DB = dbPath;
await mkdir(prototypeRoot, { recursive: true });

function redact(value) {
  return String(value ?? "")
    .replaceAll(auth.password, "[REDACTED]")
    .replaceAll(auth.username, "[REDACTED]")
    .slice(-3000);
}

async function persistEvidence() {
  evidence.elapsedMs = Date.now() - startedAt;
  evidence.activePhase = activePhase;
  const temporaryPath = `${evidencePath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  await rename(temporaryPath, evidencePath);
}

function budgetFor(maxMs) {
  return Math.max(0, Math.min(maxMs, mainDeadline - Date.now()));
}

async function phase(name, maxMs, operation) {
  const durationMs = budgetFor(maxMs);
  const record = { name, status: "RUNNING", startedAt: new Date().toISOString(), elapsedMs: 0 };
  evidence.phases.push(record);
  activePhase = name;
  await persistEvidence();
  const phaseStartedAt = Date.now();
  if (durationMs <= 0) {
    const error = new Error(`Global smoke deadline exhausted before phase ${name}.`);
    record.status = "FAILED";
    record.errorType = error.name;
    record.error = redact(error.message);
    record.completedAt = new Date().toISOString();
    record.elapsedMs = Date.now() - phaseStartedAt;
    evidence.outcome = "FAIL";
    evidence.overallStatus = "FAIL";
    await persistEvidence();
    throw error;
  }
  try {
    const result = await operation(durationMs);
    record.status = "COMPLETED";
    record.completedAt = new Date().toISOString();
    record.elapsedMs = Date.now() - phaseStartedAt;
    if (result !== undefined) record.observation = result;
    activePhase = null;
    await persistEvidence();
    return result;
  } catch (error) {
    record.status = "FAILED";
    record.completedAt = new Date().toISOString();
    record.elapsedMs = Date.now() - phaseStartedAt;
    record.errorType = error?.name ?? "Error";
    record.error = redact(error?.message ?? error);
    if (lastActionResultMetadata) record.actionResult = lastActionResultMetadata;
    evidence.outcome = "FAIL";
    evidence.overallStatus = "FAIL";
    evidence.errorPhase = name;
    activePhase = null;
    await persistEvidence();
    throw error;
  }
}

function delay(ms) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

async function ensurePortFree() {
  await new Promise((resolveListen, reject) => {
    const socket = net.createServer();
    socket.once("error", reject);
    socket.listen(port, "127.0.0.1", () => socket.close(resolveListen));
  });
}

async function waitHealthy(timeoutMs) {
  const deadline = Date.now() + Math.min(25_000, timeoutMs);
  while (Date.now() < deadline) {
    if (server.child.exitCode !== null || server.child.signalCode !== null) {
      throw new Error(`EVE process exited before health became ready: ${redact(server.output)}`);
    }
    try {
      const remaining = deadline - Date.now();
      const response = await fetch(`${host}/eve/v1/health`, { signal: AbortSignal.timeout(Math.min(1500, remaining)) });
      if (response.ok) {
        const json = await response.json();
        if (json.ok === true && json.status === "ready") return { status: response.status, ready: true };
      }
    } catch {}
    await delay(Math.min(200, Math.max(0, deadline - Date.now())));
  }
  throw new Error(`EVE health endpoint did not become ready within ${Math.min(25_000, timeoutMs)}ms: ${redact(server.output)}`);
}

async function stopServer() {
  if (!server || server.child.exitCode !== null || server.child.signalCode !== null) return true;
  server.child.kill("SIGTERM");
  await Promise.race([server.exit, delay(6000)]);
  if (server.child.exitCode === null && server.child.signalCode === null) {
    server.child.kill("SIGKILL");
    await Promise.race([server.exit, delay(2000)]);
  }
  return server.child.exitCode !== null || server.child.signalCode !== null;
}

function docker(args) {
  const timeout = dockerDeadline === undefined ? 4000 : Math.min(4000, Math.max(1, dockerDeadline - Date.now()));
  const result = spawnSync("docker", args, { encoding: "utf8", timeout, env: dockerEnv });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`docker ${args.slice(0, 3).join(" ")} failed: ${redact(result.stderr || result.stdout)}`);
  return result.stdout.trim();
}

async function removeOwnedContainer() {
  if (!sessionId) return;
  const filter = `label=eve.sandbox.tag.agent=${sessionId}`;
  const ids = docker(["ps", "-aq", "--filter", filter]).split(/\s+/u).filter(Boolean);
  for (const id of ids) {
    const details = JSON.parse(docker(["inspect", id]))[0];
    const labels = details.Config?.Labels ?? {};
    assert.equal(labels["eve.sandbox.tag.agent"], sessionId, "refuse cleanup for an unowned container");
    assert.equal(labels["eve.sandbox.role"], "session", "refuse cleanup for a non-session container");
    docker(["rm", "-f", id]);
    containerRemoved = true;
  }
  assert.equal(docker(["ps", "-aq", "--filter", filter]), "", "prototype-owned session container remains after cleanup");
}

async function verifyAuth(coordinator, signal) {
  const health = await fetch(`${host}/eve/v1/health`, { signal });
  assert.equal(health.status, 200);
  const info = await coordinator.makeClient(host, auth).info({ signal });
  assert.ok(info.agent);
  async function requestStatus(client) {
    try {
      const created = await client.sessions.create({ signal });
      const createdId = created.session.state.sessionId;
      await coordinator.makeClient(host, auth).sessions.attach(createdId).reset({ reason: "Smoke auth negative-control cleanup", signal });
      throw new Error("Unauthorized session creation unexpectedly succeeded");
    } catch (error) {
      if (error?.status === 401) return 401;
      throw error;
    }
  }
  const noAuth = await requestStatus(coordinator.makeClient(host));
  const wrongAuth = await requestStatus(coordinator.makeClient(host, { username: auth.username, password: `${auth.password}wrong` }));
  assert.equal(noAuth, 401);
  assert.equal(wrongAuth, 401);
  return { health: 200, authenticatedInfo: true, noCredentialStatus: noAuth, wrongCredentialStatus: wrongAuth };
}

async function waitForSettled(session, timeoutMs) {
  const end = Date.now() + Math.min(45_000, timeoutMs);
  let snapshot;
  while (Date.now() < end) {
    const remaining = end - Date.now();
    snapshot = await session.snapshot({ signal: AbortSignal.timeout(Math.min(5000, remaining)) });
    const events = snapshot.events;
    const terminal = events.findLastIndex((event) => ["turn.completed", "turn.failed", "turn.cancelled", "session.completed", "session.failed"].includes(event.type));
    const started = events.findLastIndex((event) => event.type === "turn.started");
    if (terminal >= 0 && terminal > started) return snapshot;
    await delay(Math.min(300, Math.max(0, end - Date.now())));
  }
  throw new Error(`EVE execution did not settle within ${Math.min(45_000, timeoutMs)}ms; observed event types: ${(snapshot?.events ?? []).slice(-20).map((event) => event.type).join(",")}`);
}

function eventShape(events) {
  const action = events.filter((event) => event.type === "action.result");
  const result = action.at(-1)?.data?.result;
  return {
    eventTypes: [...new Set(events.map((event) => event.type))],
    actionResultCount: action.length,
    actionResultDataKeys: action.at(-1)?.data && typeof action.at(-1).data === "object" ? Object.keys(action.at(-1).data).sort() : [],
    resultKind: typeof result?.kind === "string" ? result.kind : null,
    resultKeys: result && typeof result === "object" ? Object.keys(result).sort() : [],
    resultToolName: typeof result?.toolName === "string" ? result.toolName : null,
    resultCallIdPresent: typeof result?.callId === "string",
    resultErrorName: typeof result?.errorName === "string" ? result.errorName : null,
    resultIsError: typeof result?.isError === "boolean" ? result.isError : null,
    actionStatus: typeof action.at(-1)?.data?.status === "string" ? action.at(-1).data.status : null,
    actionErrorCode: typeof action.at(-1)?.data?.error?.code === "string" ? action.at(-1).data.error.code : null,
    actionErrorName: typeof action.at(-1)?.data?.error?.name === "string" ? action.at(-1).data.error.name : null,
    actionErrorMessage: typeof action.at(-1)?.data?.error?.message === "string" ? redact(action.at(-1).data.error.message) : null,
    outputType: typeof result?.output,
    outputKeys: result?.output && typeof result.output === "object" ? Object.keys(result.output).sort() : [],
    terminalEvent: events.findLast((event) => ["turn.completed", "turn.failed", "turn.cancelled", "session.completed", "session.failed"].includes(event.type))?.type ?? null,
    failedEventTypes: events.filter((event) => event.type.endsWith(".failed")).map((event) => event.type),
  };
}

function observedFailure(events) {
  const failed = events.findLast((event) => ["turn.failed", "session.failed"].includes(event.type));
  if (!failed) return null;
  const data = failed.data ?? {};
  const observed = [data.code, data.message].filter((value) => typeof value === "string").join(": ");
  const detailKeys = data.details && typeof data.details === "object" ? Object.keys(data.details).sort() : [];
  return new Error(`EVE reported ${failed.type}${observed ? `: ${redact(observed)}` : `; event data keys: ${Object.keys(data).sort().join(",")}`}${detailKeys.length ? `; detail keys: ${detailKeys.join(",")}` : ""}`);
}

async function main() {
  await phase("preflight", 5000, async () => {
    await ensurePortFree();
    evidence.workflowStateIsolation = await archivePrototypeWorkflowState(executionId);
    for (const suffix of ["", "-wal", "-shm"]) await rm(`${dbPath}${suffix}`, { force: true });
    dockerEnv = await cleanRuntimeEnvironment();
    return { port: "127.0.0.1", portAvailable: true, dockerEndpointResolved: Boolean(dockerEnv.DOCKER_HOST) };
  });
  await phase("build", 120_000, async (timeoutMs) => {
    const childTimeoutMs = Math.min(115_000, timeoutMs);
    await buildEve({ output: false, timeoutMs: childTimeoutMs });
    return { childTimeoutMs, phaseLimitMs: 120_000 };
  });
  await phase("http-startup", 25_000, async (timeoutMs) => {
    server = await startEve({ port, output: false, auth });
    return await waitHealthy(timeoutMs);
  });
  const coordinator = await import("./coordinator.mjs");
  const store = await import("./prototype-store.mjs");
  const authEvidence = await phase("authentication", 15_000, async (timeoutMs) =>
    await verifyAuth(coordinator, AbortSignal.timeout(timeoutMs)));
  evidence.authentication = authEvidence;
  await persistEvidence();
  const accepted = await phase("authenticated-submit", 15_000, async (timeoutMs) =>
    await coordinator.submitExecution({
      executionId,
      mode: "replay",
      message: coordinator.makeProbeMessage(executionId, "replay", "write and return one disposable Docker workspace artifact"),
      host,
      auth,
      signal: AbortSignal.timeout(timeoutMs),
    }));
  sessionId = accepted.sessionId;
  assert.ok(sessionId, `EVE session was not accepted: ${accepted.status}`);
  evidence.execution = { executionId, sessionId, mode: "replay", operationId };
  await persistEvidence();
  const session = coordinator.makeClient(host, auth).sessions.attach(sessionId);
  let snapshot;
  await phase("session-settlement", 45_000, async (timeoutMs) => {
    snapshot = await waitForSettled(session, timeoutMs);
    // Journal shape/metadata only, not conversation bodies or tool arguments.
    return eventShape(snapshot.events);
  });
  const events = snapshot.events;
  const shapeDiagnostic = eventShape(events);
  evidence.execution.actionResultShape = shapeDiagnostic;
  lastActionResultMetadata = shapeDiagnostic;
  await persistEvidence();
  const verification = await phase("tool-result-and-sqlite-artifact", 20_000, async (timeoutMs) => {
    const failedEvent = observedFailure(events);
    if (failedEvent) throw failedEvent;
    const actionEvents = events.filter((event) => event.type === "action.result");
    assert.equal(actionEvents.length, 1, `expected one tool result event; observed ${actionEvents.length}; ${JSON.stringify(shapeDiagnostic)}`);
    const toolResult = actionEvents[0].data?.result;
    const actionStatus = actionEvents[0].data?.status;
    lastActionResultMetadata = {
      ...shapeDiagnostic,
      observedError: actionEvents[0].data?.error?.message
        ? redact(actionEvents[0].data.error.message)
        : toolResult?.isError === true ? redact(toolResult.errorName ?? toolResult.output?.error ?? "EVE marked the action result as an error") : null,
    };
    assert.equal(actionStatus, "completed", `EVE action result status was ${actionStatus ?? "missing"}${actionEvents[0].data?.error?.message ? `: ${redact(actionEvents[0].data.error.message)}` : ""}`);
    assert.ok(toolResult && typeof toolResult === "object", "EVE action.result did not contain its documented result object");
    assert.equal(toolResult.kind, "tool-result", "EVE action.result kind was not tool-result");
    assert.notEqual(toolResult.isError, true, `EVE action result failed${toolResult.errorName ? ` (${redact(toolResult.errorName)})` : ""}`);
    assert.equal(toolResult.toolName, "replay_operation", "unexpected EVE tool result name");
    const output = toolResult.output;
    assert.ok(output && typeof output === "object" && !Array.isArray(output), "EVE tool output is not a structured object");
    assert.equal(output.executionId, executionId);
    assert.equal(output.operationId, operationId);
    assert.equal(output.operationStatus, "completed");
    assert.equal(output.effectPath, `probe/${Buffer.from(operationId).toString("hex").slice(0, 40)}.txt`);
    assert.equal(output.observedFileContents, `prototype-operation=${operationId}\nThis replaces one disposable file; it does not append.\n`);
    assert.equal(output.effectCount, 1);

    const operation = store.getOperation(operationId);
    assert.ok(operation, "matching stable operation was not persisted");
    assert.equal(operation.execution_id, executionId);
    assert.equal(operation.operation_id, operationId);
    assert.equal(operation.status, "completed");
    assert.equal(JSON.parse(operation.result_json).observedFileContents, output.observedFileContents);
    const artifact = await coordinator.persistSafeArtifact({ executionId, sessionId, toolResult: output, message: "Smoke operation result artifact" });
    assert.equal(artifact.status, "persisted");
    const persisted = await coordinator.readExecutionStatus(executionId, { host, auth, signal: AbortSignal.timeout(timeoutMs) });
    assert.equal(persisted.status, "completed");
    assert.equal(persisted.session_id, sessionId);
    assert.equal(persisted.sessionState, "completed");
    assert.equal(persisted.result.operationId, operationId);
    assert.equal(persisted.result.observedFileContents, output.observedFileContents);
    const artifactBody = JSON.parse(await readFile(artifact.path, "utf8"));
    assert.equal(artifactBody.executionId, executionId);
    assert.equal(artifactBody.sessionId, sessionId);
    assert.equal(artifactBody.result.observedFileContents, output.observedFileContents);

    return {
      operationStatus: operation.status,
      operationInvocationCount: operation.invocation_count,
      effectCount: operation.effect_count,
      observedFileContentsVerified: true,
      actionResultShape: shapeDiagnostic,
      artifactPersisted: true,
      sqliteStatusAfterReopen: persisted.status,
      sqliteSessionMatched: persisted.session_id === sessionId,
    };
  });

  return {
    outcome: "PASS",
    authentication: authEvidence,
    execution: { executionId, sessionId, mode: "replay", operationId, ...verification },
  };
}

let outcome = "FAIL";
let details = {};
try {
  details = await main();
  outcome = "PASS";
  evidence.outcome = outcome;
  evidence.overallStatus = outcome;
  evidence.result = details;
} catch (error) {
  outcome = "FAIL";
  evidence.outcome = outcome;
  evidence.overallStatus = outcome;
  if (!evidence.errorPhase) evidence.errorPhase = activePhase ?? evidence.phases.findLast((item) => item.status === "RUNNING")?.name ?? "orchestration";
  evidence.error = { type: error?.name ?? "Error", message: redact(error?.message ?? error) };
} finally {
  const previousPhase = activePhase;
  activePhase = "cleanup";
  const cleanupRecord = { name: "cleanup", status: "RUNNING", startedAt: new Date().toISOString(), elapsedMs: 0 };
  evidence.phases.push(cleanupRecord);
  await persistEvidence();
  const cleanupStarted = Date.now();
  try {
    const cleanupBudget = Math.min(CLEANUP_RESERVE_MS, GLOBAL_BUDGET_MS - (Date.now() - startedAt));
    if (cleanupBudget <= 0) throw new Error("Smoke global deadline left no cleanup budget.");
    dockerDeadline = Date.now() + cleanupBudget;
    const stopped = await stopServer();
    if (!stopped) throw new Error("EVE child process did not stop within the cleanup window.");
    await removeOwnedContainer();
    cleanupRecord.status = "COMPLETED";
    cleanupRecord.observation = {
      eveProcessStopped: true,
      ownedSessionContainerRemoved: containerRemoved,
      noTemplateImagesRemoved: true,
      noSharedRunnerImagesRemoved: true,
    };
  } catch (error) {
    cleanupRecord.status = "FAILED";
    cleanupRecord.errorType = error?.name ?? "Error";
    cleanupRecord.error = redact(error?.message ?? error);
    outcome = "FAIL";
    evidence.outcome = outcome;
    evidence.overallStatus = outcome;
    evidence.cleanupError = cleanupRecord.error;
  } finally {
    cleanupRecord.completedAt = new Date().toISOString();
    cleanupRecord.elapsedMs = Date.now() - cleanupStarted;
    evidence.cleanup = cleanupRecord.observation ?? {
      eveProcessStopped: !server || server.child.exitCode !== null || server.child.signalCode !== null,
      ownedSessionContainerRemoved: containerRemoved,
      noTemplateImagesRemoved: true,
      noSharedRunnerImagesRemoved: true,
    };
    evidence.elapsedMs = Date.now() - startedAt;
    evidence.activePhase = null;
    activePhase = null;
    if (previousPhase && outcome !== "PASS" && !evidence.errorPhase) evidence.errorPhase = previousPhase;
    await persistEvidence();
    console.log(`Smoke ${outcome}; phase ${evidence.errorPhase ?? "all scoped phases passed"}; evidence: ${evidencePath}; elapsed ${evidence.elapsedMs}ms.`);
  }
}
if (outcome !== "PASS") process.exitCode = 1;
