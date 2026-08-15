<script setup lang="ts">
import { computed, ref } from 'vue'
import { Recycle } from 'lucide-vue-next'
import { useRoute, hrefFor, phaseCrumb, navigate } from './lib/router'
import { runSweep, setProject } from './lib/api'
import SessionsList from './components/SessionsList.vue'
import SessionTrace from './components/SessionTrace.vue'
import KanbanBoard from './components/KanbanBoard.vue'
import ProjectPicker from './components/ProjectPicker.vue'

const route = useRoute()

// #/board and #/archived are peers of the sessions list over the same data;
// everything else with an adwId is the trace view.
const view = computed(() => {
  const id = route.value.adwId
  if (id === 'board') return 'board'
  if (id === 'archived') return 'archived'
  return id ? 'trace' : 'list'
})
// In the trace branch adwId is non-null by construction; the template can't
// narrow the ref, so hand it a computed string.
const traceAdwId = computed(() => route.value.adwId ?? '')

// A project switch changes the meaning of every hash route, so land on the
// L1 sessions list; the picker itself reloads the project list on mount.
function onProjectSelect(name: string) {
  setProject(name)
  navigate()
}

// Manual archival sweep across every registered project — the `sssf sweep` CLI
// as a topbar button. Result note is transient (5 s).
const sweeping = ref(false)
const sweepNote = ref('')
let sweepTimer: ReturnType<typeof setTimeout> | undefined

async function onSweep() {
  if (sweeping.value) return
  sweeping.value = true
  sweepNote.value = ''
  try {
    const results = await runSweep()
    const archived = results.reduce((n, r) => n + r.archived, 0)
    const errors = results.filter((r) => r.error).length
    sweepNote.value = errors
      ? `${archived} archived · ${errors} error(s)`
      : `${archived} session(s) archived`
  } catch {
    sweepNote.value = 'sweep failed'
  } finally {
    sweeping.value = false
    clearTimeout(sweepTimer)
    sweepTimer = setTimeout(() => (sweepNote.value = ''), 5000)
  }
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <nav class="crumbs">
        <a :href="hrefFor()" class="home" title="back to sessions">
          <!-- Inline copy of public/logo.svg (the favicon) so the mark renders
               crisply with no fetch; keep the two in sync. -->
          <svg class="logo" viewBox="0 0 32 32" aria-hidden="true">
            <rect x="4" y="6" width="17" height="5" rx="2.5" fill="#e8b64a" />
            <rect x="8" y="13.5" width="20" height="5" rx="2.5" fill="#c89bff" />
            <rect x="4" y="21" width="13" height="5" rx="2.5" fill="#5ad2dd" />
          </svg>
          <span class="brand">Super Simple Software Factory</span>
        </a>
        <span class="sep">›</span>
        <a :href="hrefFor()" :class="{ current: view === 'list' }">sessions</a>
        <a :href="hrefFor('board')" :class="{ current: view === 'board' }">board</a>
        <a :href="hrefFor('archived')" :class="{ current: view === 'archived' }">archived</a>
        <template v-if="view === 'trace' && route.adwId">
          <span class="sep">›</span>
          <a :href="hrefFor(route.adwId)" :class="{ current: !route.phaseId }">{{ route.adwId }}</a>
        </template>
        <template v-if="view === 'trace' && route.adwId && route.phaseId">
          <span class="sep">›</span>
          <span class="current">{{ phaseCrumb ?? route.phaseId }}</span>
        </template>
      </nav>
      <ProjectPicker @select="onProjectSelect" />
      <button
        class="sweep-btn"
        type="button"
        :disabled="sweeping"
        :title="sweeping ? 'Sweeping…' : 'Archive finished sessions older than 30 days (all projects)'"
        aria-label="Run the archive sweep"
        @click="onSweep"
      >
        <Recycle :size="16" :stroke-width="2" />
      </button>
      <span v-if="sweepNote" class="sweep-note dim">{{ sweepNote }}</span>
      <span class="live-hint"><span class="live-dot" /> live</span>
    </header>
    <main>
      <KanbanBoard v-if="view === 'board'" />
      <SessionsList v-else-if="view === 'list'" />
      <SessionsList v-else-if="view === 'archived'" archived />
      <SessionTrace v-else :key="traceAdwId" :adw-id="traceAdwId" :phase-id="route.phaseId" />
    </main>
  </div>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 15px 28px;
  background: rgba(11, 15, 24, 0.72);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  position: sticky;
  top: 0;
  z-index: 10;
}

/* Gradient hairline instead of a hard border — the brand colors, whispered. */
.topbar::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 1px;
  background: linear-gradient(
    90deg,
    rgba(200, 155, 255, 0.45),
    rgba(90, 210, 221, 0.35) 40%,
    rgba(90, 210, 221, 0.06)
  );
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 17px;
  min-width: 0;
}

.logo {
  width: 28px;
  height: 28px;
  flex: none;
  filter: drop-shadow(0 0 8px rgba(200, 155, 255, 0.35));
}

.brand {
  background: linear-gradient(90deg, var(--purple), var(--cyan));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-weight: 700;
  letter-spacing: 0.05em;
  white-space: nowrap;
}

.sep {
  color: var(--faint);
}

.crumbs a {
  color: var(--dim);
}

.crumbs a:hover {
  color: var(--text);
}

.crumbs .home {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
}

.crumbs .home:hover {
  color: var(--text);
}

.crumbs .current {
  color: var(--text);
}

.live-hint {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--dim);
  font-size: 16px;
  white-space: nowrap;
}

.live-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 10px rgba(74, 222, 128, 0.7);
  animation: pulse 1.6s ease-in-out infinite;
}

.sweep-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: rgba(11, 15, 24, 0.9);
  color: var(--dim);
  cursor: pointer;
}

.sweep-btn:hover {
  color: var(--text);
  border-color: rgba(200, 155, 255, 0.5);
}

.sweep-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.sweep-note {
  font-size: 13px;
  white-space: nowrap;
}
</style>
