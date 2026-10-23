import { writeFileSync } from "node:fs";
import { defineTool, toolOutput } from "eve/tools";
import { always } from "eve/tools/approval";
import { z } from "zod";
import { beginOperation, completeOperation, getOperation } from "../../prototype-store.mjs";
import { guardedToolCall, guardedToolResult } from "../../security-guard.mjs";

export const operationInputSchema = z.object({
  executionId: z.string().min(1),
  operationId: z.string().min(1),
  mode: z.enum(["vertical", "approved", "replay"]),
});

export const operationOutputSchema = z.object({
  executionId: z.string(),
  operationId: z.string(),
  operationStatus: z.string(),
  invocationCount: z.number(),
  effectCount: z.number(),
  effectPath: z.string(),
  observedFileContents: z.string(),
  workspace: z.object({
    hostCheckoutVisible: z.boolean(),
    dockerSocketVisible: z.boolean(),
    credentialLikeEnvironmentNames: z.array(z.string()),
    currentDirectory: z.string(),
  }),
  requestedNetworkPolicy: z.literal("deny-all"),
  fixture: z.literal("scripted-ai-sdk-language-model"),
});

function shellQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export async function performOperation({ executionId, operationId, mode }, ctx) {
  if (operationId !== `${executionId}:workspace-effect`) {
    throw new Error("Probe operation identity is not the stable execution-scoped key.");
  }

  const sandbox = await ctx.getSandbox();
  const effectPath = `probe/${Buffer.from(operationId).toString("hex").slice(0, 40)}.txt`;
  const script = `const fs=require('node:fs');const p=${JSON.stringify(process.env.PROTOTYPE_HOST_CHECKOUT ?? "")};const sensitive=Object.keys(process.env).filter((name)=>/(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|BWS)/i.test(name));console.log(JSON.stringify({hostCheckoutVisible:p?fs.existsSync(p):false,dockerSocketVisible:fs.existsSync('/var/run/docker.sock'),credentialLikeEnvironmentNames:sensitive,currentDirectory:process.cwd()}));`;
  const inspection = await sandbox.run({ command: `node -e ${shellQuote(script)}` });
  if (inspection.exitCode !== 0) throw new Error(`Workspace inspection failed: ${inspection.stderr}`);
  const workspace = JSON.parse(inspection.stdout.trim());

  const previous = beginOperation({ operationId, executionId, effectPath });
  let operation = getOperation(operationId);
  if (previous.status !== "completed") {
    await sandbox.writeTextFile({
      path: effectPath,
      content: `prototype-operation=${operationId}\nThis replaces one disposable file; it does not append.\n`,
    });
    const observed = await sandbox.readTextFile({ path: effectPath });
    if (!observed?.includes(`prototype-operation=${operationId}`)) {
      throw new Error("The disposable workspace effect was not observable after write.");
    }
    completeOperation(operationId, { executionId, operationId, effectPath, observedFileContents: observed });
    operation = getOperation(operationId);
  } else {
    const observed = await sandbox.readTextFile({ path: effectPath });
    if (!observed?.includes(`prototype-operation=${operationId}`)) {
      throw new Error("An idempotency record exists but its workspace checkpoint is missing.");
    }
  }

  const observedFileContents = await sandbox.readTextFile({ path: effectPath });
  if (!observedFileContents?.includes(`prototype-operation=${operationId}`)) {
    throw new Error("The execution-owned workspace file did not retain its operation identity.");
  }

  if (mode === "replay" && process.env.PROTOTYPE_CRASH_ONCE === executionId) {
    const markerPath = process.env.PROTOTYPE_CRASH_MARKER;
    if (!markerPath) throw new Error("Crash injection requires a trusted marker path.");
    // The eve CLI wraps the actual server: its exit code need not be SIGKILL.
    writeFileSync(markerPath, JSON.stringify({
      executionId, operationId, pid: process.pid,
      signal: "SIGKILL", stage: "after-effect-before-checkpoint",
    }), { mode: 0o600 });
    process.kill(process.pid, "SIGKILL");
  }

  return guardedToolResult({
    executionId,
    operationId,
    operationStatus: operation.status,
    invocationCount: operation.invocation_count,
    effectCount: operation.effect_count,
    effectPath,
    observedFileContents,
    workspace,
    requestedNetworkPolicy: "deny-all",
    fixture: "scripted-ai-sdk-language-model",
  });
}

export default defineTool({
  description: "Perform one disposable, stable-identity prototype workspace operation.",
  inputSchema: operationInputSchema,
  outputSchema: operationOutputSchema,
  approval: always(),
  async execute(input, ctx) {
    return guardedToolCall(() => performOperation(input, ctx));
  },
  toModelOutput(output) {
    return toolOutput.json(output);
  },
});
