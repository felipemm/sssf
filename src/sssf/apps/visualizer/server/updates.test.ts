import { describe, expect, test } from "bun:test";
import { updateCheck, type Spawned } from "./updates.ts";

function fakeSpawn(out: string, code = 0): (t: number) => Promise<Spawned | null> {
  return async (_t: number) => ({ code, out: out.trim() });
}

describe("updateCheck", () => {
  test("reports an update when the CLI says the local repo is behind", async () => {
    const r = await updateCheck(
      fakeSpawn(JSON.stringify({ ok: true, local_sha: "abc1234", remote_sha: "def5678", ahead: 0, behind: 3, branch: "main", dirty: 0, command: "sssf upgrade", repo: "/tmp/sssf" })),
    );
    expect(r.ok).toBe(true);
    expect(r.behind).toBe(3);
    expect(r.ahead).toBe(0);
    expect(r.remote_sha).toBe("def5678");
    expect(r.command).toBe("sssf upgrade");
  });

  test("passes through a clean (behind 0) report", async () => {
    const r = await updateCheck(
      fakeSpawn(JSON.stringify({ ok: true, local_sha: "abc1234", remote_sha: "abc1234", ahead: 0, behind: 0, branch: "main", dirty: 1, command: "sssf upgrade", repo: "/tmp/sssf" })),
    );
    expect(r.ok).toBe(true);
    expect(r.behind).toBe(0);
    expect(r.dirty).toBe(1);
  });

  test("a failing CLI run (rc != 0) is reported as cli-failed, never an update", async () => {
    const r = await updateCheck(fakeSpawn("boom", 1));
    expect(r).toEqual({ ok: false, reason: "cli-failed" });
  });

  test("a killed/timed-out spawn (null) is unreachable, not an update", async () => {
    const r = await updateCheck(async () => null);
    expect(r).toEqual({ ok: false, reason: "unreachable" });
  });

  test("non-JSON output is a bad response, never an update", async () => {
    const r = await updateCheck(fakeSpawn("this is not json"));
    expect(r).toEqual({ ok: false, reason: "bad-response" });
  });
});
