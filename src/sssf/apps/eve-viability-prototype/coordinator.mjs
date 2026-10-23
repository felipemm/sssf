import { randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readdir, writeFile } from "node:fs/promises";
import { StringDecoder } from "node:string_decoder";
import { join, resolve } from "node:path";
import { Client } from "eve/client";
import { containsSecurityGuardError, scanSensitiveValue } from "./security-guard.mjs";
import {
  createExecution,
  getExecution,
  prototypeDirectory,
  updateExecution,
} from "./prototype-store.mjs";

export { updateExecution };

const DEFAULT_HOST = "http://127.0.0.1:3217";
const secretPattern = /(?:API[_-]?KEY|ACCESS[_-]?TOKEN|SECRET|PASSWORD)\s*[:=]\s*[A-Za-z0-9._-]{16,}/giu;
const artifactDirectory = join(prototypeDirectory, "artifacts");

export function detectSecrets(value, knownValues = []) {
  const result = scanSensitiveValue(value, knownValues);
  return {
    knownValue: result.knownValue,
    pattern: result.pattern,
    unscannable: result.unscannable,
  };
}

export function redactText(value, knownValues = []) {
  let text = String(value);
  for (const secret of knownValues) {
    if (secret) text = text.split(secret).join("[REDACTED]");
  }
  return text.replace(secretPattern, "[REDACTED_SECRET_PATTERN]");
}

export function makeClient(host = DEFAULT_HOST, auth) {
  return new Client({ host, ...(auth ? { auth: { basic: auth } } : {}), redirect: "error" });
}

export function makeProbeMessage(executionId, mode, detail = "run one disposable workspace operation") {
  return `PROBE executionId=${executionId} mode=${mode}. ${detail}`;
}

export async function submitExecution({
  executionId = randomUUID(),
  mode = "vertical",
  detail,
  message,
  clientContext,
  host,
  auth,
  signal,
} = {}) {
  if (!/^[a-zA-Z0-9_-]{1,100}$/u.test(executionId)) throw new Error("Execution identity must be a bounded stable token.");
  if (!["vertical", "approved", "replay", "secret", "secret-tool", "secret-provider", "secret-provider-error"].includes(mode)) throw new Error(`Unsupported prototype mode: ${mode}`);
  const userText = message ?? makeProbeMessage(executionId, mode, detail);
  const detected = detectSecrets({ message: userText, clientContext });
  if (detected.knownValue || detected.pattern || detected.unscannable) {
    createExecution({ executionId, mode, status: "human-blocked", blockedReason: "Secret-like ingress quarantined before EVE submission." });
    return { executionId, status: "human-blocked", reason: "Secret-like ingress quarantined before EVE submission." };
  }

  createExecution({ executionId, mode, status: "accepted" });
  try {
    const client = makeClient(host, auth);
    const { session } = await client.sessions.create({ message: userText, ...(clientContext === undefined ? {} : { clientContext }), ...(signal ? { signal } : {}) });
    updateExecution(executionId, { sessionId: session.state.sessionId, status: "running" });
    return { executionId, sessionId: session.state.sessionId, status: "running" };
  } catch (error) {
    updateExecution(executionId, { status: "failed", blockedReason: redactText(error?.message ?? error) });
    throw error;
  }
}

export function deriveExecutionState(events) {
  const settledRequestIds = new Set();
  const requestedIds = new Set();
  for (const event of events) {
    if (event.type === "input.requested") {
      for (const request of event.data.requests ?? []) requestedIds.add(request.requestId);
    }
    if (event.type === "input.resolved") {
      for (const resolution of event.data.resolutions ?? []) settledRequestIds.add(resolution.requestId);
    }
    if (event.type === "approval.settled") settledRequestIds.add(event.data.requestId);
  }
  const pending = [...requestedIds].filter((requestId) => !settledRequestIds.has(requestId));
  if (pending.length > 0) return { status: "awaiting-input", pendingRequestIds: pending };

  const lastStartedIndex = events.findLastIndex((event) => event.type === "turn.started");
  const lastTerminalIndex = events.findLastIndex((event) => ["turn.completed", "turn.failed", "turn.cancelled", "session.completed", "session.failed"].includes(event.type));
  if (lastTerminalIndex > lastStartedIndex) {
    const terminal = events[lastTerminalIndex];
    if (terminal.type === "turn.failed" || terminal.type === "session.failed") return { status: "failed", pendingRequestIds: [] };
    if (terminal.type === "turn.cancelled") return { status: "cancelled", pendingRequestIds: [] };
    return { status: "completed", pendingRequestIds: [] };
  }
  if (events.some((event) => event.type === "session.waiting") && lastStartedIndex < 0) return { status: "waiting", pendingRequestIds: [] };
  return { status: "running", pendingRequestIds: [] };
}

