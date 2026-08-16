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
      dockerPs: async () => "sssf-run1 Up 2 minutes\nsssf-orphanx Up 1 hour",
    });
    expect(data.kpis.runningSessions).toBe(1);
    expect(data.kpis.liveContainers).toBe(2);
    expect(data.kpis.orphanContainers).toBe(1);
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
