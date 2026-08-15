<script setup lang="ts">
import type { Ticket } from '../lib/api'

defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ open: [ticket: Ticket] }>()

const BADGE: Record<string, string> = { jira: 'J', linear: 'L', internal: '⚙' }
</script>

<template>
  <button class="ticket" type="button" @click="emit('open', ticket)">
    <span class="badge">{{ BADGE[ticket.provider] ?? '?' }}</span>
    <span class="t-title">{{ ticket.title }}</span>
    <span class="t-meta dim">
      {{ ticket.external_id || ticket.id }} · {{ ticket.status }}
    </span>
  </button>
</template>

<style scoped>
.ticket {
  display: block;
  width: 100%;
  text-align: left;
  background: rgba(11, 15, 24, 0.66);
  border: 1px dashed rgba(232, 182, 74, 0.5);   /* distinct from session cards */
  border-radius: 12px;
  padding: 12px 14px;
  color: var(--text);
  cursor: pointer;
}
.ticket:hover {
  border-color: rgba(232, 182, 74, 0.9);
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
</style>
