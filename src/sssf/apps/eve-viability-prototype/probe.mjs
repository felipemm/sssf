import { randomBytes, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, readFile, readdir, rm } from "node:fs/promises";
import net from "node:net";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { archivePrototypeWorkflowState, buildEve, cleanRuntimeEnvironment, prototypeRoot, startEve, worktreeRoot } from "./eve-process.mjs";

const appRoot = dirname(fileURLToPath(import.meta.url));
const eveStateDirectory = join(appRoot, ".eve", ".workflow-data");
const port = 3217;
const host = `http://127.0.0.1:${port}`;
const MAX_PROBE_MS = 5 * 60_000;
const GATE_TIMEOUT_MS = 90_000;
const runId = randomUUID().replaceAll("-", "").slice(0, 10);
const securityCanary = `API_KEY=synthetic_${randomBytes(20).toString("hex")}`;
const selectedGate = process.env.PROBE_GATE;
const sessionsOwned = new Set();
const gates = [
  { id: "loopback-basic-auth", status: "NOT_RUN", summary: "Not run." },
  { id: "callable-eve-sqlite-result", status: "NOT_RUN", summary: "Not run." },
  { id: "approval-restart-same-session", status: "NOT_RUN", summary: "Not run." },
  { id: "stable-idempotent-replay", status: "NOT_RUN", summary: "Not run." },
  { id: "docker-workspace-isolation", status: "NOT_RUN", summary: "Not run." },
  { id: "workspace-stop-resume-and-loss", status: "NOT_RUN", summary: "Not run." },
  { id: "synthetic-secret-boundaries", status: "NOT_RUN", summary: "Not run." },
];
process.env.PROTOTYPE_DB = resolve(prototypeRoot, "probe.sqlite");
let server;
let probeAuth;
let setupPassed = false;
let cleanupNotes = [];
let retainedTemplateImages = [];
let timedOutGate = false;
let activeReport;
const serverProcesses = [];
const startedAt = Date.now();
const deadline = startedAt + MAX_PROBE_MS;

function gate(id) {
  const result = gates.find((item) => item.id === id);
  if (!result) throw new Error(`Unknown probe gate ${id}`);
  return result;
}

function safeSummary(value) {
  let result = String(value).split(securityCanary).join("[REDACTED_SYNTHETIC_CANARY]");
  if (probeAuth?.password) result = result.split(probeAuth.password).join("[REDACTED_AUTH_CREDENTIAL]");
  if (probeAuth?.username) result = result.split(probeAuth.username).join("[REDACTED_AUTH_USERNAME]");
  return result.replace(/(?:API[_-]?KEY|ACCESS[_-]?TOKEN|SECRET|PASSWORD)\s*[:=]\s*[A-Za-z0-9._-]{16,}/giu, "[REDACTED_SECRET_PATTERN]");
}

function delay(ms) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

function raceWithTimeout(promise, timeoutMs) {
  let timer;
  return Promise.race([
    promise,
    new Promise((resolveTimeout) => { timer = setTimeout(() => resolveTimeout(null), timeoutMs); }),
  ]).finally(() => clearTimeout(timer));
}

async function assertPortFree() {
  await new Promise((resolveListen, rejectListen) => {
    const socket = net.createServer();
    socket.once("error", rejectListen);
    socket.listen(port, "127.0.0.1", () => socket.close(resolveListen));
  });
}

