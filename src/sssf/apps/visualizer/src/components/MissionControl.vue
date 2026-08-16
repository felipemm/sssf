<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Activity,
  HeartPulse,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCw,
  Square,
  Trash2,
} from 'lucide-vue-next'
import {
  addProject,
  fetchCockpit,
  healControl,
  refreshProject,
  removeProject,
  restartRun,
  stopRun,
} from '../lib/api'
import { navigate } from '../lib/router'
import type { CockpitData, ControlResult } from '../lib/types'

const data = ref<CockpitData | null>(null)
const loading = ref(false)
const error = ref('')
const note = ref('') // transient control feedback, like the sweep toast
let timer: ReturnType<typeof setInterval> | undefined
let noteTimer: ReturnType<typeof setTimeout> | undefined

const pending = ref<Set<string>>(new Set()) // in-flight control keys ("stop:run1", …)

const POLL_MS = 8000

function flashNote(msg: string) {
  note.value = msg
  clearTimeout(noteTimer)
  noteTimer = setTimeout(() => (note.value = ''), 4000)
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    data.value = await fetchCockpit()
  } catch {
    error.value = 'cockpit unreachable — is the viz server running?'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void refresh()
  timer = setInterval(() => void refresh(), POLL_MS)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  clearTimeout(noteTimer)
})

// ── controls ───────────────────────────────────────────────────────────────

async function control(key: string, fn: () => Promise<ControlResult>, okMsg: string) {
  if (pending.value.has(key)) return
  pending.value = new Set(pending.value).add(key)
  try {
    const res = await fn()
    flashNote(res.ok ? okMsg : `failed: ${res.error ?? res.output ?? 'unknown'}`)
    if (res.ok) void refresh()
  } catch {
    flashNote('control failed')
  } finally {
    const next = new Set(pending.value)
    next.delete(key)
    pending.value = next
  }
}

function onStop(project: string, adwId: string) {
  return control(`stop:${adwId}`, () => stopRun(project, adwId), `stopped ${adwId}`)
}
function onRestart(project: string, adwId: string) {
  return control(`restart:${adwId}`, () => restartRun(project, adwId), `restarting ${adwId}`)
}
function onRefresh(project: string) {
  return control(`refresh:${project}`, () => refreshProject(project), `refreshed ${project} templates`)
}
function onRemove(project: string) {
  if (!window.confirm(`remove "${project}" from the registry?`)) return
  return control(`remove:${project}`, () => removeProject(project, true), `removed ${project}`)
}
function onHeal(action: 'start' | 'stop') {
  return control(`heal:${action}`, () => healControl(action), `healer ${action === 'start' ? 'started' : 'stopped'}`)
}

// ── add project ────────────────────────────────────────────────────────────

const newRoot = ref('')
const adding = ref(false)
async function onAdd() {
  const root = newRoot.value.trim()
  if (!root || adding.value) return
  adding.value = true
  try {
    const res = await addProject(root)
    flashNote(res.ok ? `added ${root}` : `failed: ${res.error ?? ''}`)
    if (res.ok) {
      newRoot.value = ''
      void refresh()
    }
  } catch {
    flashNote('add failed')
  } finally {
    adding.value = false
  }
}

// ── presentation helpers ───────────────────────────────────────────────────

