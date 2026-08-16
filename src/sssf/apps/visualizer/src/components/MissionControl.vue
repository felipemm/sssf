<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
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
  fetchCockpitContributions,
  fetchContainerLogs,
  healControl,
  refreshProject,
  removeProject,
  restartRun,
  stopRun,
} from '../lib/api'
import { navigate } from '../lib/router'
import type { CockpitData, ControlResult } from '../lib/types'
import type { ContributionDay } from '../lib/api'
import ContributionsHeatmap from './ContributionsHeatmap.vue'
import CompletedChart from './CompletedChart.vue'

const data = ref<CockpitData | null>(null)
const contribDays = ref<ContributionDay[]>([])
const loading = ref(false)
const error = ref('')
const note = ref('') // transient control feedback, like the sweep toast
let timer: ReturnType<typeof setInterval> | undefined
let noteTimer: ReturnType<typeof setTimeout> | undefined

const pending = ref<Set<string>>(new Set()) // in-flight control keys ("stop:run1", …)

const POLL_MS = 5000

function flashNote(msg: string) {
  note.value = msg
  clearTimeout(noteTimer)
  noteTimer = setTimeout(() => (note.value = ''), 4000)
}

const CONTRIB_POLL_MS = 5 * 60 * 1000

// Completed-sessions chart: the aggregate carries 14 days of per-hour counts;
// the selected sliding window slices + cumulates client-side (instant toggle).
const CHART_WINDOWS = [
  { key: '24h', hours: 24, label: 'last 24h' },
  { key: '72h', hours: 72, label: 'last 72h' },
  { key: '7d', hours: 168, label: 'last 7d' },
  { key: '14d', hours: 336, label: 'last 14d' },
] as const
type ChartWindow = (typeof CHART_WINDOWS)[number]['key']
const chartWindow = ref<ChartWindow>('24h')

const chartPoints = computed(() => {
  const hourly = data.value?.completedHourly ?? []
  const baseline = data.value?.completedBaseline ?? 0
  const hours = CHART_WINDOWS.find((w) => w.key === chartWindow.value)?.hours ?? 24
  const slice = hourly.slice(-hours)
  let cum = baseline // absolute cumulative — the line carries the true running total
  return slice.map((p) => ({ date: p.date, count: (cum += p.count) }))
})
const chartWindowLabel = computed(
  () => CHART_WINDOWS.find((w) => w.key === chartWindow.value)?.label ?? 'last 24h',
)

