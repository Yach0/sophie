import { act, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MutationFlow } from '../components/MutationFlow'
import { baseSession, MockEventSource, renderDebugger } from './renderDebugger'

const prepared = {
  action_id: 'action-1',
  run_id: 'run-1',
  target: 'debug:key',
  operation: 'redis.command',
  preview: { command: 'SET', key: 'debug:key' },
  warnings: [],
  expires_at: '2099-01-01T00:00:00Z',
  confirmation_text: 'SET debug:key',
}

function responseHandler(path: string) {
  if (path === '/api/v1/actions/prepare') return Response.json(prepared)
  if (path === '/api/v1/actions/action-1/execute') {
    return Response.json({ ...prepared, state: 'succeeded', result: { changed: true }, error: null })
  }
  return undefined
}

describe('confirmed write flow', () => {
  it('invalidates an old preview when the worker reloads', async () => {
    const action = { kind: 'redis.command', command: 'SET', args: ['debug:key', 'value'] }
    const { user } = renderDebugger({ handler: responseHandler, children: <MutationFlow action={action} valid /> })
    await user.click(screen.getByRole('button', { name: 'Prepare write preview' }))
    expect(await screen.findByText('SET debug:key')).toBeInTheDocument()
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1))

    act(() => {
      MockEventSource.instances[0]?.emit('status', { ...baseSession, run_id: 'run-2', state: 'ready' })
    })

    await waitFor(() => expect(screen.queryByText('SET debug:key')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Execute once' })).not.toBeInTheDocument()
  })

  it('submits one exact confirmed write even on a double click', async () => {
    const action = { kind: 'redis.command', command: 'SET', args: ['debug:key', 'value'] }
    const { user, calls } = renderDebugger({ handler: responseHandler, children: <MutationFlow action={action} valid /> })
    await user.click(screen.getByRole('button', { name: 'Prepare write preview' }))
    await user.type(await screen.findByLabelText('Type the exact confirmation text'), 'SET debug:key')
    await user.dblClick(screen.getByRole('button', { name: 'Execute once' }))

    await screen.findByText(/Action status: succeeded/)
    const executions = calls.filter((call) => call.path === '/api/v1/actions/action-1/execute')
    expect(executions).toHaveLength(1)
  })
})
