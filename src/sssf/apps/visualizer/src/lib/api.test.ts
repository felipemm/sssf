import { afterEach, describe, expect, test } from 'bun:test'
import { fetchSessions, setProject } from './api'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  setProject(null)
})

describe('fetchSessions', () => {
  test('sends the selected history filter to the project sessions API', async () => {
    const urls: string[] = []
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      urls.push(String(input))
      return new Response('[]', { headers: { 'content-type': 'application/json' } })
    }) as typeof fetch
    setProject('sample project')

    await fetchSessions('blocked')

    expect(urls).toEqual(['/api/projects/sample%20project/sessions?filter=blocked'])
  })
})
