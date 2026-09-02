import { describe, expect, test } from "bun:test";
import { backlogTicket, runTicket, setTicketContext, syncTickets, type SpawnFn } from "./ticketRoutes";

function fakeSpawn(stdout: string, stderr = "", exitCode = 0): SpawnFn {
  const calls: string[][] = [];
  const fn: SpawnFn = (args) => {
    calls.push(args);
    return {
      stdout,
      stderr,
      exitCode,
      exited: Promise.resolve(exitCode),
    };
  };
  (fn as { calls: string[][] }).calls = calls;
  return fn;
}

describe("runTicket", () => {
  test("success surfaces the adw_id from the spawn line", async () => {
    const spawn = fakeSpawn("sssf ticket: run spawned for internal:x — adw_id abc12345");
    const res = await runTicket("/proj", "internal:x", undefined, spawn);
    expect(res.ok).toBe(true);
    expect(res.adwId).toBe("abc12345");
    expect((spawn as { calls: string[][] }).calls[0]).toEqual([
      "sssf", "ticket", "run", "internal:x", "--project", "/proj",
    ]);
  });

  test("operator context is passed to the CLI via --context", async () => {
    const spawn = fakeSpawn("sssf ticket: run spawned for internal:x — adw_id abc12345");
    const res = await runTicket("/proj", "internal:x", "focus on the OAuth flow", spawn);
    expect(res.ok).toBe(true);
    expect((spawn as { calls: string[][] }).calls[0]).toEqual([
      "sssf", "ticket", "run", "internal:x", "--context", "focus on the OAuth flow", "--project", "/proj",
    ]);
  });

  test("failure surfaces stderr — never an empty run failed (audit B2)", async () => {
    const spawn = fakeSpawn("", "runner image 'sssf-runner' is stale — run sssf sandbox build", 1);
    const res = await runTicket("/proj", "internal:x", undefined, spawn);
    expect(res.ok).toBe(false);
    expect(res.output).toContain("runner image 'sssf-runner' is stale");
    expect(res.output).toContain("sssf sandbox build");
  });

  test("no adw_id on a bare success line", async () => {
    const res = await runTicket("/proj", "internal:x", undefined, fakeSpawn("ok"));
    expect(res.ok).toBe(true);
    expect(res.adwId).toBeUndefined();
  });
});

describe("syncTickets / backlogTicket", () => {
  test("sync success + stderr surfaced on failure", async () => {
    const ok = await syncTickets("/proj", fakeSpawn("synced"));
    expect(ok.ok).toBe(true);
    const bad = await syncTickets("/proj", fakeSpawn("", "ticketing not configured", 1));
    expect(bad.ok).toBe(false);
    expect(bad.output).toContain("ticketing not configured");
  });

  test("setTicketContext persists via sssf ticket context --set", async () => {
    const spawn = fakeSpawn("sssf ticket: context saved for internal:x");
    const res = await setTicketContext("/proj", "internal:x", "steer text", spawn);
    expect(res.ok).toBe(true);
    expect((spawn as { calls: string[][] }).calls[0]).toEqual([
      "sssf", "ticket", "context", "internal:x", "--set", "steer text", "--project", "/proj",
    ]);
  });

  test("backlog carries output on failure", async () => {
    const res = await backlogTicket("/proj", "internal:x", fakeSpawn("", "boom", 409));
    expect(res.ok).toBe(false);
    expect(res.output).toBe("boom");
  });
});
