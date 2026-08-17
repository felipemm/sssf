<script setup lang="ts">
import { onUnmounted, watch } from 'vue'
import { dismissToast, useToasts } from '../lib/toast'

const toasts = useToasts()
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function schedule(id: number) {
  timers.set(id, setTimeout(() => dismissToast(id), 5000))
}

watch(
  toasts,
  (list) => {
    for (const t of list) if (!timers.has(t.id)) schedule(t.id)
  },
  { deep: true, immediate: true },
)

onUnmounted(() => {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
})
</script>

<template>
  <div class="toasts" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast"
        :class="t.kind"
        role="status"
        title="dismiss"
        @click="dismissToast(t.id)"
      >
        {{ t.message }}
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 80;
  display: grid;
  gap: 8px;
  max-width: 440px;
}
.toast {
  padding: 10px 16px;
  border-radius: 10px;
  background: rgba(11, 15, 24, 0.95);
  border: 1px solid rgba(248, 113, 113, 0.5);
  color: #f87171;
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  white-space: pre-wrap;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}
.toast.info {
  border-color: rgba(200, 155, 255, 0.35);
  color: var(--text);
}
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
