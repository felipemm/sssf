import { ref } from 'vue'
import type {
  CockpitData,
  ContainerLogsResponse,
  ControlResult,
  Envelope,
  EventRow,
  EventsPage,
  GateResult,
  HealthResponse,
  PromptsResponse,
  ReviewInfo,
  SessionDetail,
  SessionSummary,
} from './types'

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`)
  return res.json()
}

// ── project selection (global service mode) ─────────────────────────────────
// `selectedProject` is null in adhoc single-db mode, where the server's
// unscoped routes serve; non-null in registry mode, where every session call
// is prefixed with /api/projects/{name}.
export interface ProjectInfo {
  name: string
  root: string
  dbExists: boolean
  lastRun: string | null
}

const selectedProject = ref<string | null>(null)
const projects = ref<ProjectInfo[]>([])
/** True once fetchProjects() has answered — the project situation is known. */
const projectsLoaded = ref(false)

export function useProjects() {
  return { selectedProject, projects, projectsLoaded }
}

const PROJECT_KEY = 'sssf:selected-project'

export function setProject(name: string | null): void {
  selectedProject.value = name
  if (name) {
    try { localStorage.setItem(PROJECT_KEY, name) } catch { /* private mode */ }
  }
}

// ── Mission Control cockpit (cross-project) ─────────────────────────────────

async function postJson(url: string, body?: unknown): Promise<ControlResult> {
  const res = await fetch(url, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return (await res.json()) as ControlResult
}

export async function fetchCockpit(): Promise<CockpitData> {
  return (await getJson('/api/cockpit')) as CockpitData
}

export function refreshProject(name: string): Promise<ControlResult> {
  return postJson(`/api/cockpit/projects/${encodeURIComponent(name)}/refresh`)
}

export function addProject(root: string): Promise<ControlResult> {
  return postJson('/api/cockpit/projects/add', { root })
}

export function removeProject(name: string, confirm: boolean): Promise<ControlResult> {
  return postJson(`/api/cockpit/projects/${encodeURIComponent(name)}/remove`, { confirm })
}

export function healControl(action: 'start' | 'stop'): Promise<ControlResult> {
  return postJson(`/api/cockpit/heal/${action}`)
}

export async function fetchCockpitContributions(): Promise<ContributionDay[]> {
  return (await getJson('/api/cockpit/contributions')) as ContributionDay[]
}

export async function fetchReview(adwId: string): Promise<ReviewInfo> {
  return getJson(`${base()}/sessions/${encodeURIComponent(adwId)}/review`) as Promise<ReviewInfo>
}

export async function fetchSessionLogs(adwId: string, tail = 200): Promise<ContainerLogsResponse> {
  return getJson(`${base()}/sessions/${encodeURIComponent(adwId)}/logs?tail=${tail}`) as Promise<ContainerLogsResponse>
}

export async function fetchContainerLogs(name: string, tail = 100): Promise<ContainerLogsResponse> {
  const res = await fetch(`/api/cockpit/containers/${encodeURIComponent(name)}/logs?tail=${tail}`)
  if (!res.ok) return { ok: false, lines: [], error: `logs ${res.status}` }
  return (await res.json()) as ContainerLogsResponse
}

export async function fetchProjects(): Promise<ProjectInfo[]> {
  const list = (await getJson('/api/projects')) as ProjectInfo[]
  projects.value = list
  projectsLoaded.value = true
  const remembered = (() => {
    try { return localStorage.getItem(PROJECT_KEY) } catch { return null }
  })()
  const match = list.find((p) => p.name === remembered) ?? list[0]
  if (list.length > 0 && match && !selectedProject.value) {
    selectedProject.value = match.name
  } else if (list.length > 0 && !selectedProject.value) {
    selectedProject.value = list[0]!.name
  }
  return list
}

/**
 * Path prefix for the selected project; '/api' when none is selected yet.
 * Never '' — an unprefixed path ('/sessions') hits the SPA fallback and comes
 * back as index.html, which breaks res.json() with a doctype parse error.
 */
function base(): string {
  return selectedProject.value ? `/api/projects/${encodeURIComponent(selectedProject.value)}` : '/api'
}

export function fetchSessions(archived = false): Promise<SessionSummary[]> {
  return getJson(
    `${base()}/sessions${archived ? '?archived=1' : ''}`,
  ) as Promise<SessionSummary[]>
}

export async function fetchSession(adwId: string): Promise<SessionDetail> {
  const detail = (await getJson(
    `${base()}/sessions/${encodeURIComponent(adwId)}`,
  )) as SessionDetail
  return {
    session: detail.session,
    usage: detail.usage ?? { read: 0, written: 0 },
    phases: detail.phases ?? [],
    agents: detail.agents ?? [],
  }
}

export async function fetchEvents(adwId: string, after: number, limit = 500): Promise<EventsPage> {
  const page = (await getJson(
    `${base()}/sessions/${encodeURIComponent(adwId)}/events?after=${after}&limit=${limit}`,
  )) as EventsPage | EventRow[]
  if (Array.isArray(page)) {
    const cursor = page.reduce((max, e) => Math.max(max, e.rowid), after)
    return { events: page, cursor, has_more: page.length === limit }
  }
  return { events: page.events ?? [], cursor: page.cursor ?? after, has_more: page.has_more ?? false }
}

/** Archive a run out of the review list (or restore it with archived=false). */
export async function archiveSession(adwId: string, archived = true): Promise<void> {
  const url = `${base()}/sessions/${encodeURIComponent(adwId)}/archive`
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ archived }),
  })
  if (!res.ok) throw new Error(`POST ${url} → ${res.status}`)
}


/** Stop a run. `project` scopes to that project (the cockpit runs-now strip
 * controls sessions in ANY project); default = the selected project. */
export async function stopRun(adwId: string, project?: string): Promise<{ ok: boolean; output?: string }> {
  const url = project
    ? `/api/projects/${encodeURIComponent(project)}/sessions/${encodeURIComponent(adwId)}/stop`
    : `${base()}/sessions/${encodeURIComponent(adwId)}/stop`
  const res = await fetch(url, { method: 'POST' })
  const data = (await res.json().catch(() => null)) as { ok?: boolean; output?: string } | null
  return { ok: data?.ok ?? res.ok, output: data?.output }
}

export async function restartRun(adwId: string, project?: string): Promise<{ ok: boolean; output?: string }> {
  const url = project
    ? `/api/projects/${encodeURIComponent(project)}/sessions/${encodeURIComponent(adwId)}/restart`
    : `${base()}/sessions/${encodeURIComponent(adwId)}/restart`
  const res = await fetch(url, { method: 'POST' })
  const data = (await res.json().catch(() => null)) as { ok?: boolean; output?: string } | null
  return { ok: data?.ok ?? res.ok, output: data?.output }
}

export interface SweepResult {
  project: string
  db: string
  archived: number
  error?: string
}

/** Manual archival sweep across every registered project — the `sssf sweep` CLI. */
export async function runSweep(): Promise<SweepResult[]> {
  const res = await fetch('/api/sweep', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
  })
  if (!res.ok) throw new Error(`POST /api/sweep → ${res.status}`)
  const data = (await res.json()) as { results?: SweepResult[] }
  return data.results ?? []
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson('/api/health') as Promise<HealthResponse>
}

// PhaseDetail imports the prompts type from here alongside fetchPrompts.
export type { PromptsResponse }

export async function fetchPrompts(adwId: string, agent: string): Promise<PromptsResponse> {
  const res = await fetch(
    `${base()}/sessions/${encodeURIComponent(adwId)}/agents/${encodeURIComponent(agent)}/prompts`,
  )
  // Not recorded (or endpoint not deployed yet) renders as "no prompts", not an error.
  if (res.status === 404) return { system: null, user: null }
  if (!res.ok) throw new Error(`GET prompts → ${res.status}`)
  const data = (await res.json()) as Partial<PromptsResponse>
  return { system: data.system ?? null, user: data.user ?? null }
}

export function fetchEnvelopes(adwId: string): Promise<Envelope[]> {
  return getJson(
    `${base()}/sessions/${encodeURIComponent(adwId)}/envelopes`,
  ) as Promise<Envelope[]>
}

export function fetchGates(adwId: string): Promise<GateResult[]> {
  return getJson(
    `${base()}/sessions/${encodeURIComponent(adwId)}/gates`,
  ) as Promise<GateResult[]>
}

export interface TicketRun {
  adw_id: string
  status: string | null
  started_at: string | null
  ended_at: string | null
}

export interface Ticket {
  id: string
  provider: string
  external_id: string | null
  title: string
  description: string
  context: string
  status: string
  prompt_file: string | null
  adw_id: string | null
  source_url: string
  runs: TicketRun[]
}

export interface TicketsResponse {
  enabled: boolean
  tickets: Ticket[]
}

export async function fetchTickets(): Promise<TicketsResponse> {
  return getJson(`${base()}/tickets`) as Promise<TicketsResponse>
}

export async function runTicket(id: string, context = ""): Promise<{ ok: boolean; adwId?: string; output?: string }> {
  const res = await fetch(`${base()}/tickets/${encodeURIComponent(id)}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context }),
  })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; adwId?: string; output?: string }
  return { ok: data.ok ?? res.ok, adwId: data.adwId, output: data.output }
}