function fmtUsd(n: number): string {
  return `$${n.toFixed(2)}`
}
function fmtAge(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
function fmtRel(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.parse(iso)
  if (Number.isNaN(ms)) return '—'
  const diff = Date.now() - ms
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  return `${Math.floor(hr / 24)}d ago`
}
</script>

<template>
  <div class="mission-control">
    <div v-if="loading && !data" class="loading"><LoaderCircle :size="18" class="spin" /> polling…</div>
    <div v-else-if="error && !data" class="error">{{ error }}</div>

    <!-- KPI strip -->
    <div v-if="data" class="kpis">
      <div class="kpi"><span class="kpi-n">{{ data.kpis.runningSessions }}</span><span class="kpi-l">running sessions</span></div>
      <div class="kpi"><span class="kpi-n">{{ data.kpis.liveContainers }}</span><span class="kpi-l">containers<template v-if="data.kpis.orphanContainers"> · {{ data.kpis.orphanContainers }} orphan</template></span></div>
      <div class="kpi"><span class="kpi-n">{{ data.kpis.sandboxWorktrees }}</span><span class="kpi-l">sandboxes</span></div>
      <div class="kpi"><span class="kpi-n">{{ data.kpis.ticketsInFlight }}</span><span class="kpi-l">tickets in flight</span></div>
      <div class="kpi"><span class="kpi-n">{{ fmtUsd(data.kpis.costTodayUsd) }}</span><span class="kpi-l">cost today</span></div>
      <div class="kpi heal" :class="data.heal.running ? 'ok' : 'off'">
        <HeartPulse :size="16" />
        <div>
          <div class="kpi-n">{{ data.heal.running ? 'healing' : 'healer off' }}</div>
          <div class="heal-actions">
            <button v-if="!data.heal.running" class="mini" @click="onHeal('start')">start</button>
            <button v-else class="mini" @click="onHeal('stop')">stop</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Projects table -->
    <section v-if="data" class="panel">
      <h3>Projects <span class="count">{{ data.projects.length }}</span></h3>
      <div v-if="!data.projects.length" class="empty">
        no registered projects — add one below
      </div>
      <table v-else class="ptable">
        <thead>
          <tr>
            <th>project</th><th>running</th><th>today</th><th>tickets</th>
            <th>ctrs</th><th>wt</th><th>cost today</th><th>last activity</th><th class="actions">actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in data.projects" :key="p.name" :class="{ stale: p.stale }">
            <td>
              <a class="proj" :title="p.root" @click.prevent="navigate({ project: p.name, tab: 'status' })">
                {{ p.name }}
              </a>
            </td>
            <td>{{ p.sessionsRunning }}</td>
            <td :class="{ bad: p.sessionsFailedToday > 0 }">
              {{ p.sessionsToday }}<template v-if="p.sessionsFailedToday"> · {{ p.sessionsFailedToday }}✗</template>
            </td>
            <td>{{ p.ticketsInFlight }} / {{ p.ticketsBacklog }}</td>
            <td>{{ p.containers }}</td>
            <td>{{ p.worktrees }}</td>
            <td>{{ fmtUsd(p.costTodayUsd) }}</td>
            <td>{{ fmtRel(p.lastActivity) }}</td>
            <td class="actions">
              <button class="icon" title="refresh templates" :disabled="pending.has(`refresh:${p.name}`)" @click="onRefresh(p.name)">
                <RefreshCw :size="14" :class="{ spin: pending.has(`refresh:${p.name}`) }" />
              </button>
              <button class="icon" title="remove from registry" @click="onRemove(p.name)">
                <Trash2 :size="14" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Running-now strip -->
    <section v-if="data" class="panel">
      <h3>Running now <span class="count">{{ data.running.length }}</span></h3>
      <div v-if="!data.running.length" class="empty">nothing running across projects</div>
      <div v-else class="runs">
        <div v-for="r in data.running" :key="r.adwId" class="run">
          <span class="chip">{{ r.project }}</span>
          <code>{{ r.adwId }}</code>
          <span class="phase">{{ r.phase ?? '—' }}</span>
          <span class="age">{{ fmtAge(r.ageSec) }}</span>
          <span class="run-actions">
            <button class="icon" title="stop" :disabled="pending.has(`stop:${r.adwId}`)" @click="onStop(r.project, r.adwId)">
              <Square :size="13" />
            </button>
            <button class="icon" title="restart" :disabled="pending.has(`restart:${r.adwId}`)" @click="onRestart(r.project, r.adwId)">
              <RotateCw :size="13" />
            </button>
          </span>
        </div>
      </div>
    </section>

    <div v-if="data" class="lower">
      <!-- Healer panel -->
      <section class="panel">
        <h3>Healer <span class="count" :class="data.heal.running ? 'ok' : ''">{{ data.heal.running ? `pid ${data.heal.pid}` : 'stopped' }}</span></h3>
        <div v-if="!data.heal.running" class="empty">the self-healing monitor is off — running sessions are not being watched</div>
        <template v-else>
          <pre class="log">{{ data.heal.logTail.join('\n') || '(no log lines yet)' }}</pre>
          <div v-if="Object.keys(data.heal.restarts).length" class="restarts">
            <span v-for="(n, id) in data.heal.restarts" :key="id" class="restart-chip"><code>{{ id }}</code> → {{ n }}/3</span>
          </div>
          <div v-else class="empty small">no restarts yet — no session has been recovered</div>
        </template>
      </section>

      <!-- Recent activity -->
      <section class="panel">
        <h3><Activity :size="14" style="vertical-align: -2px; margin-right: 5px" />Activity</h3>
        <div v-if="!data.activity.length" class="empty">no events yet</div>
        <ul v-else class="feed">
          <li v-for="(a, i) in data.activity" :key="i">
            <span class="feed-ts">{{ a.ts.slice(11, 16) }}</span>
            <span class="chip">{{ a.project }}</span>
            <code>{{ a.adwId.slice(0, 8) }}</code>
            <span class="feed-ev">{{ a.event }}</span>
          </li>
        </ul>
      </section>
    </div>

    <!-- Add project -->
    <section class="panel add">
      <h3>Add project</h3>
      <form class="add-form" @submit.prevent="onAdd">
        <input v-model="newRoot" class="root-input" placeholder="/path/to/project (with adws/)" spellcheck="false" />
        <button class="primary" type="submit" :disabled="adding || !newRoot.trim()">
          <Plus :size="15" style="vertical-align: -2px; margin-right: 5px" />Add
        </button>
      </form>
    </section>

    <Transition name="toast">
      <div v-if="note" class="toast" role="status" @click="note = ''">{{ note }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.mission-control {
  padding: 22px 28px 48px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 1240px;
  margin: 0 auto;
}

.loading, .error {
  color: var(--dim);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 30px;
}
.error { color: var(--red); }

.spin { animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* KPI strip */
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}
.kpi {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.kpi-n { font-size: 24px; font-weight: 700; color: var(--text); }
.kpi-l { font-size: 12px; color: var(--faint); text-transform: uppercase; letter-spacing: 0.06em; }
.kpi.heal { flex-direction: row; align-items: center; gap: 12px; }
.kpi.heal.ok { border-color: rgba(74, 222, 128, 0.4); }
.kpi.heal.ok svg { color: var(--green); }
.kpi.heal.off svg { color: var(--faint); }
.heal-actions { display: flex; gap: 6px; margin-top: 2px; }

/* panels */
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
}
.panel h3 {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--dim);
  display: flex;
  align-items: center;
  gap: 8px;
}
.count { color: var(--faint); font-weight: 500; }
.count.ok { color: var(--green); }
.empty { color: var(--faint); font-size: 14px; padding: 8px 0; }
.empty.small { font-size: 12px; }

.lower { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 900px) { .lower { grid-template-columns: 1fr; } }

/* projects table */
.ptable { width: 100%; border-collapse: collapse; font-size: 14px; }
.ptable th {
  text-align: left; color: var(--faint); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em; padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}
.ptable td { padding: 8px 10px; color: var(--text); border-bottom: 1px solid var(--border-soft); }
.ptable tr.stale { opacity: 0.55; }
.ptable tr.stale td:first-child::after { content: ' ⚠'; color: var(--amber); }
.ptable .bad { color: var(--red); }
.ptable .proj { color: var(--cyan); cursor: pointer; text-decoration: none; font-weight: 600; }
.ptable .proj:hover { text-decoration: underline; }
.ptable .actions { width: 90px; }

/* running strip */
.runs { display: flex; flex-direction: column; gap: 8px; }
.run {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; border-radius: 9px;
  background: rgba(255, 255, 255, 0.03); border: 1px solid var(--border-soft);
  font-size: 13px;
}
.run code { color: var(--text); font-family: var(--mono); }
.run .phase { color: var(--dim); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run .age { color: var(--faint); font-variant-numeric: tabular-nums; }
.run-actions { display: flex; gap: 6px; }

.chip {
  background: rgba(200, 155, 255, 0.14); color: var(--purple);
  border-radius: 999px; padding: 2px 9px; font-size: 12px; white-space: nowrap;
}

/* buttons */
button.icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: 7px;
  border: 1px solid var(--border); background: rgba(11, 15, 24, 0.9);
  color: var(--dim); cursor: pointer;
}
button.icon:hover { color: var(--text); border-color: rgba(200, 155, 255, 0.5); }
button.icon:disabled { opacity: 0.45; cursor: default; }
button.mini {
  border: 1px solid var(--border); background: rgba(11, 15, 24, 0.9);
  color: var(--dim); border-radius: 6px; padding: 2px 9px; font-size: 12px; cursor: pointer;
}
button.mini:hover { color: var(--text); }
button.primary {
  display: inline-flex; align-items: center;
  border: 1px solid rgba(200, 155, 255, 0.5); background: rgba(200, 155, 255, 0.14);
  color: var(--purple); border-radius: 8px; padding: 7px 14px; font-size: 14px; cursor: pointer;
}
button.primary:disabled { opacity: 0.5; cursor: default; }

/* healer panel */
.log {
  background: rgba(6, 8, 15, 0.7); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px; font-family: var(--mono);
  font-size: 12px; color: var(--dim); white-space: pre-wrap; margin: 0 0 10px;
}
.restarts { display: flex; flex-wrap: wrap; gap: 6px; }
.restart-chip {
  background: rgba(90, 210, 221, 0.1); border: 1px solid rgba(90, 210, 221, 0.25);
  color: var(--cyan); border-radius: 999px; padding: 2px 9px; font-size: 12px;
}
.restart-chip code { font-family: var(--mono); }

/* activity feed */
.feed { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.feed li { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.feed code { font-family: var(--mono); color: var(--dim); }
.feed-ts { color: var(--faint); font-variant-numeric: tabular-nums; }
.feed-ev { color: var(--dim); }

/* add project */
.add-form { display: flex; gap: 10px; }
.root-input {
  flex: 1; background: rgba(6, 8, 15, 0.7); border: 1px solid var(--border);
  border-radius: 8px; color: var(--text); padding: 8px 12px; font-size: 14px;
  font-family: var(--mono);
}
.root-input:focus { outline: none; border-color: rgba(200, 155, 255, 0.5); }

/* transient control feedback */
.toast {
  position: fixed; bottom: 24px; right: 24px; z-index: 60;
  padding: 10px 16px; border-radius: 10px;
  background: rgba(11, 15, 24, 0.95); border: 1px solid rgba(200, 155, 255, 0.35);
  color: var(--text); font-size: 14px; cursor: pointer;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
.toast-enter-active, .toast-leave-active { transition: opacity 0.25s ease, transform 0.25s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(8px); }
</style>
