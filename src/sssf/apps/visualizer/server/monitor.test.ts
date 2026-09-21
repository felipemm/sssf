import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { sweepMonitor, sweepMonitorAll, readMonitorConfig, monitorStatus } from "./monitor.ts";
import type { MrStateResult } from "./gitlab.ts";
import { ProjectRegistry } from "./registry.ts";

const DEPLOY = "ready-to-deploy";

function makeProject(tag: string): { root: string; dbPath: string } {
  const root = mkdtempSync(join(tmpdir(), `monitor-${tag}-`));
  mkdirSync(join(root, "adws", "config"), { recursive: true });
  mkdirSync(join(root, "adws", "data"), { recursive: true });
  writeFileSync(
    join(root, "adws", "config", "monitor.json"),
    JSON.stringify({ gitlab_url: "https://gitlab.example.com", token_env: "GITLAB_TOKEN" }),
  );
  const dbPath = join(root, "adws", "data", "sssf.db");
  const db = new Database(dbPath);
  db.run(`CREATE TABLE tickets (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT,
    title TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'needs-triage',
    prompt_file TEXT, adw_id TEXT, source_url TEXT, created_at TEXT, updated_at TEXT)`);
  db.run(`CREATE TABLE ticket_mrs (
    ticket_id TEXT PRIMARY KEY, repo TEXT NOT NULL, iid TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', created_at TEXT)`);
  db.close();
  return { root, dbPath };
}

function addTicket(dbPath: string, id: string, status: string, title = id): void {
  const db = new Database(dbPath);
  db.query("INSERT INTO tickets (id, provider, external_id, title, status) VALUES (?,?,?,?,?)")
    .run(id, "internal", "", title, status);
  db.close();
}

function addMr(dbPath: string, ticketId: string, repo: string, iid: string, url = ""): void {
  const db = new Database(dbPath);
  db.query("INSERT INTO ticket_mrs (ticket_id, repo, iid, url) VALUES (?,?,?,?)"
    + " ON CONFLICT(ticket_id) DO UPDATE SET repo=excluded.repo, iid=excluded.iid, url=excluded.url")
    .run(ticketId, repo, iid, url);
  db.close();
}

function readState(dbPath: string): Array<{ ticket_id: string; repo: string; iid: string; pipeline: string; state: string }> {
  const db = new Database(dbPath, { readonly: true });
  const rows = db.query("SELECT ticket_id, repo, iid, pipeline, state FROM monitor_state").all() as Array<{
    ticket_id: string; repo: string; iid: string; pipeline: string; state: string;
  }>;
  db.close();
  return rows;
}

interface FakeMr {
  repo: string;
  iid: string;
  result: MrStateResult | null;
  calls: number;
}

function fakeFetch(plans: FakeMr[]) {
  const fn = async (opts: { repo: string; iid: string }): Promise<MrStateResult | null> => {
    const plan = plans.find((p) => p.repo === opts.repo && p.iid === opts.iid);
    if (!plan) return null;
    plan.calls++;
    return plan.result;
  };
  return { fn, plans };
}

function noopNotify(): Promise<void> {
  return Promise.resolve();
}

function setup(tag: string, deps = {}) {
  const { root, dbPath } = makeProject(tag);
  return {
    root,
    dbPath,
    deps: { token: () => "glpat-x", notify: noopNotify, now: () => "2026-09-21T12:00:00.000Z", ...deps },
  };
}

describe("readMonitorConfig", () => {
  test("parses gitlab_url and token_env", () => {
    const { root } = setup("cfg");
    const cfg = readMonitorConfig(root);
    expect(cfg).toEqual({ gitlabUrl: "https://gitlab.example.com", tokenEnv: "GITLAB_TOKEN" });
  });

  test("null when the config file is missing", () => {
    const { root } = makeProject("nocfg");
    rmSync(join(root, "adws", "config", "monitor.json"));
    expect(readMonitorConfig(root)).toBeNull();
  });
});

