<script setup lang="ts">
import { ref } from 'vue'
import { LoaderCircle, Play } from 'lucide-vue-next'
import { runTicket } from '../lib/api'
import type { Ticket } from '../lib/api'

const props = defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ open: [ticket: Ticket]; ran: [] }>()

const BADGE: Record<string, string> = { jira: 'J', linear: 'L', internal: '⚙' }
const running = ref(false)

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
</script>

<template>
  <div class="ticket">
    <button class="ticket-open" type="button" @click="emit('open', ticket)">
      <span class="badge">{{ BADGE[ticket.provider] ?? '?' }}</span>
      <span class="t-title">{{ ticket.title }}</span>
      <span class="t-meta dim" :class="{ starting: ticket.status === 'starting' }">
        <LoaderCircle v-if="ticket.status === 'starting'" class="spin" :size="12" />
        {{ ticket.external_id || ticket.id }} · {{ ticket.status === 'starting' ? 'starting…' : ticket.status }}
      </span>
    </button>
    <button
      class="ticket-run"
      type="button"
      :disabled="running || ticket.status === 'starting'"
      :title="ticket.status === 'starting' ? 'Starting — the run is warming up' : (running ? 'Starting…' : 'Run this ticket (spawn simple_sdlc)')"
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
.ticket-open {
  display: block;
  width: 100%;
  text-align: left;
  padding: 12px 40px 12px 14px;   /* right padding clears the run button */
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
.ticket-run {
  position: absolute;
  top: 10px;
  right: 12px;
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
.ticket-run:hover {
  color: var(--text);
  background: rgba(232, 182, 74, 0.15);
}
.ticket-run:disabled {
  opacity: 0.5;
  cursor: default;
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
