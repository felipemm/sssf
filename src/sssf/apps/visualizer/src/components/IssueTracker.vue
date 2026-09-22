<script setup lang="ts">
/**
 * Issue tracker (issue #94): every ticket sssf knows — tracked and untracked —
 * grouped by the ticket machine's state, with origin, kind, untracked marking,
 * and parent/child lineage. The backlog is exactly the `ready-for-agent`
 * column; untracked tickets (synced, born needs-triage) sit in Needs triage
 * until marked ready-for-agent from the page.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, RefreshCw, EyeOff, GitBranch } from 'lucide-vue-next'
import { backlogTicket, fetchTickets, syncTickets, useProjects, type Ticket, type TicketsResponse } from '../lib/api'
import { notify } from '../lib/toast'
import TicketModal from './TicketModal.vue'

const { selectedProject } = useProjects()

// The ticket machine's statuses in lifecycle order (ticketing.py
// MACHINE_STATUSES). `ready-for-agent` is the backlog — nothing else is.
const COLUMNS = [
  { key: 'needs-triage', label: 'Needs triage', accent: 'var(--faint)' },
  { key: 'ready-for-agent', label: 'Backlog · ready', accent: 'var(--purple)' },
  { key: 'in-progress', label: 'In progress', accent: 'var(--blue)' },
  { key: 'ready-for-signoff', label: 'Ready for signoff', accent: 'var(--cyan)' },
  { key: 'ready-to-deploy', label: 'Ready to deploy', accent: 'var(--amber)' },
  { key: 'done', label: 'Done', accent: 'var(--green)' },
  { key: 'blocked', label: 'Blocked', accent: 'var(--red)' },
] as const

const tickets = ref<TicketsResponse>({ enabled: false, tickets: [] })
const apiError = ref<string | null>(null)
const loaded = ref(false)
const activeTicket = ref<Ticket | null>(null)
const syncing = ref(false)
const untrackedOnly = ref(false)

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function pull(force = false) {
  if (!selectedProject.value) return    // adhoc mode has no project scope
  if (inflight && !force) return
  inflight = true
  try {
    tickets.value = await fetchTickets()
    apiError.value = null
    loaded.value = true
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

async function onSync() {
  syncing.value = true
  try {
    const res = await syncTickets()
    notify(res.ok ? 'tickets synced' : res.output || 'sync failed')
  } finally {
    syncing.value = false
  }
  void pull(true)
}

// ── lineage ─────────────────────────────────────────────────────────────────
const byId = computed(() => new Map(tickets.value.tickets.map((t) => [t.id, t])))
const childrenOf = computed(() => {
  const m = new Map<string, Ticket[]>()
  for (const t of tickets.value.tickets) {
    if (!t.parent_id) continue
    const list = m.get(t.parent_id) ?? []
    list.push(t)
    m.set(t.parent_id, list)
  }
  return m
})
function parentOf(t: Ticket): Ticket | undefined {
  return t.parent_id ? byId.value.get(t.parent_id) : undefined
}

// ── filters ─────────────────────────────────────────────────────────────────
const visible = computed(() =>
  tickets.value.tickets.filter((t) => !untrackedOnly.value || !t.tracked),
)
const byColumn = computed(() => {
  const groups = new Map<string, Ticket[]>(COLUMNS.map((c) => [c.key, [] as Ticket[]]))
  for (const t of visible.value) {
    const list = groups.get(t.status) ?? groups.get('needs-triage')!
    list.push(t)
  }
  return groups
})
const counts = computed(() => ({
  total: tickets.value.tickets.length,
  untracked: tickets.value.tickets.filter((t) => !t.tracked).length,
  backlog: byColumn.value.get('ready-for-agent')!.length,
}))

// ── collapse (persisted), `done` folded by default so the page opens on the
// live pipeline — every ticket is still one click (or a glance at the header
// count) away. A saved preference wins; absent one defaults to collapsed.
const COLLAPSE_KEY = 'sssf.trackerCollapsed'
function loadCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? '{}') as Record<string, boolean>
  } catch {
    return {}
  }
}
const savedCollapsed = loadCollapsed()
const collapsed = ref<Record<string, boolean>>(
  savedCollapsed.done === undefined ? { ...savedCollapsed, done: true } : savedCollapsed,
)
function toggleCollapsed(key: string) {
  collapsed.value = { ...collapsed.value, [key]: !collapsed.value[key] }
  try {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(collapsed.value))
  } catch {
    /* private mode — the collapse just won't survive reloads */
  }
}

