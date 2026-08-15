import { describe, expect, test } from "bun:test";
import { mkdtempSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { ProjectRegistry } from "./registry";

describe("ProjectRegistry", () => {
  test("reads projects.json and lists dbs", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-registry-"));
    const db = join(dir, "adws", "adw_data", "sssf.db");
    writeFileSync(
      join(dir, "projects.json"),
      JSON.stringify({
        version: 1,
        projects: [{ name: "repo-a", root: dir, db, lastRun: null }],
      }),
    );
    const reg = new ProjectRegistry(join(dir, "projects.json"));
    expect(reg.list()).toHaveLength(1);
    expect(reg.list()[0]!.name).toBe("repo-a");
    expect(reg.list()[0]!.db).toBe(db);
    // dbFor on a non-existent file yields null (no trace db yet).
    expect(reg.dbFor("repo-a")).toBeNull();
  });

  test("missing registry lists empty", () => {
    const dir = mkdtempSync(join(tmpdir(), "sssf-registry-"));
    const reg = new ProjectRegistry(join(dir, "missing.json"));
    expect(reg.list()).toEqual([]);
  });
});
