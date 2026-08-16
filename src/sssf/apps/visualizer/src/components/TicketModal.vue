<script setup lang="ts">
import { CircleCheck, CircleX, ExternalLink, LoaderCircle, X } from 'lucide-vue-next'
import type { Ticket, TicketRun } from '../lib/api'

const props = defineProps<{ ticket: Ticket }>()
const emit = defineEmits<{ close: [] }>()

const BADGE: Record<string, string> = { jira: 'J', linear: 'L', internal: '⚙' }

function runStatus(run: TicketRun): { label: string; cls: string } {
  if (run.status === 'success') return { label: 'success', cls: 'ok' }
  if (run.status === 'fail') return { label: 'failed', cls: 'fail' }
  if (run.status === 'running') return { label: 'running', cls: 'run' }
  return { label: 'no session', cls: 'dim' }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true" aria-label="ticket">
      <header class="m-head">
        <span class="badge">{{ BADGE[ticket.provider] ?? '?' }}</span>
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

      <div v-if="ticket.runs.length" class="m-runs">
        <h4 class="runs-head">{{ ticket.runs.length }} run{{ ticket.runs.length > 1 ? 's' : '' }}</h4>
        <ul class="runs-list">
          <li v-for="r in ticket.runs" :key="r.adw_id" class="run-row">
            <span class="run-state" :class="runStatus(r).cls">
              <CircleCheck v-if="r.status === 'success'" :size="13" />
              <CircleX v-else-if="r.status === 'fail'" :size="13" />
              <LoaderCircle v-else-if="r.status === 'running'" class="spin" :size="13" />
              <span v-else class="dot" />
              {{ runStatus(r).label }}
            </span>
            <a class="run-link" :href="`#/${r.adw_id}`" target="_blank" rel="noreferrer" @click="emit('close')">
              <code>{{ r.adw_id }}</code> <ExternalLink :size="12" />
            </a>
            <span class="run-when dim">
              {{ r.started_at ? r.started_at.replace('T', ' ').slice(0, 16) + 'Z' : '' }}
              <template v-if="r.started_at && r.ended_at">→ {{ r.ended_at.replace('T', ' ').slice(11, 16) }}Z</template>
            </span>
          </li>
        </ul>
      </div>

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
.m-runs {
  margin-top: 14px;
}
.runs-head {
  margin: 0 0 6px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--faint);
}
.runs-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}
.run-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  padding: 5px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
}
.run-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 84px;
}
.run-state.ok { color: #34d399; }
.run-state.fail { color: #f87171; }
.run-state.run { color: #e8b64a; }
.run-state .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--faint);
}
.spin {
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.run-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--purple);
  text-decoration: none;
}
.run-link code {
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 6px;
  border-radius: 4px;
}
.run-when {
  margin-left: auto;
  font-size: 12px;
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
