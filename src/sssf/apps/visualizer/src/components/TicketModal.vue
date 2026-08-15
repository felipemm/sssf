<script setup lang="ts">
import { ExternalLink, X } from 'lucide-vue-next'
import type { Ticket } from '../lib/api'

defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true" aria-label="ticket">
      <header class="m-head">
        <span class="badge">{{ { jira: 'J', linear: 'L', internal: '⚙' }[ticket.provider] ?? '?' }}</span>
        <span class="m-title">{{ ticket.title }}</span>
        <button class="icon" type="button" aria-label="Close" @click="emit('close')"><X :size="18" /></button>
      </header>

      <p class="m-origin dim">
        {{ ticket.external_id || ticket.id }} · {{ ticket.status }}
        <a v-if="ticket.source_url" :href="ticket.source_url" target="_blank" rel="noreferrer">
          <ExternalLink :size="13" /> source
        </a>
      </p>

      <div class="m-body">{{ ticket.description || 'no description' }}</div>

      <p v-if="ticket.prompt_file" class="m-link dim">prompt: <code>{{ ticket.prompt_file }}</code></p>
      <p v-if="ticket.adw_id" class="m-link dim">run: <code>{{ ticket.adw_id }}</code></p>

      <footer class="m-foot">
        <button class="btn" type="button" @click="emit('close')">Close</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal {
  width: min(560px, 92vw);
  max-height: 80vh;
  overflow: auto;
  background: #0b0f18;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px 20px;
}
.m-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  flex: none;
  background: rgba(232, 182, 74, 0.18);
  color: #e8b64a;
  font-weight: 700;
}
.m-title {
  font-weight: 700;
  font-size: 17px;
  flex: 1;
}
.icon {
  background: none;
  border: none;
  color: var(--faint);
  cursor: pointer;
}
.m-origin {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 8px 0 0;
  font-size: 13px;
}
.m-origin a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--purple);
}
.m-body {
  margin-top: 14px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.m-link {
  margin-top: 8px;
  font-size: 13px;
}
.m-link code {
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 6px;
  border-radius: 4px;
}
.m-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  cursor: pointer;
}
</style>
