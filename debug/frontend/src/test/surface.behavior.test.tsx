import { act, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { eventSummary, MockEventSource, renderDebugger } from './renderDebugger'

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
})
