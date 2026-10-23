import { defineTool } from "eve/tools";
import { z } from "zod";
import { guardedToolCall, guardedToolResult } from "../../security-guard.mjs";

const inputSchema = z.object({ executionId: z.string().min(1) });
const outputSchema = z.object({
  executionId: z.string(),
  secret: z.string(),
  fixture: z.literal("synthetic-secret-tool-result"),
});

export default defineTool({
  description: "Create a synthetic-only value inside trusted tool code to verify the result guard rejects it before EVE records or delivers it.",
  inputSchema,
  outputSchema,
  async execute({ executionId }) {
    return guardedToolCall(async () => {
      let values;
      try {
        values = JSON.parse(process.env.PROTOTYPE_SYNTHETIC_CANARIES ?? "[]");
      } catch {
        throw new Error("Synthetic security fixture configuration is invalid.");
      }
      if (!Array.isArray(values) || typeof values[0] !== "string" || values[0].length === 0) {
        throw new Error("Synthetic security fixture is not configured.");
      }
      return guardedToolResult({ executionId, secret: values[0], fixture: "synthetic-secret-tool-result" });
    });
  },
});
