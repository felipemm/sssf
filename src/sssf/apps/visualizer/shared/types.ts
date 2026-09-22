/**
 * Types shared by the read-only server and the Vue client.
 *
 * Row types + status unions are GENERATED from the schema contract
 * (src/sssf/db_schema.py via scripts/gen_viz_types.py) and re-exported from
 * rows.generated.ts under the UI-facing names. Everything below is derived
 * state: phase durations, session progress and lane layout are computed in
 * the UI, never stored.
 */

import type {
  AgentSessionsRow,
  EnvelopesRow,
  EventsRow,
  GateResultsRow,
  PhasesRow,
  SandboxRunRow,
  SessionsRow,
} from "./rows.generated";

export type Session = SessionsRow;
export type Phase = PhasesRow;
export type Event = EventsRow;
export type Envelope = EnvelopesRow;
export type GateResult = GateResultsRow;
export type AgentSession = AgentSessionsRow;
export type ReviewRow = SandboxRunRow;

export type { SessionStatus, PhaseStatus, PhaseKind, EventType } from "./rows.generated";

export interface SessionSummary extends Session {
  /** Full phase rows, ordered by seq — one dot each. */
  phases: Phase[];
  phase_count: number;
  /** The originating ticket id ('jira:<key>' | 'internal:<uuid>'), when this run came from a ticket. */
  ticket_id: string | null;
  /**
   * The session's agents, same shape and merge rules as SessionDetail.agents —
   * so an L1 card can color its per-agent dots without a request per card.
   */
  agents: AgentSession[];
}

export interface GateCheck {
  item: string;
  ok: boolean;
  note: string;
}

/** agent_sessions — the queryable mirror of agent_map.json. Supplies lane labels (`name · model`). */
// ── payload_json shapes ──────────────────────────────────────────────────────
// events.payload_json is stored as a string. These are the parsed shapes for
// the two payloads the UI renders; every field is optional because the tracer
// writes what the coding agent reported, which varies by agent and by version.

/** Parsed `agent_start` payload — the live source of a lane's label and color. */
export interface AgentStartPayload {
  model?: string;
  thinking?: string;
  session_id?: string;
  color?: string;
  coding_agent?: string;
  purpose?: string;
  /** Tool allowlist; null means all tools. Absent on pre-config-payload rows. */
  tools?: string[] | null;
  harness_engineering?: string[];
}

/**
 * Tokens and dollars per component for one agent phase, summed across every
 * send it made (a retried phase paid more than once). Mirrors pi's `usage`:
 * `input_tokens` EXCLUDES cache reads, which bill at their own rate.
 */
export interface UsageBreakdown {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  /**
   * Thinking tokens — the reasoning SHARE of `output_tokens`, not a fifth
   * component. Billed at the output rate; adding it to the others would
   * double-count. Absent (undefined) on runs predating the field.
   */
  reasoning_tokens?: number;
  total_tokens: number;
  input_cost: number;
  output_cost: number;
  cache_read_cost: number;
  cache_write_cost: number;
  total_cost: number;
}

/** Parsed `agent_end` payload — closes out a call with its cost and context use. */
export interface AgentEndPayload {
  cost?: number;
  /** Absent on runs predating the breakdown; `cost` alone survives there. */
  usage?: UsageBreakdown;
  /** Window occupancy after the final turn, and the model's ceiling. */
  context_tokens?: number;
  context_window?: number;
}

/**
 * Parsed `tool_call` payload — one event per real tool call, emitted when the
 * tool returns. `result_snippet` and `duration_ms` are absent when the coding
 * agent never reported a result.
 */
export interface ToolCallPayload {
  tool?: string;
  tool_call_id?: string;
  args?: Record<string, unknown>;
  result_snippet?: string;
  ok?: boolean;
  duration_ms?: number;
  agent?: string;
}

// ── API responses ────────────────────────────────────────────────────────────

/** GET /api/sessions */
export type SessionsResponse = SessionSummary[];

/** GET /api/sessions/:adw_id */
/**
 * What actually moved through a session, summed across every agent.
 *
 * Deliberately NOT the billed total: `sessions.total_tokens` also counts every
 * cached re-read, which is the same context charged again on each turn.
 */
export interface SessionUsage {
  /** Raw prompt tokens read for the first time: new input + cache writes. */
  read: number;
  /** Tokens generated. Each produced exactly once, so this needs no adjusting. */
  written: number;
}

