import { describe, expect, test } from 'bun:test'
import { MAX_PHASE_DOTS, windowPhases } from './phases'
import type { Phase } from './types'

function phase(seq: number, status = 'success'): Phase {
  return { phase_id: `p${seq}`, adw_id: 'r1', seq, name: `ph${seq}`, status } as Phase
}

describe('windowPhases', () => {
  test('returns every phase unchanged when within the window', () => {
    const phases = [phase(1), phase(2), phase(3)]
    const { visible, hidden } = windowPhases(phases, 5)
    expect(visible.map((p) => p.seq)).toEqual([1, 2, 3])
    expect(hidden).toBe(0)
  })

  test('keeps the NEWEST phases and reports the hidden count when over the max', () => {
    // a restarted run accumulating phases across attempts (f9e445e9: 26 rows)
    const phases = Array.from({ length: 26 }, (_, i) => phase(i + 1))
    const { visible, hidden } = windowPhases(phases, MAX_PHASE_DOTS)
    expect(hidden).toBe(26 - MAX_PHASE_DOTS)
    // the newest phases (the latest attempt) survive; the oldest roll off
    expect(visible[0].seq).toBe(26 - MAX_PHASE_DOTS + 1)
    expect(visible[visible.length - 1].seq).toBe(26)
  })

  test('does not mutate or depend on the input order', () => {
    const phases = [phase(3), phase(1), phase(2)]
    const { visible, hidden } = windowPhases(phases, 2)
    expect(visible.map((p) => p.seq)).toEqual([2, 3])
    expect(hidden).toBe(1)
    expect(phases.map((p) => p.seq)).toEqual([3, 1, 2]) // untouched
  })

  test('empty and degenerate inputs', () => {
    expect(windowPhases([], 12)).toEqual({ visible: [], hidden: 0 })
    expect(windowPhases([phase(1)], 0)).toEqual({ visible: [], hidden: 1 })
  })
})
