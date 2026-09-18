import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { eventSummary, renderDebugger } from './renderDebugger'

describe('untrusted payload rendering', () => {
  it('renders HTML and script-bearing event data as inert text', async () => {
    const event = eventSummary({ seq: 7, summary: 'Untrusted log payload', category: 'log' })
    const payload = '<script>window.__debugPayloadExecuted = true</script><b>not markup</b>'
    renderDebugger({
      path: '/logs?event=7&level=debug',
      events: [event],
      handler: (path) => path === '/api/v1/events/7'
        ? Response.json({ ...event, monotonic_ns: '123', payload: { message: payload } })
        : undefined,
    })

    const json = await screen.findByTestId('event-json')
    expect(json).toHaveTextContent(payload)
    expect(json.querySelector('script')).toBeNull()
    expect(document.querySelector('b')).toBeNull()
    expect((window as Window & { __debugPayloadExecuted?: boolean }).__debugPayloadExecuted).toBeUndefined()
  })
})
