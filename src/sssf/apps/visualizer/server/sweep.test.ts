import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { dirname, join } from "path";
import { Database } from "bun:sqlite";
import { ProjectRegistry } from "./registry";
import { sweepAll, sweepDb } from "./sweep";

/** A minimal sessions table in the tracer's timestamp format (+00:00). */
function makeDb(path: string): void {
  const db = new Database(path);
  db.run(
    "CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT, ended_at TEXT, archived INTEGER DEFAULT 0)",
  );
  const iso = (msAgo: number) =>
    new Date(Date.now() - msAgo * 864e5).toISOString().replace("Z", "+00:00");
  const ins = db.query(
    "INSERT INTO sessions (adw_id, status, ended_at) VALUES (?,?,?)",
  );
  ins.run("old-success", "success", iso(40));
  ins.run("old-fail", "fail", iso(40));
  ins.run("recent", "success", iso(1));
  ins.run("running", "running", null);
  db.close();
}

describe("sweepDb", () => {
  test("archives old finished sessions, leaves recent and running", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-sweep-"));
    const db = join(dir, "sssf.db");
    makeDb(db);

    const archived = sweepDb(db, "-30 days");
    expect(archived).toBe(2);

    const check = new Database(db, { readonly: true });
    const rows = check
      .query<{ adw_id: string; archived: number }, []>("SELECT adw_id, archived FROM sessions")
      .all();
    const byId = Object.fromEntries(rows.map((r) => [r.adw_id, r.archived]));
    expect(byId["old-success"]).toBe(1);
    expect(byId["old-fail"]).toBe(1);
    expect(byId["recent"]).toBe(0);
    expect(byId["running"]).toBe(0);
    check.close();
  });

  test("missing db is a no-op, never an error", () => {
    expect(sweepDb(join(tmpdir(), "nope", "missing.db"))).toBe(0);
  });
});

describe("sweepAll", () => {
  test("sweeps every registered project", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-sweep-all-"));
    const a = join(dir, "a", "adws", "adw_data", "sssf.db");
    const b = join(dir, "b", "adws", "adw_data", "sssf.db");
    for (const p of [a, b]) {
      mkdirSync(dirname(p), { recursive: true });
      makeDb(p);
    }
    writeFileSync(
      join(dir, "projects.json"),
      JSON.stringify({
        version: 1,
        projects: [
          { name: "a", root: join(dir, "a"), db: a, lastRun: null },
          { name: "b", root: join(dir, "b"), db: b, lastRun: null },
          { name: "gone", root: join(dir, "gone"), db: join(dir, "gone", "x.db"), lastRun: null },
        ],
      }),
    );
    const results = sweepAll(new ProjectRegistry(join(dir, "projects.json")), null, "-30 days");
    const byProject = Object.fromEntries(results.map((r) => [r.project, r]));
    expect(byProject["a"].archived).toBe(2);
    expect(byProject["b"].archived).toBe(2);
    expect(byProject["gone"].archived).toBe(0);   // missing db — skipped, no error
    expect(byProject["gone"].error).toBeUndefined();
  });
});