export async function readExecutionStatus(executionId, { host, auth, signal } = {}) {
  const row = getExecution(executionId);
  if (!row) return null;
  const status = { ...row, result: row.result_json ? JSON.parse(row.result_json) : null };
  delete status.result_json;
  if (row.session_id) {
    try {
      const snapshot = await makeClient(host, auth).sessions.attach(row.session_id).snapshot({ signal: signal ?? AbortSignal.timeout(7000) });
      const events = snapshot.events;
      const derived = deriveExecutionState(events);
      status.eventCount = events.length;
      status.sessionState = derived.status;
      status.pendingRequestIds = derived.pendingRequestIds;
      const durableStatus = row.status === "human-blocked" || containsSecurityGuardError(events) ? "human-blocked"
        : derived.status === "awaiting-input" ? "awaiting-input"
          : derived.status === "failed" ? "failed"
            : derived.status === "cancelled" ? "cancelled"
              : derived.status === "completed" ? "completed"
                : row.status;
      if (durableStatus !== row.status) {
        updateExecution(executionId, { status: durableStatus });
        status.status = durableStatus;
      }
    } catch (error) {
      status.sessionState = "unavailable";
      status.sessionError = redactText(error?.message ?? error);
    }
  }
  return status;
}

export async function approveExecution(executionId, requestId, { host, auth, optionId = "approve" } = {}) {
  const row = getExecution(executionId);
  if (!row?.session_id) throw new Error("Execution has no EVE session to approve.");
  const session = makeClient(host, auth).sessions.attach(row.session_id);
  const response = await session.respond([{ requestId, optionId }], { signal: AbortSignal.timeout(30_000) });
  const result = await response.result();
  const snapshot = await session.snapshot({ signal: AbortSignal.timeout(7000) });
  const derived = deriveExecutionState(snapshot.events);
  const nextStatus = derived.status === "awaiting-input" ? "awaiting-input"
    : derived.status === "failed" ? "failed"
      : derived.status === "completed" ? "completed"
        : "running";
  updateExecution(executionId, { status: nextStatus });
  return result;
}

export function extractToolResults(events) {
  return events
    .filter((event) => event.type === "action.result")
    .map((event) => event.data?.result)
    .filter((result) => result && typeof result === "object")
    .map((result) => result.output ?? result.result ?? result);
}

export async function persistSafeArtifact({ executionId, sessionId, toolResult, message, knownValues = [] }) {
  const raw = { executionId, sessionId, toolResult, message };
  const detected = detectSecrets(raw, knownValues);
  await mkdir(artifactDirectory, { recursive: true });
  const file = join(artifactDirectory, `${executionId}.json`);
  if (detected.knownValue || detected.pattern || detected.unscannable) {
    const safe = {
      executionId,
      sessionId,
      status: "human-blocked",
      quarantine: { knownValue: detected.knownValue, pattern: detected.pattern, unscannable: detected.unscannable },
      result: "[quarantined; raw output was not persisted by coordinator]",
    };
    await writeFile(file, `${JSON.stringify(safe, null, 2)}\n`, { mode: 0o600 });
    updateExecution(executionId, { status: "human-blocked", artifactPath: file, blockedReason: "Secret-like output quarantined before coordinator artifact persistence." });
    return { path: file, status: "quarantined", detection: detected };
  }

  const artifact = {
    executionId,
    sessionId,
    status: "completed",
    modelBoundary: "scripted AI SDK fixture; not a real LLM",
    result: toolResult,
    message: redactText(message ?? ""),
  };
  await writeFile(file, `${JSON.stringify(artifact, null, 2)}\n`, { mode: 0o600 });
  updateExecution(executionId, { status: "completed", result: toolResult ?? null, artifactPath: file });
  return { path: file, status: "persisted", detection: detected };
}

