<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { useProjects, fetchStatus } from '../lib/api'
import type { StatusResponse } from '../lib/api'
import { hrefFor } from '../lib/router'
import { fmtCost, fmtTokens } from '../lib/format'
import StatusCharts from './StatusCharts.vue'
import ContributionsHeatmap from './ContributionsHeatmap.vue'

const { selectedProject, projectsLoaded } = useProjects()
const status = ref<StatusResponse | null>(null)
const apiError = ref<string | null>(null)
const loading = ref(false)
const windowDays = ref(30)
const WINDOWS = [7, 30, 90] as const

async function load() {
  if (!selectedProject.value || !projectsLoaded.value) return
  loading.value = true
  apiError.value = null
  try {
    status.value = await fetchStatus(windowDays.value)
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function setWindow(d: number) {
  if (windowDays.value === d) return
  windowDays.value = d
  void load()
}

const hasData = computed(() => (status.value?.totals.runs ?? 0) > 0)

onMounted(() => {
  void load()
})
watch(projectsLoaded, () => {
  if (projectsLoaded.value) void load()
})
// On a project switch, clear the old content immediately (a failing fetch
// must never leave the previous project's data on screen), then reload.
watch(selectedProject, () => {
  status.value = null
  apiError.value = null
  void load()
})
</script>

<template>
  <div class="status-page">
    <header class="s-head">
      <div>
        <h1 class="s-title">status · {{ status?.project.name ?? selectedProject ?? '…' }}</h1>
        <p v-if="status" class="s-sub dim">
          {{ status.project.root }}
          <span v-if="status.project.last_run"> · last run {{ status.project.last_run.slice(0, 10) }}</span>
        </p>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">
        <RefreshCw :size="15" :class="{ spin: loading }" /> refresh
      </button>
    </header>

    <div v-if="apiError" class="banner">{{ apiError }} — <button class="link" @click="load">retry</button></div>

    <template v-if="status">
      <!-- main info strip -->
      <div class="strip">
        <div class="tile"><span class="k">db</span><span class="v code">adws/adw_data/sssf.db</span></div>
        <div class="tile"><span class="k">ticketing</span><span class="v">{{ status.project.ticketing_enabled ? 'on' : 'off' }}</span></div>
        <div class="tile">
          <span class="k">active runs</span>
          <a class="v" :href="hrefFor({})">{{ status.totals.active }}</a>
        </div>
        <div class="tile"><span class="k">success rate</span><span class="v">{{ Math.round(status.totals.success_rate * 100) }}%</span></div>
      </div>

      <div v-if="!hasData" class="board-empty">no sessions yet — run an ADW to see stats here</div>

      <template v-else>
        <!-- KPI cards -->
        <div class="cards">
          <section class="kpi">
            <h2 class="kpi-title">runs & health</h2>
            <dl>
              <dt class="hint hint-line" data-hint="All sessions ever run in this project.">total</dt><dd>{{ status.totals.runs }}</dd>
              <dt class="hint hint-line" data-hint="Sessions that ended in 'fail'. Stopped runs count too — stop finalizes as 'fail'.">failed</dt><dd>{{ status.totals.failed }}</dd>
              <dt class="hint hint-line" data-hint="Average wall-clock time of successful runs, in minutes.">avg duration</dt><dd>{{ Math.round(status.totals.avg_duration_s / 60) }}m</dd>
              <dt class="hint hint-line" data-hint="Sessions archived as reviewed (archive buttons / the archive sweep).">archived</dt><dd>{{ status.totals.archived }}</dd>
            </dl>
          </section>
          <section class="kpi">
            <h2 class="kpi-title">cost & tokens</h2>
            <dl>
              <dt class="hint hint-line" data-hint="Sum of sessions.total_cost — real provider billing from agent_end payloads.">total cost</dt><dd>{{ fmtCost(status.totals.total_cost) }}</dd>
              <dt class="hint hint-line" data-hint="Total cost ÷ total runs.">avg / run</dt><dd>{{ fmtCost(status.totals.avg_cost_per_run) }}</dd>
              <dt class="hint hint-line" data-hint="Sum of sessions.total_tokens (prompt + completion).">total tokens</dt><dd>{{ fmtTokens(status.totals.total_tokens) }}</dd>
              <dt class="hint hint-line" data-hint="Total tokens ÷ total runs.">avg / run</dt><dd>{{ fmtTokens(status.totals.avg_tokens_per_run) }}</dd>
            </dl>
          </section>
          <section class="kpi">
            <h2 class="kpi-title">quality</h2>
            <dl>
              <dt class="hint hint-line" data-hint="Share of review gates passed — gates success ÷ (success + failed).">gate pass</dt><dd>{{ Math.round(status.quality.gate_pass_rate * 100) }}%</dd>
              <dt class="hint hint-line" data-hint="The phase that failed the most times (N×) — the weakest step of the runs.">hotspot</dt><dd>{{ status.quality.hotspot_phase ?? '—' }}<span v-if="status.quality.hotspot_phase" class="x">{{ status.quality.hotspot_count }}×</span></dd>
              <dt class="hint hint-line" data-hint="Total agent retries across phases.">retries</dt><dd>{{ status.quality.total_retries }}</dd>
              <dt class="hint hint-line" data-hint="Phases that ended in 'fail'.">failed phases</dt><dd>{{ status.quality.failed_phases }}</dd>
            </dl>
          </section>
          <section class="kpi">
            <h2 class="kpi-title">repo</h2>
            <dl>
              <dt class="hint hint-line" data-hint="All commits reachable from HEAD (+N/30d = commits in the last 30 days).">commits</dt><dd>{{ status.git.commits }}<span class="x" v-if="status.git.commits_30d">+{{ status.git.commits_30d }}/30d</span></dd>
              <dt class="hint hint-line" data-hint="Distinct authors from git shortlog.">contributors</dt><dd>{{ status.git.contributors.length }}</dd>
              <dt class="hint hint-line" data-hint="Current git branch (X total = local branches).">branch</dt><dd class="agent"><span class="model">{{ status.git.current_branch ?? '—' }}</span><span class="x">{{ status.git.branches }} total</span></dd>
              <dt class="hint hint-line" data-hint="The branch tip commit — subject + date.">last commit</dt><dd v-if="status.git.last_commit" class="agent"><span class="model">{{ status.git.last_commit.subject }}</span><span class="x">{{ status.git.last_commit.date }}</span></dd>
              <dd v-else>—</dd>
              <dt class="hint hint-line" data-hint="Working-tree changes not committed (git status --porcelain count).">uncommitted</dt><dd>{{ status.git.dirty }}<span class="x" v-if="status.git.dirty">dirty</span></dd>
            </dl>
          </section>
          <section v-if="status.tickets" class="kpi">
            <h2 class="kpi-title">tickets</h2>
            <dl>
              <dt class="hint hint-line" data-hint="Tickets waiting to be run.">backlog</dt><dd>{{ status.tickets.backlog }}</dd>
              <dt class="hint hint-line" data-hint="Tickets with a live session — stage derives from the SESSION, not the ticket row.">running</dt><dd>{{ status.tickets.running }}</dd>
              <dt class="hint hint-line" data-hint="Tickets whose session finished 'success'.">done</dt><dd>{{ status.tickets.done }}</dd>
              <dt class="hint hint-line" data-hint="Tickets whose session finished 'fail'.">failed</dt><dd>{{ status.tickets.failed }}</dd>
            </dl>
          </section>
        </div>

        <!-- agent & model costs side by side -->
        <div class="cost-row">
          <section class="kpi">
            <h2 class="kpi-title">agents — cost</h2>
            <table class="cost-tbl">
              <thead><tr>
                <th class="hint" data-hint="Agent role in the run — planner, builder, reviewer, documenter, …">role</th>
                <th class="hint" data-hint="The model the role ran on.">model</th>
                <th class="hint" data-hint="Actual tokens reported by the role's agent_end events.">tokens</th>
                <th class="hint" data-hint="Summed provider billing from agent_end payloads — what this role actually cost.">actual</th>
                <th class="hint" data-hint="Session cost apportioned by token share — this role's share of the run cost.">share</th>
              </tr></thead>
              <tbody>
                <tr v-for="a in status.agents" :key="a.role">
                  <td>{{ a.role }}</td>
                  <td class="m">{{ a.model ?? '—' }}</td>
                  <td>{{ fmtTokens(a.tokens) }}</td>
                  <td>{{ fmtCost(a.cost_actual) }}</td>
                  <td class="dim">{{ fmtCost(a.cost_share) }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="kpi">
            <h2 class="kpi-title">models — cost</h2>
            <table class="cost-tbl">
              <thead><tr>
                <th class="hint" data-hint="Model used for the runs.">model</th>
                <th class="hint" data-hint="Actual tokens billed to this model.">tokens</th>
                <th class="hint" data-hint="Sessions that used this model.">runs</th>
                <th class="hint" data-hint="Summed provider billing from agent_end payloads.">actual</th>
                <th class="hint" data-hint="Session cost apportioned by token share.">share</th>
              </tr></thead>
              <tbody>
                <tr v-for="m in status.models" :key="m.model">
                  <td class="m">{{ m.model }}</td>
                  <td>{{ fmtTokens(m.tokens) }}</td>
                  <td>{{ m.sessions }}</td>
                  <td>{{ fmtCost(m.cost_actual) }}</td>
                  <td class="dim">{{ fmtCost(m.cost_share) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="footnote">
              actual = summed provider billing per agent call · share = each run's cost split by
              token count — the gap reflects models with different $/token.
            </p>
          </section>
        </div>

        <!-- trends -->
        <section class="trends">
          <div class="trends-head">
            <h2 class="kpi-title">trends</h2>
            <div class="seg" role="group" aria-label="window">
              <button
                v-for="d in WINDOWS"
                :key="d"
                type="button"
                :class="{ on: windowDays === d }"
                @click="setWindow(d)"
              >{{ d }}d</button>
            </div>
          </div>
          <StatusCharts :buckets="status.trends.buckets" />
        </section>

        <section v-if="status.contributions.length" class="hm-sec">
          <h2 class="kpi-title">contributions</h2>
          <ContributionsHeatmap :days="status.contributions" />
        </section>

      </template>
    </template>

    <div v-else-if="!apiError" class="board-empty">loading status…</div>
  </div>
</template>

<style scoped>
.status-page {
  padding: 22px 28px 40px;
}
.s-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.s-title { font-size: 20px; margin: 0; }
.s-sub { margin: 4px 0 0; font-size: 13px; word-break: break-all; }
.btn {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 8px 14px; border-radius: 8px;
  border: 1px solid var(--border); background: rgba(255,255,255,0.04);
  color: var(--text); cursor: pointer;
}
.btn:disabled { opacity: 0.5; cursor: default; }
.spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.banner {
  margin-bottom: 14px; padding: 10px 14px;
  border: 1px solid rgba(248,113,113,0.4); border-radius: 10px;
  background: rgba(248,113,113,0.08); color: var(--red); font-size: 13px;
}
.link { background: none; border: none; color: var(--purple); cursor: pointer; text-decoration: underline; }
.strip {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px; margin-bottom: 18px;
}
.tile {
  padding: 10px 14px; border: 1px solid var(--border-soft);
  border-radius: 10px; background: var(--surface);
  display: flex; flex-direction: column; gap: 3px;
}
.tile .k { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--faint); }
.tile .v { font-size: 14px; }
.tile .v.code { font-size: 12px; font-family: ui-monospace, monospace; }
.tile a.v { color: var(--purple); }
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.cost-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.cost-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.cost-tbl th {
  text-align: left; font-weight: 500; color: var(--faint);
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 2px 8px 6px 0; border-bottom: 1px solid var(--border-soft);
}
.cost-tbl td { padding: 5px 8px 5px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.cost-tbl td.m { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--cyan); font-size: 12px; }
.cost-tbl td.dim { color: var(--faint); }
.footnote { margin: 10px 0 0; font-size: 11px; color: var(--faint); line-height: 1.5; }
.kpi {
  padding: 14px 16px; border: 1px solid var(--border-soft);
  border-radius: 12px; background: var(--surface);
}
.kpi-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--faint); margin: 0 0 10px; }
.kpi dl { margin: 0; display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; }
.kpi dt { font-size: 13px; color: var(--faint); }
.kpi dd { margin: 0; font-size: 14px; text-align: right; }
.kpi dd.agent { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; min-width: 0; }
.model { font-size: 12px; color: var(--cyan); max-width: 100%; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.x { font-size: 11px; color: var(--faint); margin-left: 6px; }
.trends { margin-bottom: 20px; }
.trends-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button {
  background: none; border: none; color: var(--faint);
  padding: 5px 12px; font-size: 13px; cursor: pointer;
}
.seg button.on { background: rgba(167,139,250,0.15); color: var(--purple); }
.board-empty {
  padding: 40px 0; text-align: center; color: var(--faint); font-size: 14px;
}
.hm-sec { margin-bottom: 20px; }
</style>
