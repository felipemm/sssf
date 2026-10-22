<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import type { SessionSummary, SessionsFilter } from '../lib/types'
import { fetchSessions } from '../lib/api'
import { cmpTs } from '../lib/format'
import SessionCard from './SessionCard.vue'
import { useProjects } from '../lib/api'

const props = withDefaults(defineProps<{ initialFilter?: SessionsFilter }>(), { initialFilter: 'all' })
const { projectsLoaded, selectedProject } = useProjects()
watch(selectedProject, () => {
  sessions.value = []
  void tick()
})

const filter = ref<SessionsFilter>(props.initialFilter)
watch(() => props.initialFilter, (value) => { filter.value = value })
watch(filter, () => {
  sessions.value = []
  loaded.value = false
  void tick()
})

const sessions = shallowRef<SessionSummary[]>([])
const apiError = ref<string | null>(null)
const loaded = ref(false)
const nowMs = ref(Date.now())

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function tick() {
  if (document.hidden) return
  if (inflight) return
  if (!projectsLoaded.value) return   // wait for the project situation before fetching
  inflight = true
  const requestedFilter = filter.value
  try {
    const result = await fetchSessions(requestedFilter)
    if (requestedFilter !== filter.value) return
    sessions.value = result
    nowMs.value = Date.now()
    apiError.value = null
    loaded.value = true
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 500)
})

onUnmounted(() => clearInterval(timer))

function onChanged() {
  void tick()
}

const ordered = computed(() =>
  sessions.value.toSorted((a, b) => cmpTs(b.started_at, a.started_at)),
)

const filters: { value: SessionsFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'done', label: 'Done' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'archived', label: 'Archived' },
]
</script>

<template>
  <div class="sessions">
    <div v-if="apiError" class="error-bar">api unreachable — retrying {{ apiError }}</div>

    <div class="filters" role="group" aria-label="Filter sessions">
      <button v-for="option in filters" :key="option.value" type="button" class="filter-chip"
        :aria-pressed="filter === option.value" @click="filter = option.value">
        {{ option.label }}
      </button>
    </div>

    <div v-if="ordered.length" class="list-head dim">
      {{ ordered.length }} {{ filter === 'archived' ? 'archived runs' : 'runs' }}
    </div>

    <div v-if="ordered.length" class="cards">
      <SessionCard
        v-for="s in ordered"
        :key="s.adw_id"
        :session="s"
        :now-ms="nowMs"
        :archived="filter === 'archived'"
        @changed="onChanged"
      />
    </div>
    <div v-else-if="loaded" class="empty-state">
      {{ filter === 'archived' ? 'no archived sessions' : 'no sessions yet — run an ADW to see it here' }}
    </div>
    <div v-else-if="!apiError" class="empty-state">loading sessions…</div>
  </div>
</template>

<style scoped>
.sessions {
  display: flex;
  flex-direction: column;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 16px 24px 0;
}

.filter-chip {
  padding: 5px 11px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--dim);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.filter-chip:hover,
.filter-chip[aria-pressed='true'] {
  border-color: rgba(200, 155, 255, 0.55);
  background: rgba(200, 155, 255, 0.12);
  color: var(--text);
}

.list-head {
  padding: 12px 24px 0;
  font-size: 16px;
}

.cards {
  /* Uniform grid: every card the same width and (fixed in SessionCard) height,
     independent of content. */
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(460px, 1fr));
  gap: 18px;
  padding: 16px 24px 28px;
}





</style>
