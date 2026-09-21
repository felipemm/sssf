/** Update checks for the sssf tool itself: `sssf upgrade --check` JSON. */

export interface UpdateReport {
  ok: boolean
  local_sha?: string
  remote_sha?: string
  ahead?: number
  behind?: number
  branch?: string
  dirty?: number
  command?: string
  repo?: string
  reason?: string
}

export interface Spawned {
  code: number
  out: string
}

export const CHECK_TIMEOUT_MS = 20_000;

/** Run `sssf upgrade --check` with a hard timeout — the CLI's git fetch must
 * never hang the viz. Bun.spawn has no timeout of its own, so kill the
 * process after the ceiling and report null (unreachable). */
export async function spawnCheck(timeoutMs = CHECK_TIMEOUT_MS): Promise<Spawned | null> {
  const proc = Bun.spawn(["sssf", "upgrade", "--check"], { stdout: "pipe", stderr: "pipe" });
  const timer = setTimeout(() => proc.kill(), timeoutMs);
  try {
    const out = await new Response(proc.stdout).text();
    const err = await new Response(proc.stderr).text();
    await proc.exited;
    return { code: proc.exitCode ?? 0, out: (out + err).trim() };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** The update report for the banner. A failed, timed-out or unreadable check
 * is reported with a reason and never with a behind count — the UI stays
 * silent instead of inventing an update state. */
export async function updateCheck(
  spawn: (timeoutMs: number) => Promise<Spawned | null> = spawnCheck,
): Promise<UpdateReport> {
  const r = await spawn(CHECK_TIMEOUT_MS);
  if (!r) return { ok: false, reason: "unreachable" };
  if (r.code !== 0) return { ok: false, reason: "cli-failed" };
  try {
    const data = JSON.parse(r.out) as Partial<UpdateReport>;
    if (typeof data.ok !== "boolean") return { ok: false, reason: "bad-response" };
    return data as UpdateReport;
  } catch {
    return { ok: false, reason: "bad-response" };
  }
}
