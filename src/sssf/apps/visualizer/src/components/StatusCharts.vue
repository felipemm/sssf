<script setup lang="ts">
import { computed } from 'vue'
import { areaY, barY, defineChart, lineY } from '@tanstack/charts'
import { scaleLinear } from '@tanstack/charts/scales/linear'
import { scalePoint } from '@tanstack/charts/scales/point'
import { tooltip } from '@tanstack/charts/tooltip'
import { Chart } from '@tanstack/charts/vue'
import type { StatusTrendBucket } from '../lib/api'

// Status-page trends, rendered with TanStack Charts (v0.14) — sssf palette:
// runs purple · cost cyan · success-rate green · tokens blue.
const props = defineProps<{ buckets: StatusTrendBucket[] }>()

const empty = computed(() => props.buckets.length === 0)

const MARGIN = { left: 46, right: 12, top: 10, bottom: 26 } // y labels + rotated x labels get explicit room
const xScale = () => scalePoint<string>().padding(0.3)
// Taller charts + explicit tick config so bars and axis labels never overlap:
// y labels thinned/spaced, x day labels rotated and collision-thinned.
const xAxis = {
  ticks: { spacing: 72 },
  tickLabels: { rotate: -35, thin: { minGap: 6, priority: 'ends' as const } },
}
const yAxis = {
  ticks: { spacing: 26 },
  tickLabels: { thin: { minGap: 4 } },
}
const yLinear = { scale: scaleLinear, nice: true, grid: true, axis: yAxis }

const runs = computed(() =>
  defineChart({
    marks: [barY(props.buckets, { id: 'runs', x: 'day', y: 'runs', fill: '#c89bff', fillOpacity: 0.85 })],
    x: { scale: xScale, axis: xAxis },
    y: yLinear,
    tooltip,
    margin: MARGIN,
  }),
)

const cost = computed(() =>
  defineChart({
    marks: [
      areaY(props.buckets, { id: 'cost-area', x: 'day', y: 'cost', fill: '#5ad2dd', fillOpacity: 0.16 }),
      lineY(props.buckets, { id: 'cost', x: 'day', y: 'cost', stroke: '#5ad2dd', strokeWidth: 2 }),
    ],
    x: { scale: xScale, axis: xAxis },
    y: yLinear,
    tooltip,
    margin: MARGIN,
  }),
)

const rate = computed(() =>
  defineChart({
    marks: [
      lineY(props.buckets, {
        id: 'rate',
        x: 'day',
        y: (b) => (b.success + b.fail > 0 ? b.success / (b.success + b.fail) : 0),
        stroke: '#4ade80',
        strokeWidth: 2,
      }),
    ],
    x: { scale: xScale, axis: xAxis },
    y: { ...yLinear, tickFormat: (v: number) => `${Math.round(v * 100)}%` },
    tooltip,
    margin: MARGIN,
  }),
)

const tokens = computed(() =>
  defineChart({
    marks: [barY(props.buckets, { id: 'tokens', x: 'day', y: 'tokens', fill: '#6cb6ff', fillOpacity: 0.85 })],
    x: { scale: xScale, axis: xAxis },
    y: yLinear,
    tooltip,
    margin: MARGIN,
  }),
)
</script>

<template>
  <div class="charts">
    <figure class="chart">
      <figcaption>runs / day</figcaption>
      <Chart v-if="!empty" :definition="runs" aria-label="runs per day" :aspect-ratio="2.0" />
      <div v-else class="chart-empty">no runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>cost / day</figcaption>
      <Chart v-if="!empty" :definition="cost" aria-label="cost per day" :aspect-ratio="2.0" />
      <div v-else class="chart-empty">no runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>success rate / day</figcaption>
      <Chart v-if="!empty" :definition="rate" aria-label="success rate per day" :aspect-ratio="2.0" />
      <div v-else class="chart-empty">no finished runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>tokens / day</figcaption>
      <Chart v-if="!empty" :definition="tokens" aria-label="tokens per day" :aspect-ratio="2.0" />
      <div v-else class="chart-empty">no runs in window</div>
    </figure>
  </div>
</template>

<style scoped>
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}
.chart {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--surface);
}
.chart figcaption {
  font-size: 12px;
  color: var(--faint);
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
/* dark theme for the TanStack host: axis text inherits the host color, the
   tooltip colors are driven by the ts-chart-tooltip-* custom properties. */
.chart :deep(.ts-chart-host) {
  color: var(--dim);
  --ts-chart-tooltip-background: #0d1119;
  --ts-chart-tooltip-border: 1px solid rgba(200, 155, 255, 0.4);
  --ts-chart-tooltip-color: var(--text);
  --ts-chart-tooltip-padding: 8px 10px;
  --ts-chart-tooltip-border-radius: 8px;
  --ts-chart-tooltip-font: 12px var(--sans);
}
.chart :deep(.ts-chart-host text) {
  fill: var(--dim);
}
.chart :deep(.ts-chart-host .ts-chart-grid line),
.chart :deep(.ts-chart-host [data-ts-chart-role='grid'] line) {
  stroke: var(--border-soft);
}
.chart-empty {
  height: 84px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--faint);
  font-size: 13px;
}
</style>
