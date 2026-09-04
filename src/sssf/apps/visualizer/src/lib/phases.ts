import type { Phase } from './types'

/** How many phase dots a card shows before older ones roll off. */
export const MAX_PHASE_DOTS = 12

/**
 * The rolling window a card's progress dots display. A restarted run keeps
 * appending phases at rising seq (each attempt continues the sequence), so the
 * newest phases are the latest attempt — when a session accumulates more than
 * `max` phases, only the newest are shown and the oldest roll off instead of
 * overflowing the card.
 *
 * Phases are sorted by seq defensively (the server already orders them; a
 * display helper should not depend on that). The input list is not mutated.
 */
export function windowPhases(phases: Phase[], max: number = MAX_PHASE_DOTS): { visible: Phase[]; hidden: number } {
  if (max <= 0) return { visible: [], hidden: phases.length }
  const ordered = [...phases].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))
  const hidden = Math.max(0, ordered.length - max)
  return hidden > 0 ? { visible: ordered.slice(-max), hidden } : { visible: ordered, hidden: 0 }
}
