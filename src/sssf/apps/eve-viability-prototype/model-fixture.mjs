import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { MockLanguageModelV3 } from "ai/test";

const usage = {
  inputTokens: { total: 0, noCache: 0, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 0, text: 0, reasoning: 0 },
};

function messageParts(message) {
  if (Array.isArray(message.content)) return message.content;
  if (message.role === "user" && typeof message.content === "string") return [{ type: "text", text: message.content }];
  return [];
}

function userText(prompt) {
  return prompt
    .filter((message) => message.role === "user")
    .flatMap(messageParts)
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n");
}

function toolResults(prompt) {
  return prompt
    .flatMap(messageParts)
    .filter((part) => part.type === "tool-result");
}

function lastMatch(text, pattern) {
  return [...text.matchAll(pattern)].at(-1)?.[1];
}

function readMode(text) {
  return lastMatch(text, /\bmode=([a-z-]+)/g) ?? "vertical";
}

function readExecutionId(text) {
  return lastMatch(text, /\bexecutionId=([a-zA-Z0-9_-]+)/g) ?? "missing-execution-id";
}

function readSyntheticSecret() {
  let values;
  try {
    values = JSON.parse(process.env.PROTOTYPE_SYNTHETIC_CANARIES ?? "[]");
  } catch {
    throw new Error("Synthetic security fixture configuration is invalid.");
  }
  if (!Array.isArray(values) || typeof values[0] !== "string" || values[0].length === 0) {
    throw new Error("Synthetic security fixture is not configured.");
  }
  return values[0];
}

function recordObservation(observation) {
  const path = process.env.PROTOTYPE_OBSERVATIONS;
  if (!path) return;
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, `${JSON.stringify(observation)}\n`, { mode: 0o600 });
}

function stream(parts) {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: "stream-start", warnings: [] });
      for (const part of parts) controller.enqueue(part);
      controller.enqueue({
        type: "finish",
        finishReason: { unified: parts.some((part) => part.type === "tool-call") ? "tool-calls" : "stop", raw: "fixture" },
        usage,
      });
      controller.close();
    },
  });
}

function toolCall(toolName, input, callId) {
  return [{ type: "tool-call", toolCallId: callId, toolName, input: JSON.stringify(input) }];
}

export const scriptedModel = new MockLanguageModelV3({
  provider: "sssf-test-fixture",
  modelId: "deterministic-scripted-ai-sdk-v3",
  async doStream({ prompt }) {
    const input = userText(prompt);
    const mode = readMode(input);
    const executionId = readExecutionId(input);
    const results = toolResults(prompt);
    const lastUserIndex = prompt.findLastIndex((message) => message.role === "user");
    const lastToolResultIndex = prompt.findLastIndex((message) =>
      messageParts(message).some((part) => part.type === "tool-result"),
    );
    const hasToolResult = lastToolResultIndex > lastUserIndex;
    const secret = ["secret-tool", "secret-provider", "secret-provider-error"].includes(mode) ? readSyntheticSecret() : undefined;
    const serializedPrompt = JSON.stringify(prompt);
    const serializedResults = JSON.stringify(results);
    const resultHasGuardError = /SENSITIVE_CONTENT_REJECTED|SecretBoundaryError/u.test(serializedResults);
    recordObservation({
      executionId,
      mode,
      requestKind: hasToolResult ? "post-tool-answer" : "initial-tool-request",
      toolResultContainsKnownSecret: secret ? serializedResults.includes(secret) : false,
      modelInputContainsKnownSecret: secret ? serializedPrompt.includes(secret) : false,
      resultHasGuardError,
      emitsSplitSecret: mode === "secret-provider" && !hasToolResult,
      emitsSensitiveProviderError: mode === "secret-provider-error" && !hasToolResult,
      observationContainsSecretBytes: false,
    });

    if (mode === "secret-provider-error" && !hasToolResult) {
      const nested = Object.assign(new Error("nested synthetic provider failure"), { request: { body: secret } });
      const providerError = new AggregateError([nested], "synthetic provider failure");
      providerError.response = { body: { nested: secret } };
      providerError.cause = Object.assign(new Error("synthetic nested cause"), { details: { secret } });
      providerError.self = providerError;
      throw providerError;
    }

    if (mode === "secret-provider" && !hasToolResult) {
      const split = Math.floor(secret.length / 2);
      return {
        stream: stream([
          { type: "text-start", id: `fixture-${executionId}-answer` },
          { type: "text-delta", id: `fixture-${executionId}-answer`, delta: `Synthetic model output: ${secret.slice(0, split)}` },
          { type: "text-delta", id: `fixture-${executionId}-answer`, delta: `${secret.slice(split)} is intentionally unsafe.` },
          { type: "text-end", id: `fixture-${executionId}-answer` },
        ]),
      };
    }

    if (mode === "secret-tool" && !hasToolResult) {
      return { stream: stream(toolCall("secret_tool_result", { executionId }, `fixture-${executionId}-secret`)) };
    }

    if (!hasToolResult) {
      if (mode === "resume" || mode === "loss") {
        return { stream: stream(toolCall("workspace_resume_check", { executionId, mode }, `fixture-${executionId}-${mode}`)) };
      }
      const toolName = mode === "approved" ? "approved_operation" : mode === "replay" ? "replay_operation" : "workspace_operation";
      return {
        stream: stream(toolCall(toolName, {
          executionId,
          operationId: `${executionId}:workspace-effect`,
          mode,
        }, `fixture-${executionId}-operation`)),
      };
    }

    const message = JSON.stringify({
      executionId,
      status: resultHasGuardError ? "human-blocked" : "tool-result-observed",
      model: "deterministic-scripted-ai-sdk-v3",
    });
    return {
      stream: stream([
        { type: "text-start", id: `fixture-${executionId}-answer` },
        { type: "text-delta", id: `fixture-${executionId}-answer`, delta: message },
        { type: "text-end", id: `fixture-${executionId}-answer` },
      ]),
    };
  },
});
