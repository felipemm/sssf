<script setup lang="ts">
import { computed } from 'vue'
import { areaY, defineChart, lineY } from '@tanstack/charts'
import { scaleLinear } from '@tanstack/charts/scales/linear'
import { scalePoint } from '@tanstack/charts/scales/point'
import { tooltip } from '@tanstack/charts/tooltip'
import { Chart } from '@tanstack/charts/vue'
import type { CockpitCompletedPoint } from '../lib/types'

// Live cumulative completed-sessions line (TanStack Charts v0.14) — cyan line
// over a soft area fill, sssf palette. The parent slices + cumulates the
// window; this renders the resulting cumulative points.
const props = defineProps<{ points: CockpitCompletedPoint[] }>()

const total = computed(() => (props.points.length ? props.points[props.points.length - 1]!.count : 0))

const empty = computed(() => props.points.length === 0 || total.value === 0)

const def = computed(() =>
  defineChart({
    marks: [
      areaY(props.points, { id: 'completed-area', x: 'date', y: 'count', fill: '#5ad2dd', fillOpacity: 0.14 }),
      lineY(props.points, { id: 'completed', x: 'date', y: 'count', points: true, stroke: '#5ad2dd', strokeWidth: 2 }),
    ],
    x: { scale: () => scalePoint<string>().padding(0.1) },
    y: { scale: scaleLinear, nice: true, grid: true },
    tooltip,
    margin: { left: 46, right: 12, top: 10, bottom: 26 },
    clip: true,
  }),
)
</script>

<template>
  <figure class="completed-chart">
    <figcaption class="cc-head">
      <span>{{ total }} completed sessions <span class="cc-total">(cumulative)</span></span>
    </figcaption>
    <Chart v-if="!empty" :definition="def" aria-label="completed sessions per day (cumulative)" :aspect-ratio="3.4" />
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
.completed-chart :deep(.ts-chart-host) {
  color: var(--dim);
  --ts-chart-tooltip-background: #0d1119;
  --ts-chart-tooltip-border: 1px solid rgba(200, 155, 255, 0.4);
  --ts-chart-tooltip-color: var(--text);
  --ts-chart-tooltip-padding: 8px 10px;
  --ts-chart-tooltip-border-radius: 8px;
  --ts-chart-tooltip-font: 12px var(--sans);
}
.completed-chart :deep(.ts-chart-host text) {
  fill: var(--dim);
}
.completed-chart :deep(.ts-chart-host .ts-chart-grid line),
.completed-chart :deep(.ts-chart-host [data-ts-chart-role='grid'] line) {
  stroke: var(--border-soft);
}
.chart-empty {
  color: var(--faint);
  font-size: 14px;
  padding: 20px 0;
}
</style>
