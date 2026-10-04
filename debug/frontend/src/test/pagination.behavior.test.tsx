import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { AiCacheResponse, JsonValue, RedisQueryResponse } from '../api/types'
import { renderDebugger } from './renderDebugger'

describe('live inspector pagination', () => {
  it.each(['scan', 'hash', 'set'] as const)('follows the returned %s cursor through the final page', async (operation) => {
    const { user } = renderDebugger({
      path: '/redis',
      handler: (path, init) => {
        if (path !== '/api/v1/redis/query') return undefined
        const request = JSON.parse(String(init.body)) as Record<string, JsonValue>
        const last = request.cursor === 37
        const response: RedisQueryResponse = {
          type: operation,
          ttl: last ? 120 : 60,
          data: last ? ['last item'] : [],
          next_cursor: last ? 0 : 37,
          has_more: !last,
          truncated: false,
          duration_ms: 1,
          run_id: 'run-1',
        }
        return Response.json(response)
      },
    })
    await user.selectOptions(await screen.findByLabelText('Read type'), operation)
    if (operation !== 'scan') await user.type(screen.getByLabelText('Key or base64 object'), 'debug:key')
    await user.click(screen.getByRole('button', { name: 'Inspect key data' }))

    expect(await screen.findByText('37', { selector: 'code' })).toBeInTheDocument()
    const nextPage = screen.getByRole('button', { name: 'Load next bounded page' })
    expect(nextPage).toBeEnabled()
    await user.click(nextPage)

    expect(await screen.findByText(/TTL 120/)).toBeInTheDocument()
    expect(screen.getByLabelText('Cursor')).toHaveValue('37')
    expect(screen.getByRole('button', { name: 'Load next bounded page' })).toBeDisabled()
    expect(screen.getByText('0', { selector: 'code' })).toBeInTheDocument()
  })

  it.each([0, 1])('advances message offsets past malformed rows with %s valid entries', async (validCount) => {
    const { user } = renderDebugger({ path: '/ai-cache' })
    const defaultFetch = globalThis.fetch
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
      const url = new URL(String(input), 'http://127.0.0.1:5174')
      if (!url.pathname.startsWith('/api/v1/ai-cache/')) return defaultFetch(input, init)
      const last = url.searchParams.get('offset') === '200'
      const response: AiCacheResponse = {
        kind: 'messages',
        key: 'messages:-10055',
        ttl: last ? 120 : 60,
        configured_ttl_seconds: 172800,
        count: 201,
        entries: last ? [{ message_id: 999, text: 'last message' }] : Array.from({ length: validCount }, (_, index) => ({ message_id: index, text: 'first message' })),
        invalid_entries: last ? [] : Array.from({ length: 200 - validCount }, (_, index) => ({ index: validCount + index, error: 'ValidationError' })),
        offset: Number(url.searchParams.get('offset')),
        has_more: !last,
        truncated: false,
        run_id: 'run-1',
      }
      return Response.json(response)
    })
    const chatInputs = await screen.findAllByLabelText('Telegram chat ID')
    await user.type(chatInputs[0]!, '-10055')

    expect(await screen.findByText('Malformed stored entries were not modified')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Load next bounded page' }))

    expect(await screen.findByText('120', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.queryByText('Malformed stored entries were not modified')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load next bounded page' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'AI cache entries' })).toBeInTheDocument()
  })
})

it.each(['mongo', 'redis'] as const)('refetches identical %s form submissions', async (kind) => {
  let calls = 0
  const { user } = renderDebugger({
    path: `/${kind}`,
    handler: (path) => {
      if (path !== `/api/v1/${kind}/query`) return undefined
      calls += 1
      return Response.json(kind === 'mongo'
        ? { items: [], has_more: false, truncated: false, duration_ms: calls, run_id: 'run-1' }
        : { type: 'scan', ttl: calls, data: [], next_cursor: 0, has_more: false, truncated: false, duration_ms: calls, run_id: 'run-1' })
    },
  })
  if (kind === 'mongo') await user.type((await screen.findAllByLabelText('Collection'))[0]!, 'records')
  const button = await screen.findByRole('button', { name: kind === 'mongo' ? 'Run bounded query' : 'Inspect key data' })
  await user.click(button)
  expect(await screen.findByText(kind === 'mongo' ? /1.0 ms/ : /TTL 1/)).toBeInTheDocument()
  await user.click(button)
  expect(await screen.findByText(kind === 'mongo' ? /2.0 ms/ : /TTL 2/)).toBeInTheDocument()
  expect(calls).toBe(2)
  const input = screen.getByLabelText(kind === 'mongo' ? 'Filter (Extended JSON)' : 'Pattern')
  await user.clear(input)
  await user.type(input, kind === 'mongo' ? 'invalid' : '{{}')
  await user.click(button)
  expect((await screen.findAllByRole('alert')).some((alert) => alert.textContent?.includes(kind === 'mongo' ? 'JSON' : 'Binary values'))).toBe(true)
  expect(calls).toBe(2)
})
