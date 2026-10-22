export interface SessionActions {
  canStop: boolean
  canRestart: boolean
}

/** Archived sessions are terminal history, not actionable run cards. */
export function sessionActions(status: string | null, archived: boolean): SessionActions {
  return {
    canStop: !archived && status === 'running',
    canRestart: !archived && (status === 'success' || status === 'fail'),
  }
}
