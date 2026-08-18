// Ticket action handlers, extracted from index.ts for unit-testability (audit
// B2): each shells out to the sssf CLI and surfaces stderr so failures are
// visible in the UI. The spawn function is injectable for tests.

export interface SpawnHandle {
  stdout: string | ReadableStream;
  stderr: string | ReadableStream;
  exitCode: number;
  exited: Promise<number>;
}

export type SpawnFn = (args: string[]) => SpawnHandle;

export function bunSpawn(args: string[]): SpawnHandle {
  const proc = Bun.spawn(args, { stdout: "pipe", stderr: "pipe" });
  return { stdout: proc.stdout, stderr: proc.stderr, exitCode: proc.exitCode, exited: proc.exited };
}

async function runCli(args: string[], spawnFn: SpawnFn): Promise<{ exitCode: number; output: string }> {
  const proc = spawnFn(args);
  const [output, errout] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  await proc.exited;
  return { exitCode: proc.exitCode, output: (output + (errout ? "\n" + errout : "")).trim() };
}

export interface TicketActionResult {
  ok: boolean;
  output: string;
  adwId?: string;
}

export async function syncTickets(root: string, spawnFn: SpawnFn = bunSpawn): Promise<TicketActionResult> {
  const { exitCode, output } = await runCli(["sssf", "ticket", "sync", "--project", root], spawnFn);
  return { ok: exitCode === 0, output };
}

export async function runTicket(root: string, id: string, spawnFn: SpawnFn = bunSpawn): Promise<TicketActionResult> {
  const { exitCode, output } = await runCli(["sssf", "ticket", "run", id, "--project", root], spawnFn);
  if (exitCode !== 0) return { ok: false, output };
  const adwId = output.match(/adw_id ([a-f0-9]+)/)?.[1] ?? null;
  return { ok: true, adwId: adwId ?? undefined, output };
}

export async function backlogTicket(root: string, id: string, spawnFn: SpawnFn = bunSpawn): Promise<TicketActionResult> {
  const { exitCode, output } = await runCli(["sssf", "ticket", "backlog", id, "--project", root], spawnFn);
  return { ok: exitCode === 0, output };
}
