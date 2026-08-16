<script setup lang="ts">
import { computed } from 'vue'
import type { CockpitCompletedPoint } from '../lib/types'

// Live cumulative completed-sessions line — same hand-rolled SVG style as the
// status page charts: cyan line over a soft area fill, value labels thinned
// when crowded, the latest point always labelled with the running total.
const props = defineProps<{ points: CockpitCompletedPoint[] }>()

const W = 360
const H = 96
const PAD = 14

const total = computed(() => props.points.length ? props.points[props.points.length - 1]!.count : 0)

const step = computed(() => (props.points.length <= 20 ? 1 : props.points.length <= 45 ? 2 : 3))
const lab = (i: number): boolean => i % step.value === 0 || i === props.points.length - 1

const line = computed(() => {
  const vals = props.points.map((p) => p.count)
  const m = Math.max(1, ...vals)
  const pts = props.points.map((p, i) => {
    const x = PAD + (i * (W - PAD * 2)) / Math.max(1, props.points.length - 1)
    const y = H - PAD - (p.count / m) * (H - PAD * 2)
    return { x, y, date: p.date, count: p.count, lab: lab(i) }
  })
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const area = pts.length
    ? `${d} L${pts[pts.length - 1]!.x.toFixed(1)},${H - PAD} L${pts[0]!.x.toFixed(1)},${H - PAD} Z`
    : ''
  return { d, area, pts }
})

const empty = computed(() => props.points.length === 0 || total.value === 0)
</script>

<template>
  <figure class="completed-chart">
    <figcaption class="cc-head">
      <span>{{ total }} completed sessions <span class="cc-total">(cumulative)</span></span>
    </figcaption>
    <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="completed sessions per day (cumulative)">
      <path v-if="line.area" :d="line.area" fill="rgba(34,211,238,0.12)" />
      <path :d="line.d" fill="none" stroke="var(--cyan)" stroke-width="2" stroke-linejoin="round" />
      <g v-for="p in line.pts" :key="p.date">
        <circle :cx="p.x" :cy="p.y" r="2.2" fill="var(--cyan)" />
        <text v-if="p.lab" :x="p.x" :y="p.y - 5" text-anchor="middle" class="cc-label">{{ p.count }}</text>
        <text v-if="p.lab" :x="p.x" :y="H - 2" text-anchor="middle" class="cc-date">{{ p.date.slice(5) }}</text>
      </g>
    </svg>
    <div v-else class="chart-empty">no completed sessions yet</div>
  </figure>
</template>

<style scoped>
.completed-chart {
  margin: 0;
}
.cc-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  color: var(--text);
  font-size: 14px;
  margin-bottom: 8px;
}
.cc-total {
  color: var(--faint);
  font-size: 12px;
}
.completed-chart svg {
  width: 100%;
  height: auto;
  max-height: 220px;
}
.cc-label {
  fill: var(--dim);
  font-size: 9px;
}
.cc-date {
  fill: var(--faint);
  font-size: 8px;
}
.chart-empty {
  color: var(--faint);
  font-size: 14px;
  padding: 20px 0;
}
</style>
