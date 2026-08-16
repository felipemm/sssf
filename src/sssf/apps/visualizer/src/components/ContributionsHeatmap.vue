<script setup lang="ts">
import { computed } from 'vue'
import type { ContributionDay } from '../lib/api'

const props = defineProps<{ days: ContributionDay[] }>()

// Weekday rows (Sun..Sat) via CSS grid; month labels where the month changes.
const LEVELS = [0, 1, 2, 3, 4] as const

function level(count: number): number {
  if (count === 0) return 0
  if (count <= 2) return 1
  if (count <= 5) return 2
  if (count <= 9) return 3
  return 4
}

const cells = computed(() =>
  props.days.map((d) => {
    const dow = new Date(`${d.date}T00:00:00Z`).getUTCDay()
    return { ...d, dow, level: level(d.count) }
  }),
)

const months = computed(() => {
  const out: { label: string; col: number }[] = []
  let prev: string | null = null
  cells.value.forEach((c, i) => {
    const m = c.date.slice(0, 7)
    if (m !== prev) {
      out.push({ label: new Date(`${c.date}T00:00:00Z`).toLocaleString('en', { month: 'short' }), col: Math.floor(i / 7) })
      prev = m
    }
  })
  return out
})

const total = computed(() => props.days.reduce((n, d) => n + d.count, 0))
</script>

<template>
  <figure class="heatmap">
    <figcaption class="hm-head">
      <span>{{ total }} commits in the last year</span>
      <span class="hm-legend">
        <span class="hm-less">less</span>
        <span v-for="l in LEVELS" :key="l" class="cell" :class="'lvl-' + l" />
        <span class="hm-more">more</span>
      </span>
    </figcaption>
    <div class="hm-grid-wrap">
        <div class="hm-dows">
          <span>Mon</span><span /><span>Wed</span><span /><span>Fri</span><span /><span />
        </div>
        <div class="hm-body">
          <div class="hm-months">
            <span
              v-for="m in months"
              :key="m.col"
              class="hm-month"
              :style="{ gridColumnStart: m.col + 1 }"
            >{{ m.label }}</span>
          </div>
          <div class="hm-grid">
            <span
              v-for="(c, i) in cells"
              :key="c.date"
              class="cell"
              :class="'lvl-' + c.level"
              :style="{ gridRow: c.dow + 1, gridColumn: Math.floor(i / 7) + 1 }"
              :title="`${c.count} commit${c.count === 1 ? '' : 's'} · ${c.date}`"
            />
          </div>
        </div>
    </div>
  </figure>
</template>

<style scoped>
.heatmap {
  --hm-gap: clamp(1px, 0.22vw, 3px);
  margin: 0;
  padding: 14px 16px;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--surface);
}
.hm-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--faint);
  margin-bottom: 12px;
}
.hm-legend { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; }
.hm-grid-wrap { display: flex; gap: 8px; min-width: 0; }
.hm-dows {
  display: grid;
  grid-template-rows: repeat(7, 1fr);
  gap: var(--hm-gap);
  font-size: 10px;
  color: var(--faint);
  padding-top: 18px;
}
.hm-body { flex: 1; min-width: 0; }
.hm-months {
  display: grid;
  grid-template-columns: repeat(53, minmax(0, 1fr));
  gap: var(--hm-gap);
  height: 16px;
  font-size: 10px;
  color: var(--faint);
}
.hm-month { grid-row: 1; white-space: nowrap; min-width: 0; overflow: visible; }
.hm-grid {
  display: grid;
  grid-template-columns: repeat(53, minmax(0, 1fr));
  gap: var(--hm-gap);
}
.cell {
  width: 100%;
  min-width: 0;
  aspect-ratio: 1;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.05);
}
.hm-legend .cell { width: 12px; }
.cell.lvl-1 { background: rgba(74, 222, 128, 0.25); }
.cell.lvl-2 { background: rgba(74, 222, 128, 0.5); }
.cell.lvl-3 { background: rgba(74, 222, 128, 0.75); }
.cell.lvl-4 { background: #4ade80; }
.hm-month { color: var(--faint); }
</style>
