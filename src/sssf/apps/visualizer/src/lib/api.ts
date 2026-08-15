import { ref } from 'vue'
import type {
  Envelope,
  EventRow,
  EventsPage,
  GateResult,
  HealthResponse,
  PromptsResponse,
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

export function useProjects() {
  return { selectedProject, projects }
}

export function setProject(name: string | null): void {
  selectedProject.value = name
}

export async function fetchProjects(): Promise<ProjectInfo[]> {
  const list = (await getJson('/api/projects')) as ProjectInfo[]
  projects.value = list
  if (list.length > 0 && !selectedProject.value) {
    selectedProject.value = list[0]!.name
  }
  return list
}

/** Path prefix for the selected project; empty in adhoc mode. */
function base(): string {
  return selectedProject.value ? `/api/projects/${encodeURIComponent(selectedProject.value)}` : ''
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
