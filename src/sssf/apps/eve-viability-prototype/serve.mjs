import { startEve } from "./eve-process.mjs";

const port = Number(process.argv[2] ?? 3217);
const service = await startEve({ port, output: false });
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => service.child.kill(signal));
}
const result = await service.exit;
process.exitCode = result.code ?? (result.signal ? 1 : 0);
