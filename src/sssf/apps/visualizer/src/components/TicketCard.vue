<script setup lang="ts">
import { computed, ref } from 'vue'
import { LoaderCircle, Play, RotateCw, Undo2 } from 'lucide-vue-next'
import { backlogTicket, runTicket } from '../lib/api'
import { notify } from '../lib/toast'
import type { Ticket } from '../lib/api'
import { isBlockedTicket, isRetryTicket, isRunnableTicket } from '../lib/ticketFilters'

const props = defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ open: [ticket: Ticket]; ran: []; backlogged: [] }>()

const BADGE: Record<string, string> = { jira: 'J', linear: 'L', internal: '⚙' }
const running = ref(false)
const moving = ref(false)

// Requeued ready-for-agent tickets with run history are retries; blocked
// tickets retain the manual edge back to ready-for-agent.
const retry = computed(() => isRetryTicket(props.ticket))
const blocked = computed(() => isBlockedTicket(props.ticket))
const runnable = computed(() => isRunnableTicket(props.ticket))

const statusLabel = computed(() => retry.value ? 'retry' : props.ticket.status)

// The card body opens the modal; this action uses the same implement flow as
// the modal and board refetches on 'ran'.
async function run(event: MouseEvent) {
  event.stopPropagation()
  running.value = true
  try {
    const res = await runTicket(props.ticket.id)
    if (!res.ok) {
      notify(res.output || 'run failed — see the ticket modal')
      return
    }
    emit('ran')
  } catch (e) {
    notify(String(e))
  } finally {
    running.value = false
  }
}

// A blocked ticket can be requeued manually; its run history is preserved.
async function toBacklog(event: MouseEvent) {
  event.stopPropagation()
  moving.value = true
  try {
    const res = await backlogTicket(props.ticket.id)
    if (!res.ok) {
      notify(res.output || 'move to backlog failed')
      return
    }
    emit('backlogged')
  } catch (e) {
    notify(String(e))
  } finally {
    moving.value = false
  }
}
</script>

<template>
  <div class="ticket" :class="{ retry, blocked }">
    <button class="ticket-open" type="button" @click="emit('open', ticket)">
      <span class="sr-only">Open ticket: </span>
      <span class="badge">{{ BADGE[ticket.provider] ?? '?' }}</span>
      <span class="t-title">{{ ticket.title }}</span>
      <span class="t-meta dim">
        <RotateCw v-if="retry" class="retry-ico" :size="12" />
        {{ ticket.external_id || ticket.id }} · {{ statusLabel }}
        <span v-if="retry" class="runs-chip" :title="`${ticket.runs.length} run(s) — see the modal for the trace of each`">
          {{ ticket.runs.length }} run{{ ticket.runs.length > 1 ? 's' : '' }}
        </span>
      </span>
    </button>
    <button
      v-if="blocked"
      class="ticket-act ticket-backlog"
      type="button"
      :disabled="moving"
      :aria-busy="moving"
      :title="'Move this blocked ticket back to ready-for-agent (keeps run history)'"
      aria-label="Back to backlog"
      @click="toBacklog"
    >
      <LoaderCircle v-if="moving" class="spin" :size="14" :stroke-width="2" />
      <Undo2 v-else :size="14" :stroke-width="2" />
    </button>
    <button
      v-if="runnable"
      class="ticket-act ticket-run"
      type="button"
      :disabled="running"
      :aria-busy="running"
      :title="running ? 'Starting…' : 'Run this ticket via flow implement'"
      aria-label="Run ticket"
      @click="run"
    >
      <LoaderCircle v-if="running" class="spin" :size="14" :stroke-width="2" />
      <Play v-else :size="14" :stroke-width="2" />
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
.ticket.blocked {
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
</style>
