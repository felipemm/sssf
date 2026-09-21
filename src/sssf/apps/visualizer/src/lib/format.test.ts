import { describe, expect, test } from 'bun:test'
import { cmpTs, ts } from './format'

describe('cmpTs', () => {
  test('sorts ISO-8601 strings lexicographically (same format) ascending', () => {
    const a = '2026-09-04T09:00:00.000Z'
    const b = '2026-09-04T10:00:00.000Z'
    expect(cmpTs(a, b)).toBe(-1)
    expect(cmpTs(b, a)).toBe(1)
  })

  test('sorts a list chronologically with the component comparator', () => {
    const rows = [
      '2026-09-01T10:00:00.000Z',
      '2026-09-01T08:00:00.000Z',
      '2026-09-01T09:00:00.000Z',
    ]
    rows.sort((a, b) => cmpTs(a, b))
    expect(rows).toEqual([
      '2026-09-01T08:00:00.000Z',
      '2026-09-01T09:00:00.000Z',
      '2026-09-01T10:00:00.000Z',
    ])
  })

  test('descending (newest first) via argument swap', () => {
    const rows = [
      '2026-09-01T10:00:00.000Z',
      '2026-09-01T08:00:00.000Z',
      '2026-09-01T09:00:00.000Z',
    ]
    rows.sort((a, b) => cmpTs(b, a))
    expect(rows[0]).toBe('2026-09-01T10:00:00.000Z')
  })

  test('handles null / undefined as sortable empty strings (go first asc)', () => {
    expect(cmpTs(null, '2026-09-01T09:00:00.000Z')).toBe(-1)
    expect(cmpTs(undefined, undefined)).toBe(0)
    expect(cmpTs(null, null)).toBe(0)
  })

  test('identical strings are equal', () => {
    const s = '2026-09-01T09:00:00.000Z'
    expect(cmpTs(s, s)).toBe(0)
  })
})

describe('ts', () => {
  test('memoizes repeated parsing to the same milliseconds', () => {
    const iso = '2026-09-04T12:00:00.000Z'
    const expected = new Date(iso).getTime()
    expect(ts(iso)).toBe(expected)
    // Second call hits the cache — same value, no throw.
    expect(ts(iso)).toBe(expected)
  })

  test('returns NaN for empty input', () => {
    expect(ts(null)).toBeNaN()
    expect(ts(undefined)).toBeNaN()
    expect(ts('')).toBeNaN()
  })
})