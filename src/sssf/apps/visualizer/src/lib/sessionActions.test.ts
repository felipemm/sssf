import { describe, expect, test } from 'bun:test'
import { sessionActions } from './sessionActions'

describe('sessionActions', () => {
  test('archived sessions are terminal and expose no run controls', () => {
    expect(sessionActions('success', true)).toEqual({ canStop: false, canRestart: false })
    expect(sessionActions('fail', true)).toEqual({ canStop: false, canRestart: false })
    expect(sessionActions('running', true)).toEqual({ canStop: false, canRestart: false })
  })

  test('live and completed sessions keep their appropriate run controls', () => {
    expect(sessionActions('running', false)).toEqual({ canStop: true, canRestart: false })
    expect(sessionActions('success', false)).toEqual({ canStop: false, canRestart: true })
    expect(sessionActions('fail', false)).toEqual({ canStop: false, canRestart: true })
  })
})
