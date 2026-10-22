import { defineTool, toolOutput } from "eve/tools";
import { always } from "eve/tools/approval";
import { guardedToolCall } from "../../security-guard.mjs";
import { operationInputSchema, operationOutputSchema, performOperation } from "./workspace_operation.js";

export default defineTool({
  description: "Pause the deterministic prototype operation until a human approves it, then use a stable operation identity.",
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
