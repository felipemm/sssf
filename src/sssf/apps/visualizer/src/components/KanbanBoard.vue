<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import { Archive, ChevronDown, ChevronRight } from 'lucide-vue-next'
import type { SessionSummary } from '../lib/types'
import { archiveSession, fetchSessions } from '../lib/api'
import { fmtCost, fmtDate, fmtTokens, ts } from '../lib/format'
import { hrefFor } from '../lib/router'
import PhaseDots from './PhaseDots.vue'

// A read-only stage board: sessions grouped by where they are in the chain. Status is
// produced by the factory, not triaged here, so the board never reorders by
// dragging — it is the list view's data, organized for a glance.
const sessions = shallowRef<SessionSummary[]>([])
const apiError = ref<string | null>(null)
const loaded = ref(false)

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function tick() {
  if (inflight) return
  inflight = true
  try {
    sessions.value = await fetchSessions()
    apiError.value = null
    loaded.value = true
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 500)
})

onUnmounted(() => clearInterval(timer))

const AGENT_STAGE: Record<string, string> = {
  planner: 'planning',
  builder: 'building',
  reviewer: 'reviewing',
}

/**
 * Where a running session sits in the chain. The running phase decides the
 * stage; when it is an engineer/code phase (request, commit, test, …) it
 * inherits the stage of the nearest agent phase at or before it — a mid-test
 * session belongs to the build, a mid-request session is still planning.
 */
function stageOf(s: SessionSummary): string {
  const phases = s.phases ?? []
  const running = phases.find((p) => p.status === 'running')
  if (!running) return 'planning'   // running but nothing reported yet
  const own = AGENT_STAGE[running.owner ?? '']
  if (own) return own
  const idx = phases.indexOf(running)
  for (let i = idx; i >= 0; i--) {
    const stage = AGENT_STAGE[phases[i].owner ?? '']
    if (stage) return stage
  }
  return 'planning'                 // before any agent phase (mid-request)
}

const COLUMNS = [
  { key: 'backlog', label: 'Backlog', accent: 'var(--faint)', stub: true },
  { key: 'planning', label: 'Planning', accent: 'var(--purple)', stub: false },
  { key: 'building', label: 'Building', accent: 'var(--blue)', stub: false },
  { key: 'reviewing', label: 'Reviewing', accent: 'var(--cyan)', stub: false },
  { key: 'success', label: 'Done', accent: 'var(--green)', stub: false },
  { key: 'fail', label: 'Blocked', accent: 'var(--red)', stub: false },
] as const

const byColumn = computed(() => {
  const groups: Record<string, SessionSummary[]> = {
    backlog: [], planning: [], building: [], reviewing: [], success: [], fail: [],
  }
  for (const s of sessions.value) {
    const status = s.status ?? 'fail'
    if (status === 'running') groups[stageOf(s)].push(s)
    else ;(groups[status] ?? groups.fail).push(s)
  }
  for (const list of Object.values(groups)) {
    list.sort((a, b) => (ts(b.started_at) || 0) - (ts(a.started_at) || 0))
  }
  return groups
})

const total = computed(() => sessions.value.length)

// Per-stage collapse, persisted so the board opens the way you left it. The
// count stays visible in the header while a stage is folded away.
const COLLAPSE_KEY = 'sssf.boardCollapsed'
const collapsed = ref<Record<string, boolean>>(loadCollapsed())

function loadCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? '{}') as Record<string, boolean>
  } catch {
    return {}
  }
}

function toggleCollapsed(key: string) {
  collapsed.value = { ...collapsed.value, [key]: !collapsed.value[key] }
  try {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(collapsed.value))
  } catch {
    /* private mode — the collapse just won't survive reloads */
  }
}

// Archive from the board: the card is an <a>, so the click must not navigate.
// The board polls every 500 ms — a failed write just re-syncs on the next tick.
async function archive(s: SessionSummary, event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  try {
    await archiveSession(s.adw_id)
    void tick()
  } catch {
    /* the next poll reconciles */
  }
}
</script>