describe("sweepMonitor", () => {
  test("scans only ready-to-deploy tickets with a registered MR", async () => {
    const { root, dbPath, deps } = setup("scan");
    addTicket(dbPath, "t-deploy", DEPLOY);
    addTicket(dbPath, "t-other", "in-progress");
    addTicket(dbPath, "t-nomr", DEPLOY);
    addMr(dbPath, "t-deploy", "group/a", "1");
    addMr(dbPath, "t-other", "group/b", "2"); // MR exists but ticket not deployable
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "running", state: "open" }, calls: 0 }]);
    const res = await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn });
    expect(res.watched).toBe(1);
    expect(fetchMrState.plans[0]!.calls).toBe(1);
    expect(res.notified).toBe(0);
  });

  test("notifies green once — the second sweep is silent", async () => {
    const { root, dbPath, deps } = setup("green");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "success", state: "open" }, calls: 0 }]);
    const notified: Array<{ ticket: string; text: string }> = [];
    const notify = (ticket: string, text: string) => { notified.push({ ticket, text }); return Promise.resolve(); };
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    expect(notified).toHaveLength(1);
    expect(notified[0]!.ticket).toBe("t1");
    expect(notified[0]!.text).toMatch(/green/);
  });

  test("notifies failed on a failed pipeline, once", async () => {
    const { root, dbPath, deps } = setup("failed");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "failed", state: "open" }, calls: 0 }]);
    const notified: string[] = [];
    const notify = (_t: string, text: string) => { notified.push(text); return Promise.resolve(); };
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    expect(notified).toHaveLength(1);
    expect(notified[0]).toMatch(/failed/);
  });

  test("notifies merged when the MR merges", async () => {
    const { root, dbPath, deps } = setup("merged");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "success", state: "merged" }, calls: 0 }]);
    const notified: string[] = [];
    const notify = (_t: string, text: string) => { notified.push(text); return Promise.resolve(); };
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    expect(notified).toHaveLength(1);
    expect(notified[0]).toMatch(/merged/);
  });

  test("running pipeline never notifies", async () => {
    const { root, dbPath, deps } = setup("running");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "running", state: "open" }, calls: 0 }]);
    const notified: string[] = [];
    const notify = (_t: string, text: string) => { notified.push(text); return Promise.resolve(); };
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify });
    expect(notified).toHaveLength(0);
  });

  test("a replaced MR resets the state — the new MR alerts again", async () => {
    const { root, dbPath, deps } = setup("replaced");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "failed", state: "open" }, calls: 0 }]);
    const notified: string[] = [];
    const notify = (_t: string, text: string) => { notified.push(text); return Promise.resolve(); };
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn, notify }); // failed alert
    addMr(dbPath, "t1", "group/a", "2"); // deploy flow re-opened with a new MR
    const fetchMrState2 = fakeFetch([{ repo: "group/a", iid: "2", result: { pipeline: "success", state: "open" }, calls: 0 }]);
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState2.fn, notify });
    expect(notified).toHaveLength(2);
    expect(notified[1]).toMatch(/green/);
  });

  test("stores the last-seen state in monitor_state", async () => {
    const { root, dbPath, deps } = setup("state");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "running", state: "open" }, calls: 0 }]);
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn });
    const rows = readState(dbPath);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ ticket_id: "t1", repo: "group/a", iid: "1", pipeline: "running", state: "open" });
  });

  test("unconfigured project reports an error, never crashes", async () => {
    const { root } = makeProject("unconf");
    rmSync(join(root, "adws", "config", "monitor.json"));
    const dbPath = join(root, "adws", "data", "sssf.db");
    const res = await sweepMonitor(dbPath, root, { token: () => "", notify: noopNotify });
    expect(res.error).toMatch(/not configured/);
    expect(res.watched).toBe(0);
  });

  test("a 404 from gitlab skips the ticket without crashing", async () => {
    const { root, dbPath, deps } = setup("gone");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: null, calls: 0 }]);
    const res = await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn });
    expect(res.watched).toBe(1);
    expect(res.notified).toBe(0);
    expect(res.error).toBeUndefined();
    expect(readState(dbPath)).toHaveLength(0); // nothing recorded for a gone MR
  });
});

describe("monitorStatus", () => {
  test("reports config and last-seen states", async () => {
    const { root, dbPath, deps } = setup("status");
    addTicket(dbPath, "t1", DEPLOY);
    addMr(dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "success", state: "open" }, calls: 0 }]);
    await sweepMonitor(dbPath, root, { ...deps, fetchMrState: fetchMrState.fn });
    const status = monitorStatus(dbPath, root);
    expect(status.configured).toBe(true);
    expect(status.gitlabUrl).toBe("https://gitlab.example.com");
    expect(status.states).toHaveLength(1);
    expect(status.states[0]).toMatchObject({ ticket_id: "t1", pipeline: "success", state: "open" });
  });

  test("unconfigured project reads as not configured", () => {
    const { root } = makeProject("status-nc");
    rmSync(join(root, "adws", "config", "monitor.json"));
    const status = monitorStatus(join(root, "adws", "data", "sssf.db"), root);
    expect(status.configured).toBe(false);
    expect(status.states).toEqual([]);
  });
});

describe("sweepMonitorAll", () => {
  test("sweeps every registered project", async () => {
    const a = setup("all-a");
    const b = setup("all-b");
    addTicket(a.dbPath, "t1", DEPLOY);
    addMr(a.dbPath, "t1", "group/a", "1");
    const fetchMrState = fakeFetch([{ repo: "group/a", iid: "1", result: { pipeline: "success", state: "open" }, calls: 0 }]);
    const regPath = join(a.root, "projects.json");
    writeFileSync(
      regPath,
      JSON.stringify({
        projects: [
          { name: "a", root: a.root, db: a.dbPath, lastRun: null },
          { name: "b", root: b.root, db: b.dbPath, lastRun: null },
        ],
      }),
    );
    const registry = new ProjectRegistry(regPath);
    const results = await sweepMonitorAll(registry, null, { ...a.deps, fetchMrState: fetchMrState.fn });
    expect(results).toHaveLength(2);
    expect(results.find((r) => r.project === "a")!.notified).toBe(1);
    expect(results.find((r) => r.project === "b")!.watched).toBe(0);
  });
});
