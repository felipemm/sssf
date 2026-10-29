import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { test } from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = dirname(fileURLToPath(import.meta.url));
const testDatabase = resolve(appRoot, ".prototype", "security-tests.sqlite");
process.env.PROTOTYPE_DB = testDatabase;
const coordinator = await import("./coordinator.mjs");
const store = await import("./prototype-store.mjs");

const secret = `API_KEY=synthetic_${randomUUID().replaceAll("-", "")}`;

function assertNoSecretInObject(root, value) {
  const pending = [root];
  const seen = new Set();
  while (pending.length > 0) {
    const current = pending.pop();
    if (typeof current === "string") {
      assert.equal(current.includes(value), false);
    } else if (current !== null && typeof current === "object" && !seen.has(current)) {
      seen.add(current);
      for (const key of Reflect.ownKeys(current)) {
        const descriptor = Object.getOwnPropertyDescriptor(current, key);
        if (descriptor.get || descriptor.set) {
          assert.equal(current instanceof Error && key === "stack", true, "sanitized errors must not retain custom accessors");
          continue;
        }
        pending.push(descriptor.value);
      }
    }
  }
}

test("known values and secret patterns are detected and redacted across joined stream chunks", () => {
  const joined = [`partial ${secret.slice(0, 12)}`, `${secret.slice(12)} tail`].join("");
  assert.deepEqual(coordinator.detectSecrets(joined, [secret]), { knownValue: true, pattern: true, unscannable: false });
  assert.equal(coordinator.redactText(joined, [secret]).includes(secret), false);
});

test("coordinator ingress quarantines secret-like input before creating an EVE session", async () => {
  const splitContext = coordinator.detectSecrets({ clientContext: { first: secret.slice(0, 9), second: secret.slice(9) } });
  assert.equal(splitContext.knownValue, false);
  assert.equal(splitContext.pattern, true);
  assert.equal(splitContext.unscannable, false);

  const executionId = `unit_${randomUUID().replaceAll("-", "")}`;
  const result = await coordinator.submitExecution({
    executionId,
    mode: "secret",
    message: `PROBE executionId=${executionId} mode=secret secret=${secret}`,
    host: "http://127.0.0.1:1",
  });
  assert.equal(result.status, "human-blocked");
  assert.equal(result.sessionId, undefined);
  assert.equal(store.getExecution(executionId).session_id, null);
});

test("coordinator rejects nested client context and unscannable attachments before session creation", async () => {
  const contextId = `unit_${randomUUID().replaceAll("-", "")}`;
  const contextResult = await coordinator.submitExecution({
    executionId: contextId,
    mode: "secret-tool",
    message: "safe text",
    clientContext: { nested: [{ value: secret }] },
    host: "http://127.0.0.1:1",
  });
  assert.equal(contextResult.status, "human-blocked");
  assert.equal(store.getExecution(contextId).session_id, null);

  const attachmentId = `unit_${randomUUID().replaceAll("-", "")}`;
  const attachmentResult = await coordinator.submitExecution({
    executionId: attachmentId,
    mode: "secret-tool",
    message: [{ type: "file", data: "data:text/plain;base64,c3ludGhldGlj", mediaType: "text/plain" }],
    host: "http://127.0.0.1:1",
  });
  assert.equal(attachmentResult.status, "human-blocked");
  assert.equal(store.getExecution(attachmentId).session_id, null);
});