export async function syncTickets(): Promise<{ ok: boolean; output?: string }> {
  const res = await fetch(`${base()}/tickets/sync`, { method: 'POST' })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; output?: string }
  return { ok: data.ok ?? res.ok, output: data.output }
}

export async function saveTicketContext(id: string, context: string): Promise<{ ok: boolean; output?: string }> {
  const res = await fetch(`${base()}/tickets/${encodeURIComponent(id)}/context`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context }),
  })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; output?: string }
  return { ok: data.ok ?? res.ok, output: data.output }
}

export async function backlogTicket(id: string): Promise<{ ok: boolean; output?: string }> {
  const res = await fetch(`${base()}/tickets/${encodeURIComponent(id)}/backlog`, { method: 'POST' })
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; output?: string }
  return { ok: data.ok ?? res.ok, output: data.output }
}

export interface StatusProject {
  name: string
  root: string
  ticketing_enabled: boolean
  last_run: string | null
}
export interface StatusTotals {
  runs: number
  active: number
  success: number
  failed: number
  archived: number
  success_rate: number
  avg_duration_s: number
  total_cost: number
  avg_cost_per_run: number
  total_tokens: number
  avg_tokens_per_run: number
}
export interface StatusQuality {
  gate_pass_rate: number
  hotspot_phase: string | null
  hotspot_count: number
  total_retries: number
  failed_phases: number
}
export interface StatusAgent {
  role: string
  model: string | null
  sessions: number
  context_tokens: number
  tokens: number
  cost_actual: number
  cost_share: number
}
export interface GitContributor { name: string; commits: number }
export interface GitStats {
  commits: number
  commits_30d: number
  commits_year: number
  contributors: GitContributor[]
  branches: number
  current_branch: string | null
  last_commit: { date: string; subject: string } | null
  dirty: number
  first_commit: string | null
}
export interface ContributionDay { date: string; count: number }
export interface StatusModel {
  model: string
  tokens: number
  sessions: number
  cost_actual: number
  cost_share: number
}
export interface StatusTickets {
  backlog: number
  running: number
  done: number
  failed: number
}
export interface StatusTrendBucket {
  day: string
  runs: number
  cost: number
  tokens: number
  success: number
  fail: number
}
export interface StatusResponse {
  project: StatusProject
  totals: StatusTotals
  quality: StatusQuality
  agents: StatusAgent[]
  models: StatusModel[]
  tickets: StatusTickets | null
  trends: { window: number; buckets: StatusTrendBucket[] }
  git: GitStats
  contributions: ContributionDay[]
}

export async function fetchStatus(windowDays = 30): Promise<StatusResponse> {
  const res = await fetch(`${base()}/status?window=${windowDays}`)
  if (!res.ok) throw new Error(`status ${res.status}`)
  return (await res.json()) as StatusResponse
}
