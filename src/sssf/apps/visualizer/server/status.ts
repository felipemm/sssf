/** Status dashboard: one aggregate payload per project, computed from the trace db. */
import { ticketingEnabled } from "./ticketing";
import { Database } from "bun:sqlite";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { contributions, gitStats } from "./git";
import type { ContributionDay, GitStats } from "./git";

export interface ProjectInfo {
  name: string;
  root: string;
  ticketing_enabled: boolean;
  last_run: string | null;   // most recent sessions.started_at (ISO); null when no runs
}

export interface Totals {
  runs: number;
  active: number;
  success: number;
  failed: number;
  archived: number;
  success_rate: number;      // success / (success + failed); 0 when none finished
  avg_duration_s: number;    // successful runs only
  total_cost: number;
  avg_cost_per_run: number;  // total_cost / runs (0 when no runs)
  total_tokens: number;
  avg_tokens_per_run: number;
}

export interface Quality {
  gate_pass_rate: number;    // ok checks / total checks in gate_results.checks_json
  hotspot_phase: string | null;
  hotspot_count: number;
  total_retries: number;
  failed_phases: number;
}

export interface AgentStat {
  role: string;
  model: string | null;      // most recent agent_sessions.model; null if never used
  sessions: number;          // distinct adw_ids (one agent_sessions row per run+agent) — displayed as 'runs'
  context_tokens: number;    // sum across rows
  tokens: number;         // actual tokens from agent_end events
  cost_actual: number;    // summed provider billing (agent_end payload .cost)
  cost_share: number;     // session cost apportioned by token share
}

export interface ModelStat {
  model: string;
  tokens: number;
  sessions: number;
  cost_actual: number;
  cost_share: number;
}

export interface TicketsCounts {
  backlog: number;
  running: number;
  done: number;
  failed: number;
}

export interface TrendBucket {
  day: string;               // YYYY-MM-DD (UTC, from started_at)
  runs: number;
  cost: number;
  tokens: number;
  success: number;           // finished-success sessions started that day
  fail: number;              // finished-fail sessions started that day
}

export interface StatusResponse {
  project: ProjectInfo;
  totals: Totals;
  quality: Quality;
  agents: AgentStat[];
  models: ModelStat[];
  tickets: TicketsCounts | null;
  trends: { window: number; buckets: TrendBucket[] };
  git: GitStats;
  contributions: ContributionDay[];
}

const AGENT_ROLES = ["planner", "builder", "reviewer", "documenter"];


