import { afterAll, beforeAll, describe, expect, test } from 'bun:test'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Database } from 'bun:sqlite'

const SERVER_ENTRY = resolve(import.meta.dir, 'index.ts')
const PROJECT = 'history'
let tmp: string
let port: number
let server: ChildProcess | undefined
let serverLog = ''

const baseUrl = () => `http://localhost:${port}`

async function waitForServer(timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      // Poll sequentially so a failed connect finishes before the next probe.
      // oxlint-disable-next-line no-await-in-loop
      const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/sessions`)
      if (res.status === 200) return
    } catch {
      // Keep polling until Bun.serve has bound its port.
    }
    // oxlint-disable-next-line no-await-in-loop
    await Bun.sleep(100)
  }
  throw new Error(`server did not come up in ${timeoutMs}ms — log:\n${serverLog}`)
}

beforeAll(async () => {
  tmp = mkdtempSync(join(tmpdir(), 'sssf-sessions-'))
  const root = join(tmp, 'project')
  mkdirSync(join(root, 'adws', 'data'), { recursive: true })
  const dbPath = join(root, 'adws', 'data', 'sssf.db')
  const db = new Database(dbPath)
  db.run('PRAGMA user_version = 7')
  db.run(`CREATE TABLE sessions (
    adw_id TEXT PRIMARY KEY, adw_name TEXT, request TEXT, status TEXT, engineer TEXT,
    started_at TEXT, ended_at TEXT, total_tokens INTEGER, total_cost REAL, archived INTEGER DEFAULT 0
  )`)
  db.run(`CREATE TABLE phases (
    phase_id TEXT PRIMARY KEY, adw_id TEXT, seq INTEGER, name TEXT, kind TEXT, owner TEXT,
    description TEXT, status TEXT DEFAULT 'fail', attempt INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
    error TEXT, started_at TEXT, ended_at TEXT
  )`)
  const insert = db.query('INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
  for (const [id, status, archived, hour] of [
    ['run-done', 'success', 0, 10],
    ['run-blocked', 'fail', 0, 11],
    ['run-live', 'running', 0, 12],
    ['old-done', 'success', 1, 13],
    ['old-blocked', 'fail', 1, 14],
  ] as const) {
    insert.run(id, 'adw_plan', id, status, 'operator', `2026-09-01T${hour}:00:00Z`, null, 0, 0, archived)
  }
  db.close()

  writeFileSync(join(tmp, 'registry.json'), JSON.stringify({
    version: 1,
    projects: [{ name: PROJECT, root, db: dbPath, lastRun: null }],
  }))
  port = 40_000 + Math.floor(Math.random() * 20_000)
  server = spawn(process.execPath, [SERVER_ENTRY], {
    env: { ...process.env, SSSF_REGISTRY: join(tmp, 'registry.json'), PORT: String(port) },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  server.stdout!.on('data', (data: Buffer) => { serverLog += data.toString() })
  server.stderr!.on('data', (data: Buffer) => { serverLog += data.toString() })
  await waitForServer()
})

afterAll(() => {
  server?.kill('SIGTERM')
  if (tmp) rmSync(tmp, { recursive: true, force: true })
})

describe('sessions filter API', () => {
  test('returns the selected run membership and keeps archived history separate', async () => {
    const ids = async (filter: string) => {
      const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/sessions?filter=${filter}`)
      expect(res.status).toBe(200)
      return (await res.json() as { adw_id: string }[]).map((session) => session.adw_id)
    }

    expect(await ids('all')).toEqual(['run-live', 'run-blocked', 'run-done'])
    expect(await ids('done')).toEqual(['run-done'])
    expect(await ids('blocked')).toEqual(['run-blocked'])
    expect(await ids('archived')).toEqual(['old-blocked', 'old-done'])
    expect(await ids('invalid')).toEqual(['run-live', 'run-blocked', 'run-done'])
  })

  test('does not expose a per-session archive or restore writer', async () => {
    const res = await fetch(`${baseUrl()}/api/projects/${PROJECT}/sessions/run-done/archive`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ archived: true }),
    })
    expect(res.status).toBe(404)
  })
})