test("tool and model boundaries reject nested, split, non-streaming, and error secrets", async () => {
  const guard = await import("./security-guard.mjs");
  const canary = `API_KEY=synthetic_${randomUUID().replaceAll("-", "")}`;
  const previous = process.env.PROTOTYPE_SYNTHETIC_CANARIES;
  process.env.PROTOTYPE_SYNTHETIC_CANARIES = JSON.stringify([canary]);
  try {
    assert.throws(() => guard.guardedToolResult({ nested: { value: canary } }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assert.equal(error.message.includes(canary), false);
      return true;
    });
    const toolFailure = Object.assign(new Error("sandbox failure"), { response: { body: canary } });
    await assert.rejects(() => guard.guardedToolCall(() => { throw toolFailure; }), (error) => {
      assert.equal(error.name, "ProtectedToolError");
      assert.notEqual(error, toolFailure);
      assertNoSecretInObject(error, canary);
      return true;
    });
    await assert.rejects(() => guard.securityModelMiddleware.transformParams({
      params: { prompt: [{ role: "user", content: [{ type: "text", text: canary }] }] },
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));

    const split = canary.indexOf("=") + 5;
    assert.deepEqual(guard.scanSensitiveValue(canary.slice(0, split)), { knownValue: false, pattern: false, unscannable: false });
    assert.deepEqual(guard.scanSensitiveValue(canary.slice(split)), { knownValue: false, pattern: false, unscannable: false });
    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => ({
        stream: new ReadableStream({
          start(controller) {
            controller.enqueue({ type: "text-delta", text: canary.slice(0, split) });
            controller.enqueue({ type: "text-delta", text: canary.slice(split) });
            controller.close();
          },
        }),
      }),
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));

    await assert.rejects(() => guard.securityModelMiddleware.wrapGenerate({
      doGenerate: async () => ({ content: [{ type: "reasoning", text: canary }] }),
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));
    await assert.rejects(() => guard.securityModelMiddleware.wrapGenerate({
      doGenerate: async () => { throw new Error(canary); },
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));
  } finally {
    if (previous === undefined) delete process.env.PROTOTYPE_SYNTHETIC_CANARIES;
    else process.env.PROTOTYPE_SYNTHETIC_CANARIES = previous;
  }
});

test("provider stream results reject unsafe metadata and never forward accessor or Error payloads", async () => {
  const guard = await import("./security-guard.mjs");
  const canary = `API_KEY=synthetic_${randomUUID().replaceAll("-", "")}`;
  const previous = process.env.PROTOTYPE_SYNTHETIC_CANARIES;
  process.env.PROTOTYPE_SYNTHETIC_CANARIES = JSON.stringify([canary]);
  const completedStream = () => new ReadableStream({
    start(controller) {
      controller.enqueue({ type: "text-delta", text: "safe" });
      controller.close();
    },
  });
  try {
    const accessorError = Object.assign(new Error("provider metadata getter failed"), { body: canary });
    let getterRead = false;
    const resultWithThrowingGetter = { stream: completedStream() };
    Object.defineProperty(resultWithThrowingGetter, "response", {
      enumerable: true,
      get() {
        getterRead = true;
        throw accessorError;
      },
    });
    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => resultWithThrowingGetter,
    }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assert.equal(error instanceof guard.SecretBoundaryError, true);
      assert.notEqual(error, accessorError);
      assertNoSecretInObject(error, canary);
      return true;
    });
    assert.equal(getterRead, false, "provider result accessors are rejected without invocation");

    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => ({ stream: completedStream(), response: { headers: { "x-provider-body": canary } } }),
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));
    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => ({ stream: completedStream(), request: { body: canary } }),
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));
    await assert.rejects(() => guard.securityModelMiddleware.wrapGenerate({
      doGenerate: async () => ({
        content: [{ type: "text", text: "safe" }],
        finishReason: { unified: "stop", raw: "stop" },
        usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
        warnings: [],
        response: { body: canary },
      }),
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));
    await assert.rejects(() => guard.securityModelMiddleware.wrapGenerate({
      doGenerate: async () => ({
        content: [{ type: "text", text: "safe" }],
        finishReason: { unified: "stop", raw: "stop" },
        usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
        warnings: [{ type: "other", message: canary }],
      }),
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));

    const generatedAt = new Date("2026-10-22T12:34:56.000Z");
    const generatedResult = {
      content: [{ type: "text", text: "safe" }],
      finishReason: { unified: "stop", raw: "stop" },
      usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
      warnings: [],
      response: { timestamp: generatedAt, headers: { "x-provider-status": "ok" } },
    };
    const safeGenerated = await guard.securityModelMiddleware.wrapGenerate({ doGenerate: async () => generatedResult });
    assert.notEqual(safeGenerated, generatedResult);
    assert.notEqual(safeGenerated.response, generatedResult.response);
    assert.equal(safeGenerated.response.timestamp instanceof Date, true);
    assert.equal(safeGenerated.response.timestamp.toISOString(), generatedAt.toISOString());

    const safeWrapped = await guard.securityModelMiddleware.wrapStream({
      doStream: async () => ({
        stream: completedStream(),
        request: { body: { prompt: "safe" } },
        response: { headers: { "x-provider-status": "ok" } },
      }),
    });
    assert.deepEqual(safeWrapped.request, { body: { prompt: "safe" } });
    assert.deepEqual(safeWrapped.response, { headers: { "x-provider-status": "ok" } });
    const safeReader = safeWrapped.stream.getReader();
    assert.equal((await safeReader.read()).value.text, "safe");
    assert.equal((await safeReader.read()).done, true);

    const mutableMetadata = { headers: { "x-provider-status": "safe" } };
    let releaseProviderStream;
    const providerStreamGate = new Promise((resolve) => { releaseProviderStream = resolve; });
    const mutableResult = {
      response: { headers: mutableMetadata.headers },
      stream: new ReadableStream({
        async pull(controller) {
          await providerStreamGate;
          mutableMetadata.headers["x-provider-status"] = canary;
          controller.enqueue({ type: "text-delta", text: "safe" });
          controller.close();
        },
      }),
    };
    setTimeout(releaseProviderStream, 0);
    const copiedMetadata = await guard.securityModelMiddleware.wrapStream({ doStream: async () => mutableResult });
    assert.equal(mutableMetadata.headers["x-provider-status"], canary);
    assert.equal(copiedMetadata.response.headers["x-provider-status"], "safe");

    let changingGetterReads = 0;
    const changingMetadata = { stream: completedStream() };
    Object.defineProperty(changingMetadata, "request", {
      enumerable: true,
      get() {
        changingGetterReads += 1;
        return { body: changingGetterReads === 1 ? "safe" : canary };
      },
    });
    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => changingMetadata,
    }), (error) => error.name === "SecretBoundaryError" && !error.message.includes(canary));
    assert.equal(changingGetterReads, 0, "accessor metadata is rejected without invoking its getter");

    const nestedError = Object.assign(new Error("provider payload error"), { body: canary });
    await assert.rejects(() => guard.securityModelMiddleware.wrapGenerate({
      doGenerate: async () => ({
        content: [{ type: "text", text: "safe" }],
        providerMetadata: { fixture: { details: nestedError } },
      }),
    }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assertNoSecretInObject(error, canary);
      return true;
    });

    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => ({
        stream: new ReadableStream({
          start(controller) {
            controller.enqueue({ type: "error", error: nestedError });
            controller.close();
          },
        }),
      }),
    }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assertNoSecretInObject(error, canary);
      return true;
    });
  } finally {
    if (previous === undefined) delete process.env.PROTOTYPE_SYNTHETIC_CANARIES;
    else process.env.PROTOTYPE_SYNTHETIC_CANARIES = previous;
  }
});

