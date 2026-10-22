import { describe, expect, test } from 'bun:test'
import { MACHINE_STATUSES } from '../../shared/machineStatuses'
import { backlogTickets, blockedTickets, isBlockedTicket, isRetryTicket, isRunnableTicket } from './ticketFilters'

type BoardTicket = { id: string; status: string; runs: { adw_id: string }[] }

function ticket(id: string, status: string, runs: string[] = []): BoardTicket {
  return { id, status, runs: runs.map((adw_id) => ({ adw_id })) }
}

describe('ticket machine board filters', () => {
  const queued = ticket('queued', MACHINE_STATUSES.READY_FOR_AGENT)
  const retried = ticket('retried', MACHINE_STATUSES.READY_FOR_AGENT, ['old-run'])
  const triage = ticket('triage', MACHINE_STATUSES.NEEDS_TRIAGE)
  const blocked = ticket('blocked', MACHINE_STATUSES.BLOCKED, ['failed-run'])
  const inProgress = ticket('in-progress', MACHINE_STATUSES.IN_PROGRESS)
  const signoff = ticket('signoff', MACHINE_STATUSES.READY_FOR_SIGNOFF)
  const readyToDeploy = ticket('ready-to-deploy', MACHINE_STATUSES.READY_TO_DEPLOY)
  const done = ticket('done', MACHINE_STATUSES.DONE)
  const tickets = [queued, retried, triage, blocked, inProgress, signoff, readyToDeploy, done]

  test('the board backlog contains only ready-for-agent tickets', () => {
    expect(backlogTickets(tickets).map((t) => t.id)).toEqual(['queued', 'retried'])
    expect(backlogTickets([ticket('legacy', 'backlog'), ticket('starting', 'starting')])).toEqual([])
  })

  test('the blocked column contains blocked tickets, not other machine states', () => {
    expect(blockedTickets(tickets).map((t) => t.id)).toEqual(['blocked'])
  })

  test('only ready-for-agent tickets can run across every machine state', () => {
    for (const status of Object.values(MACHINE_STATUSES)) {
      expect(isRunnableTicket(ticket(status, status))).toBe(status === MACHINE_STATUSES.READY_FOR_AGENT)
    }
    expect(isRunnableTicket(triage)).toBe(false)
    expect(isRunnableTicket(blocked)).toBe(false)
  })

  test('blocked tickets get the manual requeue action, not a runnable action', () => {
    expect(isBlockedTicket(blocked)).toBe(true)
    expect(isBlockedTicket(queued)).toBe(false)
  })

  test('a retry is a ready-for-agent ticket with prior run history', () => {
    expect(isRetryTicket(queued)).toBe(false)
    expect(isRetryTicket(retried)).toBe(true)
    expect(isRetryTicket(blocked)).toBe(false)
  })
})
