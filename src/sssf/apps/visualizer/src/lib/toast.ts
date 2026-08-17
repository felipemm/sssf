// A tiny reactive toast store: actions push messages; the Toasts component
// renders them bottom-right and auto-dismisses. Used for CLI/action failures
// (ticket run/backlog, sweep) so an error is visible even when the action
// lives deep in a card — not just in a modal.
import { reactive } from 'vue'

export interface Toast {
  id: number
  message: string
  kind: 'error' | 'info'
}

const toasts = reactive<Toast[]>([])
let nextId = 1

export function notify(message: string, kind: 'error' | 'info' = 'error'): void {
  toasts.push({ id: nextId++, message, kind })
}

export function dismissToast(id: number): void {
  const i = toasts.findIndex((t) => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

export function useToasts() {
  return toasts
}