async function fetchContribs() {
  try {
    contribDays.value = await fetchCockpitContributions()
  } catch {
    /* heatmap is auxiliary — a failure never disturbs the cockpit */
  }
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
  void fetchContribs()
  timer = setInterval(() => void refresh(), POLL_MS)
  contribTimer = setInterval(() => void fetchContribs(), CONTRIB_POLL_MS)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  clearInterval(logTimer)
  clearInterval(contribTimer)
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

// ── container log tailing ──────────────────────────────────────────────────

const logName = ref<string | null>(null)
const logLines = ref<string[]>([])
const logError = ref('')
const logTail = ref(100)
const logLoading = ref(false)
let logTimer: ReturnType<typeof setInterval> | undefined
let contribTimer: ReturnType<typeof setInterval> | undefined

async function fetchLogs() {
  if (!logName.value || logLoading.value) return
  logLoading.value = true
  logError.value = ''
  try {
    const res = await fetchContainerLogs(logName.value, logTail.value)
    if (res.ok) {
      logLines.value = res.lines
    } else {
      logError.value = res.error ?? 'docker logs failed'
      logLines.value = []
    }
  } catch {
    logError.value = 'log fetch failed'
  } finally {
    logLoading.value = false
  }
}

async function toggleLogs(name: string) {
  if (logName.value === name) {
    logName.value = null
    logLines.value = []
    clearInterval(logTimer)
    return
  }
  logName.value = name
  logLines.value = []
  await fetchLogs()
  clearInterval(logTimer)
  logTimer = setInterval(() => void fetchLogs(), 5000) // tail: refresh while open
}

function onLogTailChange() {
  void fetchLogs()
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
      <div class="kpi hint" data-hint="Sessions currently running (status 'running') — live ADW runs that haven't finished or been stopped. Stopped runs finalize as 'fail'.">
        <span class="kpi-n">{{ data.kpis.runningSessions }}</span><span class="kpi-l hint-line">running sessions</span>
      </div>
      <div class="kpi hint" data-hint="sssf-* Docker containers owned by registered projects — one per running sandboxed run. 'orphan' = containers mapping to no registered project.">
        <span class="kpi-n">{{ data.kpis.liveContainers }}</span><span class="kpi-l hint-line">containers<template v-if="data.kpis.orphanContainers"> · {{ data.kpis.orphanContainers }} orphan</template></span>
      </div>
      <div class="kpi hint" data-hint="Per-run git worktree dirs under ~/.sssf/sandboxes/&lt;project&gt;/ — one per run, auto-torn-down when the run finishes. Leftovers are cleaned by the healer.">
        <span class="kpi-n">{{ data.kpis.sandboxWorktrees }}</span><span class="kpi-l hint-line">sandboxes</span>
      </div>
      <div class="kpi hint" data-hint="Tickets leaving the backlog — spawned ('starting') or with a live session. The stage derives from the SESSION, not the ticket row (the ticket is provenance).">
        <span class="kpi-n">{{ data.kpis.ticketsInFlight }}</span><span class="kpi-l hint-line">tickets in flight</span>
      </div>
      <div class="kpi hint" data-hint="Sum of sessions.total_cost for sessions started today (UTC — flips at 21:00 BRT). Real provider billing from agent_end payloads.">
        <span class="kpi-n">{{ fmtUsd(data.kpis.costTodayUsd) }}</span><span class="kpi-l hint-line">cost today</span>
      </div>
      <div class="kpi hint" data-hint="Sum of sessions.total_cost across ALL sessions (all time), not just today.">
        <span class="kpi-n">{{ fmtUsd(data.kpis.costTotalUsd) }}</span><span class="kpi-l hint-line">total cost</span>
      </div>
      <div class="kpi heal hint" :class="data.heal.running ? 'ok' : 'off'"
           data-hint="The self-healing monitor daemon (sssf heal): watches running sessions, restarts hung ones (max 3× per session), finalizes dead ones. 'Healed' = recovery actions taken in the LAST 7 DAYS (timestamped in heal-state.json). Start/stop toggles it.">
        <HeartPulse :size="16" />
        <div>
          <div class="kpi-n">{{ data.heal.running ? data.heal.healed7d : 'off' }}</div>
          <div class="kpi-l">{{ data.heal.running ? 'sessions healed · 7d' : 'healer' }}</div>
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
            <th class="hint" data-hint="Project name in the registry. Click to drill down to its status page (#/p/&lt;name&gt;).">project</th>
            <th class="hint" data-hint="Sessions currently in 'running' status right now.">running</th>
            <th class="hint" data-hint="Sessions started today (UTC). The red N✗ is the count failed today — stopped runs count too, since stop finalizes as 'fail'.">today</th>
            <th class="hint" data-hint="Tickets in-flight / backlog / completed. In-flight = spawned or with a live session; backlog = waiting to be run; completed = done + failed (stage derived from the session). Only populated when ticketing is enabled.">tickets</th>
            <th class="hint" data-hint="sssf-* Docker containers owned by this project (matched by the session's adw_id or a sandbox worktree dir).">ctrs</th>
            <th class="hint" data-hint="Per-run git worktree dirs under ~/.sssf/sandboxes/&lt;project&gt;/ — one per run, cleaned by auto-teardown.">wt</th>
            <th class="hint" data-hint="Sum of sessions.total_cost for sessions started today (UTC).">cost today</th>
            <th class="hint" data-hint="Sum of sessions.total_cost over ALL sessions (all time).">total</th>
            <th class="hint" data-hint="Most recent event timestamp in this project's db — how fresh the project is. Falls back to the registry's last_run.">last activity</th>
            <th class="actions">actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in data.projects" :key="p.name" :class="{ stale: p.stale }"
              :title="p.stale ? 'Stale — db unreadable (missing/corrupt/partial schema) or idle past the healer\'s 10-min threshold. Values render as zeros.' : ''">
            <td>
              <a class="proj" :title="p.root" @click.prevent="navigate({ project: p.name, tab: 'status' })">
                {{ p.name }}
              </a>
            </td>
            <td class="hint hint-line" data-hint="Live sessions in 'running' status.">{{ p.sessionsRunning }}</td>
            <td class="hint hint-line" :class="{ bad: p.sessionsFailedToday > 0 }"
                data-hint="Started today (UTC); red ✗ = failed today (stopped runs count as failed).">
              {{ p.sessionsToday }}<template v-if="p.sessionsFailedToday"> · {{ p.sessionsFailedToday }}✗</template>
            </td>
            <td class="hint hint-line" data-hint="In-flight / backlog / completed tickets (stage derived from the session).">{{ p.ticketsInFlight }} / {{ p.ticketsBacklog }} / {{ p.ticketsDone }}</td>
            <td class="hint hint-line" data-hint="sssf-* Docker containers owned by this project.">{{ p.containers }}</td>
            <td class="hint hint-line" data-hint="Sandbox git worktree dirs for this project.">{{ p.worktrees }}</td>
            <td class="hint hint-line" data-hint="Sessions started today (UTC) — real provider billing.">{{ fmtUsd(p.costTodayUsd) }}</td>
            <td class="hint hint-line" data-hint="All-time session cost.">{{ fmtUsd(p.costTotalUsd) }}</td>
            <td class="hint hint-line" data-hint="Latest event time in the project db.">{{ fmtRel(p.lastActivity) }}</td>
            <td class="actions">
              <button class="icon" title="Refresh — sssf init --refresh --auto: accept all template updates, non-interactive" :disabled="pending.has(`refresh:${p.name}`)" @click="onRefresh(p.name)">
                <RefreshCw :size="14" :class="{ spin: pending.has(`refresh:${p.name}`) }" />
              </button>
              <button class="icon" title="Remove from the registry (confirm dialog). Does not delete the project's files." @click="onRemove(p.name)">
                <Trash2 :size="14" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Running-now + Containers: side by side, both height-capped so many
         parallel runs never grow the page unboundedly -->
    <div class="now-row">
    <section v-if="data" class="panel">
      <h3>Running now <span class="count">{{ data.running.length }}</span></h3>
      <div v-if="!data.running.length" class="empty">nothing running across projects</div>
      <div v-else class="runs">
        <div v-for="r in data.running" :key="r.adwId" class="run">
          <span class="chip hint" data-hint="The registered project this session belongs to.">{{ r.project }}</span>
          <code class="hint" data-hint="The run's id — the same id in the trace URL (#/p/&lt;project&gt;/s/&lt;id&gt;).">{{ r.adwId }}</code>
          <span class="phase hint" data-hint="The phase currently executing (latest running phase).">{{ r.phase ?? '—' }}</span>
          <span class="age hint" data-hint="How long the session has been running (mm:ss).">{{ fmtAge(r.ageSec) }}</span>
          <span class="run-actions">
            <button class="icon" title="Stop — finalizes the session + in-flight phases as 'fail' (stopped by the engineer)" :disabled="pending.has(`stop:${r.adwId}`)" @click="onStop(r.project, r.adwId)">
              <Square :size="13" />
            </button>
            <button class="icon" title="Restart — reuses the adw_id + request and attaches to the same branch" :disabled="pending.has(`restart:${r.adwId}`)" @click="onRestart(r.project, r.adwId)">
              <RotateCw :size="13" />
            </button>
          </span>
        </div>
      </div>
    </section>

    <!-- Containers (docker ps filtered to sssf) — always visible, even when empty -->
    <section v-if="data" class="panel">
      <h3>Containers <span class="count">{{ data.containers.length }}</span></h3>
      <div v-if="data.kpis.dockerOk === false" class="docker-warn" role="alert">
        <strong>docker is not running</strong> — container list and log tailing are unavailable.
        <code>{{ data.kpis.dockerError }}</code>
      </div>
      <div v-else-if="!data.containers.length" class="empty">no sssf containers running</div>
      <div v-else class="ctable-wrap">
      <table class="ctable">
        <thead>
          <tr>
            <th class="hint" data-hint="sssf-&lt;adwId&gt; — the Docker container running the sandboxed ADW.">container</th>
            <th class="hint" data-hint="Owning project. 'orphan' = no registered project owns this container (stale from a crashed teardown).">project</th>
            <th class="hint" data-hint="Docker image — sssf-runner (python + git + node/pi + sssf).">image</th>
            <th class="hint" data-hint="docker ps status — 'Up N' = running, 'Exited' = finished.">status</th>
            <th class="hint" data-hint="When the container was created.">created</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in data.containers" :key="c.name" :class="{ selected: logName === c.name }">
            <td><code class="hint" data-hint="sssf-&lt;adwId&gt; — container name = the run's adw_id.">{{ c.name }}</code></td>
            <td><span class="chip hint" data-hint="Owning project ('' = orphan).">{{ c.project || 'orphan' }}</span></td>
            <td class="dim hint" data-hint="The Docker image the container runs.">{{ c.image }}</td>
            <td :class="c.running ? 'up' : 'down'" class="hint" data-hint="docker ps status.">{{ c.status }}</td>
            <td class="dim hint" data-hint="Container creation time.">{{ c.created }}</td>
            <td>
              <button class="strip-archive" :class="{ on: logName === c.name }"
                      :title="logName === c.name ? 'Close logs' : 'Tail the container logs — docker logs --tail N --timestamps, auto-refreshed every 5s while open'"
                      @click="toggleLogs(c.name)">
                <Terminal :size="15" :stroke-width="2" />
              </button>
            </td>
          </tr>
          <tr v-if="logName">
            <td colspan="6" class="log-cell">
              <div class="logbar">
                <span class="log-title">docker logs --tail {{ logTail }} <code>{{ logName }}</code></span>
                <select v-model.number="logTail" class="tail-select" @change="onLogTailChange">
                  <option :value="50">50</option><option :value="100">100</option><option :value="250">250</option>
                </select>
                <button class="mini" :disabled="logLoading" @click="fetchLogs">
                  <RefreshCw :size="12" :class="{ spin: logLoading }" style="vertical-align: -1px; margin-right: 4px" />refresh
                </button>
                <span class="log-hint">auto-tails every 5s</span>
              </div>
              <pre v-if="logError" class="log error-log">{{ logError }}</pre>
              <pre v-else class="log">{{ logLines.join('\n') || '(no log lines — container may have just started)' }}</pre>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </section>
    </div>

    <section v-if="data" class="panel">
      <h3>Completed sessions <span class="count hint" data-hint="Absolute cumulative count of finished sessions (success + fail, by ended_at, UTC) across all projects — the line carries the true running total and keeps growing. Updates live with the cockpit poll.">{{ chartWindowLabel }} · live</span></h3>
      <div class="window-switch" role="tablist" aria-label="chart window">
        <button
          v-for="w in CHART_WINDOWS"
          :key="w.key"
          class="window-btn"
          :class="{ active: chartWindow === w.key }"
          role="tab"
          :aria-selected="chartWindow === w.key"
          @click="chartWindow = w.key"
        >{{ w.key }}</button>
      </div>
      <CompletedChart :points="chartPoints" />
    </section>

    <!-- Cross-project contributions heatmap (git commits over the last year) -->
    <section v-if="contribDays.length" class="panel">
      <h3>Contributions</h3>
      <ContributionsHeatmap :days="contribDays" />
    </section>

    <div v-if="data" class="lower">
      <!-- Healer panel -->
      <section class="panel">
        <h3>Healer <span class="count hint" :class="data.heal.running ? 'ok' : ''" data-hint="The self-healing monitor daemon (sssf heal) — pid from ~/.sssf/heal.pid, alive-checked. 'healed N' = recovery actions taken in the LAST 7 DAYS.">{{ data.heal.running ? `pid ${data.heal.pid} · healed ${data.heal.healed7d} (7d)` : 'stopped' }}</span></h3>
        <div v-if="!data.heal.running" class="empty hint" data-hint="The daemon is off — running sessions are not being watched. Start it to enable auto-recovery.">the self-healing monitor is off — running sessions are not being watched</div>
        <template v-else>
          <pre class="log hint" data-hint="The daemon's log tail (~/.sssf/heal.log) — every recovery action it takes.">{{ data.heal.logTail.join('\n') || '(no log lines yet)' }}</pre>
          <div v-if="Object.keys(data.heal.restarts).length" class="restarts">
            <span v-for="(n, id) in data.heal.restarts" :key="id" class="restart-chip hint" data-hint="Restart budget: this session was restarted N/3 times by the healer. At 3 it's finalized as failed instead of restarted again."><code>{{ id }}</code> → {{ n }}/3</span>
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
            <span class="feed-ts hint" data-hint="Event time (UTC, HH:MM).">{{ a.ts.slice(11, 16) }}</span>
            <span class="chip hint" data-hint="The project that produced the event.">{{ a.project }}</span>
            <code class="hint" data-hint="The session the event belongs to (first 8 chars).">{{ a.adwId.slice(0, 8) }}</code>
            <span class="feed-ev hint" data-hint="Event type — agent_start / agent_end / commit_plan / …">{{ a.event }}</span>
          </li>
        </ul>
      </section>
    </div>

    <!-- Add project -->
    <section class="panel add">
      <h3>Add project</h3>
      <form class="add-form" @submit.prevent="onAdd">
        <input v-model="newRoot" class="root-input hint" data-hint="Filesystem path to a project (must contain adws/). Registered in ~/.sssf/projects.json — the project's runs become visible to the cockpit." placeholder="/path/to/project (with adws/)" spellcheck="false" />
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

.now-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) { .now-row { grid-template-columns: 1fr; } }

/* height-capped so many parallel runs/containers never grow the page */
.now-row .runs {
  max-height: 340px;
  overflow-y: auto;
  overflow-x: hidden;   /* rows truncate, never scroll sideways */
}
.ctable-wrap {
  max-height: 340px;
  overflow-y: auto;
}
.ctable-wrap thead th {
  position: sticky;
  top: 0;
  background: var(--panel);
  z-index: 1;
}

.lower {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}
@media (max-width: 900px) { .lower { grid-template-columns: 1fr; } }
/* healer + activity share the same fixed height; content scrolls inside */
.lower .panel {
  height: 320px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.lower .panel .log,
.lower .panel .feed {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

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
  min-width: 0;         /* flex children may shrink below their content */
}
.run > * { min-width: 0; }
.run code {
  color: var(--text); font-family: var(--mono);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.run .phase { color: var(--dim); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run .chip { overflow: hidden; text-overflow: ellipsis; }
.run .age { flex: none; }
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

/* log button — same style as the run-strip archive buttons on project pages */
.strip-archive {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: rgba(11, 15, 24, 0.9);
  color: var(--dim);
  cursor: pointer;
}
.strip-archive:hover {
  color: var(--text);
  border-color: rgba(200, 155, 255, 0.5);
}
.strip-archive.on {
  color: var(--purple);
  background: rgba(200, 155, 255, 0.14);
  border-color: rgba(200, 155, 255, 0.45);
}
/* the container whose logs are open is highlighted */
.ctable tr.selected {
  background: rgba(200, 155, 255, 0.07);
}
.ctable tr.selected td:first-child {
  box-shadow: inset 3px 0 0 var(--purple);
}

/* chart window switch */
.window-switch {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border);
  margin-bottom: 10px;
}
.window-btn {
  padding: 4px 12px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--dim);
  font-size: 12px;
  cursor: pointer;
}
.window-btn:hover { color: var(--text); }
.window-btn.active {
  color: var(--text);
  background: rgba(200, 155, 255, 0.16);
  box-shadow: inset 0 0 0 1px rgba(200, 155, 255, 0.35);
}

/* docker-down warning */
.docker-warn {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  background: rgba(232, 182, 74, 0.1); border: 1px solid rgba(232, 182, 74, 0.4);
  color: var(--amber); border-radius: 8px; padding: 9px 12px;
  font-size: 13px; margin-bottom: 10px;
}
.docker-warn code { font-family: var(--mono); font-size: 12px; color: var(--dim); }

/* containers table */
.ctable { width: 100%; border-collapse: collapse; font-size: 13px; }
.ctable th {
  text-align: left; color: var(--faint); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em; padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}
.ctable td { padding: 7px 10px; color: var(--text); border-bottom: 1px solid var(--border-soft); }
.ctable code { font-family: var(--mono); font-size: 12px; }
.ctable .up { color: var(--green); }
.ctable .down { color: var(--faint); }
.ctable .dim { color: var(--dim); }
.log-cell { background: rgba(6, 8, 15, 0.55); }
.logbar {
  display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
  font-size: 12px; color: var(--dim);
}
.log-title { flex: 1; font-family: var(--mono); }
.log-title code { color: var(--cyan); }
.tail-select {
  background: rgba(6, 8, 15, 0.9); border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; padding: 2px 6px; font-size: 12px;
}
.log-hint { color: var(--faint); font-size: 11px; white-space: nowrap; }
.error-log { color: var(--red); }

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
