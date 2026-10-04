import { act, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { baseSession, eventSummary, MockEventSource, renderDebugger } from './renderDebugger'

describe('correlated event surface', () => {
  it('follows one trace across debugger tabs', async () => {
    const telegram = eventSummary({ seq: 1, summary: 'Incoming /help', category: 'telegram' })
    const mongo = eventSummary({ seq: 2, summary: 'find chats', category: 'mongo', span_id: 'span-db' })
    const { user, router } = renderDebugger({ path: '/telegram?event=1', events: [telegram, mongo] })

    expect(await screen.findByText('Incoming /help', { selector: 'h2' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Follow trace across tabs' }))
    await user.click(screen.getByRole('link', { name: 'MongoDB' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/mongo'))
    expect(router.state.location.search.trace).toBe('trace-one')
    expect(screen.getByText(/2 retained summaries/)).toBeInTheDocument()
  })

  it('pauses visual following and resumes without losing selection', async () => {
    const selected = eventSummary({ seq: 1, summary: 'Selected dispatch' })
    const { user } = renderDebugger({ path: '/telegram?event=1', events: [selected] })
    expect(await screen.findByText('Selected dispatch', { selector: 'h2' })).toBeInTheDocument()
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Pause following' }))
    act(() => {
      MockEventSource.instances[0]?.emit('event', eventSummary({ seq: 2, summary: 'Later dispatch' }))
    })
    expect(screen.getByText(/1 retained summaries/)).toBeInTheDocument()
    expect(screen.getByText('Selected dispatch', { selector: 'h2' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Resume to latest' }))
    expect(await screen.findByText(/2 retained summaries/)).toBeInTheDocument()
    expect(screen.getByText('Selected dispatch', { selector: 'h2' })).toBeInTheDocument()
  })

  it('keeps pause active after repeated worker reloads and same-run status updates', async () => {
    let session = baseSession
    const { user } = renderDebugger({
      path: '/telegram',
      events: [eventSummary({ seq: 1 })],
      handler: (path) => path === '/api/v1/session' ? Response.json(session) : undefined,
    })
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Pause following' }))

    for (const runId of ['run-2', 'run-3']) {
      session = { ...session, run_id: runId, state: 'ready' }
      act(() => MockEventSource.instances[0]?.emit('status', session))
      await user.click(await screen.findByRole('button', { name: 'Pause following' }))
      const seq = runId === 'run-2' ? 2 : 3
      session = { ...session, latest_seq: seq, event_count: seq, recorder_errors: seq }
      act(() => {
        MockEventSource.instances[0]?.emit('status', session)
        MockEventSource.instances[0]?.emit('event', eventSummary({ seq, run_id: runId, summary: `Dispatch ${seq}` }))
      })
      expect(screen.getByRole('button', { name: 'Resume to latest' })).toBeInTheDocument()
      expect(screen.getByText(new RegExp(`${seq - 1} retained summaries`))).toBeInTheDocument()
    }

    await user.click(screen.getByRole('button', { name: 'Resume to latest' }))
    expect(await screen.findByText(/3 retained summaries/)).toBeInTheDocument()
  })
})

it('retains a bounded paused snapshot when the live buffer overflows', async () => {
  const { user } = renderDebugger({ path: '/telegram', events: [eventSummary({ seq: 1, summary: 'Paused row' })] })
  await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
  await user.click(screen.getByRole('button', { name: 'Pause following' }))
  await act(async () => {
    for (let seq = 2; seq <= 1002; seq += 1) MockEventSource.instances[0]?.emit('event', eventSummary({ seq, summary: `Live row ${seq}` }))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  expect(screen.getByText(/1 retained summaries/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Resume to latest' }))
  expect(await screen.findByText(/1000 retained summaries/)).toBeInTheDocument()
})

it.each([
  ['/telegram?from=2026-09-13T12%3A00%3A00Z', '2026-09-13T12:00:00.000Z', 1],
  ['/telegram?to=2026-09-13T12%3A00%3A00.100Z', '2026-09-13T12:00:00Z', 1],
  ['/telegram?from=2026-09-13T14%3A00%3A00%2B02%3A00', '2026-09-13T11:59:59Z', 0],
  ['/telegram?to=2026-09-13T10%3A00%3A00-02%3A00', '2026-09-13T12:00:01Z', 0],
])('compares RFC3339 time filters as instants: %s', async (path, timestamp, count) => {
  renderDebugger({ path, events: [eventSummary({ timestamp })] })
  await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))
  expect(await screen.findByText(new RegExp(`${count} retained summaries`))).toBeInTheDocument()
})
