/** The canonical ticket-machine vocabulary shared by the visualizer server and UI. */
export const MACHINE_STATUSES = {
  NEEDS_TRIAGE: 'needs-triage',
  READY_FOR_AGENT: 'ready-for-agent',
  IN_PROGRESS: 'in-progress',
  READY_FOR_SIGNOFF: 'ready-for-signoff',
  READY_TO_DEPLOY: 'ready-to-deploy',
  DONE: 'done',
  BLOCKED: 'blocked',
} as const

export type MachineStatus = (typeof MACHINE_STATUSES)[keyof typeof MACHINE_STATUSES]

const machineStatusValues: ReadonlySet<string> = new Set(Object.values(MACHINE_STATUSES))

export function isMachineStatus(status: string): status is MachineStatus {
  return machineStatusValues.has(status)
}
