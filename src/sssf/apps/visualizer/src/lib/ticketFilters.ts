import { MACHINE_STATUSES } from '../../shared/machineStatuses'

type TicketState = { status: string; runs: readonly unknown[] }

export function backlogTickets<T extends TicketState>(tickets: readonly T[]): T[] {
  return tickets.filter((ticket) => ticket.status === MACHINE_STATUSES.READY_FOR_AGENT)
}

export function blockedTickets<T extends TicketState>(tickets: readonly T[]): T[] {
  return tickets.filter((ticket) => ticket.status === MACHINE_STATUSES.BLOCKED)
}

export function isRunnableTicket(ticket: TicketState): boolean {
  return ticket.status === MACHINE_STATUSES.READY_FOR_AGENT
}

export function isBlockedTicket(ticket: TicketState): boolean {
  return ticket.status === MACHINE_STATUSES.BLOCKED
}

export function isRetryTicket(ticket: TicketState): boolean {
  return isRunnableTicket(ticket) && ticket.runs.length > 0
}