test("provider failures from generate, stream startup, and stream reads never forward custom error metadata", async () => {
  const guard = await import("./security-guard.mjs");
  const canary = `API_KEY=synthetic_${randomUUID().replaceAll("-", "")}`;
  const previous = process.env.PROTOTYPE_SYNTHETIC_CANARIES;
  process.env.PROTOTYPE_SYNTHETIC_CANARIES = JSON.stringify([canary]);
  try {
    const nested = Object.assign(new Error("nested provider failure"), { metadata: { body: canary } });
    const generateError = new AggregateError([nested], "provider generation failed");
    generateError.response = { body: { nested: canary } };
    generateError.cause = Object.assign(new Error("provider cause"), { details: { body: canary } });
    generateError.self = generateError;
    await assert.rejects(() => guard.securityModelMiddleware.wrapGenerate({
      doGenerate: async () => { throw generateError; },
    }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assert.notEqual(error, generateError);
      assertNoSecretInObject(error, canary);
      return true;
    });

    let getterRead = false;
    const startupError = new Error("provider startup failed");
    Object.defineProperty(startupError, "response", {
      enumerable: true,
      get() {
        getterRead = true;
        return { body: canary };
      },
    });
    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => { throw startupError; },
    }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assert.notEqual(error, startupError);
      assertNoSecretInObject(error, canary);
      return true;
    });
    assert.equal(getterRead, false);

    const readError = Object.assign(new Error("provider stream read failed"), {
      response: { body: { nested: canary } },
    });
    readError.self = readError;
    await assert.rejects(() => guard.securityModelMiddleware.wrapStream({
      doStream: async () => ({
        stream: new ReadableStream({
          start(controller) { controller.error(readError); },
        }),
      }),
    }), (error) => {
      assert.equal(error.name, "SecretBoundaryError");
      assert.notEqual(error, readError);
      assertNoSecretInObject(error, canary);
      return true;
    });
  } finally {
    if (previous === undefined) delete process.env.PROTOTYPE_SYNTHETIC_CANARIES;
    else process.env.PROTOTYPE_SYNTHETIC_CANARIES = previous;
  }
});