// ── adopt: untracked needs-triage → ready-for-agent (the backlog) ───────────
const adopting = ref<Set<string>>(new Set())
async function onAdopt(ticket: Ticket) {
  adopting.value = new Set(adopting.value).add(ticket.id)
  try {
    const res = await backlogTicket(ticket.id)
    notify(res.ok ? 'added to the backlog (ready-for-agent)' : res.output || 'add to backlog failed')
  } catch (err) {
    notify(String(err))
  } finally {
    adopting.value = new Set([...adopting.value].filter((x) => x !== ticket.id))
  }
  void pull(true)
}

const ORIGIN_BADGE: Record<string, string> = { jira: 'J', linear: 'L', github: 'G', gitlab: 'GL', internal: '⚙' }

function open(ticket: Ticket) {
  activeTicket.value = ticket
}

// A project switch must not show the previous project's tickets while the new
// fetch is in flight — clear first, then reload.
watch(selectedProject, () => {
  tickets.value = { enabled: false, tickets: [] }
  loaded.value = false
  if (selectedProject.value) void pull()
})

onMounted(() => {
  void pull()
  timer = setInterval(() => {
    if (!document.hidden) void pull()
  }, 10_000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="tracker">
    <div v-if="apiError" class="error-bar">api unreachable — retrying {{ apiError }}</div>

    <div class="tracker-head">
      <p class="head-title">
        <GitBranch :size="15" :stroke-width="2" class="head-ico" />
        issue tracker
        <span v-if="loaded && counts.total" class="head-counts dim">
          {{ counts.total }} tickets · {{ counts.backlog }} in the backlog · {{ counts.untracked }} untracked
        </span>
        <span v-else-if="loaded" class="head-counts dim">no tickets yet — sync a provider or run the plan flow</span>
      </p>
      <div class="head-actions">
        <label class="chip-toggle" title="Show only tickets not yet adopted into the backlog">
          <input v-model="untrackedOnly" type="checkbox" />
          <EyeOff :size="13" /> untracked only
        </label>
        <button class="sync-link" type="button" :disabled="syncing" aria-label="Fetch external tickets" @click="onSync">
          <RefreshCw :size="13" :class="{ spin: syncing }" /> {{ syncing ? 'syncing…' : 'sync' }}
        </button>
      </div>
    </div>
    <p class="head-hint dim">
      The backlog is the <code>ready-for-agent</code> column. Untracked tickets (born
      <code>needs-triage</code> by sync) are invisible to it until marked ready-for-agent here.
    </p>

    <div class="columns">
      <section v-for="col in COLUMNS" :key="col.key" class="col">
        <div class="col-head">
          <button
            type="button"
            class="col-toggle"
            :aria-expanded="!collapsed[col.key]"
            :aria-label="`Toggle ${col.label} column`"
            :title="collapsed[col.key] ? 'Expand' : 'Collapse'"
            @click="toggleCollapsed(col.key)"
          >
            <ChevronRight v-if="collapsed[col.key]" :size="15" :stroke-width="2" class="chev" aria-hidden="true" />
            <ChevronDown v-else :size="15" :stroke-width="2" class="chev" aria-hidden="true" />
            <span class="dot" :style="{ background: col.accent }" />
            <span class="col-name">{{ col.label }}</span>
            <span class="col-count">{{ (byColumn.get(col.key) ?? []).length }}</span>
          </button>
        </div>

        <div v-if="!collapsed[col.key]" class="cards">
          <article
            v-for="t in byColumn.get(col.key) ?? []"
            :key="t.id"
            class="tk"
            :class="{ untracked: !t.tracked }"
          >
            <button class="tk-open" type="button" @click="open(t)">
              <span class="badge">{{ ORIGIN_BADGE[t.origin] ?? '?' }}</span>
              <span class="tk-title">{{ t.title }}</span>
              <span class="tk-meta dim">
                <code>{{ t.external_id || t.id }}</code>
                <span class="tk-tags">
                  <span class="tag kind" :class="t.kind">{{ t.kind }}</span>
                  <span v-if="!t.tracked" class="tag unt" title="Synced from an external tracker — not in the backlog until adopted">untracked</span>
                  <span v-if="t.runs.length" class="runs-chip" :title="`${t.runs.length} run(s) — see the modal for the trace of each`">
                    {{ t.runs.length }} run{{ t.runs.length > 1 ? 's' : '' }}
                  </span>
                </span>
                <span v-if="t.spec" class="tk-spec"><code>{{ t.spec }}</code></span>
              </span>
              <!-- lineage: implementation tickets trace to their idea -->
              <span v-if="t.kind === 'implementation' && parentOf(t)" class="tk-parent dim">
                ← {{ parentOf(t)!.title }}
              </span>
              <!-- lineage: idea tickets expand to their slices -->
              <span v-if="t.kind === 'idea' && (childrenOf.get(t.id)?.length ?? 0) > 0" class="tk-children dim">
                {{ childrenOf.get(t.id)!.length }} implementation ticket{{ childrenOf.get(t.id)!.length > 1 ? 's' : '' }}
                — click to expand
              </span>
            </button>
            <button
              v-if="!t.tracked && t.status === 'needs-triage'"
              class="tk-adopt"
              type="button"
              :disabled="adopting.has(t.id)"
              :aria-busy="adopting.has(t.id)"
              :title="'Mark ready-for-agent — the ticket becomes part of the backlog'"
              @click="onAdopt(t)"
            >
              <span>{{ adopting.has(t.id) ? 'adding…' : 'add to backlog' }}</span>
            </button>
          </article>
          <p v-if="(byColumn.get(col.key) ?? []).length === 0" class="col-empty dim">—</p>
        </div>
      </section>
    </div>

    <TicketModal v-if="activeTicket" :ticket="activeTicket" @close="activeTicket = null" @ran="void pull(true)" />
  </div>
</template>

<style scoped>
.tracker {
  padding: 14px 18px 24px;
}
.error-bar {
  background: rgba(255, 111, 103, 0.12);
  border: 1px solid var(--red);
  color: var(--red);
  border-radius: 10px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.tracker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text);
}
.head-ico {
  color: var(--faint);
}
.head-counts {
  font-size: 12px;
  margin-left: 4px;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.chip-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--dim);
  cursor: pointer;
  user-select: none;
}
.chip-toggle input {
  accent-color: var(--purple);
}
.sync-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: none;
  border: 1px solid var(--border);
  color: var(--dim);
  font-size: 12px;
  border-radius: 8px;
  padding: 4px 9px;
  cursor: pointer;
}
.sync-link:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--faint);
}
.sync-link:disabled {
  opacity: 0.6;
  cursor: default;
}
.head-hint {
  font-size: 12px;
  margin: 0 0 12px;
}
.head-hint code {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--cyan);
}
.columns {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  overflow-x: auto;
  padding-bottom: 8px;
}
.col {
  min-width: 240px;
  max-width: 300px;
  flex: 1;
  background: rgba(11, 15, 24, 0.55);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  padding: 8px;
}
.col-head {
  display: flex;
  align-items: center;
}
.col-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 4px 6px;
  width: 100%;
  text-align: left;
}
.chev {
  color: var(--faint);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.col-name {
  flex: 1;
}
.col-count {
  color: var(--faint);
  font-family: var(--mono);
  font-size: 11px;
}
.cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 6px;
}
.col-empty {
  font-size: 12px;
  text-align: center;
  padding: 10px 0;
}
.tk {
  position: relative;
  display: block;
  background: rgba(11, 15, 24, 0.66);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.tk.untracked {
  border-style: dashed;
  border-color: rgba(232, 182, 74, 0.45);
}
.tk:hover {
  border-color: var(--faint);
}
.tk-open {
  display: block;
  width: 100%;
  background: none;
  border: none;
  text-align: left;
  color: var(--text);
  padding: 9px 10px 8px;
  cursor: pointer;
  border-radius: inherit;
}
.tk-title {
  display: block;
  font-size: 13px;
  line-height: 1.35;
  margin: 2px 0 4px;
}
.tk-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
}
.tk-meta code {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--faint);
}
.tk-tags {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.tag {
  font-size: 10px;
  line-height: 1;
  padding: 2px 5px;
  border-radius: 5px;
  border: 1px solid var(--border);
  color: var(--dim);
}
.tag.kind.idea {
  color: var(--purple);
  border-color: rgba(200, 155, 255, 0.4);
}
.tag.kind.implementation {
  color: var(--blue);
  border-color: rgba(108, 182, 255, 0.4);
}
.tag.unt {
  color: var(--amber);
  border-color: rgba(232, 182, 74, 0.45);
}
.runs-chip {
  font-size: 10px;
  color: var(--faint);
}
.tk-spec {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tk-parent,
.tk-children {
  display: block;
  font-size: 11px;
  margin-top: 5px;
  color: var(--faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tk-adopt {
  display: block;
  width: calc(100% - 20px);
  margin: 0 10px 9px;
  background: rgba(232, 182, 74, 0.12);
  border: 1px solid rgba(232, 182, 74, 0.5);
  color: var(--amber);
  font-size: 11px;
  border-radius: 8px;
  padding: 5px 8px;
  cursor: pointer;
}
.tk-adopt:hover:not(:disabled) {
  background: rgba(232, 182, 74, 0.22);
}
.tk-adopt:disabled {
  opacity: 0.6;
  cursor: default;
}
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  margin-right: 8px;
  background: rgba(232, 182, 74, 0.18);
  color: #e8b64a;
  font-weight: 700;
  font-size: 11px;
  flex: none;
}
.spin {
  display: inline-block;
  animation: spin 0.9s linear infinite;
}
</style>