export interface SessionDetail {
  session: Session;
  /** Derived from agent_end payloads, so historical runs have it too. */
  usage: SessionUsage;
  /** Ordered by seq. */
  phases: Phase[];
  /**
   * One entry per agent that has run OR is running under this adw_id — lane
   * labels come from here. Finished agents come from the agent_sessions table;
   * an agent still in flight has no row there yet, so its entry is built from
   * its agent_start event (coding_agent is null until it finishes).
   */
  agents: AgentSession[];
}

/**
 * GET /api/sessions/:adw_id/events?after=<rowid>&limit=500
 *
 * Poll with `after` = the cursor from the previous response. `cursor` is the
 * highest rowid in this page (or the `after` you sent, when the page is empty),
 * so it can be fed straight back in. `has_more` means the page hit the limit.
 */
export interface EventsPage {
  events: Event[];
  cursor: number;
  has_more: boolean;
}

/**
 * GET /api/sessions/:adw_id/agents/:agent/prompts
 *
 * The exact compiled prompts sent to an agent, read from
 * `{data_dir}/sessions/{adw_id}/{agent}/prompts/`. These live only as files —
 * the db has no copy. Either field is null when that file isn't on disk, which
 * is the normal state for an agent that never ran in this session, so a 200
 * with two nulls is a valid answer rather than an error.
 */
export interface AgentPrompts {
  system: string | null;
  user: string | null;
}

/** Alias matching the naming of the other endpoint payloads. */
export type PromptsResponse = AgentPrompts;

/** GET /api/sessions/:adw_id/envelopes */
export type EnvelopesResponse = Envelope[];

/** GET /api/sessions/:adw_id/gates */
export type GatesResponse = GateResult[];

/** GET /api/health */
export interface HealthResponse {
  ok: boolean;
  db: string;
  journal_mode: string;
  sessions: number;
}

export interface ApiError {
  error: string;
}

// ── Mission Control cockpit (cross-project) ────────────────────────────────

export interface CockpitKpis {
  runningSessions: number
  liveContainers: number
  orphanContainers: number
  sandboxWorktrees: number
  ticketsInFlight: number
  costTodayUsd: number
  costTotalUsd: number
  healRunning: boolean
  healPid: number | null
  dockerOk: boolean
  dockerError: string
}

export interface CockpitProject {
  name: string
  root: string
  sessionsRunning: number
  sessionsToday: number
  sessionsFailedToday: number
  ticketsBacklog: number
  ticketsInFlight: number
  ticketsDone: number
  containers: number
  worktrees: number
  costTodayUsd: number
  costTotalUsd: number
  lastActivity: string | null
  stale: boolean
}

export interface RunningSession {
  project: string
  adwId: string
  phase: string | null
  phaseStatus: string | null
  ageSec: number
}

export interface HealSummary {
  running: boolean
  pid: number | null
  logTail: string[]
  restarts: Record<string, number>
  /** Recovery actions taken in the last 7 days (from heal-state.json). */
  healed7d: number
}

export interface ActivityItem {
  project: string
  adwId: string
  ts: string
  event: string
}

export interface CockpitCompletedPoint {
  date: string
  count: number
}

export interface CockpitData {
  generatedAt: string
  kpis: CockpitKpis
  projects: CockpitProject[]
  running: RunningSession[]
  containers: CockpitContainer[]
  heal: HealSummary
  activity: ActivityItem[]
  /** Per-hour completed-session counts, last 14 days (oldest first). */
  completedHourly: CockpitCompletedPoint[]
  /** Per-minute completed-session counts, last 120 minutes (oldest first) — feeds the 1h window. */
  completedMinute: CockpitCompletedPoint[]
  /** Completed sessions before the 14-day window — the chart's cumulative baseline. */
  completedBaseline: number
}

export interface ControlResult {
  ok: boolean
  output?: string
  error?: string
}

/** `sssf upgrade --check` — is the sssf tool itself behind its origin? */
export interface UpdateReport {
  ok: boolean
  local_sha?: string
  remote_sha?: string
  ahead?: number
  behind?: number
  branch?: string
  dirty?: number
  command?: string
  repo?: string
  reason?: string
}

export interface CockpitContainer {
  name: string
  adwId: string
  image: string
  status: string
  created: string
  running: boolean
  project: string  // '' when the container maps to no registered project
}

export interface ContainerLogsResponse {
  ok: boolean
  lines: string[]
  error?: string
}

export interface ReviewInfo {
  row: ReviewRow | null
  container: { state: 'running' | 'exited' | 'absent' }
}