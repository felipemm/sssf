<script setup lang="ts">
import { computed, ref } from 'vue'
import { LoaderCircle, Play, RotateCw, Undo2 } from 'lucide-vue-next'
import { backlogTicket, runTicket } from '../lib/api'
import type { Ticket } from '../lib/api'

const props = defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ open: [ticket: Ticket]; ran: []; backlogged: [] }>()

const BADGE: Record<string, string> = { jira: 'J', linear: 'L', internal: '⚙' }
const running = ref(false)
const moving = ref(false)

// A retry ticket: it sits in the backlog but already has runs behind it (a
// failed attempt the healer or the operator moved back). Distinct accent.
const retry = computed(() => props.ticket.status === 'backlog' && props.ticket.runs.length >= 1)
const failed = computed(() => props.ticket.status === 'failed')
const starting = computed(() => props.ticket.status === 'starting')

const statusLabel = computed(() => {
  if (starting.value) return 'starting…'
  if (failed.value) return 'failed'
  if (retry.value) return 'retry'
  return props.ticket.status
})

// Run lives on the card, not in the modal. The card body opens the modal;
// this button spawns the ADW directly and the board refetches on 'ran'.
async function run(event: MouseEvent) {
  event.stopPropagation()
  running.value = true
  try {
    await runTicket(props.ticket.id)
    emit('ran')
  } catch {
    /* the next poll reconciles */
  } finally {
    running.value = false
  }
}

// Manual fallback for a failed run — the healer usually beats you to it.
// History is preserved: the failed run stays in the ticket's run list.
async function toBacklog(event: MouseEvent) {
  event.stopPropagation()
  moving.value = true
  try {
    await backlogTicket(props.ticket.id)
    emit('backlogged')
  } catch {
    /* the next poll reconciles */
  } finally {
    moving.value = false
  }
}
</script>

<template>
  <div class="ticket" :class="{ retry, failed }">
    <button class="ticket-open" type="button" @click="emit('open', ticket)">
      <span class="badge">{{ BADGE[ticket.provider] ?? '?' }}</span>
      <span class="t-title">{{ ticket.title }}</span>
      <span class="t-meta dim" :class="{ starting }">
        <LoaderCircle v-if="starting" class="spin" :size="12" />
        <RotateCw v-else-if="retry" class="retry-ico" :size="12" />
        {{ ticket.external_id || ticket.id }} · {{ statusLabel }}
        <span v-if="retry" class="runs-chip" :title="`${ticket.runs.length} run(s) — see the modal for the trace of each`">
          {{ ticket.runs.length }} run{{ ticket.runs.length > 1 ? 's' : '' }}
        </span>
      </span>
    </button>
    <button
      v-if="failed"
      class="ticket-act ticket-backlog"
      type="button"
      :disabled="moving"
      :title="'Put this ticket back into the backlog (keeps the failed run in its history)'"
      aria-label="Back to backlog"
      @click="toBacklog"
    >
      <Undo2 :size="14" :stroke-width="2" />
    </button>
    <button
      class="ticket-act ticket-run"
      type="button"
      :disabled="running || starting"
      :title="starting ? 'Starting — the run is warming up' : (running ? 'Starting…' : 'Run this ticket (spawn simple_sdlc)')"
      aria-label="Run ticket"
      @click="run"
    >
      <Play :size="14" :stroke-width="2" />
    </button>
  </div>
</template>

<style scoped>
.ticket {
  position: relative;
  display: block;
  background: rgba(11, 15, 24, 0.66);
  border: 1px dashed rgba(232, 182, 74, 0.5);   /* distinct from session cards */
  border-radius: 12px;
}
.ticket:hover {
  border-color: rgba(232, 182, 74, 0.9);
}
/* A retry ticket: at least one run behind it — rose accent + runs chip. */
.ticket.retry {
  border-color: rgba(251, 113, 133, 0.55);
  border-style: solid;
}
.ticket.retry:hover {
  border-color: rgba(251, 113, 133, 0.9);
}
.ticket.retry .badge {
  background: rgba(251, 113, 133, 0.18);
  color: #fb7185;
}
.ticket.failed {
  border-color: rgba(248, 113, 113, 0.7);
  border-style: solid;
}
.ticket-open {
  display: block;
  width: 100%;
  text-align: left;
  padding: 12px 72px 12px 14px;   /* right padding clears the action buttons */
  background: none;
  border: none;
  color: var(--text);
  cursor: pointer;
}
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  margin-right: 8px;
  background: rgba(232, 182, 74, 0.18);
  color: #e8b64a;
  font-weight: 700;
  font-size: 13px;
}
.t-title {
  font-weight: 700;
  font-size: 14px;
}
.t-meta {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}
.ticket-act {
  position: absolute;
  top: 10px;
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--faint);
  cursor: pointer;
}
.ticket-act:hover {
  color: var(--text);
  background: rgba(232, 182, 74, 0.15);
}
.ticket-act:disabled {
  opacity: 0.5;
  cursor: default;
}
.ticket-run {
  right: 12px;
}
.ticket-backlog {
  right: 42px;
}
.ticket-backlog:hover {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
}
.t-meta.starting {
  color: #e8b64a;
}
.spin {
  display: inline-block;
  vertical-align: -2px;
  margin-right: 4px;
  animation: spin 0.9s linear infinite;
}
.retry-ico {
  display: inline-block;
  vertical-align: -2px;
  margin-right: 4px;
  color: #fb7185;
}
.runs-chip {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(251, 113, 133, 0.18);
  color: #fb7185;
  font-size: 11px;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
/* a starting ticket flashes gently so it stands out in the backlog */
.ticket:has(.t-meta.starting) {
  animation: flash 1.4s ease-in-out infinite;
}
@keyframes flash {
  0%, 100% { border-color: rgba(232, 182, 74, 0.5); }
  50% { border-color: rgba(232, 182, 74, 0.95); box-shadow: 0 0 10px rgba(232, 182, 74, 0.25); }
}
</style>
