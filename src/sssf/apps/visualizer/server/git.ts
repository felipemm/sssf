/** Git repo stats + daily contributions for a project root, via `git -C`. */
import { spawnSync } from "node:child_process";

export interface GitContributor { name: string; commits: number }
export interface GitStats {
  commits: number;
  commits_30d: number;
  commits_year: number;
  contributors: GitContributor[];
  branches: number;
  current_branch: string | null;
  last_commit: { date: string; subject: string } | null;
  dirty: number;
  first_commit: string | null;
}
export interface ContributionDay { date: string; count: number }

const EMPTY: GitStats = {
  commits: 0, commits_30d: 0, commits_year: 0, contributors: [],
  branches: 0, current_branch: null, last_commit: null, dirty: 0, first_commit: null,
};

const DAY_MS = 86400_000;

/** Run a fixed git command; null on any failure (non-repo, no commits, etc). */
function run(root: string, args: string[]): { ok: true; out: string } | { ok: false } {
  const res = spawnSync("git", ["-C", root, ...args], { encoding: "utf8" });
  if (res.status !== 0) return { ok: false };
  return { ok: true, out: String(res.stdout ?? "").trim() };
}

/**
 * All committer timestamps (epoch seconds) reachable from HEAD.
 * We fetch every commit and filter in JS rather than using `--since`:
 * git prunes date-bounded walks at the first commit older than the boundary
 * (assuming ancestors are older), which returns nothing for repos whose HEAD
 * predates the window. Timestamps also let us render UTC dates, which
 * `--date=short` would format in the machine's local timezone.
 */
function allTimes(root: string): number[] {
  const r = run(root, ["log", "--format=%ct", "HEAD"]);
  if (!r.ok) return [];
  const times: number[] = [];
  for (const line of r.out.split("\n")) {
    if (line === "") continue;
    const n = Number(line);
    if (Number.isFinite(n)) times.push(n);
  }
  return times;
}

const utcDate = (ts: number): string => new Date(ts * 1000).toISOString().slice(0, 10);

export function gitStats(root: string): GitStats {
  try {
    const times = allTimes(root);
    const commits = times.length;
    if (commits === 0) return EMPTY;   // not a repo or no commits — nothing to show

    const now = Date.now();
    const cutoff30 = now - 30 * DAY_MS;
    const year = new Date().getUTCFullYear();
    let commits30d = 0;
    let commitsYear = 0;
    for (const t of times) {
      if (t * 1000 >= cutoff30) commits30d++;
      if (new Date(t * 1000).getUTCFullYear() === year) commitsYear++;
    }

    const contributors: GitContributor[] = [];
    const sl = run(root, ["shortlog", "-sne", "HEAD"]);
    if (sl.ok) {
      for (const line of sl.out.split("\n")) {
        const m = line.match(/^\s*(\d+)\s+(.+)$/);
        if (m) contributors.push({ name: m[2]!.trim(), commits: Number.parseInt(m[1]!, 10) });
      }
    }

    // `for-each-ref --count` takes an integer argument since git 2.44 (it was
    // never a bare flag), so count lines of `--format=%(refname)` instead.
    const br = run(root, ["for-each-ref", "--format=%(refname)", "refs/heads"]);
    const branches = br.ok && br.out.length > 0 ? br.out.split("\n").length : 0;
    const cb = run(root, ["branch", "--show-current"]);

    // "last commit" = the branch tip: `git log -1` walks from HEAD (topological
    // order on this git), not the newest commit by date.
    const last = run(root, ["log", "-1", "--format=%ct|%s", "HEAD"]);
    let lastCommit: GitStats["last_commit"] = null;
    if (last.ok) {
      const [ts, ...rest] = last.out.split("|");
      const n = Number(ts);
      if (Number.isFinite(n)) lastCommit = { date: utcDate(n), subject: rest.join("|") };
    }

    // oldest commit by date (root of the walk). `git log --reverse -1` would
    // return HEAD instead — the -1 limit is applied before the reversal.
    let firstCommit: string | null = null;
    if (commits > 0) {
      let min = Number.POSITIVE_INFINITY;
      for (const t of times) if (t < min) min = t;
      firstCommit = utcDate(min);
    }

    const st = run(root, ["status", "--porcelain"]);
    const dirty = st.ok ? st.out.split("\n").filter((l) => l.length > 0).length : 0;

    return {
      commits, commits_30d: commits30d, commits_year: commitsYear,
      contributors, branches, current_branch: cb.ok ? cb.out : null,
      last_commit: lastCommit, dirty, first_commit: firstCommit,
    };
  } catch {
    return EMPTY;
  }
}

export function contributions(root: string): ContributionDay[] {
  try {
    const times = allTimes(root);
    if (times.length === 0) return [];
    const now = Date.now();
    const since = new Date(now - 364 * DAY_MS).toISOString().slice(0, 10); // UTC
    const counts = new Map<string, number>();
    for (const t of times) {
      const d = utcDate(t);
      if (d >= since) counts.set(d, (counts.get(d) ?? 0) + 1);
    }
    const days: ContributionDay[] = [];
    for (let i = 363; i >= 0; i--) {
      const date = new Date(now - i * DAY_MS).toISOString().slice(0, 10);
      days.push({ date, count: counts.get(date) ?? 0 });
    }
    return days; // oldest first
  } catch {
    return [];
  }
}
