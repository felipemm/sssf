<script setup lang="ts">
import { computed } from 'vue'
import { windowPhases } from '../lib/phases'
import type { Phase } from '../lib/types'

const props = defineProps<{ phases: Phase[] }>()

// A restarted run keeps appending phases at rising seq, so a card can
// accumulate far more dots than it has room for (f9e445e9: 26 across three
// attempts). Show a rolling window of the NEWEST phases; the oldest roll off
// and a dimmed +N marker signals the hidden count.
const win = computed(() => windowPhases(props.phases))

const glyph: Record<string, string> = {
  success: '●',
  running: '◐',
  queued: '○',
  fail: '✗',
  not_passed: '!',
}
</script>

<template>
  <span class="dots">
    <span
      v-for="p in win.visible"
      :key="p.phase_id"
      class="d"
      :class="p.status"
      :title="`${p.name} — ${p.status}`"
      >{{ glyph[p.status ?? ''] ?? '○' }}</span
    >
    <span
      v-if="win.hidden"
      class="d more"
      :title="`${win.hidden} older phase${win.hidden === 1 ? '' : 's'} hidden`"
      >+{{ win.hidden }}</span
    >
    <span v-if="!win.visible.length && !win.hidden" class="faint">—</span>
  </span>
</template>

<style scoped>
.dots {
  display: inline-flex;
  gap: 5px;
  font-size: 16px;
  letter-spacing: 0;
}

.d.success {
  color: var(--green);
}

.d.fail {
  color: var(--red);
}

.d.not_passed {
  color: var(--amber, #fbbf24);
}

.d.running {
  color: var(--blue);
  animation: pulse 1.2s ease-in-out infinite;
}

.d.queued {
  color: var(--faint);
}

/* The +N overflow marker — signals hidden older phases without pretending to
   be a phase dot (the status glyphs above are never repurposed). */
.d.more {
  color: var(--faint);
  font-size: 11px;
  line-height: 18px;
  padding-left: 1px;
}
</style>
