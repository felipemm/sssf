<script setup lang="ts">
import { computed } from 'vue'
import type { StatusTrendBucket } from '../lib/api'
import { fmtCost, fmtTokens } from '../lib/format'

const props = defineProps<{ buckets: StatusTrendBucket[] }>()

const W = 300
const H = 84
const PAD = 4

function max(vals: number[]): number {
  return Math.max(1, ...vals)
}

// runs/day — bars
const runBars = computed(() => {
  const vals = props.buckets.map((b) => b.runs)
  const m = max(vals)
  return props.buckets.map((b, i) => {
    const h = (b.runs / m) * (H - PAD * 2)
    return {
      x: PAD + (i * (W - PAD * 2)) / props.buckets.length,
      w: Math.max(2, (W - PAD * 2) / props.buckets.length - 2),
      y: H - PAD - h,
      h,
      day: b.day,
      runs: b.runs,
    }
  })
})

// cost/day — area
const costArea = computed(() => {
  const vals = props.buckets.map((b) => b.cost)
  const m = max(vals)
  const pts = props.buckets.map((b, i) => {
    const x = PAD + (i * (W - PAD * 2)) / Math.max(1, props.buckets.length - 1)
    const y = H - PAD - (b.cost / m) * (H - PAD * 2)
    return { x, y, day: b.day, cost: b.cost }
  })
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const area = pts.length
    ? `${line} L${pts[pts.length - 1]!.x.toFixed(1)},${H - PAD} L${pts[0]!.x.toFixed(1)},${H - PAD} Z`
    : ''
  return { line, area, pts }
})

// success-rate/day — line (rate of finished runs that succeeded)
const rateLine = computed(() => {
  const pts = props.buckets.map((b, i) => {
    const fin = b.success + b.fail
    const rate = fin > 0 ? b.success / fin : 0
    const x = PAD + (i * (W - PAD * 2)) / Math.max(1, props.buckets.length - 1)
    const y = H - PAD - rate * (H - PAD * 2)
    return { x, y, day: b.day, rate: Math.round(rate * 100) }
  })
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  return { line, pts }
})

// tokens/day — bars
const tokenBars = computed(() => {
  const vals = props.buckets.map((b) => b.tokens)
  const m = max(vals)
  return props.buckets.map((b, i) => {
    const h = (b.tokens / m) * (H - PAD * 2)
    return {
      x: PAD + (i * (W - PAD * 2)) / props.buckets.length,
      w: Math.max(2, (W - PAD * 2) / props.buckets.length - 2),
      y: H - PAD - h,
      h,
      day: b.day,
      tokens: b.tokens,
    }
  })
})

const empty = computed(() => props.buckets.length === 0)
</script>

<template>
  <div class="charts">
    <figure class="chart">
      <figcaption>runs / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="runs per day">
        <rect v-for="b in runBars" :key="b.day" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="2" fill="var(--purple)" />
        <title v-for="b in runBars" :key="'t' + b.day">{{ b.day }}: {{ b.runs }} run(s)</title>
      </svg>
      <div v-else class="chart-empty">no runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>cost / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="cost per day">
        <path v-if="costArea.area" :d="costArea.area" fill="rgba(34,211,238,0.15)" />
        <path :d="costArea.line" fill="none" stroke="var(--cyan)" stroke-width="2" stroke-linejoin="round" />
        <title v-for="p in costArea.pts" :key="'t' + p.day">{{ p.day }}: {{ fmtCost(p.cost) }}</title>
      </svg>
      <div v-else class="chart-empty">no runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>success rate / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="success rate per day">
        <path :d="rateLine.line" fill="none" stroke="var(--green)" stroke-width="2" stroke-linejoin="round" />
        <title v-for="p in rateLine.pts" :key="'t' + p.day">{{ p.day }}: {{ p.rate }}%</title>
      </svg>
      <div v-else class="chart-empty">no finished runs in window</div>
    </figure>

    <figure class="chart">
      <figcaption>tokens / day</figcaption>
      <svg v-if="!empty" :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="tokens per day">
        <rect v-for="b in tokenBars" :key="b.day" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="2" fill="var(--blue)" />
        <title v-for="b in tokenBars" :key="'t' + b.day">{{ b.day }}: {{ fmtTokens(b.tokens) }}</title>
      </svg>
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
.chart svg {
  width: 100%;
  height: auto;
  display: block;
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
