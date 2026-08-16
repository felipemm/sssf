import { ref } from 'vue'

// Hash routes:
//   #/                      → Mission Control cockpit (cross-project landing)
//   #/cockpit               → same
//   #/p/<project>           → per-project status (default tab)
//   #/p/<project>/<tab>     → board | sessions | archived
//   #/p/<project>/s/<adwId>[/<phaseId>] → session trace
//   legacy: #/<adwId>[/<phaseId>] and #/<tab> — kept working; the app
//   resolves the project from the picker (selectedProject).
export interface Route {
  cockpit: boolean
  project: string | null
  tab: 'status' | 'board' | 'sessions' | 'archived' | null
  adwId: string | null
  phaseId: string | null
}

const empty = (): Route => ({ cockpit: false, project: null, tab: null, adwId: null, phaseId: null })

const VIEW_WORDS = new Set(['cockpit', 'status', 'board', 'sessions', 'archived'])

function asTab(word: string | undefined): Route['tab'] {
  return word === 'board' || word === 'sessions' || word === 'archived' || word === 'status'
    ? word
    : null
}

export function parseHash(hash: string): Route {
  const parts = hash
    .replace(/^#\/?/, '')
    .split('/')
    .filter(Boolean)
    .map(decodeURIComponent)
  if (parts.length === 0 || parts[0] === 'cockpit') return { ...empty(), cockpit: true }
  if (parts[0] === 'p') {
    const project = parts[1] ?? null
    if (!project) return { ...empty(), cockpit: true }
    if (parts[2] === 's') {
      return { ...empty(), project, adwId: parts[3] ?? null, phaseId: parts[4] ?? null }
    }
    return { ...empty(), project, tab: asTab(parts[2]) ?? 'status' }
  }
  // legacy routes: a known view word is a tab, anything else is a trace id
  if (VIEW_WORDS.has(parts[0]!)) return { ...empty(), tab: asTab(parts[0]) }
  return { ...empty(), adwId: parts[0] ?? null, phaseId: parts[1] ?? null }
}

export interface NavTarget {
  project?: string | null
  tab?: string | null
  adwId?: string | null
  phaseId?: string | null
}

export function hrefFor(target?: NavTarget): string {
  const { project, tab, adwId, phaseId } = target ?? {}
  if (project) {
    if (adwId) {
      const base = `#/p/${encodeURIComponent(project)}/s/${encodeURIComponent(adwId)}`
      return phaseId ? `${base}/${encodeURIComponent(phaseId)}` : base
    }
    if (tab) return `#/p/${encodeURIComponent(project)}/${tab}`
    return `#/p/${encodeURIComponent(project)}`
  }
  if (adwId) {
    const base = `#/${encodeURIComponent(adwId)}`
    return phaseId ? `${base}/${encodeURIComponent(phaseId)}` : base
  }
  return '#/'
}

const route = ref<Route>(typeof window !== 'undefined' ? parseHash(window.location.hash) : empty())

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    route.value = parseHash(window.location.hash)
  })
}

export function useRoute() {
  return route
}

// Display name for the phase crumb — set by the trace view once phases load,
// since the phase_id in the URL is not the display name.
export const phaseCrumb = ref<string | null>(null)

export function navigate(target?: NavTarget): void {
  window.location.hash = hrefFor(target)
}
