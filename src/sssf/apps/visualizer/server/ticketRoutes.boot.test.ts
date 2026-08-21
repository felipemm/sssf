/**
 * Boots the real visualizer server (server/index.ts) against a scratch
 * project and drives the ticket action routes through the actual Bun.serve
 * wiring.
 *
 * Regression for the Aug-2026 wiring bug: commit 32eb4be extracted the ticket
 * action handlers into ticketRoutes.ts but never imported them in index.ts, so
 * POST /api/projects/:project/tickets/:id/run threw
 * `ReferenceError: runTicket is not defined` — the kanban board showed
 * "run failed" and no run ever spawned. The unit tests in ticketRoutes.test.ts
 * inject a fake spawn into ticketRoutes.ts directly and could not see the
 * broken import; this test goes through the booted server instead, with a
 * fake `sssf` CLI on PATH standing in for the real installation.
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { spawn, type ChildProcess } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Database } from "bun:sqlite";

const SERVER_ENTRY = resolve(import.meta.dir, "index.ts");
const PROJECT = "bootproj";
const TICKET = "internal:boot";
const TICKET_ENCODED = encodeURIComponent(TICKET); // the frontend URL-encodes ids
const ADW_ID = "cafebabe1234";

let tmp: string;
let root: string;
let cliLog: string;
let port: number;
let server: ChildProcess | undefined;
let serverLog = "";

const baseUrl = () => `http://localhost:${port}`;

async function waitForServer(timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/tickets`);
      if (res.status === 200) return;
    } catch {
      // not up yet — keep polling
    }
    await Bun.sleep(200);
  }
  throw new Error(`server did not come up in ${timeoutMs}ms — log:\n${serverLog}`);
}

beforeAll(async () => {
  tmp = mkdtempSync(join(tmpdir(), "sssf-boot-"));
  root = join(tmp, "proj");
  cliLog = join(tmp, "sssf.calls");
  const fakeBin = join(tmp, "bin");
  mkdirSync(join(root, "adws", "config"), { recursive: true });
  mkdirSync(join(root, "adws", "data"), { recursive: true });
  mkdirSync(fakeBin);

  // Ticketing enabled for the scratch project (v2 layout).
  writeFileSync(
    join(root, "adws", "config", "ticketing.yaml"),
    "providers:\n  - internal\n",
  );

  // A real, openable trace db — left schema-less so readTickets' own
  // CREATE TABLE IF NOT EXISTS ddl is the one that runs (a partial tickets
  // table would make its SELECT fail on the missing columns).
  const db = new Database(join(root, "adws", "data", "sssf.db"));
  db.close();

  // Registry pointing at the scratch project.
  writeFileSync(
    join(tmp, "registry.json"),
    JSON.stringify({
      version: 1,
      projects: [{ name: PROJECT, root, db: join(root, "adws", "data", "sssf.db") }],
    }),
  );

  // A fake `sssf` CLI so the routes' Bun.spawn calls never reach the real
  // installation. It records every argv line and answers like the CLI does.
  const script = [
    "#!/bin/sh",
    `printf '%s\\n' "$*" >> "${cliLog}"`,
    `if [ "$1" = "ticket" ] && [ "$2" = "run" ]; then`,
    `  echo "sssf ticket: run spawned for $3 — adw_id ${ADW_ID}"`,
    "else",
    '  echo "ok"',
    "fi",
    "exit 0",
  ].join("\n");
  writeFileSync(join(fakeBin, "sssf"), `${script}\n`);
  chmodSync(join(fakeBin, "sssf"), 0o755);

  port = 40_000 + Math.floor(Math.random() * 20_000);
  server = spawn(process.execPath, [SERVER_ENTRY], {
    env: {
      ...process.env,
      SSSF_REGISTRY: join(tmp, "registry.json"),
      PORT: String(port),
      PATH: `${fakeBin}:${process.env.PATH ?? ""}`,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  server.stdout!.on("data", (d: Buffer) => (serverLog += d.toString()));
  server.stderr!.on("data", (d: Buffer) => (serverLog += d.toString()));

  await waitForServer();
});

afterAll(() => {
  server?.kill("SIGTERM");
  if (tmp) rmSync(tmp, { recursive: true, force: true });
});

describe("booted ticket routes (regression: ticketRoutes import in index.ts)", () => {
  test("the board route reads the backlog — wiring intact", async () => {
    const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/tickets`);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.enabled).toBe(true);
    expect(Array.isArray(data.tickets)).toBe(true);
  });

  test("POST /tickets/:id/run reaches the CLI and surfaces the adw_id", async () => {
    const res = await fetch(
      `${baseUrl()}/api/projects/${PROJECT}/tickets/${TICKET_ENCODED}/run`,
      { method: "POST" },
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.adwId).toBe(ADW_ID);
  });

  test("POST /tickets/:id/backlog reaches the CLI", async () => {
    const res = await fetch(
      `${baseUrl()}/api/projects/${PROJECT}/tickets/${TICKET_ENCODED}/backlog`,
      { method: "POST" },
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.ok).toBe(true);
  });

  test("POST /tickets/sync reaches the CLI", async () => {
    const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/tickets/sync`, {
      method: "POST",
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.ok).toBe(true);
  });

  test("the fake CLI was invoked once per action with the project root", () => {
    const calls = readFileSync(cliLog, "utf8").split("\n").filter(Boolean);
    expect(calls).toHaveLength(3);
    expect(calls).toContain(`ticket run ${TICKET} --project ${root}`);
    expect(calls).toContain(`ticket backlog ${TICKET} --project ${root}`);
    expect(calls).toContain(`ticket sync --project ${root}`);
  });
});
