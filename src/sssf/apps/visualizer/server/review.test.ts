import { describe, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SssfDb } from "./db";

function makeDb(path: string): Database {
  const db = new Database(path);
  db.run(`CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT)`);
  db.run(`CREATE TABLE run_reviews (
    adw_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    host_port INTEGER, updated_at TEXT)`);
  return db;
}

describe("reviewFor", () => {
  test("returns the review record or null", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-review-"));
    const dbPath = join(dir, "sssf.db");
    const db = makeDb(dbPath);
    db.query("INSERT INTO run_reviews VALUES (?,?,?,?)").run("r1", "pending", 3456, "t");
    db.close();

    const ssfdb = new SssfDb(dbPath);
    expect(ssfdb.reviewFor("r1")).toEqual({ status: "pending", host_port: 3456 });
    expect(ssfdb.reviewFor("nope")).toBeNull();
  });

  test("missing run_reviews table yields null, never an error", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-review-"));
    const dbPath = join(dir, "sssf.db");
    new Database(dbPath).close();   // empty db — no run_reviews table
    const ssfdb = new SssfDb(dbPath);
    expect(ssfdb.reviewFor("anything")).toBeNull();
  });
});
