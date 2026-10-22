import type { SessionsFilter } from '../shared/types'

export function parseSessionsFilter(value: string | null): SessionsFilter {
  return value === 'done' || value === 'blocked' || value === 'archived' ? value : 'all'
}
