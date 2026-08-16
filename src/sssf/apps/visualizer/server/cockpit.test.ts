import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Database } from "bun:sqlite";
import { ProjectRegistry } from "./registry.ts";
import { computeCockpit } from "./cockpit.ts";

function fakeDb(dir: string): string {
  const path = join(dir, "adws", "adw_data", "sssf.db");
  mkdirSync(join(dir, "adws", "adw_data"), { recursive: true });
  const db = new Database(path);
  db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, started_at TEXT,
          ended_at TEXT, total_cost REAL, total_tokens INTEGER, archived INTEGER DEFAULT 0)`);
  db.run(`CREATE TABLE phases (phase_id TEXT PRIMARY KEY, adw_id TEXT, status TEXT, started_at TEXT)`);
  db.run(`CREATE TABLE events (event_id TEXT PRIMARY KEY, adw_id TEXT, type TEXT, started_at TEXT)`);
  db.run(`CREATE TABLE tickets (id TEXT PRIMARY KEY, status TEXT, adw_id TEXT, updated_at TEXT)`);
  db.close();
  return path;
}

function makeEnv() {
  const root = mkdtempSync(join(tmpdir(), "cockpit-"));
  const home = join(root, ".sssf");
  mkdirSync(home, { recursive: true });
  const regPath = join(home, "projects.json");
  const a = join(root, "proj-a");
  mkdirSync(a, { recursive: true });
  fakeDb(a);
  const b = join(root, "proj-b");
  mkdirSync(b, { recursive: true });
  fakeDb(b);
  const da = new Database(join(a, "adws", "adw_data", "sssf.db"));
  da.run(`INSERT INTO sessions VALUES ('run1','running','2026-08-16T10:00:00',NULL,0.5,100,0)`);
  da.run(`INSERT INTO sessions VALUES ('done1','success','2026-08-16T09:00:00','2026-08-16T09:30:00',1.2,200,0)`);
  da.run(`INSERT INTO phases VALUES ('ph1','run1','running','2026-08-16T10:00:00')`);
  da.run(`INSERT INTO events VALUES ('e1','run1','agent_end','2026-08-16T10:05:00')`);
  da.run(`INSERT INTO tickets VALUES ('internal:t1','backlog',NULL,'2026-08-16T08:00:00')`);
  da.close();
  mkdirSync(join(home, "sandboxes", "proj-a", "run1"), { recursive: true });
  writeFileSync(join(home, "heal-state.json"), '{"restarts": {"run1": 2}}');
  writeFileSync(join(home, "heal.log"), "h1\nh2\nh3\nh4\nh5\nh6\n");
  writeFileSync(
    regPath,
    JSON.stringify({
      projects: [
        { name: "proj-a", root: a, db: join(a, "adws", "adw_data", "sssf.db"), lastRun: null },
        { name: "proj-b", root: b, db: join(b, "adws", "adw_data", "sssf.db"), lastRun: null },
      ],
    }),
  );
  return { root, home, registry: new ProjectRegistry(regPath) };
}

describe("computeCockpit", () => {
  test("aggregates kpis, projects, running, heal, activity", async () => {
    const env = makeEnv();
    const data = await computeCockpit({
      registry: env.registry,
      sssfHome: env.home,
      dockerPs: async () => "sssf-run1\tsssf-runner:latest\tUp 2 minutes\t2026-08-16 15:00:00 +0000 UTC\nsssf-orphanx\tsssf-runner:latest\tUp 1 hour\t2026-08-16 14:00:00 +0000 UTC",
    });
    expect(data.kpis.dockerOk).toBe(true);
    expect(data.kpis.runningSessions).toBe(1);
    expect(data.kpis.liveContainers).toBe(2);
    expect(data.kpis.orphanContainers).toBe(1);
    expect(data.containers.length).toBe(2);
    expect(data.containers[0]!.name).toBe("sssf-run1");
    expect(data.containers[0]!.project).toBe("proj-a");
    expect(data.containers[0]!.image).toBe("sssf-runner:latest");
    expect(data.containers[0]!.running).toBe(true);
    expect(data.containers.find((c) => c.adwId === "orphanx")!.project).toBe("");
    expect(data.kpis.sandboxWorktrees).toBe(1);
    expect(data.kpis.ticketsInFlight).toBe(0);
    expect(data.kpis.costTodayUsd).toBeGreaterThan(0);
    expect(data.kpis.healRunning).toBe(false);
    const pa = data.projects.find((p) => p.name === "proj-a")!;
    expect(pa.sessionsRunning).toBe(1);
    expect(pa.sessionsToday).toBe(2);
    expect(pa.ticketsBacklog).toBe(1);
    expect(pa.containers).toBe(1); // sssf-run1 owned by proj-a
    expect(data.projects.find((p) => p.name === "proj-b")!.containers).toBe(0);
    expect(data.running[0]!.adwId).toBe("run1");
    expect(data.running[0]!.project).toBe("proj-a");
    expect(data.running[0]!.phase).toBe("ph1");
    expect(data.heal.restarts).toEqual({ run1: 2 });
    expect(data.heal.logTail).toEqual(["h2", "h3", "h4", "h5", "h6"]); // last 5 of 6 lines
    expect(data.activity[0]!.event).toBe("agent_end");
    rmSync(env.root, { recursive: true, force: true });
  });

  test("broken db renders zeros + stale, never throws", async () => {
    const env = makeEnv();
    rmSync(join(env.root, "proj-a", "adws", "adw_data", "sssf.db"));
    const data = await computeCockpit({
      registry: env.registry,
      sssfHome: env.home,
      dockerPs: async () => "",
    });
    const pa = data.projects.find((p) => p.name === "proj-a")!;
    expect(pa.stale).toBe(true);
    expect(pa.sessionsRunning).toBe(0);
    expect(data.projects.length).toBe(2); // both projects still listed
    rmSync(env.root, { recursive: true, force: true });
  });

  test("docker down → dockerOk false, containers [], error surfaced, projects still listed", async () => {
    const env = makeEnv();
    const data = await computeCockpit({
      registry: env.registry,
      sssfHome: env.home,
      dockerPs: async () => {
        throw new Error("Cannot connect to the Docker daemon at unix:///var/run/docker.sock");
      },
    });
    expect(data.kpis.dockerOk).toBe(false);
    expect(data.kpis.dockerError).toContain("Cannot connect to the Docker daemon");
    expect(data.kpis.liveContainers).toBe(0);
    expect(data.containers).toEqual([]);
    expect(data.projects.length).toBe(2); // other projects still aggregate
    rmSync(env.root, { recursive: true, force: true });
  });

  test("empty db (no tables) renders zeros without stale", async () => {
    const env = makeEnv();
    // proj-b's db file is empty (no tables yet — a freshly registered project)
    const dbPath = join(env.root, "proj-b", "adws", "adw_data", "sssf.db");
    const db = new Database(dbPath);
    db.close();
    const data = await computeCockpit({
      registry: env.registry,
      sssfHome: env.home,
      dockerPs: async () => "",
    });
    const pb = data.projects.find((p) => p.name === "proj-b")!;
    expect(pb.stale).toBe(false);
    expect(pb.sessionsToday).toBe(0);
    expect(existsSync(dbPath)).toBe(true);
    rmSync(env.root, { recursive: true, force: true });
  });
});

import { handleControl } from "./cockpit.ts";

describe("handleControl", () => {
  test("add validates the dir before spawning", async () => {
    const env = makeEnv();
    const calls: string[][] = [];
    const spawn = async (a: string[]) => {
      calls.push(a);
      return { code: 0, out: "ok" };
    };
    const bad = await handleControl("add", { root: join(env.root, "no-such") },
      { registry: env.registry, sssfHome: env.home, spawnCli: spawn });
    expect(bad.ok).toBe(false);
    expect(calls.length).toBe(0); // never spawned for a missing dir
    const good = await handleControl("add", { root: join(env.root, "proj-b") },
      { registry: env.registry, sssfHome: env.home, spawnCli: spawn });
    expect(good.ok).toBe(true);
    expect(calls[0]).toEqual(["projects", "add", join(env.root, "proj-b")]);
    rmSync(env.root, { recursive: true, force: true });
  });

  test("add requires an adws/ directory", async () => {
    const env = makeEnv();
    const calls: string[][] = [];
    const bare = join(env.root, "not-a-project");
    mkdirSync(bare, { recursive: true }); // exists but has no adws/
    const res = await handleControl("add", { root: bare },
      { registry: env.registry, sssfHome: env.home, spawnCli: async (a) => { calls.push(a); return { code: 0, out: "" }; } });
    expect(res.ok).toBe(false);
    expect(calls.length).toBe(0);
    rmSync(env.root, { recursive: true, force: true });
  });

  test("remove requires confirm", async () => {
    const env = makeEnv();
    const calls: string[][] = [];
    const spawn = async (a: string[]) => { calls.push(a); return { code: 0, out: "" }; };
    const no = await handleControl("remove", { project: "proj-a" },
      { registry: env.registry, sssfHome: env.home, spawnCli: spawn });
    expect(no.ok).toBe(false);
    const yes = await handleControl("remove", { project: "proj-a", confirm: true },
      { registry: env.registry, sssfHome: env.home, spawnCli: spawn });
    expect(yes.ok).toBe(true);
    expect(calls[0]).toEqual(["projects", "remove", "proj-a"]);
    rmSync(env.root, { recursive: true, force: true });
  });

  test("refresh spawns init --refresh --auto at the project root", async () => {
    const env = makeEnv();
    const calls: string[][] = [];
    const res = await handleControl("refresh", { project: "proj-a" },
      { registry: env.registry, sssfHome: env.home, spawnCli: async (a) => { calls.push(a); return { code: 0, out: "" }; } });
    expect(res.ok).toBe(true);
    expect(calls[0]).toEqual(["init", "--refresh", "--auto", "--project", join(env.root, "proj-a")]);
    rmSync(env.root, { recursive: true, force: true });
  });

  test("non-zero cli exit surfaces the error", async () => {
    const env = makeEnv();
    const res = await handleControl("refresh", { project: "proj-a" },
      { registry: env.registry, sssfHome: env.home, spawnCli: async () => ({ code: 3, out: "boom" }) });
    expect(res.ok).toBe(false);
    expect(res.error).toBe("boom");
    rmSync(env.root, { recursive: true, force: true });
  });
});

import { containerLogs } from "./cockpit.ts";

describe("containerLogs", () => {
  test("rejects non-sssf names before spawning", async () => {
    const calls: string[][] = [];
    const res = await containerLogs("../../../etc/passwd", 100, async (a) => { calls.push(a); return ""; });
    expect(res.ok).toBe(false);
    expect(calls.length).toBe(0);
  });

  test("tails with clamped count + timestamps", async () => {
    const calls: string[][] = [];
    const res = await containerLogs("sssf-abc123", 9999, async (a) => { calls.push(a); return "l1\nl2\n"; });
    expect(res.ok).toBe(true);
    expect(res.lines).toEqual(["l1", "l2"]);
    expect(calls[0]).toEqual(["logs", "--tail", "500", "--timestamps", "sssf-abc123"]);
  });

  test("clamps below the floor", async () => {
    const calls: string[][] = [];
    await containerLogs("sssf-abc123", 2, async (a) => { calls.push(a); return ""; });
    expect(calls[0]).toEqual(["logs", "--tail", "10", "--timestamps", "sssf-abc123"]);
  });

  test("docker failure surfaces the error", async () => {
    const res = await containerLogs("sssf-abc123", 100, async () => { throw new Error("boom"); });
    expect(res.ok).toBe(false);
    expect(res.error).toBe("boom");
  });
});
