import { defineAgent } from "eve";
import { wrapLanguageModel } from "ai";
import { scriptedModel } from "../model-fixture.mjs";
import { securityModelMiddleware } from "../security-guard.mjs";

export default defineAgent({
  model: wrapLanguageModel({ model: scriptedModel, middleware: securityModelMiddleware }),
  modelContextWindowTokens: 8192,
  defaultTools: false,
  tool: false,
});
