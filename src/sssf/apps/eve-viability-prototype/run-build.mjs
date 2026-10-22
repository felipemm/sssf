import { buildEve } from "./eve-process.mjs";

try {
  await buildEve({ output: false, timeoutMs: 300_000 });
  console.log("EVE prototype build passed (clean child environment).");
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
