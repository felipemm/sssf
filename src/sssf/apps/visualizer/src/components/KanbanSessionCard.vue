<script setup lang="ts">
import { Archive, ArchiveRestore, RotateCw, Square } from 'lucide-vue-next'
import type { SessionSummary } from '../lib/types'
import { archiveSession, restartRun, stopRun } from '../lib/api'
import { fmtCost, fmtDate, fmtTokens } from '../lib/format'
import { hrefFor } from '../lib/router'
import PhaseDots from './PhaseDots.vue'

// Lightweight kanban card — the classic board format, intentionally much
// lighter than the full SessionCard (sessions/archived pages): the ADW name,
// L0 phase dots, archive/stop/restart, the request, engineer + date, and
// tokens/cost. No per-card event streams, no timeline.
const props = defineProps<{ session: SessionSummary }>()
const emit = defineEmits<{ changed: [adwId: string] }>()

// The card is an <a>; the buttons inside must not navigate. The parent polls
// every 500 ms — a failed write just re-syncs on the next tick.
async function restart(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  try {
    await restartRun(props.session.adw_id)
  } catch { /* the next poll reconciles */ }
  emit('changed', props.session.adw_id)
}

async function stop(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  try {
    await stopRun(props.session.adw_id)
  } catch { /* the next poll reconciles */ }
  emit('changed', props.session.adw_id)
}

async function archive(event: MouseEvent) {
  event.preventDefault()
  event.stopPropagation()
  try {
    await archiveSession(props.session.adw_id)
  } catch { /* the next poll reconciles */ }
  emit('changed', props.session.adw_id)
}
</script>

<template>
  <a class="card" :href="hrefFor({ adwId: session.adw_id })">
    <div class="card-top">
      <span class="adw" :title="session.adw_id">{{ session.adw_name || session.adw_id }}</span>
      <span class="card-actions">
        <PhaseDots :phases="session.phases ?? []" />
        <button
          class="card-archive"
          type="button"
          :disabled="session.status !== 'success' && session.status !== 'fail'"
          :title="session.status === 'running' ? 'Running — archive available once done or failed' : 'Archive — remove this run from review'"
          aria-label="Archive run"
          @click="archive"
        >
          <Archive v-if="!session.archived" :size="15" :stroke-width="2" />
          <ArchiveRestore v-else :size="15" :stroke-width="2" />
        </button>
        <button
          v-if="session.status === 'running'"
          class="card-archive card-second"
          type="button"
          title="Stop — cancel this run (marked failed, sandbox torn down)"
          aria-label="Stop run"
          @click="stop"
        >
          <Square :size="15" :stroke-width="2" />
        </button>
        <button
          v-if="session.status === 'success' || session.status === 'fail'"
          class="card-archive card-second"
          type="button"
          title="Restart — re-run this session in a fresh sandbox"
          aria-label="Restart run"
          @click="restart"
        >
          <RotateCw :size="15" :stroke-width="2" />
        </button>
      </span>
    </div>
    <p class="req" :title="session.request ?? ''">{{ session.request || '—' }}</p>
    <div class="meta">
      <span class="engineer">{{ session.engineer }}</span>
      <span class="time">{{ fmtDate(session.started_at) }}</span>
    </div>
    <div class="stats">
      <span>{{ fmtTokens(session.total_tokens) }} tok</span>
      <span>{{ fmtCost(session.total_cost) }}</span>
    </div>
  </a>
</template>

<style scoped>
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
.card-archive:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
/* A second action beside the archive button (stop/restart). */
.card-second {
  right: 46px;
}
.card-archive:disabled:hover {
  color: var(--faint);
  background: none;
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

.stats {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 13px;
  color: var(--text);
}

.stats span:first-child {
  color: var(--faint);
}
</style>
