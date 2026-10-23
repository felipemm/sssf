import { defineTool, toolOutput } from "eve/tools";
import { guardedToolCall } from "../../security-guard.mjs";
import { operationInputSchema, operationOutputSchema, performOperation } from "./workspace_operation.js";

export default defineTool({
  description: "Run the idempotent local replay fixture without a human approval pause.",
  inputSchema: operationInputSchema,
  outputSchema: operationOutputSchema,
  async execute(input, ctx) {
    return guardedToolCall(() => performOperation(input, ctx));
  },
  toModelOutput(output) {
    return toolOutput.json(output);
  },
});
