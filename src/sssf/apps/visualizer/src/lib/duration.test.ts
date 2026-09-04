import { describe, expect, test } from 'bun:test'
import { runDurationMs } from './duration'
import type { Phase, Session } from './types'

// Fixed, self-consistent clock: 2026-09-04 09:00 local. Offsets between the
// fabricated phases are what matter — never mix in the real Date.now().
const T0 = new Date(2026, 8, 4, 9, 0, 0).getTime() // attempt 1 start
const T1 = T0 + 5 * 60_000 // attempt 1 end (5 min run)
const T2 = T0 + 12 * 3_600_000 // attempt 2 start (12 h later)
const T3 = T2 + 5 * 60_000 // attempt 2 end

function iso(ms: number): string {
  return new Date(ms).toISOString()
}

function phase(seq: number, startMs: number, endMs: number | null, status = 'success'): Phase {
  return {
    phase_id: `p${seq}`, adw_id: 'r1', seq, name: `ph${seq}`, status,
    started_at: iso(startMs), ended_at: endMs === null ? null : iso(endMs),
  } as Phase
}

function session(phases: Phase[], status = 'success'): Session & { phases: Phase[] } {
  return {
    adw_id: 'r1', adw_name: 'adw_simple_sdlc', request: 'x', archived: 0,
    status, engineer: 't',
    started_at: phases.length ? phases[0].started_at : null,
    ended_at: phases.length ? phases[phases.length - 1].ended_at : null,
    total_tokens: 0, total_cost: 0, phases,
  } as Session & { phases: Phase[] }
}

function bareSession(status: Session['status']): Session & { phases: Phase[] } {
  return {
    adw_id: 'r1', adw_name: 'adw_simple_sdlc', request: 'x', archived: 0,
    status, engineer: 't', started_at: null, ended_at: null,
    total_tokens: 0, total_cost: 0, phases: [],
  } as Session & { phases: Phase[] }
}

describe('runDurationMs', () => {
  test('sums both attempts — the idle gap between re-runs is not counted', () => {
    // attempt 1: 5 min at 09:00; attempt 2: 5 min at 21:00. Row span = 12h05m.
    const s = session([
      phase(1, T0, T0 + 2 * 60_000),
      phase(2, T0 + 2 * 60_000, T1),
      phase(3, T2, T2 + 3 * 60_000),
      phase(4, T2 + 3 * 60_000, T3),
    ])
    expect(runDurationMs(s, T3)).toBe(10 * 60_000)
  })

  test('a still-running phase ticks to nowMs', () => {
    const now = T2 + 2 * 60_000 // 2 minutes into the running attempt-2 phase
    const s = session([phase(1, T0, T1), phase(2, T2, null, 'running')], 'running')
    expect(runDurationMs(s, now)).toBe(5 * 60_000 + 2 * 60_000)
  })

  test('falls back to the row span when there are no usable phases', () => {
    const s = bareSession('success')
    s.started_at = iso(T0)
    s.ended_at = iso(T1)
    expect(runDurationMs(s, T3)).toBe(5 * 60_000)
  })

  test('a running row with no phases ticks to nowMs', () => {
    const s = bareSession('running')
    s.started_at = iso(T2)
    const now = T2 + 3 * 60_000
    expect(runDurationMs(s, now)).toBe(3 * 60_000)
  })

  test('a phase with no end that is not running contributes nothing', () => {
    const s = session([phase(1, T0, T0 + 2 * 60_000), phase(2, T0 + 2 * 60_000, null, 'success')])
    expect(runDurationMs(s, T3)).toBe(2 * 60_000) // only phase 1 counts
  })

  test('returns NaN when the session has no time anchors at all', () => {
    expect(Number.isNaN(runDurationMs(bareSession(null), T3))).toBe(true)
  })
})
