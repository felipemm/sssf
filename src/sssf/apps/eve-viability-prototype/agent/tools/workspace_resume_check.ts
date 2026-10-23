import { defineTool, toolOutput } from "eve/tools";
import { z } from "zod";
import { getOperation } from "../../prototype-store.mjs";
import { guardedToolCall, guardedToolResult } from "../../security-guard.mjs";

const inputSchema = z.object({ executionId: z.string().min(1), mode: z.enum(["resume", "loss"]) });
const outputSchema = z.object({
  executionId: z.string(),
  status: z.enum(["restored", "human-blocked"]),
  workspaceRestored: z.boolean(),
  effectPath: z.string(),
  reason: z.string(),
  fixture: z.literal("real-eve-docker-workspace"),
});

function blocked(executionId, effectPath, reason) {
  return {
    executionId,
    status: "human-blocked",
    workspaceRestored: false,
    effectPath,
    reason,
    fixture: "real-eve-docker-workspace",
  };
}

export default defineTool({
  description: "Verify the execution-owned Docker workspace still contains its expected checkpoint; report human-blocked when the checkpoint cannot be observed.",
  inputSchema,
  outputSchema,
  async execute({ executionId }, ctx) {
    return guardedToolCall(async () => {
      const operation = getOperation(`${executionId}:workspace-effect`);
      if (!operation || operation.status !== "completed") {
        return guardedToolResult(blocked(executionId, "missing-operation", "No completed disposable operation is available to restore."));
      }

      let sandbox;
      try {
        sandbox = await ctx.getSandbox();
      } catch {
        return guardedToolResult(blocked(executionId, operation.effect_path, "The EVE provider could not resume the execution-owned workspace."));
      }

      let observed;
      try {
        observed = await sandbox.readTextFile({ path: operation.effect_path });
      } catch {
        return guardedToolResult(blocked(executionId, operation.effect_path, "The expected checkpoint file could not be read from the resumed workspace."));
      }
      const workspaceRestored = observed?.includes(`prototype-operation=${operation.operation_id}`) ?? false;
      if (!workspaceRestored) {
        return guardedToolResult(blocked(executionId, operation.effect_path, "The expected checkpoint is absent; a fresh workspace is not accepted as recovery."));
      }
      return guardedToolResult({
        executionId,
        status: "restored",
        workspaceRestored: true,
        effectPath: operation.effect_path,
        reason: "The expected stable-operation file was read from the execution-owned workspace.",
        fixture: "real-eve-docker-workspace",
      });
    });
  },
  toModelOutput(output) {
    return toolOutput.json(output);
  },
});
