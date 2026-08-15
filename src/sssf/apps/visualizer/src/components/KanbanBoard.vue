<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import type { SessionSummary } from '../lib/types'
import { fetchSessions } from '../lib/api'
import { fmtCost, fmtDate, fmtTokens, ts } from '../lib/format'
import { hrefFor } from '../lib/router'
import PhaseDots from './PhaseDots.vue'

// A read-only status board: sessions grouped by their run state. Status is
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

const COLUMNS = [
  { status: 'running', label: 'running', accent: 'var(--blue)' },
  { status: 'success', label: 'success', accent: 'var(--green)' },
  { status: 'fail', label: 'fail', accent: 'var(--red)' },
] as const

const byStatus = computed(() => {
  const groups: Record<string, SessionSummary[]> = { running: [], success: [], fail: [] }
  for (const s of sessions.value) {
    const status = s.status ?? 'fail'
    ;(groups[status] ?? groups.fail).push(s)
  }
  for (const list of Object.values(groups)) {
    list.sort((a, b) => (ts(b.started_at) || 0) - (ts(a.started_at) || 0))
  }
  return groups
})

const total = computed(() => sessions.value.length)
</script>

<template>
  <div class="board">
    <div v-if="apiError" class="error-bar">api unreachable — retrying {{ apiError }}</div>

    <div v-if="loaded && total" class="board-head dim">
      {{ total }} runs · grouped by status — click a card for its trace
    </div>

    <div class="columns">
      <section v-for="col in COLUMNS" :key="col.status" class="col">
        <header class="col-head">
          <span class="dot" :style="{ background: col.accent }" />
          <span class="col-name">{{ col.label }}</span>
          <span class="col-count">{{ byStatus[col.status].length }}</span>
        </header>

        <div class="cards">
          <a
            v-for="s in byStatus[col.status]"
            :key="s.adw_id"
            class="card"
            :href="hrefFor(s.adw_id)"
          >
            <div class="card-top">
              <span class="adw" :title="s.adw_id">{{ s.adw_name || s.adw_id }}</span>
              <PhaseDots :phases="s.phases" />
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

          <div v-if="loaded && !byStatus[col.status].length" class="empty">no runs</div>
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
  font-size: 15px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--dim);
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
