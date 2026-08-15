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

// The board is the default landing page: #/ and #/board both show it.
// #/sessions, #/archived are the other views; anything else is a trace.
const view = computed(() => {
  const id = route.value.adwId
  if (!id || id === 'board') return 'board'
  if (id === 'sessions') return 'list'
  if (id === 'archived') return 'archived'
  return 'trace'
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
    sweepTimer = setTimeout(() => (sweepNote.value = ''), 4000)
  }
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <nav class="crumbs">
        <a :href="hrefFor()" class="home" title="board">
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
        <div class="tabs" role="tablist" aria-label="views">
          <a
            :href="hrefFor()"
            class="tab"
            :class="{ active: view === 'board' }"
            role="tab"
            :aria-selected="view === 'board'"
            >board</a
          >
          <a
            :href="hrefFor('sessions')"
            class="tab"
            :class="{ active: view === 'list' }"
            role="tab"
            :aria-selected="view === 'list'"
            >sessions</a
          >
          <a
            :href="hrefFor('archived')"
            class="tab"
            :class="{ active: view === 'archived' }"
            role="tab"
            :aria-selected="view === 'archived'"
            >archived</a
          >
        </div>
        <template v-if="view === 'trace' && route.adwId">
          <span class="sep">›</span>
          <a :href="hrefFor(route.adwId)" :class="{ current: !route.phaseId }">{{ route.adwId }}</a>
        </template>
        <template v-if="view === 'trace' && route.adwId && route.phaseId">
          <span class="sep">›</span>
          <span class="current">{{ phaseCrumb ?? route.phaseId }}</span>
        </template>
      </nav>
      <div class="topbar-right">
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
        <span class="live-hint"><span class="live-dot" /> live</span>
      </div>
    </header>
    <main>
      <KanbanBoard v-if="view === 'board'" />
      <SessionsList v-else-if="view === 'list'" />
      <SessionsList v-else-if="view === 'archived'" archived />
      <SessionTrace v-else :key="traceAdwId" :adw-id="traceAdwId" :phase-id="route.phaseId" />
    </main>

    <!-- Transient sweep result — floats over the app, never displaces the header. -->
    <Transition name="toast">
      <div v-if="sweepNote" class="sweep-toast" role="status" @click="sweepNote = ''">
        {{ sweepNote }}
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.app {
  /* Shared with .topbar (height) and the trace view (viewport fit). */
  --topbar-height: 66px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 15px 28px;
  height: var(--topbar-height);
  box-sizing: border-box;
  background: rgba(11, 15, 24, 0.72);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  position: sticky;
  top: 0;
  z-index: 10;
}

/* Project selector, sweep button and live status stay packed on the right. */
.topbar-right {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: none;
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

/* View tabs: sessions | board | archived, active one highlighted. */
.tabs {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border);
}

.tabs .tab {
  padding: 5px 14px;
  border-radius: 7px;
  font-size: 15px;
  color: var(--dim);
  text-decoration: none;
  transition: color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
}

.tabs .tab:hover {
  color: var(--text);
}

.tabs .tab.active {
  color: var(--text);
  background: rgba(200, 155, 255, 0.16);
  box-shadow: inset 0 0 0 1px rgba(200, 155, 255, 0.35);
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

.sweep-toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 60;
  padding: 10px 16px;
  border-radius: 10px;
  background: rgba(11, 15, 24, 0.95);
  border: 1px solid rgba(200, 155, 255, 0.35);
  color: var(--text);
  font-size: 14px;
  cursor: pointer;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
