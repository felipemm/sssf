import type { Phase, Session } from './types'
import { ts } from './format'

export interface SessionWithPhases extends Session {
  phases?: Phase[] | null
}

/**
 * Total run time of a session, in milliseconds — the SUM of its phases'
 * durations.
 *
 * A re-run joins the SAME session row (phases accumulate across attempts at
 * rising seq), so the row's wall-clock span (ended_at − started_at) includes
 * the idle gap between attempts — a 09:00 run plus a 21:00 re-run reads as a
 * 12-hour run. No phase spans that gap, so summing each phase's own
 * started_at→ended_at window yields the actual work time: both attempts
 * count, the gap does not.
 *
 * A phase still running (status 'running', no end yet) counts up to `nowMs`;
 * a phase with no usable window contributes nothing. When the session has no
 * usable phases at all (a spawn-death row that never started), fall back to
 * the row's wall-clock span so the card still shows something.
 */
export function runDurationMs(s: SessionWithPhases, nowMs: number): number {
  let total = 0
  for (const p of s.phases ?? []) {
    const start = ts(p.started_at)
    if (!Number.isFinite(start)) continue
    let end = ts(p.ended_at)
    if (!Number.isFinite(end) && p.status === 'running') end = nowMs
    if (!Number.isFinite(end) || end < start) continue
    total += end - start
  }
  if (total > 0) return total
  // No usable phases (spawn-death / never-started row): the row span is the
  // only signal — a running row ticks to now, an ended row is its span.
  const start = ts(s.started_at)
  if (!Number.isFinite(start)) return NaN
  const end = s.status === 'running' ? nowMs : ts(s.ended_at)
  return (Number.isFinite(end) ? end : nowMs) - start
}
