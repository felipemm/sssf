<script setup lang="ts">
import { computed } from 'vue'
import { Archive, ArchiveRestore, RotateCw, Square, User } from 'lucide-vue-next'
import type { SessionSummary } from '../lib/types'
import { archiveSession, restartRun, stopRun } from '../lib/api'
import { runDurationMs } from '../lib/duration'
import { hrefFor } from '../lib/router'
import { adwIconFor } from '../lib/adwIcons'
import PhaseDots from './PhaseDots.vue'
import StatChip from './StatChip.vue'

// Lightweight kanban card: same header family as the full SessionCard (ADW
// icon + session id + inline actions, L0 phase dots, 2-line request, username,
// duration/tokens/cost) but WITHOUT the event-tail polling or the full
// timeline — it stays cheap with many cards on the board.
const props = defineProps<{ session: SessionSummary; nowMs: number }>()
const emit = defineEmits<{ changed: [adwId: string] }>()

const adwIcon = computed(() => adwIconFor(props.session.adw_name))

// Total run time is the sum of each phase's duration, NOT the session row's
// wall-clock span — a re-run joins the same row, so the span would count the
// idle gap between attempts (09:00 run + 21:00 re-run = a 12-hour "run").
const durationMs = computed(() => runDurationMs(props.session, props.nowMs))

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
    <div class="card-head">
      <span class="adw-icon" :title="session.adw_name ?? ''">
        <component :is="adwIcon" :size="15" :stroke-width="2" />
      </span>
      <span class="card-id">{{ session.adw_id }}</span>
      <span class="head-actions">
        <button
          class="card-archive"
          type="button"
          :disabled="session.status !== 'success' && session.status !== 'fail'"
          :title="session.status === 'running' ? 'Running — archive available once done or failed' : 'Archive — remove this run from review'"
          aria-label="Archive run"
          @click="archive"
        >
          <Archive v-if="!session.archived" :size="14" :stroke-width="2" />
          <ArchiveRestore v-else :size="14" :stroke-width="2" />
        </button>
        <button
          v-if="session.status === 'running'"
          class="card-archive"
          type="button"
          title="Stop — cancel this run (marked failed, sandbox torn down)"
          aria-label="Stop run"
          @click="stop"
        >
          <Square :size="14" :stroke-width="2" />
        </button>
        <button
          v-if="session.status === 'success' || session.status === 'fail'"
          class="card-archive"
          type="button"
          title="Restart — re-run this session in a fresh sandbox"
          aria-label="Restart run"
          @click="restart"
        >
          <RotateCw :size="14" :stroke-width="2" />
        </button>
      </span>
    </div>

    <PhaseDots :phases="session.phases ?? []" class="dots" />

    <p class="req" :title="session.request ?? ''">{{ session.request || '—' }}</p>
    <span class="user"><User :size="11" :stroke-width="2" />{{ session.engineer ?? '—' }}</span>
    <div class="card-stats">
      <span class="stats-left">
        <StatChip kind="runtime" :value="durationMs" />
        <StatChip kind="tokens" :value="session.total_tokens" />
      </span>
      <StatChip kind="cost" :value="session.total_cost" />
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
.card:hover {
  border-color: rgba(200, 155, 255, 0.45);
  transform: translateY(-1px);
}
.card.running {
  border-color: rgba(108, 182, 255, 0.6);
}
.card.fail {
  border-color: rgba(255, 111, 103, 0.6);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.adw-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: rgba(200, 155, 255, 0.12);
  border: 1px solid rgba(200, 155, 255, 0.3);
  color: var(--purple);
}
.card-id {
  flex: none;
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--purple);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.head-actions {
  flex: none;
  display: inline-flex;
  gap: 2px;
  margin-left: auto;
}
.card-archive {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--faint);
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.card-archive:hover {
  background: rgba(255, 111, 103, 0.16);
  color: #ff6f67;
  border-color: rgba(255, 111, 103, 0.35);
}
.card-archive:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.card-archive:disabled:hover {
  background: none;
  color: var(--faint);
  border-color: transparent;
}

.dots {
  margin-top: 6px;
}

.req {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.35;
  color: var(--text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.6em;
}

.user {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.user svg {
  flex: none;
}

.card-stats {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.stats-left {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
/* StatChip is sized for the full SessionCard — shrink it here only */
.card-stats :deep(.stat) {
  font-size: 12px;
  padding: 1px 8px;
  gap: 5px;
}
.card-stats :deep(.stat-icon) {
  width: 13px;
  height: 13px;
}
</style>
