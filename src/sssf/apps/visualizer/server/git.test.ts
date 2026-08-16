import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gitStats, contributions } from "./git";

function git(root: string, args: string[], env: Record<string, string> = {}): void {
  const res = spawnSync("git", ["-C", root, ...args], { env: { ...process.env, ...env }, encoding: "utf8" });
  if (res.status !== 0) throw new Error(`git ${args.join(" ")} failed: ${res.stderr}`);
}

function makeRepo(daysAgo: number[], extraFile = false): { root: string; today: string } {
  const root = mkdtempSync(join(tmpdir(), "sssf-git-"));
  git(root, ["init", "-b", "main", "-q"]);
  git(root, ["config", "user.email", "t@t"]);
  git(root, ["config", "user.name", "Test"]);
  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  daysAgo.forEach((n, i) => {
    const when = new Date(Date.now() - n * 86400_000);
    writeFileSync(join(root, "f.txt"), `line ${i}\n`, { flag: "a" });
    const env = {
      GIT_AUTHOR_DATE: when.toISOString(),
      GIT_COMMITTER_DATE: when.toISOString(),
    };
    git(root, ["add", "."], env);
    git(root, ["commit", "-m", `c${i}`, "-q"], env);
  });
  if (extraFile) {
    writeFileSync(join(root, "dirty.txt"), "uncommitted\n");
  }
  return { root, today: iso(today) };
}

describe("gitStats", () => {
  test("parses commits, contributors, branches, last/first commit, dirty", () => {
    // 2 today, 1 yesterday, 1 40 days ago, 1 400 days ago (outside the 1-year window)
    const { root } = makeRepo([0, 0, 1, 40, 400]);
    const s = gitStats(root);
    expect(s.commits).toBe(5);
    expect(s.commits_30d).toBe(3);       // 2 today + 1 yesterday
    // commits in the current UTC year — expectation derived from the fixture's
    // own dates so the assertion survives year boundaries (e.g. January runs)
    const thisYear = new Date().getUTCFullYear();
    const expectedYear = [0, 0, 1, 40].filter(
      (n) => new Date(Date.now() - n * 86400_000).getUTCFullYear() === thisYear,
    ).length;
    expect(s.commits_year).toBe(expectedYear);
    expect(s.contributors).toEqual([{ name: "Test <t@t>", commits: 5 }]);
    expect(s.branches).toBe(1);
    expect(s.current_branch).toBe("main");
    // `git log -1` walks from HEAD (topological order on this git), so the "last"
    // commit is the branch tip — the last commit *created* (c4, 400 days ago),
    // not the newest by date. Its date is therefore 400 days ago, not today.
    expect(s.last_commit?.subject).toBe("c4");
    expect(s.last_commit?.date).toBe(new Date(Date.now() - 400 * 86400_000).toISOString().slice(0, 10));
    expect(s.first_commit).toBe(new Date(Date.now() - 400 * 86400_000).toISOString().slice(0, 10));
    expect(s.dirty).toBe(0);
  });

  test("dirty count and non-repo root degrade", () => {
    const { root } = makeRepo([0], true);
    expect(gitStats(root).dirty).toBe(1);
    const notRepo = mkdtempSync(join(tmpdir(), "sssf-git-"));
    const z = gitStats(notRepo);
    expect(z.commits).toBe(0);
    expect(z.contributors).toEqual([]);
    expect(z.last_commit).toBeNull();
    expect(z.current_branch).toBeNull();
  });
});

describe("contributions", () => {
  test("returns 364 days, counts commits per day, excludes out-of-window", () => {
    const { root } = makeRepo([0, 0, 1, 40, 400]);
    const days = contributions(root);
    expect(days).toHaveLength(364);
    const byDate = new Map(days.map((d) => [d.date, d.count]));
    const today = new Date(Date.now()).toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400_000).toISOString().slice(0, 10);
    const d40 = new Date(Date.now() - 40 * 86400_000).toISOString().slice(0, 10);
    const d400 = new Date(Date.now() - 400 * 86400_000).toISOString().slice(0, 10);
    expect(byDate.get(today)).toBe(2);
    expect(byDate.get(yesterday)).toBe(1);
    expect(byDate.get(d40)).toBe(1);
    expect(byDate.get(d400)).toBeUndefined();
  });

  test("non-repo root returns empty", () => {
    const notRepo = mkdtempSync(join(tmpdir(), "sssf-git-"));
    expect(contributions(notRepo)).toEqual([]);
  });
});