export async function scanFilesForSecrets(paths, knownValues = []) {
  const matches = [];
  const coverageIssues = [];
  let fileCount = 0;
  let scannedFileCount = 0;
  let bytesScanned = 0;
  let unscannableCount = 0;
  let readFailureCount = 0;
  const chunkSize = 64 * 1024;
  const overlapBytes = 4_096;

  function markUnscannable(path, type, readFailure = false) {
    unscannableCount += 1;
    if (readFailure) readFailureCount += 1;
    coverageIssues.push({ path, type });
    matches.push({ path, knownValue: false, pattern: false, unscannable: true, readFailure });
  }

  async function visit(path) {
    let entries;
    try {
      entries = await readdir(path, { withFileTypes: true });
    } catch {
      markUnscannable(path, "directory-read-failure", true);
      return;
    }
    for (const entry of entries) {
      const fullPath = join(path, entry.name);
      if (entry.isSymbolicLink()) {
        markUnscannable(fullPath, "symbolic-link");
      } else if (entry.isDirectory()) {
        await visit(fullPath);
      } else if (entry.isFile()) {
        fileCount += 1;
        let fileBytes = 0;
        let tail = "";
        let knownValue = false;
        let pattern = false;
        const decoder = new StringDecoder("utf8");
        let unscannableChunk = false;
        try {
          for await (const chunk of createReadStream(fullPath, { highWaterMark: chunkSize })) {
            fileBytes += chunk.length;
            const text = decoder.write(chunk);
            const scanned = detectSecrets(`${tail}${text}`, knownValues);
            knownValue ||= scanned.knownValue;
            pattern ||= scanned.pattern;
            if (scanned.unscannable) {
              unscannableChunk = true;
              throw new Error("chunk-unscannable");
            }
            tail = `${tail}${text}`.slice(-overlapBytes);
          }
          const finalText = decoder.end();
          if (finalText) {
            const scanned = detectSecrets(`${tail}${finalText}`, knownValues);
            knownValue ||= scanned.knownValue;
            pattern ||= scanned.pattern;
            if (scanned.unscannable) {
              unscannableChunk = true;
              throw new Error("chunk-unscannable");
            }
          }
          scannedFileCount += 1;
          bytesScanned += fileBytes;
          if (knownValue || pattern) matches.push({ path: fullPath, knownValue, pattern, unscannable: false });
        } catch {
          markUnscannable(fullPath, unscannableChunk ? "chunk-unscannable" : "file-read-failure", !unscannableChunk);
        }
      } else {
        markUnscannable(fullPath, "unsupported-entry");
      }
    }
  }

  for (const path of paths) await visit(resolve(path));
  return {
    fileCount,
    scannedFileCount,
    bytesScanned,
    unscannableCount,
    readFailureCount,
    complete: unscannableCount === 0 && readFailureCount === 0 && scannedFileCount === fileCount,
    coverageIssues,
    matches,
  };
}

export async function writeEvidence(evidence) {
  await mkdir(prototypeDirectory, { recursive: true });
  const evidencePath = join(prototypeDirectory, "evidence.json");
  const summaryPath = join(prototypeDirectory, "evidence-summary.md");
  await writeFile(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  const lines = [
    "# EVE viability probe evidence",
    "",
    `Overall: **${evidence.overallStatus}**; adoption: **${evidence.adoptionDecision}**.`,
    "",
    "| Gate | Status | Summary |",
    "| --- | --- | --- |",
    ...evidence.gates.map((gate) => `| ${gate.id} | ${gate.status} | ${redactText(gate.summary).replaceAll("|", "\\|")} |`),
    "",
    "Synthetic-only probe. Do not infer real-provider or universal security validation.",
  ];
  await writeFile(summaryPath, `${lines.join("\n")}\n`, { mode: 0o600 });
  return { evidencePath, summaryPath };
}

export function summarizeExecution(executionId) {
  const row = getExecution(executionId);
  if (!row) throw new Error(`No execution found for ${executionId}`);
  return {
    executionId: row.execution_id,
    mode: row.mode,
    sessionId: row.session_id,
    status: row.status,
    blockedReason: row.blocked_reason,
    artifactPath: row.artifact_path,
  };
}

export async function approvePending(executionId, requestId, options = {}) {
  const result = await approveExecution(executionId, requestId, options);
  return { executionId, status: result.status, sessionId: result.sessionId };
}

async function cli(args) {
  const [command, executionId, ...rest] = args;
  const auth = process.env.PROTOTYPE_ROUTE_AUTH_USER && process.env.PROTOTYPE_ROUTE_AUTH_PASSWORD
    ? { username: process.env.PROTOTYPE_ROUTE_AUTH_USER, password: process.env.PROTOTYPE_ROUTE_AUTH_PASSWORD }
    : undefined;
  if (command === "submit") {
    const mode = rest[0] ?? "vertical";
    const detail = rest.slice(1).join(" ") || undefined;
    const result = await submitExecution({ executionId: executionId ?? randomUUID(), mode, detail, auth });
    console.log(JSON.stringify(result));
    return;
  }
  if (command === "status") {
    const status = await readExecutionStatus(executionId, { auth });
    console.log(JSON.stringify(status ?? { executionId, status: "not-found" }));
    return;
  }
  if (command === "approve") {
    const requestId = rest[0];
    if (!requestId) throw new Error("Usage: node coordinator.mjs approve <executionId> <requestId>");
    console.log(JSON.stringify(await approvePending(executionId, requestId, { auth })));
    return;
  }
  throw new Error("Usage: node coordinator.mjs <submit|status|approve> <executionId> [mode|requestId] [detail]");
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(new URL(import.meta.url).pathname)) {
  cli(process.argv.slice(2)).catch((error) => {
    console.error(JSON.stringify({ error: redactText(error?.message ?? error) }));
    process.exitCode = 1;
  });
}
