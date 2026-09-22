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

// The machine fields the tracker reads (issue #94) — the shape the booted
// /tickets route must surface for a seeded db.
interface TicketRow {
  id: string;
  status: string;
  kind: string;
  tracked: boolean;
  origin: string;
  parent_id: string | null;
  spec: string;
}

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

  test("GET /tickets surfaces machine fields + lineage (issue #94 tracker data)", async () => {
    // Seed the boot project's trace db the way a real project looks after the
    // machine + sync landed: an untracked github issue (needs-triage), an
    // idea (needs-triage) and its implementation child (ready-for-agent).
    const db = new Database(join(root, "adws", "data", "sssf.db"));
    db.run(`CREATE TABLE IF NOT EXISTS tickets (
      id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
      title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'backlog',
      prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT,
      kind TEXT NOT NULL DEFAULT 'implementation', tracked INTEGER NOT NULL DEFAULT 1,
      origin TEXT NOT NULL DEFAULT 'internal', parent_id TEXT, spec TEXT NOT NULL DEFAULT '')`);
    db.run(`CREATE TABLE IF NOT EXISTS sessions (adw_id TEXT PRIMARY KEY, status TEXT)`);
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin, source_url) VALUES (?,?,?,?,?,?,?,?,?)")
      .run("github:acme/widgets#9", "github", "acme/widgets#9", "found in the wild", "needs-triage", "idea", 0, "github", "https://github.com/acme/widgets/issues/9");
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin) VALUES (?,?,?,?,?,?,?,?)")
      .run("internal:idea1", "internal", "", "the feature", "needs-triage", "idea", 1, "internal");
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, kind, tracked, origin, parent_id, spec) VALUES (?,?,?,?,?,?,?,?,?,?)")
      .run("internal:slice1", "internal", "", "build the feature", "ready-for-agent", "implementation", 1, "internal", "internal:idea1", "adws/specs/feat.md");
    // a queued ticket keeping an old failed session must stay queued
    db.query("INSERT INTO tickets (id, provider, external_id, title, status, adw_id) VALUES (?,?,?,?,?,?)")
      .run("internal:queued", "internal", "", "keeps history", "ready-for-agent", "sess_old");
    db.query("INSERT INTO sessions (adw_id, status) VALUES (?,?)").run("sess_old", "fail");
    db.close();

    const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/tickets`);
    expect(res.status).toBe(200);
    const data = await res.json();
    const byId = Object.fromEntries((data.tickets as TicketRow[]).map((t) => [t.id, t]));
    const untracked = byId["github:acme/widgets#9"];
    expect(untracked.tracked).toBe(false);
    expect(untracked.origin).toBe("github");
    expect(untracked.status).toBe("needs-triage");
    expect(byId["internal:slice1"].parent_id).toBe("internal:idea1");   // lineage
    expect(byId["internal:slice1"].kind).toBe("implementation");
    expect(byId["internal:slice1"].spec).toBe("adws/specs/feat.md");
    expect(byId["internal:queued"].status).toBe("ready-for-agent");     // machine state wins
  });

  test("the fake CLI was invoked once per action with the project root", () => {
    const calls = readFileSync(cliLog, "utf8").split("\n").filter(Boolean);
    expect(calls).toHaveLength(3);
    expect(calls).toContain(`ticket run ${TICKET} --project ${root}`);
    expect(calls).toContain(`ticket backlog ${TICKET} --project ${root}`);
    expect(calls).toContain(`ticket sync --project ${root}`);
  });
});