export function computeStatus(dbPath: string, root: string, name: string, windowDays: number): StatusResponse {
  const db = new Database(dbPath);
  const empty: StatusResponse = {
    project: { name, root, ticketing_enabled: ticketingEnabled(root), last_run: null },
    totals: { runs: 0, active: 0, success: 0, failed: 0, archived: 0, success_rate: 0,
              avg_duration_s: 0, total_cost: 0, avg_cost_per_run: 0,
              total_tokens: 0, avg_tokens_per_run: 0 },
    quality: { gate_pass_rate: 0, hotspot_phase: null, hotspot_count: 0,
               total_retries: 0, failed_phases: 0 },
    agents: [],
    models: [],
    tickets: null,
    trends: { window: windowDays, buckets: [] },
    git: {
      commits: 0, commits_30d: 0, commits_year: 0, contributors: [],
      branches: 0, current_branch: null, last_commit: null, dirty: 0, first_commit: null,
    },
    contributions: [],
  };
  try {
    const has = (table: string): boolean =>
      (db.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(table) !== null);

    // ── totals ────────────────────────────────────────────────────────────
    const t = has("sessions")
      ? db.query<{ n: number; active: number; success: number; failed: number; archived: number;
                   total_cost: number; total_tokens: number;
                   avg_duration_s: number; } & Record<string, unknown>, []>(
        `SELECT COUNT(*) n,
                SUM(status='running') active,
                SUM(status='success') success,
                SUM(status='fail') failed,
                COALESCE(SUM(archived),0) archived,
                COALESCE(SUM(total_cost),0) total_cost,
                COALESCE(SUM(total_tokens),0) total_tokens,
                AVG(CASE WHEN status='success' AND ended_at IS NOT NULL
                         THEN (julianday(ended_at)-julianday(started_at))*86400 END) avg_duration_s
           FROM sessions`).get()!
      : null;
    const totals: Totals = t
      ? { runs: t.n, active: Number(t.active ?? 0), success: Number(t.success ?? 0),
          failed: Number(t.failed ?? 0), archived: Number(t.archived ?? 0),
          success_rate: (Number(t.success ?? 0) + Number(t.failed ?? 0)) > 0
            ? Number(t.success ?? 0) / (Number(t.success ?? 0) + Number(t.failed ?? 0)) : 0,
          avg_duration_s: t.avg_duration_s ?? 0,
          total_cost: Number(t.total_cost ?? 0),
          avg_cost_per_run: t.n > 0 ? Number(t.total_cost ?? 0) / t.n : 0,
          total_tokens: Number(t.total_tokens ?? 0),
          avg_tokens_per_run: t.n > 0 ? Math.round(Number(t.total_tokens ?? 0) / t.n) : 0 }
      : empty.totals;

    // ── quality ───────────────────────────────────────────────────────────
    let quality = empty.quality;
    if (has("phases")) {
      const failed = db.query<{ name: string; count: number }, []>(
        "SELECT name, COUNT(*) count FROM phases WHERE status='fail' GROUP BY name ORDER BY count DESC, name"
      ).all();
      const retries = db.query<{ r: number }, []>(
        "SELECT COALESCE(SUM(retries),0) r FROM phases"
      ).get()!;
      quality = {
        gate_pass_rate: 0,
        hotspot_phase: failed.length ? failed[0]!.name : null,
        hotspot_count: failed.length ? failed[0]!.count : 0,
        total_retries: Number(retries.r ?? 0),
        failed_phases: failed.reduce((n, f) => n + f.count, 0),
      };
    }
    if (has("gate_results")) {
      const rows = db.query<{ checks_json: string | null }, []>(
        "SELECT checks_json FROM gate_results"
      ).all();
      let ok = 0, total = 0;
      for (const row of rows) {
        if (!row.checks_json) continue;
        try {
          const checks = JSON.parse(row.checks_json) as { ok?: boolean }[];
          if (!Array.isArray(checks)) continue;
          for (const c of checks) { total++; if (c.ok) ok++; }
        } catch { /* unparseable checks_json — skip */ }
      }
      quality.gate_pass_rate = total > 0 ? ok / total : 0;
    }

    // ── agents: dynamic roles with cost attribution ──────────────────────
    const agents: AgentStat[] = [];
    const models: ModelStat[] = [];
    if (has("events") && has("phases")) {
      // per-role tokens + actual cost (agent_end events carry provider billing)
      const rows = db.query<{ agent: string; tokens: number; cost: number }, []>(
        `SELECT p.owner agent, SUM(e.tokens) tokens,
                SUM(json_extract(e.payload_json, '$.cost')) cost
           FROM events e JOIN phases p ON p.phase_id = e.phase_id
          WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL
          GROUP BY p.owner`,
      ).all();
      const costByAgent = new Map<string, number>();
      const tokensByAgent = new Map<string, number>();
      for (const row of rows) {
        tokensByAgent.set(row.agent, Number(row.tokens ?? 0));
        costByAgent.set(row.agent, Number(row.cost ?? 0));
      }
      // per-session agent tokens → token-share cost
      const perSession = db.query<{ adw_id: string; agent: string; tokens: number }, []>(
        `SELECT e.adw_id, p.owner agent, SUM(e.tokens) tokens
           FROM events e JOIN phases p ON p.phase_id = e.phase_id
          WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL
          GROUP BY e.adw_id, p.owner`,
      ).all();
      const sessionTotals = new Map<string, { cost: number; tokens: number }>();
      if (has("sessions")) {
        for (const s of db.query<{ adw_id: string; total_cost: number; total_tokens: number }, []>(
          "SELECT adw_id, total_cost, total_tokens FROM sessions",
        ).all()) {
          sessionTotals.set(s.adw_id, { cost: Number(s.total_cost ?? 0), tokens: Number(s.total_tokens ?? 0) });
        }
      }
      const shareByAgent = new Map<string, number>();
      for (const row of perSession) {
        const tot = sessionTotals.get(row.adw_id);
        if (!tot || tot.tokens <= 0) continue;
        shareByAgent.set(row.agent, (shareByAgent.get(row.agent) ?? 0) + tot.cost * (Number(row.tokens) / tot.tokens));
      }
      // models: per-model tokens/cost/share via agent_sessions join
      if (has("agent_sessions")) {
        const modelRows = db.query<{ model: string; tokens: number; cost: number; n: number }, []>(
          `SELECT ag.model model, SUM(e.tokens) tokens,
                  SUM(json_extract(e.payload_json, '$.cost')) cost, COUNT(DISTINCT e.adw_id) n
             FROM events e
             JOIN phases p ON p.phase_id = e.phase_id
             JOIN agent_sessions ag ON ag.adw_id = e.adw_id AND ag.agent = p.owner
            WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL AND ag.model IS NOT NULL
            GROUP BY ag.model`,
        ).all();
        const modelShare = new Map<string, number>();
        const perSessionModel = db.query<{ adw_id: string; model: string; tokens: number }, []>(
          `SELECT e.adw_id, ag.model model, SUM(e.tokens) tokens
             FROM events e
             JOIN phases p ON p.phase_id = e.phase_id
             JOIN agent_sessions ag ON ag.adw_id = e.adw_id AND ag.agent = p.owner
            WHERE e.type = 'agent_end' AND e.tokens IS NOT NULL AND ag.model IS NOT NULL
            GROUP BY e.adw_id, ag.model`,
        ).all();
        for (const row of perSessionModel) {
          const tot = sessionTotals.get(row.adw_id);
          if (!tot || tot.tokens <= 0) continue;
          modelShare.set(row.model, (modelShare.get(row.model) ?? 0) + tot.cost * (Number(row.tokens) / tot.tokens));
        }
        for (const row of modelRows) {
          models.push({
            model: row.model,
            tokens: Number(row.tokens ?? 0),
            sessions: row.n,
            cost_actual: Number(row.cost ?? 0),
            cost_share: modelShare.get(row.model) ?? 0,
          });
        }
        models.sort((a, b) => b.cost_actual - a.cost_actual);
      }

      // merge agent_sessions metadata (model, sessions, context_tokens) for each role.
      // Model = most recent agent_sessions row per role (MAX(last_used_at)); a tie
      // picks arbitrarily. Counts/tokens come from a separate aggregate so the
      // most-recent join never double-counts.
      const meta = has("agent_sessions")
        ? (() => {
            const byAgent = new Map<string, { model: string | null; n: number; tokens: number }>();
            const models = db.query<{ agent: string; model: string | null }, []>(
              `SELECT a.agent, a.model FROM agent_sessions a
                 JOIN (SELECT agent, MAX(last_used_at) m FROM agent_sessions GROUP BY agent) mx
                   ON mx.agent = a.agent AND mx.m = a.last_used_at`,
            ).all();
            for (const row of models) byAgent.set(row.agent, { model: row.model, n: 0, tokens: 0 });
            const counts = db.query<{ agent: string; n: number; tokens: number }, []>(
              `SELECT agent, COUNT(DISTINCT adw_id) n, COALESCE(SUM(context_tokens),0) tokens
                 FROM agent_sessions GROUP BY agent`,
            ).all();
            for (const row of counts) {
              const e = byAgent.get(row.agent) ?? { model: null, n: 0, tokens: 0 };
              e.n = row.n;
              e.tokens = Number(row.tokens ?? 0);
              byAgent.set(row.agent, e);
            }
            return byAgent;
          })()
        : new Map<string, { model: string | null; n: number; tokens: number }>();

      const allRoles = new Set([...AGENT_ROLES, ...tokensByAgent.keys()]);
      const roleOrder = [...AGENT_ROLES, ...Array.from(allRoles).filter((r) => !AGENT_ROLES.includes(r)).sort()];
      for (const role of roleOrder) {
        const m = meta.get(role);
        agents.push({
          role,
          model: m?.model ?? null,
          sessions: m?.n ?? 0,
          context_tokens: Number(m?.tokens ?? 0),
          tokens: tokensByAgent.get(role) ?? 0,
          cost_actual: costByAgent.get(role) ?? 0,
          cost_share: shareByAgent.get(role) ?? 0,
        });
      }
    }

    // ── tickets ───────────────────────────────────────────────────────────
    let tickets: TicketsCounts | null = null;
    if (ticketingEnabled(root) && has("tickets")) {
      const rows = db.query<{ status: string; adw_id: string | null }, []>(
        "SELECT status, adw_id FROM tickets"
      ).all();
      const counts: TicketsCounts = { backlog: 0, running: 0, done: 0, failed: 0 };
      for (const row of rows) {
        let status = row.status;
        if (row.adw_id) {
          try {
            const s = db.query<{ status: string }, [string]>(
              "SELECT status FROM sessions WHERE adw_id = ?"
            ).get(row.adw_id);
            if (s) status = s.status === "success" ? "done" : s.status === "fail" ? "failed" : "running";
          } catch { /* sessions table may not exist yet */ }
        }
        // a 'starting' ticket (spawned, run still warming up) counts as running
        if (status === "starting") status = "running";
        if (status in counts) (counts as unknown as Record<string, number>)[status]++;
      }
      tickets = counts;
    }

    // ── trends ────────────────────────────────────────────────────────────
    const buckets: TrendBucket[] = [];
    let lastRun: string | null = null;
    if (has("sessions")) {
      const row = db.query<{ started_at: string | null }, []>(
        "SELECT MAX(started_at) started_at FROM sessions"
      ).get();
      lastRun = row?.started_at ?? null;
      const cutoff = new Date(Date.now() - windowDays * 86400_000).toISOString().slice(0, 10);
      const rows = db.query<{ day: string; n: number; cost: number; tokens: number; success: number; fail: number }, [string]>(
        `SELECT date(started_at) day, COUNT(*) n,
                COALESCE(SUM(total_cost),0) cost, COALESCE(SUM(total_tokens),0) tokens,
                SUM(status='success') success, SUM(status='fail') fail
           FROM sessions
          WHERE started_at IS NOT NULL AND date(started_at) >= ?
          GROUP BY day ORDER BY day ASC`,
      ).all(cutoff);
      for (const row of rows) {
        buckets.push({ day: row.day, runs: row.n, cost: Number(row.cost ?? 0),
                       tokens: Number(row.tokens ?? 0),
                       success: Number(row.success ?? 0), fail: Number(row.fail ?? 0) });
      }
    }

    return {
      project: { name, root, ticketing_enabled: ticketingEnabled(root), last_run: lastRun },
      totals, quality, agents, models, tickets,
      trends: { window: windowDays, buckets },
      git: gitStats(root),
      contributions: contributions(root),
    };
  } catch (err) {
    // Any read problem degrades to the zeroed payload — a dashboard never 500s.
    console.error(`[sssf] status for ${name} failed:`, err);
    return empty;
  } finally {
    db.close();
  }
}
