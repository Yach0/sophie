import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { vi } from 'vitest'
import type { EventSummary, JsonValue, Session } from '../api/types'
import { routeTree } from '../routeTree.gen'
import { DebugProvider } from '../state/debug-context'

export const baseSession: Session = {
  session_id: 'session-1',
  run_id: 'run-1',
  state: 'ready',
  restart_required: false,
  worker_pid: 123,
  sanitized_targets: { mongo: 'debug', redis: 15 },
  capabilities: { redis_pubsub: false, mongo: true },
  dropped_total: 0,
  recorder_errors: 0,
  oldest_seq: 1,
  latest_seq: 2,
  event_count: 2,
  event_bytes: 500,
  limits: { events: 10_000 },
  csrf_token: 'csrf-test',
  sensitive: true,
  detail: null,
}

export function eventSummary(overrides: Partial<EventSummary> = {}): EventSummary {
  return {
    schema_version: 1,
    seq: 1,
    session_id: 'session-1',
    run_id: 'run-1',
    pid: 123,
    timestamp: '2026-09-13T12:00:00Z',
    category: 'telegram',
    name: 'update.message',
    phase: 'finish',
    level: 'info',
    trace_id: 'trace-one',
    span_id: 'span-one',
    parent_span_id: null,
    task_id: 'task-one',
    update_id: 44,
    chat_tid: -10055,
    duration_ms: 12,
    error_type: null,
    origin: 'update',
    outcome: 'ok',
    summary: 'Incoming /help',
    truncated: false,
    redacted: false,
    ...overrides,
  }
}

export class MockEventSource extends EventTarget {
  static instances: MockEventSource[] = []
  readonly url: string
  readonly withCredentials: boolean
  readyState = 1
  onerror: ((this: EventSource, event: Event) => unknown) | null = null
  onmessage: ((this: EventSource, event: MessageEvent) => unknown) | null = null
  onopen: ((this: EventSource, event: Event) => unknown) | null = null
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSED = 2

  constructor(url: string | URL, init?: EventSourceInit) {
    super()
    this.url = String(url)
    this.withCredentials = init?.withCredentials ?? false
    MockEventSource.instances.push(this)
  }

  close(): void {
    this.readyState = 2
  }

  emit(type: 'event' | 'status' | 'gap', data: JsonValue | object): void {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

interface HarnessOptions {
  path?: string
  events?: EventSummary[]
  session?: Session
  handler?: (path: string, init: RequestInit) => Response | Promise<Response> | undefined
  children?: ReactNode
}

export function renderDebugger({ path = '/', events = [], session = baseSession, handler, children }: HarnessOptions = {}) {
  MockEventSource.instances = []
  Object.defineProperty(globalThis, 'EventSource', { value: MockEventSource, configurable: true })
  const calls: Array<{ path: string; init: RequestInit }> = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const requestPath = new URL(String(input), 'http://127.0.0.1:5174').pathname
    calls.push({ path: requestPath, init })
    const handled = handler?.(requestPath, init)
    if (handled) return await handled
    if (requestPath === '/api/v1/session') return Response.json(session)
    if (requestPath === '/api/v1/events') {
      return Response.json({ events, next_after: events.at(-1)?.seq ?? 0, oldest_seq: events[0]?.seq ?? null, latest_seq: events.at(-1)?.seq ?? null, gap: false, dropped_total: 0 })
    }
    if (requestPath.startsWith('/api/v1/events/')) {
      const seq = Number(requestPath.split('/').at(-1))
      const event = events.find((candidate) => candidate.seq === seq)
      return event ? Response.json({ ...event, monotonic_ns: '123', payload: { text: event.summary } }) : Response.json({ error: { code: 'event_evicted', message: 'gone', request_id: 'r' } }, { status: 404 })
    }
    if (requestPath === '/api/v1/resources') return Response.json({ run_id: session.run_id, worker: [], collector: [], vite: [] })
    return Response.json({ error: { code: 'not_mocked', message: requestPath, request_id: 'r' } }, { status: 500 })
  })
  vi.stubGlobal('fetch', fetchMock)

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({ routeTree, history })
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <DebugProvider initialSession={session}>
        <RouterProvider router={router} />
        {children}
      </DebugProvider>
    </QueryClientProvider>,
  )
  return { ...rendered, user: userEvent.setup(), router, queryClient, calls }
}