test("file secret scan covers large files, chunk boundaries, and reports inaccessible scopes", async () => {
  const scanDirectory = await mkdtemp(resolve(appRoot, ".prototype", "security-scan-"));
  const canary = `API_KEY=synthetic_${randomUUID().replaceAll("-", "")}`;
  const file = resolve(scanDirectory, "oversized.bin");
  const byteLength = 2_250_000;
  const bytes = Buffer.alloc(byteLength, 0x78);
  const chunkBoundary = 64 * 1024;
  bytes.write(canary, chunkBoundary - 7);
  bytes.write(canary, 1_200_000);
  try {
    await writeFile(file, bytes);
    const complete = await coordinator.scanFilesForSecrets([scanDirectory], [canary]);
    assert.equal(complete.complete, true);
    assert.equal(complete.fileCount, 1);
    assert.equal(complete.scannedFileCount, 1);
    assert.equal(complete.bytesScanned, byteLength);
    assert.equal(complete.unscannableCount, 0);
    assert.equal(complete.readFailureCount, 0);
    assert.deepEqual(complete.matches.map((match) => ({ knownValue: match.knownValue, unscannable: match.unscannable })), [
      { knownValue: true, unscannable: false },
    ]);

    const missing = await coordinator.scanFilesForSecrets([resolve(scanDirectory, "missing-required-scope")], [canary]);
    assert.equal(missing.complete, false);
    assert.equal(missing.unscannableCount, 1);
    assert.equal(missing.readFailureCount, 1);
  } finally {
    await rm(scanDirectory, { recursive: true, force: true });
  }
});

test("artifact quarantine stores no raw secret and parks the execution for human intervention", async () => {
  const executionId = `unit_${randomUUID().replaceAll("-", "")}`;
  store.createExecution({ executionId, mode: "secret" });
  const artifact = await coordinator.persistSafeArtifact({
    executionId,
    sessionId: "synthetic-test-session",
    toolResult: { sensitive: secret },
    message: "Untrusted tool output",
    knownValues: [secret],
  });
  try {
    assert.equal(artifact.status, "quarantined");
    const content = await readFile(artifact.path, "utf8");
    assert.equal(content.includes(secret), false);
    assert.equal(store.getExecution(executionId).status, "human-blocked");
    assert.equal(store.getExecution(executionId).result_json, null);
  } finally {
    await rm(artifact.path, { force: true });
  }
});

test("execution status clears an approval after durable settlement instead of waiting forever", () => {
  const pending = coordinator.deriveExecutionState([
    { type: "turn.started" },
    { type: "input.requested", data: { requests: [{ requestId: "approval-1" }] } },
    { type: "turn.waiting" },
  ]);
  assert.equal(pending.status, "awaiting-input");
  const settled = coordinator.deriveExecutionState([
    { type: "turn.started" },
    { type: "input.requested", data: { requests: [{ requestId: "approval-1" }] } },
    { type: "input.resolved", data: { resolutions: [{ requestId: "approval-1" }] } },
    { type: "turn.completed" },
  ]);
  assert.equal(settled.status, "completed");
  assert.deepEqual(settled.pendingRequestIds, []);
});

test("stable operation identity records one effect while counting a replayed invocation", () => {
  const executionId = `unit_${randomUUID().replaceAll("-", "")}`;
  const operationId = `${executionId}:workspace-effect`;
  const effectPath = "probe/disposable.txt";
  const first = store.beginOperation({ operationId, executionId, effectPath });
  assert.equal(first.status, "running");
  store.completeOperation(operationId, { operationId });
  const replay = store.beginOperation({ operationId, executionId, effectPath });
  assert.equal(replay.status, "completed");
  assert.equal(replay.invocation_count, 2);
  assert.equal(replay.effect_count, 1);
});

test("isolated runtime enables BuildKit without inheriting Docker auth or host secrets", async () => {
  const { cleanRuntimeEnvironment } = await import("./eve-process.mjs");
  const previousHost = process.env.DOCKER_HOST;
  const previousToken = process.env.BWS_ACCESS_TOKEN;
  process.env.DOCKER_HOST = "unix:///tmp/sssf-prototype-test.sock";
  process.env.BWS_ACCESS_TOKEN = "synthetic-not-a-real-token";
  try {
    const env = await cleanRuntimeEnvironment();
    assert.equal(env.DOCKER_BUILDKIT, "1");
    assert.equal(env.BWS_ACCESS_TOKEN, undefined);
    assert.equal(env.DOCKER_CONFIG, resolve(appRoot, ".prototype/docker-config"));
    const config = JSON.parse(await readFile(resolve(env.DOCKER_CONFIG, "config.json"), "utf8"));
    assert.deepEqual(Object.keys(config), ["cliPluginsExtraDirs"]);
    assert.equal(config.auths, undefined);
    assert.equal(config.credsStore, undefined);
  } finally {
    if (previousHost === undefined) delete process.env.DOCKER_HOST;
    else process.env.DOCKER_HOST = previousHost;
    if (previousToken === undefined) delete process.env.BWS_ACCESS_TOKEN;
    else process.env.BWS_ACCESS_TOKEN = previousToken;
  }
});

test("test database is removed after all prototype unit checks", async () => {
  await rm(testDatabase, { force: true });
  await rm(`${testDatabase}-wal`, { force: true });
  await rm(`${testDatabase}-shm`, { force: true });
});
