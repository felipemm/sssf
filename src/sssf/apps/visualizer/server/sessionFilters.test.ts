import { describe, expect, test } from 'bun:test'
import { parseSessionsFilter } from './sessionFilters'

describe('parseSessionsFilter', () => {
  test('accepts the four history filters and defaults unknown values to all', () => {
    expect(parseSessionsFilter('all')).toBe('all')
    expect(parseSessionsFilter('done')).toBe('done')
    expect(parseSessionsFilter('blocked')).toBe('blocked')
    expect(parseSessionsFilter('archived')).toBe('archived')
    expect(parseSessionsFilter('running')).toBe('all')
    expect(parseSessionsFilter(null)).toBe('all')
  })
})