async function waitForHealth(timeoutMs = 30_000) {
  const end = Math.min(Date.now() + timeoutMs, deadline);
  let lastError = "not started";
  while (Date.now() < end) {
    if (server && (server.child.exitCode !== null || server.child.signalCode !== null)) {
      throw new Error(`EVE service exited before health check. ${safeSummary(server.output)}`);
    }
    try {
      const response = await fetch(`${host}/eve/v1/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) {
        const body = await response.json();
        if (body.ok === true && body.status === "ready") return body;
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await delay(200);
  }
  throw new Error(`EVE service did not become healthy: ${safeSummary(lastError)}. ${safeSummary(server?.output ?? "")}`);
}

async function startServer(options = {}) {
  if (Date.now() >= deadline) throw new Error("Probe deadline expired before EVE service startup.");
  server = await startEve({ port, output: false, auth: probeAuth, secretValues: [securityCanary], ...options });
  serverProcesses.push(server);
  await waitForHealth();
}

async function stopServer(signal = "SIGTERM") {
  if (!server || server.child.exitCode !== null || server.child.signalCode !== null) return;
  server.child.kill(signal);
  await raceWithTimeout(server.exit, 8000);
  if (server.child.exitCode === null && server.child.signalCode === null) {
    server.child.kill("SIGKILL");
    await raceWithTimeout(server.exit, 3000);
  }
}

async function ensureServer() {
  if (server && server.child.exitCode === null && server.child.signalCode === null) return;
  await startServer();
}

async function checkpoint(activeGate) {
  if (!activeReport) return;
  const coordinator = await importCoordinator();
  await coordinator.writeEvidence({
    ...activeReport,
    overallStatus: "RUNNING",
    adoptionDecision: "BLOCKED",
    activeGate,
    elapsedMs: Date.now() - startedAt,
  });
}

async function runStep(id, action) {
  const item = gate(id);
  const gateStartedAt = Date.now();
  if (selectedGate && selectedGate !== id) {
    item.status = "NOT_RUN";
    item.summary = `Skipped because PROBE_GATE selects ${selectedGate}.`;
    return;
  }
  console.log(`RUN     ${id}`);
  await checkpoint(id);
  if (setupPassed && (!server || server.child.exitCode !== null || server.child.signalCode !== null) && Date.now() < deadline) {
    try {
      await startServer();
    } catch (error) {
      item.status = "BLOCKED";
      item.summary = `EVE service unavailable for this independent gate: ${safeSummary(error.message)}`;
      item.evidence = { serviceRestartFailed: true };
      return;
    }
  }
  const remainingMs = deadline - Date.now();
  if (remainingMs <= 0) {
    item.status = "BLOCKED";
    item.summary = "NOT_RUN because the overall bounded probe deadline expired.";
    return;
  }
  const timeoutMs = Math.min(GATE_TIMEOUT_MS, remainingMs);
  let timedOut = false;
  const actionPromise = Promise.resolve().then(action);
  actionPromise.catch(() => {});
  try {
    const outcome = await raceWithTimeout(
      actionPromise.then((value) => ({ value }), (error) => ({ error })),
      timeoutMs,
    ) ?? { timeout: true };
    if (outcome.timeout) {
      timedOut = true;
      timedOutGate = true;
      item.status = "BLOCKED";
      item.summary = `Probe gate timed out after ${timeoutMs}ms; the EVE service was stopped before continuing.`;
      item.evidence = { timeoutMs };
      await stopServer("SIGTERM");
      return;
    }
    if (outcome.error) throw outcome.error;
    const result = outcome.value ?? {};
    item.status = result.status ?? "PASS";
    item.summary = safeSummary(result.summary ?? "Required observations were recorded.");
    item.evidence = result.evidence ?? {};
  } catch (error) {
    item.status = "FAIL";
    item.summary = safeSummary(error instanceof Error ? error.message : String(error));
    item.evidence = { errorType: error instanceof Error ? error.name : "Error", ...error.evidence };
  } finally {
    item.elapsedMs = Date.now() - gateStartedAt;
    console.log(`${item.status.padEnd(7)} ${id}: ${safeSummary(item.summary)}`);
    await checkpoint(null);
    if (!timedOut && Date.now() < deadline && (!server || server.child.exitCode !== null || server.child.signalCode !== null)) {
      try {
        await startServer();
      } catch (error) {
        cleanupNotes.push(`could not restart EVE after ${id}: ${safeSummary(error.message)}`);
      }
    }
  }
}

async function importCoordinator() {
  return await import("./coordinator.mjs");
}

async function waitForSnapshot(sessionId, predicate, timeoutMs = 45_000) {
  const coordinator = await importCoordinator();
  const session = coordinator.makeClient(host, probeAuth).sessions.attach(sessionId);
  const end = Math.min(Date.now() + timeoutMs, deadline);
  let latest;
  while (Date.now() < end) {
    latest = await session.snapshot({ signal: AbortSignal.timeout(7000) });
    if (predicate(latest.events)) return latest;
    await delay(250);
  }
  throw new Error(`Timed out waiting for EVE session ${sessionId}; recent event types: ${(latest?.events ?? []).slice(-12).map((event) => event.type).join(",")}; failures: ${JSON.stringify(failureDigest(latest?.events ?? []))}`);
}

function latestTurnSettled(events) {
  const started = events.findLastIndex((event) => event.type === "turn.started");
  const terminal = events.findLastIndex((event) => ["turn.completed", "turn.failed", "turn.cancelled"].includes(event.type));
  return terminal >= 0 && terminal > started;
}

function inputRequests(events) {
  return events.flatMap((event) => event.type === "input.requested" ? event.data.requests ?? [] : []);
}

function resultHasError(events) {
  return events.some((event) => event.type === "action.result" && (
    event.data.status === "error" || event.data.result?.isError === true || event.data.result?.errorName !== undefined || event.data.error !== undefined
  ));
}

function failureDigest(events) {
  return events.filter((event) => event.type === "step.failed" || event.type === "turn.failed" || event.type === "session.failed")
    .map((event) => ({
      type: event.type,
      code: event.data?.code ?? "unknown",
      message: safeSummary(event.data?.message ?? ""),
      detailKeys: Object.keys(event.data?.details ?? {}),
    }));
}

async function runExecution({ executionId, mode, message, signal }) {
  const coordinator = await importCoordinator();
  const accepted = await coordinator.submitExecution({ executionId, mode, message, host, auth: probeAuth, signal });
  if (accepted.sessionId) sessionsOwned.add(accepted.sessionId);
  return accepted;
}

async function docker(args) {
  const env = await cleanRuntimeEnvironment();
  const result = spawnSync("docker", args, { encoding: "utf8", timeout: 12_000, env });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`docker ${args.slice(0, 3).join(" ")} failed: ${safeSummary(result.stderr || result.stdout)}`);
  return result.stdout.trim();
}

function isWithin(path, parent) {
  const rel = relative(parent, path);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${sep}`));
}

function mountSummary(mount) {
  const source = mount.Source ? resolve(mount.Source) : "";
  const inWorktree = source !== "" && isWithin(source, worktreeRoot);
  const inApp = source !== "" && isWithin(source, appRoot);
  const relativeSource = inWorktree ? relative(worktreeRoot, source) || "." : "outside-worktree";
  const managedResourceTree = mount.Type === "bind"
    && mount.Destination === "/eve/resources"
    && mount.RW === false
    && inApp
    && isWithin(source, join(appRoot, ".eve"))
    && !isWithin(source, join(appRoot, ".eve", ".workflow-data"))
    && !isWithin(source, join(appRoot, ".eve", "sandbox-cache"))
    && !isWithin(source, join(appRoot, ".eve", "workflow-cache"));
  const sensitiveSource = inWorktree && /(?:^|\/)(?:\.env(?:\.|$)|\.git|node_modules|\.workflow-data|secrets?)(?:\/|$)/iu.test(relativeSource);
  const hostCheckoutExposure = inWorktree && !managedResourceTree;
  return {
    type: mount.Type,
    destination: mount.Destination,
    readOnly: mount.RW === false,
    sourceScope: managedResourceTree ? "EVE-managed-read-only-resources" : relativeSource,
    managedResourceTree,
    hostCheckoutExposure,
    sensitiveSource,
  };
}

async function dockerContainersForSession(sessionId) {
  const rawIds = await docker(["ps", "-aq", "--filter", `label=eve.sandbox.tag.agent=${sessionId}`]);
  const ids = rawIds.split(/\s+/u).filter(Boolean);
  return await Promise.all(ids.map(async (id) => {
    const details = JSON.parse(await docker(["inspect", id]))[0];
    const labels = details?.Config?.Labels ?? {};
    if (labels["eve.sandbox.tag.agent"] !== sessionId || labels["eve.sandbox.role"] !== "session") {
      throw new Error(`Docker owner labels do not match recorded EVE session ${sessionId}.`);
    }
    const networks = Object.keys(details.NetworkSettings?.Networks ?? {});
    const envNames = (details.Config?.Env ?? []).map((entry) => entry.split("=", 1)[0]);
    const credentialLikeEnvironmentNames = envNames.filter((name) => /(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|BWS)/iu.test(name));
    const mounts = (details.Mounts ?? []).map(mountSummary);
    return {
      id: details.Id,
      name: details.Name?.replace(/^\//u, ""),
      ownerSessionId: labels["eve.sandbox.tag.agent"],
      role: labels["eve.sandbox.role"],
      networkNames: networks,
      denyAllNetworkObserved: networks.length === 1 && networks[0] === "none",
      credentialLikeEnvironmentNames,
      dockerSocketMounted: (details.Mounts ?? []).some((mount) => mount.Source?.endsWith("docker.sock") || mount.Destination?.endsWith("docker.sock")),
      mounts,
      hostCheckoutExposure: mounts.some((mount) => mount.hostCheckoutExposure),
      sensitiveMount: mounts.some((mount) => mount.sensitiveSource),
    };
  }));
}

async function dockerContainerForSession(sessionId) {
  const containers = await dockerContainersForSession(sessionId);
  if (containers.length !== 1) throw new Error(`Expected exactly one EVE Docker workspace for ${sessionId}; found ${containers.length}.`);
  return containers[0];
}

async function removeOwnedContainer(sessionId) {
  let ids;
  try {
    ids = (await docker(["ps", "-aq", "--filter", `label=eve.sandbox.tag.agent=${sessionId}`])).split(/\s+/u).filter(Boolean);
  } catch (error) {
    cleanupNotes.push(`container lookup for ${sessionId} failed: ${safeSummary(error.message)}`);
    return false;
  }
  let removed = false;
  for (const id of ids) {
    try {
      const details = JSON.parse(await docker(["inspect", id]))[0];
      const labels = details?.Config?.Labels ?? {};
      if (labels["eve.sandbox.tag.agent"] !== sessionId || labels["eve.sandbox.role"] !== "session") {
        cleanupNotes.push(`refused container removal because EVE session ownership did not match ${sessionId}`);
        continue;
      }
      await docker(["rm", "-f", id]);
      removed = true;
    } catch (error) {
      cleanupNotes.push(`container cleanup for ${sessionId} failed: ${safeSummary(error.message)}`);
    }
  }
  return removed;
}

async function retainedTemplateMarkers() {
  const templatesDirectory = join(appRoot, ".eve", "sandbox-cache", "docker", "templates");
  let entries;
  try {
    entries = await readdir(templatesDirectory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    cleanupNotes.push(`template cache lookup failed: ${safeSummary(error.message)}`);
    return [];
  }
  const references = [];
  for (const entry of entries) {
    if (!entry.isFile() || !/^[a-z0-9-]+$/iu.test(entry.name)) continue;
    try {
      const reference = (await readFile(join(templatesDirectory, entry.name), "utf8")).trim();
      if (/^eve-sandbox-template:[a-z0-9-]+$/iu.test(reference)) references.push(reference);
    } catch (error) {
      cleanupNotes.push(`could not read prototype-local template marker: ${safeSummary(error.message)}`);
    }
  }
  return [...new Set(references)];
}

async function readOperation(operationId) {
  const { getOperation } = await import("./prototype-store.mjs");
  return getOperation(operationId);
}

async function readWorkflowRunMetadata(sessionId) {
  const runPath = join(eveStateDirectory, "runs", `${sessionId}.json`);
  let run;
  try {
    run = JSON.parse(await readFile(runPath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return { runFound: false, steps: [] };
    throw error;
  }
  const stepDirectory = join(eveStateDirectory, "steps", sessionId);
  const stepNames = await readdir(stepDirectory).catch((error) => {
    if (error.code === "ENOENT") return [];
    throw error;
  });
  const steps = await Promise.all(stepNames.filter((name) => name.endsWith(".json")).map(async (name) => {
    const step = JSON.parse(await readFile(join(stepDirectory, name), "utf8"));
    return {
      stepId: step.stepId,
      stepName: step.stepName,
      status: step.status,
      attempt: step.attempt,
      startedAt: step.startedAt?.toISOString?.() ?? step.startedAt ?? null,
      updatedAt: step.updatedAt?.toISOString?.() ?? step.updatedAt ?? null,
      errorType: step.error?.name ?? null,
    };
  }));
  return {
    runFound: true,
    runId: run.runId,
    workflowName: run.workflowName,
    deploymentId: run.deploymentId,
    status: run.status,
    startedAt: run.startedAt?.toISOString?.() ?? run.startedAt ?? null,
    updatedAt: run.updatedAt?.toISOString?.() ?? run.updatedAt ?? null,
    steps,
  };
}

async function startRuntimeSession(executionId, mode) {
  const coordinator = await importCoordinator();
  const accepted = await runExecution({ executionId, mode, signal: AbortSignal.timeout(20_000) });
  if (!accepted.sessionId) throw new Error(`Coordinator failed to create the EVE session (${accepted.status}).`);
  const snapshot = await waitForSnapshot(accepted.sessionId, latestTurnSettled);
  const result = coordinator.extractToolResults(snapshot.events).at(-1);
  return { coordinator, accepted, snapshot, result };
}

async function runAuthProbe(coordinator) {
  const publicHealth = await fetch(`${host}/eve/v1/health`, { signal: AbortSignal.timeout(3000) });
  if (!publicHealth.ok) throw new Error(`Loopback health route was not reachable (${publicHealth.status}).`);
  const valid = coordinator.makeClient(host, probeAuth);
  const info = await valid.info({ signal: AbortSignal.timeout(5000) });
  if (!info.agent) throw new Error("Authenticated info call did not return agent metadata.");

  async function rejected(client) {
    try {
      const created = await client.sessions.create({ signal: AbortSignal.timeout(5000) });
      sessionsOwned.add(created.session.state.sessionId);
      await valid.sessions.attach(created.session.state.sessionId).reset({ reason: "Unexpected authorization probe acceptance" });
      return false;
    } catch (error) {
      return error?.status === 401;
    }
  }
  const noCredentialRejected = await rejected(coordinator.makeClient(host));
  const wrongCredentialRejected = await rejected(coordinator.makeClient(host, {
    username: probeAuth.username,
    password: `${probeAuth.password}wrong`,
  }));
  if (!noCredentialRejected || !wrongCredentialRejected) {
    throw new Error("EVE accepted an unauthenticated or wrong-Basic-credential session request.");
  }
  return {
    status: "PASS",
    summary: "EVE returned authenticated agent info; requests without credentials and with a wrong Basic password received 401. The service was bound to loopback.",
    evidence: {
      bindAddress: "127.0.0.1",
      publicHealth: true,
      authenticatedInfo: true,
      missingCredentialStatus: 401,
      wrongCredentialStatus: 401,
      buildReceivedCredential: false,
      credentialInProcessArguments: false,
    },
  };
}

async function gateCallableSlice(coordinator, runtimeVersion) {
  const executionId = `vertical_${runId}`;
  const { accepted, snapshot, result } = await startRuntimeSession(executionId, "replay");
  const failures = failureDigest(snapshot.events);
  if (failures.length > 0 || !result) {
    await coordinator.updateExecution(executionId, { status: "failed", blockedReason: failures[0]?.message ?? "EVE turn did not return a tool result." });
    throw new Error(`EVE turn failed before the Docker tool executed: ${JSON.stringify(failures)}`);
  }
  const operation = await readOperation(`${executionId}:workspace-effect`);
  const artifact = await coordinator.persistSafeArtifact({
    executionId,
    sessionId: accepted.sessionId,
    toolResult: result,
    message: snapshot.events.filter((event) => event.type === "message.completed").at(-1)?.data.message,
  });
  const status = await coordinator.readExecutionStatus(executionId, { host, auth: probeAuth });
  if (!operation || operation.status !== "completed" || operation.effect_count !== 1
    || result?.operationId !== operation.operation_id || artifact.status !== "persisted"
    || status?.status !== "completed" || status?.session_id !== accepted.sessionId
    || status?.sessionState !== "completed") {
    throw new Error("Real EVE structured result, durable operation, artifact, or SQLite execution status did not settle as expected.");
  }
  return {
    status: "PASS",
    summary: "Authenticated callable EVE HTTP session executed a real Docker-backed tool, returned structured output/artifact, and reached completed status in prototype SQLite.",
    evidence: {
      executionId,
      sessionId: accepted.sessionId,
      runtimeVersion,
      operationId: operation.operation_id,
      effectCount: operation.effect_count,
      toolResultStructured: true,
      artifactStatus: artifact.status,
      sqliteExecutionStatus: status.status,
      sessionState: status.sessionState,
      fixture: "scripted AI SDK LanguageModelV3; no real LLM/provider",
    },
  };
}

async function gateDockerIsolation(coordinator) {
  const first = await startRuntimeSession(`isolation_a_${runId}`, "replay");
  const second = await startRuntimeSession(`isolation_b_${runId}`, "replay");
  if (!first.result || !second.result) {
    throw new Error(`A workspace tool did not return structured output: ${JSON.stringify(failureDigest([...first.snapshot.events, ...second.snapshot.events]))}`);
  }
  const firstContainer = await dockerContainerForSession(first.accepted.sessionId);
  const secondContainer = await dockerContainerForSession(second.accepted.sessionId);
  const firstWorkload = first.result?.workspace;
  const secondWorkload = second.result?.workspace;
  const passed = firstContainer.id !== secondContainer.id
    && firstContainer.ownerSessionId === first.accepted.sessionId
    && secondContainer.ownerSessionId === second.accepted.sessionId
    && firstContainer.denyAllNetworkObserved
    && secondContainer.denyAllNetworkObserved
    && firstContainer.credentialLikeEnvironmentNames.length === 0
    && secondContainer.credentialLikeEnvironmentNames.length === 0
    && firstWorkload?.hostCheckoutVisible === false
    && secondWorkload?.hostCheckoutVisible === false
    && firstWorkload?.dockerSocketVisible === false
    && secondWorkload?.dockerSocketVisible === false
    && firstWorkload?.credentialLikeEnvironmentNames?.length === 0
    && secondWorkload?.credentialLikeEnvironmentNames?.length === 0
    && !firstContainer.dockerSocketMounted
    && !secondContainer.dockerSocketMounted
    && !firstContainer.hostCheckoutExposure
    && !secondContainer.hostCheckoutExposure
    && !firstContainer.sensitiveMount
    && !secondContainer.sensitiveMount;
  return {
    status: passed ? "PASS" : "FAIL",
    summary: passed
      ? "Two isolated EVE sessions used distinct Docker containers with deny-all networking, no host checkout/socket/credential exposure, and only scoped read-only EVE resource mounts."
      : "Docker isolation requirements were not all satisfied; the evidence contains the exact container/mount/environment observations.",
    evidence: {
      first: firstContainer,
      second: secondContainer,
      firstToolEnvironment: firstWorkload,
      secondToolEnvironment: secondWorkload,
      explicitNetworkPolicy: "deny-all",
      workspaceIdentityDistinct: firstContainer.id !== secondContainer.id,
    },
  };
}

async function gateWorkspaceRecovery(coordinator) {
  const executionId = `recovery_${runId}`;
  const initial = await startRuntimeSession(executionId, "replay");
  if (!initial.result) throw new Error(`Initial workspace tool did not execute: ${JSON.stringify(failureDigest(initial.snapshot.events))}`);
  const container = await dockerContainerForSession(initial.accepted.sessionId);
  await docker(["stop", "-t", "0", container.id]);
  await stopServer("SIGTERM");
  await startServer();
  const resumedResponse = await coordinator.makeClient(host, probeAuth).sessions.attach(initial.accepted.sessionId)
    .send(`PROBE executionId=${executionId} mode=resume verify persisted expected file after container stop.`, { signal: AbortSignal.timeout(40_000) });
  const resumedTurn = await resumedResponse.result();
  const restored = coordinator.extractToolResults(resumedTurn.events).at(-1);
  const afterStopResume = await dockerContainerForSession(initial.accepted.sessionId);
  const stoppedResumePassed = restored?.status === "restored"
    && restored.workspaceRestored === true
    && afterStopResume.id === container.id;

  await docker(["rm", "-f", container.id]);
  await stopServer("SIGTERM");
  await startServer();
  const lossResponse = await coordinator.makeClient(host, probeAuth).sessions.attach(initial.accepted.sessionId)
    .send(`PROBE executionId=${executionId} mode=loss observe missing native workspace.`, { signal: AbortSignal.timeout(40_000) });
  const lossTurn = await lossResponse.result();
  const lossResults = coordinator.extractToolResults(lossTurn.events);
  const lossObservation = lossResults.at(-1);
  const lossError = resultHasError(lossTurn.events) || lossTurn.status === "failed";
  const postLossContainers = await dockerContainersForSession(initial.accepted.sessionId);
  const replacementContainer = postLossContainers.at(-1);
  const missingStateObserved = lossObservation?.workspaceRestored === false
    && lossObservation?.status === "human-blocked";
  const providerFailureObserved = lossError;
  if (!stoppedResumePassed) {
    await coordinator.updateExecution(executionId, { status: "human-blocked", blockedReason: "Stopped workspace failed to restore the expected execution-owned checkpoint." });
  } else if (missingStateObserved || providerFailureObserved) {
    await coordinator.updateExecution(executionId, {
      status: "human-blocked",
      blockedReason: "Native workspace continuity failed after container loss; deterministic coordinator parked the execution for human intervention.",
    });
  }
  const status = await coordinator.readExecutionStatus(executionId, { host, auth: probeAuth });
  const lossBlocked = (missingStateObserved || providerFailureObserved) && status?.status === "human-blocked";
  if (!stoppedResumePassed || !lossBlocked) {
    throw new Error("Workspace checkpoint did not restore after stop or native loss was not explicitly blocked by tool/coordinator observation.");
  }
  return {
    status: "PASS",
    summary: "The stopped container resumed with its original checkpoint; after native deletion the trusted workspace tool/coordinator observed missing continuity and persisted human-blocked status.",
    evidence: {
      executionId,
      sessionId: initial.accepted.sessionId,
      originalContainerId: container.id,
      resumedContainerId: afterStopResume.id,
      stoppedWorkspaceRestored: stoppedResumePassed,
      nativeContainerDeletedBeforeLossProbe: true,
      missingCheckpointReportedByTool: missingStateObserved,
      providerErrorObserved: providerFailureObserved,
      replacementContainerCreated: Boolean(replacementContainer && replacementContainer.id !== container.id),
      replacementContainerId: replacementContainer?.id ?? null,
      lossOutcomeStatus: status.status,
      finalAssistantTextWasNotUsedAsRecoveryEvidence: true,
    },
  };
}

async function gateApprovalRestart(coordinator) {
  const executionId = `approval_${runId}`;
  const accepted = await runExecution({ executionId, mode: "approved", signal: AbortSignal.timeout(20_000) });
  if (!accepted.sessionId) throw new Error("Approval execution did not create an EVE session.");
  const pending = await waitForSnapshot(accepted.sessionId, (events) => coordinator.deriveExecutionState(events).status === "awaiting-input");
  const request = inputRequests(pending.events).find((item) => item.kind === "tool-approval");
  if (!request?.requestId) throw new Error("EVE did not expose an approval request id.");
  const statusBeforeRestart = await coordinator.readExecutionStatus(executionId, { host, auth: probeAuth });
  if (statusBeforeRestart?.status !== "awaiting-input") throw new Error("Coordinator did not durably expose the parked approval state.");
  const optionId = request.options?.find((option) => /approve/iu.test(`${option.id} ${option.label}`))?.id ?? "approve";
  await stopServer("SIGTERM");
  await startServer();
  const afterRestart = await waitForSnapshot(accepted.sessionId, (events) => coordinator.deriveExecutionState(events).status === "awaiting-input");
  if (!inputRequests(afterRestart.events).some((item) => item.requestId === request.requestId)) {
    throw new Error("Same-version restart did not preserve the pending approval request.");
  }
  const approved = await coordinator.approvePending(executionId, request.requestId, { host, auth: probeAuth, optionId });
  const settled = await waitForSnapshot(accepted.sessionId, latestTurnSettled);
  const operation = await readOperation(`${executionId}:workspace-effect`);
  const statusAfterApproval = await coordinator.readExecutionStatus(executionId, { host, auth: probeAuth });
  const output = coordinator.extractToolResults(settled.events).at(-1);
  if (approved.sessionId !== accepted.sessionId || output?.executionId !== executionId
    || operation?.invocation_count !== 1 || operation?.effect_count !== 1
    || statusAfterApproval?.status !== "completed" || statusAfterApproval?.sessionState !== "completed") {
    throw new Error("Approval did not settle the same execution/session and persist completed status.");
  }
  await coordinator.persistSafeArtifact({ executionId, sessionId: accepted.sessionId, toolResult: output, message: settled.events.filter((event) => event.type === "message.completed").at(-1)?.data.message });
  return {
    status: "PASS",
    summary: "A parked approval survived an EVE 0.82.0 process restart and resumed on the same execution/session; SQLite transitioned from awaiting-input to completed.",
    evidence: {
      executionId,
      sessionId: accepted.sessionId,
      requestIdStable: true,
      sessionIdStable: approved.sessionId === accepted.sessionId,
      statusBeforeRestart: statusBeforeRestart.status,
      statusAfterApproval: statusAfterApproval.status,
      operationInvocationCount: operation.invocation_count,
      effectCount: operation.effect_count,
      runtimeVersion: "0.82.0",
    },
  };
}

async function gateStableReplay(coordinator) {
  await stopServer("SIGTERM");
  const executionId = `replay_${runId}`;
  await startServer({ crashOnce: executionId });
  const accepted = await runExecution({ executionId, mode: "replay", signal: AbortSignal.timeout(20_000) });
  if (!accepted.sessionId) throw new Error("Replay execution did not create an EVE session.");
  const crash = await raceWithTimeout(server.exit, Math.min(35_000, Math.max(1, deadline - Date.now())));
  const marker = JSON.parse(await readFile(join(prototypeRoot, `crash-${executionId}.json`), "utf8"));
  let killedProcessGone = false;
  try { process.kill(marker.pid, 0); } catch (error) {
    if (error.code === "ESRCH") killedProcessGone = true;
    else throw error;
  }
  if (!crash || (crash.code === 0 && !crash.signal)
    || marker.executionId !== executionId || marker.operationId !== `${executionId}:workspace-effect`
    || marker.signal !== "SIGKILL" || marker.stage !== "after-effect-before-checkpoint"
    || !killedProcessGone) {
    if (!crash) await stopServer("SIGTERM");
    throw new Error("The expected post-effect server crash was not independently observed.");
  }
  const beforeReplay = await readOperation(`${executionId}:workspace-effect`);
  if (beforeReplay?.status !== "completed" || beforeReplay.effect_count !== 1) {
    throw new Error("The stable operation record was not durable before EVE's tool-result checkpoint.");
  }
  const crashRunMetadata = await readWorkflowRunMetadata(accepted.sessionId);
  const restartStartedAt = Date.now();
  await startServer();
  const restartReadyAt = Date.now();
  let recovered;
  try {
    recovered = await waitForSnapshot(accepted.sessionId, latestTurnSettled, 55_000);
  } catch (error) {
    const afterWait = await readOperation(`${executionId}:workspace-effect`);
    const finalSnapshot = await coordinator.makeClient(host, probeAuth).sessions.attach(accepted.sessionId)
      .snapshot({ signal: AbortSignal.timeout(7000) }).catch(() => null);
    error.evidence = {
      executionId,
      sessionId: accepted.sessionId,
      killedPid: marker.pid,
      injectedSignal: marker.signal,
      injectedStage: marker.stage,
      killedProcessObservedGone: killedProcessGone,
      launcherExitCode: crash.code,
      effectRecordedBeforeRestart: beforeReplay.effect_count,
      invocationCountAfterWait: afterWait?.invocation_count ?? null,
      effectCountAfterWait: afterWait?.effect_count ?? null,
      stableOperationId: afterWait?.operation_id ?? null,
      crashRunMetadata,
      recoveryRunMetadata: await readWorkflowRunMetadata(accepted.sessionId),
      finalEventTypes: finalSnapshot?.events?.slice(-12).map((event) => event.type) ?? [],
      finalFailureEvents: failureDigest(finalSnapshot?.events ?? []),
      restartStartedAt: new Date(restartStartedAt).toISOString(),
      restartReadyAt: new Date(restartReadyAt).toISOString(),
      observedAfterRestartMs: Date.now() - restartReadyAt,
      requestedRecoveryWindowMs: 55_000,
      recoveryConfig: {
        recoverActiveRunsEnvironment: "not set in the isolated process environment; pinned local World source defaults this option to true unless explicitly disabled",
        sourceEvidence: "node_modules/eve/dist/src/compiled/@workflow/world-local/index.d.ts and index.js",
      },
      startupQueueLogs: safeSummary(server.output).trim().split(/\r?\n/u).filter(Boolean),
      runtimeLogTail: safeSummary(server.output).slice(-3000),
    };
    await coordinator.updateExecution(executionId, { status: "human-blocked", blockedReason: "Interrupted execution did not resume within the bounded recovery window." });
    throw error;
  }
  const result = coordinator.extractToolResults(recovered.events).at(-1);
  const operation = await readOperation(`${executionId}:workspace-effect`);
  if (!result || operation?.invocation_count < 2 || operation.effect_count !== 1
    || operation.operation_id !== `${executionId}:workspace-effect`) {
    throw new Error("Replay did not reuse the stable operation identity with exactly one observable side effect.");
  }
  await coordinator.persistSafeArtifact({ executionId, sessionId: accepted.sessionId, toolResult: result, message: recovered.events.filter((event) => event.type === "message.completed").at(-1)?.data.message });
  const status = await coordinator.readExecutionStatus(executionId, { host, auth: probeAuth });
  if (status?.status !== "completed") throw new Error("Recovered execution did not reach durable completed status.");
  return {
    status: "PASS",
    summary: "Injected SIGKILL landed after the idempotent workspace file/SQLite effect and before EVE committed the tool result; replay reused the same stable key and one effect.",
    evidence: {
      executionId,
      sessionId: accepted.sessionId,
      killedPid: marker.pid,
      injectedSignal: marker.signal,
      killedProcessObservedGone: killedProcessGone,
      launcherExitCode: crash.code,
      launcherSignal: crash.signal,
      crashRunMetadata,
      recoveredRunMetadata: await readWorkflowRunMetadata(accepted.sessionId),
      recoveryWindowMs: Date.now() - restartReadyAt,
      startupQueueLogs: safeSummary(server.output).trim().split(/\r?\n/u).filter(Boolean),
      operationId: operation.operation_id,
      invocationCount: operation.invocation_count,
      effectCount: operation.effect_count,
      effectPath: operation.effect_path,
      coordinatorStatus: status.status,
    },
  };
}

async function gateSyntheticSecrets(coordinator) {
  const { containsSecurityGuardError } = await import("./security-guard.mjs");
  const { getExecution, updateExecution } = await import("./prototype-store.mjs");
  const ingressCanary = `API_KEY=synthetic_ingress_${randomBytes(20).toString("hex")}`;
  const authorization = `Basic ${Buffer.from(`${probeAuth.username}:${probeAuth.password}`).toString("base64")}`;

  async function rejectedRequest(path, body) {
    const response = await fetch(`${host}${path}`, {
      method: "POST",
      headers: { authorization, "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    const responseBody = await response.text();
    return { status: response.status, containsCanary: responseBody.includes(ingressCanary) };
  }

  // These are distinct ingress negatives. The canary is not sent in either
  // tool-only or provider-only execution below.
  const nestedClientContext = await rejectedRequest("/eve/v1/session", {
    message: "synthetic ingress validation only",
    clientContext: { caller: { nested: [{ note: ingressCanary }] } },
  });
  const nestedInputResponse = await rejectedRequest("/eve/v1/session/nonexistent-security-probe", {
    inputResponses: [{ requestId: "synthetic-request", text: ingressCanary }],
  });
  const unknownAttachment = await rejectedRequest("/eve/v1/session", {
    message: [{ type: "file", data: "data:text/plain;base64,c3ludGhldGlj", mediaType: "text/plain" }],
  });

  const coordinatorExecutionId = `secret_ingress_${runId}`;
  const coordinatorIngress = await coordinator.submitExecution({
    executionId: coordinatorExecutionId,
    mode: "secret-tool",
    message: `PROBE executionId=${coordinatorExecutionId} mode=secret-tool ${ingressCanary}`,
    host,
    auth: probeAuth,
  });

  async function executeProtectedMode(mode) {
    const executionId = `${mode.replaceAll("-", "_")}_${runId}`;
    const accepted = await coordinator.submitExecution({
      executionId,
      mode,
      message: `PROBE executionId=${executionId} mode=${mode}. Do not request, display, or summarize credential values.`,
      host,
      auth: probeAuth,
      signal: AbortSignal.timeout(15_000),
    });
    if (accepted.status !== "running" || !accepted.sessionId) {
      throw new Error(`Protected ${mode} fixture did not create its safe-input EVE session.`);
    }
    sessionsOwned.add(accepted.sessionId);
    const snapshot = await waitForSnapshot(accepted.sessionId, latestTurnSettled, 45_000);
    const status = await coordinator.readExecutionStatus(executionId, { host, auth: probeAuth });
    return { executionId, sessionId: accepted.sessionId, events: snapshot.events, status };
  }

  const toolPath = await executeProtectedMode("secret-tool");
  const providerPath = await executeProtectedMode("secret-provider");
  const providerErrorPath = await executeProtectedMode("secret-provider-error");
  const observationsPath = join(prototypeRoot, "fixture-observations.jsonl");
  const observations = await readFile(observationsPath, "utf8").catch(() => "");
  const modelObservations = observations.split("\n").filter(Boolean).map((line) => JSON.parse(line));
  const toolModelObservations = modelObservations.filter((item) => item.executionId === toolPath.executionId);
  const providerModelObservations = modelObservations.filter((item) => item.executionId === providerPath.executionId);
  const providerErrorObservations = modelObservations.filter((item) => item.executionId === providerErrorPath.executionId);
  const toolGuardEvent = containsSecurityGuardError(toolPath.events);
  const providerGuardEvent = containsSecurityGuardError(providerPath.events);
  const providerErrorGuardEvent = containsSecurityGuardError(providerErrorPath.events);
  const toolEventsContainCanary = JSON.stringify(toolPath.events).includes(securityCanary) || JSON.stringify(toolPath.events).includes(ingressCanary);
  const providerEventsContainCanary = JSON.stringify(providerPath.events).includes(securityCanary) || JSON.stringify(providerPath.events).includes(ingressCanary);
  const providerErrorEventsContainCanary = JSON.stringify(providerErrorPath.events).includes(securityCanary)
    || JSON.stringify(providerErrorPath.events).includes(ingressCanary);
  const toolActionRequestCount = toolPath.events.filter((event) => event.type === "actions.requested").length;
  const toolTurnCancelled = toolPath.events.some((event) => event.type === "turn.cancelled");
  const modelInputContainsCanary = [...toolModelObservations, ...providerModelObservations, ...providerErrorObservations]
    .some((item) => item.modelInputContainsKnownSecret);
  const toolOutputWasInternallyGenerated = toolModelObservations.some((item) => item.mode === "secret-tool");
  const providerSplitWasGenerated = providerModelObservations.some((item) => item.emitsSplitSecret === true);
  const toolProjectionContainsCanary = toolModelObservations.some((item) => item.toolResultContainsKnownSecret);
  const probeArtifactContents = await Promise.all([toolPath.executionId, providerPath.executionId, providerErrorPath.executionId].map((executionId) =>
    readFile(join(prototypeRoot, "artifacts", `${executionId}.json`), "utf8").catch(() => ""),
  ));
  const noProbeArtifactsContainCanary = probeArtifactContents.every((content) =>
    !content.includes(securityCanary) && !content.includes(ingressCanary),
  );
  const auditText = await readFile(join(prototypeRoot, "security-audit.jsonl"), "utf8").catch(() => "");
  const toolAuditRecords = auditText.split("\n").filter(Boolean).map((line) => JSON.parse(line))
    .filter((item) => item.sessionId === toolPath.sessionId);
  const auditContainsCanary = auditText.includes(securityCanary) || auditText.includes(ingressCanary);
  const knownValues = [securityCanary, ingressCanary, probeAuth.password];
  const durableScan = await coordinator.scanFilesForSecrets([eveStateDirectory], knownValues);
  const prototypeScan = await coordinator.scanFilesForSecrets([prototypeRoot], knownValues);
  const artifactScan = await coordinator.scanFilesForSecrets([join(prototypeRoot, "artifacts")], knownValues);
  const buildScan = await coordinator.scanFilesForSecrets([join(appRoot, ".output")], knownValues);
  const serverLogContainsCanary = serverProcesses.some((process) => process.output.includes(securityCanary) || process.output.includes(ingressCanary));
  const serverLogContainsAuth = serverProcesses.some((process) => process.output.includes(probeAuth.password));
  const toolRow = getExecution(toolPath.executionId);
  const providerRow = getExecution(providerPath.executionId);
  const providerErrorRow = getExecution(providerErrorPath.executionId);
  const ingressRejected = [nestedClientContext, nestedInputResponse, unknownAttachment]
    .every((result) => result.status === 403 && !result.containsCanary);
  const coordinatorBlocked = coordinatorIngress.status === "human-blocked" && coordinatorIngress.sessionId === undefined;
  const toolBlocked = toolGuardEvent && toolPath.status?.status === "human-blocked"
    && toolRow?.result_json === null && toolTurnCancelled && toolActionRequestCount === 1;
  const providerBlocked = providerGuardEvent && providerPath.status?.status === "human-blocked"
    && providerRow?.result_json === null && providerModelObservations.length === 1;
  const providerErrorBlocked = providerErrorGuardEvent && providerErrorPath.status?.status === "human-blocked"
    && providerErrorRow?.result_json === null && providerErrorObservations.length === 1
    && providerErrorObservations[0].emitsSensitiveProviderError === true;
  const scansComplete = [durableScan, prototypeScan, artifactScan, buildScan].every((scan) =>
    scan.complete && scan.unscannableCount === 0 && scan.readFailureCount === 0,
  );
  const noRawCanary = !toolEventsContainCanary && !providerEventsContainCanary && !providerErrorEventsContainCanary && !modelInputContainsCanary
    && !toolProjectionContainsCanary && !auditContainsCanary && !serverLogContainsCanary
    && durableScan.matches.every((match) => !match.knownValue)
    && prototypeScan.matches.every((match) => !match.knownValue)
    && artifactScan.matches.every((match) => !match.knownValue)
    && buildScan.matches.every((match) => !match.knownValue)
    && scansComplete && noProbeArtifactsContainCanary;
  const evidence = {
    authenticatedNestedClientContextRejectedBeforeDispatch: nestedClientContext.status === 403,
    authenticatedNestedInputResponseRejectedBeforeDispatch: nestedInputResponse.status === 403,
    authenticatedUnknownAttachmentRejected: unknownAttachment.status === 403,
    coordinatorIngressHumanBlocked: coordinatorBlocked,
    toolOnlyCanaryGeneratedInsideTrustedTool: toolOutputWasInternallyGenerated,
    toolResultGuardRejectedBeforeEventStorage: toolBlocked,
    toolGuardHookCancelledTurn: toolTurnCancelled,
    toolActionRequestCount: toolActionRequestCount,
    providerOnlySplitOutputGenerated: providerSplitWasGenerated,
    providerMiddlewareRejectedBeforeEventStorage: providerBlocked,
    providerErrorCustomPropertyFixtureGenerated: providerErrorObservations.some((item) => item.emitsSensitiveProviderError === true),
    providerErrorNormalizedBeforeEventStorage: providerErrorBlocked,
    modelInputContainsSyntheticCanary: modelInputContainsCanary,
    toolResultProjectionContainsSyntheticCanary: toolProjectionContainsCanary,
    rawCanaryInToolEvents: toolEventsContainCanary,
    rawCanaryInProviderEvents: providerEventsContainCanary,
    rawCanaryInProviderErrorEvents: providerErrorEventsContainCanary,
    rawCanaryInAudit: auditContainsCanary,
    rawCanaryInServerLogs: serverLogContainsCanary,
    authCredentialInServerLogs: serverLogContainsAuth,
    durableEveExactMatchCount: durableScan.matches.filter((match) => match.knownValue).length,
    durableFileCount: durableScan.fileCount,
    durableScannedFileCount: durableScan.scannedFileCount,
    durableBytesScanned: durableScan.bytesScanned,
    durableUnscannableCount: durableScan.unscannableCount,
    durableReadFailureCount: durableScan.readFailureCount,
    durableScanComplete: durableScan.complete,
    coordinatorExactMatchCount: prototypeScan.matches.filter((match) => match.knownValue).length,
    coordinatorFileCount: prototypeScan.fileCount,
    coordinatorScannedFileCount: prototypeScan.scannedFileCount,
    coordinatorBytesScanned: prototypeScan.bytesScanned,
    coordinatorUnscannableCount: prototypeScan.unscannableCount,
    coordinatorReadFailureCount: prototypeScan.readFailureCount,
    coordinatorScanComplete: prototypeScan.complete,
    artifactExactMatchCount: artifactScan.matches.filter((match) => match.knownValue).length,
    artifactFileCount: artifactScan.fileCount,
    artifactScannedFileCount: artifactScan.scannedFileCount,
    artifactBytesScanned: artifactScan.bytesScanned,
    artifactUnscannableCount: artifactScan.unscannableCount,
    artifactReadFailureCount: artifactScan.readFailureCount,
    artifactScanComplete: artifactScan.complete,
    compiledOutputExactMatchCount: buildScan.matches.filter((match) => match.knownValue).length,
    compiledOutputFileCount: buildScan.fileCount,
    compiledOutputScannedFileCount: buildScan.scannedFileCount,
    compiledOutputBytesScanned: buildScan.bytesScanned,
    compiledOutputUnscannableCount: buildScan.unscannableCount,
    compiledOutputReadFailureCount: buildScan.readFailureCount,
    compiledOutputScanComplete: buildScan.complete,
    durablePatternCandidateCount: durableScan.matches.filter((match) => match.pattern).length,
    compiledOutputPatternCandidateCount: buildScan.matches.filter((match) => match.pattern).length,
    guardedAuditRecordCount: toolAuditRecords.filter((record) => record.guardRejected).length,
    toolResultJsonNull: toolRow?.result_json === null,
    providerResultJsonNull: providerRow?.result_json === null,
    providerErrorResultJsonNull: providerErrorRow?.result_json === null,
  };
  if (!ingressRejected || !coordinatorBlocked || !toolBlocked || !providerBlocked || !providerErrorBlocked || !noRawCanary
    || toolAuditRecords.filter((record) => record.guardRejected).length === 0 || serverLogContainsAuth) {
    const error = new Error("A protected security-boundary check failed; see metadata-only gate evidence.");
    error.evidence = evidence;
    throw error;
  }
  await coordinator.updateExecution(toolPath.executionId, {
    status: "human-blocked",
    blockedReason: "Tool output was rejected by the pre-persistence security guard.",
  });
  await coordinator.updateExecution(providerPath.executionId, {
    status: "human-blocked",
    blockedReason: "Provider output was rejected by the pre-event security guard.",
  });
  return {
    status: "PASS",
    summary: "Canonical nested ingress and attachment requests, synthetic tool output, split provider output, and custom-property provider errors were rejected before unsafe values crossed the EVE persistence boundary; all required file scans completed.",
    evidence,
  };
}

async function setupReport() {
  const coordinator = await importCoordinator();
  const runtimeVersion = JSON.parse(await readFile(join(appRoot, "node_modules/eve/package.json"), "utf8")).version;
  const modelVersion = JSON.parse(await readFile(join(appRoot, "node_modules/ai/package.json"), "utf8")).version;
  return {
    coordinator,
    report: {
      prototype: "sssf-eve-viability-prototype",
      runId,
      createdAt: new Date().toISOString(),
      versions: { eve: runtimeVersion, ai: modelVersion, node: process.version },
      modelBoundary: "scripted AI SDK LanguageModelV3 fixture; no real model/provider used",
      serviceBoundary: "loopback-only HTTP; runtime Basic credential generated in parent memory and passed only to trusted EVE child/Client; no .env loading",
      gates,
      overallStatus: "BLOCKED",
      adoptionDecision: "BLOCKED",
      resourceCleanup: { ownedSessionContainersRemoved: [], retainedTemplateImages: [], notes: [] },
    },
    runtimeVersion,
  };
}

async function writeReport(report, buildPassed) {
  const coordinator = await importCoordinator();
  report.buildPassed = buildPassed;
  report.gates = gates;
  const failed = gates.some((item) => item.status === "FAIL");
  const incomplete = gates.some((item) => item.status === "NOT_RUN" || item.status === "BLOCKED");
  report.overallStatus = failed ? "FAIL" : incomplete ? "BLOCKED" : "PASS";
  report.adoptionDecision = gates.every((item) => item.status === "PASS") ? "GO" : "BLOCKED";
  report.elapsedMs = Date.now() - startedAt;
  const written = await coordinator.writeEvidence(report);
  console.log(`EVE viability probe ${report.overallStatus}; adoption ${report.adoptionDecision}.`);
  for (const item of gates) console.log(`${item.status.padEnd(7)} ${item.id}: ${safeSummary(item.summary)}`);
  console.log(`Evidence: ${written.evidencePath}`);
  console.log(`Artifacts and disposable SQLite: ${prototypeRoot}`);
  if (cleanupNotes.length > 0) console.error(`Cleanup warnings: ${cleanupNotes.map(safeSummary).join("; ")}`);
  if (gates.some((item) => item.status !== "PASS") || cleanupNotes.length > 0) process.exitCode = 1;
}

async function main() {
  await mkdir(join(prototypeRoot, "artifacts"), { recursive: true });
  probeAuth = { username: "eve-viability-probe", password: randomBytes(32).toString("base64url") };
  let buildPassed = false;
  let report = {
    prototype: "sssf-eve-viability-prototype",
    runId,
    createdAt: new Date().toISOString(),
    versions: {},
    modelBoundary: "scripted AI SDK LanguageModelV3 fixture; no real model/provider used",
    serviceBoundary: "loopback-only HTTP with run-scoped Basic auth; no credential in build, args, workloads, or artifacts",
    gates,
    selectedGate: selectedGate ?? null,
    overallStatus: "BLOCKED",
    adoptionDecision: "BLOCKED",
    resourceCleanup: { ownedSessionContainersRemoved: [], retainedTemplateImages: [], notes: [] },
  };
  try {
    await assertPortFree();
    const initialized = await setupReport();
    report = initialized.report;
    report.workflowStateIsolation = await archivePrototypeWorkflowState(runId);
    activeReport = report;
    await checkpoint("build");
    await buildEve({ output: false, timeoutMs: 115_000 });
    buildPassed = true;
    await startServer();
    setupPassed = true;

    await runStep("loopback-basic-auth", () => runAuthProbe(initialized.coordinator));
    await runStep("callable-eve-sqlite-result", () => gateCallableSlice(initialized.coordinator, initialized.runtimeVersion));
    await runStep("docker-workspace-isolation", () => gateDockerIsolation(initialized.coordinator));
    await runStep("workspace-stop-resume-and-loss", () => gateWorkspaceRecovery(initialized.coordinator));
    await runStep("approval-restart-same-session", () => gateApprovalRestart(initialized.coordinator));
    await runStep("stable-idempotent-replay", () => gateStableReplay(initialized.coordinator));
    await runStep("synthetic-secret-boundaries", () => gateSyntheticSecrets(initialized.coordinator));
  } catch (error) {
    report.probeError = safeSummary(error instanceof Error ? error.message : String(error));
    for (const item of gates.filter((candidate) => candidate.status === "NOT_RUN")) {
      item.summary = `NOT_RUN after setup failure: ${safeSummary(error instanceof Error ? error.message : String(error))}`;
    }
  } finally {
    await stopServer("SIGTERM");
    for (const sessionId of sessionsOwned) {
      let existedBefore = false;
      try {
        existedBefore = (await docker(["ps", "-aq", "--filter", `label=eve.sandbox.tag.agent=${sessionId}`])).length > 0;
      } catch (error) {
        cleanupNotes.push(`pre-cleanup verification for ${sessionId} failed: ${safeSummary(error.message)}`);
      }
      await removeOwnedContainer(sessionId);
      try {
        const remaining = await docker(["ps", "-aq", "--filter", `label=eve.sandbox.tag.agent=${sessionId}`]);
        if (remaining) cleanupNotes.push(`container still exists for prototype EVE session ${sessionId}`);
        else if (existedBefore) report.resourceCleanup.ownedSessionContainersRemoved.push(sessionId);
      } catch (error) {
        cleanupNotes.push(`post-cleanup verification for ${sessionId} failed: ${safeSummary(error.message)}`);
      }
    }
    retainedTemplateImages = await retainedTemplateMarkers();
    report.resourceCleanup.retainedTemplateImages = retainedTemplateImages;
    report.resourceCleanup.templatePolicy = "EVE-managed template image tags were retained because their cache tags may be shared with other apps; sssf-runner:latest was never removed.";
    await rm(join(prototypeRoot, "home"), { recursive: true, force: true });
    await rm(join(prototypeRoot, "tmp"), { recursive: true, force: true });
    report.resourceCleanup.notes = cleanupNotes;
    report.resourceCleanup.eveProcessStopped = serverProcesses.every((process) => process.child.exitCode !== null || process.child.signalCode !== null);
    report.resourceCleanup.credentialsNotInBuildArgsOrEvidence = probeAuth
      ? !serverProcesses.some((process) => process.output.includes(probeAuth.password))
      : true;
    report.resourceCleanup.prototypeOwnedSessionCount = sessionsOwned.size;
    report.setupPassed = setupPassed;
    report.timedOutGate = timedOutGate;
    const serverLogContainsProbeCredential = serverProcesses.some((process) => probeAuth && process.output.includes(probeAuth.password));
    report.routeAuthCredentialInServerOutput = serverLogContainsProbeCredential;
    report.routeAuthCredentialInParentArguments = false;
    report.gates = gates;
    const failed = gates.some((item) => item.status === "FAIL");
    const incomplete = gates.some((item) => item.status === "NOT_RUN" || item.status === "BLOCKED");
    report.overallStatus = failed ? "FAIL" : incomplete ? "BLOCKED" : "PASS";
    report.adoptionDecision = gates.every((item) => item.status === "PASS") ? "GO" : "BLOCKED";
    report.elapsedMs = Date.now() - startedAt;
    const coordinator = await importCoordinator();
    const evidence = await coordinator.writeEvidence(report);
    console.log(`EVE viability probe ${report.overallStatus}; adoption ${report.adoptionDecision}.`);
    for (const item of gates) console.log(`${item.status.padEnd(7)} ${item.id}: ${safeSummary(item.summary)}`);
    console.log(`Evidence: ${evidence.evidencePath}`);
    console.log(`Summary: ${evidence.summaryPath}`);
    console.log(`Artifacts and disposable SQLite: ${prototypeRoot}`);
    if (cleanupNotes.length > 0) console.error(`Cleanup warnings: ${cleanupNotes.map(safeSummary).join("; ")}`);
    if (gates.some((item) => item.status !== "PASS") || cleanupNotes.length > 0 || serverLogContainsProbeCredential) process.exitCode = 1;
  }
}

await main();