<template>
  <div class="board">
    <div v-if="apiError" class="error-bar">api unreachable — retrying {{ apiError }}</div>

    <div v-if="loaded && total" class="board-head dim">
      {{ total }} runs · grouped by stage — click a card for its trace
    </div>

    <div class="columns">
      <section v-for="col in COLUMNS" :key="col.key" class="col">
        <button
          type="button"
          class="col-head"
          :title="collapsed[col.key] ? 'Expand stage' : 'Collapse stage'"
          @click="toggleCollapsed(col.key)"
        >
          <ChevronRight v-if="collapsed[col.key]" :size="15" :stroke-width="2" class="chev" />
          <ChevronDown v-else :size="15" :stroke-width="2" class="chev" />
          <span class="dot" :style="{ background: col.accent }" />
          <span class="col-name">{{ col.label }}</span>
          <span class="col-count">{{ byColumn[col.key].length }}</span>
        </button>

        <div v-if="!collapsed[col.key]" class="cards">
          <a
            v-for="s in byColumn[col.key]"
            :key="s.adw_id"
            class="card"
            :href="hrefFor(s.adw_id)"
          >
            <div class="card-top">
              <span class="adw" :title="s.adw_id">{{ s.adw_name || s.adw_id }}</span>
              <span class="card-actions">
                <PhaseDots :phases="s.phases" />
                <button
                  class="card-archive"
                  type="button"
                  title="Archive — remove this run from review"
                  aria-label="Archive run"
                  @click="archive(s, $event)"
                >
                  <Archive :size="15" :stroke-width="2" />
                </button>
              </span>
            </div>
            <p class="req" :title="s.request ?? ''">{{ s.request || '—' }}</p>
            <div class="meta">
              <span class="engineer">{{ s.engineer }}</span>
              <span class="time">{{ fmtDate(s.started_at) }}</span>
            </div>
            <div class="stats">
              <span>{{ fmtTokens(s.total_tokens) }} tok</span>
              <span>{{ fmtCost(s.total_cost) }}</span>
            </div>
          </a>

          <div v-if="loaded && !byColumn[col.key].length" class="empty">
            {{ col.stub ? 'backlog — not wired yet' : 'no runs' }}
          </div>
        </div>
      </section>
    </div>

    <div v-if="loaded && !total" class="board-empty">no sessions yet — run an ADW to see it here</div>
    <div v-else-if="!loaded && !apiError" class="board-empty">loading sessions…</div>
  </div>
</template>

<style scoped>
.board {
  display: flex;
  flex-direction: column;
}

.board-head {
  padding: 16px 24px 0;
  font-size: 16px;
}

.error-bar {
  margin: 14px 24px 0;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: var(--red);
  font-size: 15px;
}

.columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  padding: 16px 24px 28px;
  align-items: start;
}

.col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.col-head {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 0;
  background: none;
  border: none;
  font-size: 15px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--dim);
  cursor: pointer;
}

.col-head:hover {
  color: var(--text);
}

.col-head .chev {
  flex: none;
  color: var(--faint);
}

.col-head .dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}

.col-count {
  margin-left: auto;
  color: var(--faint);
  font-size: 14px;
}

.cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 60px;
}

.card {
  display: block;
  background: rgba(11, 15, 24, 0.66);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  text-decoration: none;
  color: var(--text);
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.card-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.card-archive {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--faint);
  cursor: pointer;
}

.card-archive:hover {
  color: var(--text);
  background: rgba(200, 155, 255, 0.12);
}

.card:hover {
  border-color: rgba(200, 155, 255, 0.45);
  transform: translateY(-1px);
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.adw {
  font-weight: 700;
  font-size: 15px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.req {
  margin: 6px 0 0;
  font-size: 14px;
  color: var(--dim);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.6em;
}

.meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
  color: var(--faint);
}

.engineer {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time {
  flex: none;
}

.stats {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 13px;
  color: var(--faint);
}

.empty {
  padding: 18px 0;
  text-align: center;
  color: var(--faint);
  font-size: 14px;
  border: 1px dashed var(--border);
  border-radius: 12px;
}

.board-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--faint);
  font-size: 16px;
}
</style>
