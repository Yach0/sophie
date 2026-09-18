import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { api, setCsrfToken } from '../api/client'
import type { EventPage, EventSummary, Session } from '../api/types'

const MAX_VISIBLE_EVENTS = 1_000
const EVENT_PAGE_SIZE = 500
const MAX_ACTIVE_PAGES = 4

interface FeedState {
  events: EventSummary[]
  oldestSeq: number | null
  latestSeq: number | null
  droppedTotal: number
}

interface DebugState {
  session: Session
  events: EventSummary[]
  live: boolean
  gap: boolean
  connection: 'connecting' | 'live' | 'reconnecting'
  setGap: (gap: boolean) => void
  pause: () => void
  resume: () => void
  restart: () => Promise<void>
}

const DebugContext = createContext<DebugState | null>(null)

function appendEvent(feed: FeedState | undefined, event: EventSummary): FeedState {
  const current = feed ?? { events: [], oldestSeq: null, latestSeq: null, droppedTotal: 0 }
  const latest = current.events.at(-1)
  if (latest?.seq === event.seq) return current
  let events: EventSummary[]
  if (latest === undefined || event.seq > latest.seq) {
    events = [...current.events, event].slice(-MAX_VISIBLE_EVENTS)
  } else if (current.events.some((candidate) => candidate.seq === event.seq)) {
    return current
  } else {
    events = [...current.events, event].sort((left, right) => left.seq - right.seq).slice(-MAX_VISIBLE_EVENTS)
  }
  return {
    ...current,
    events,
    oldestSeq: events[0]?.seq ?? current.oldestSeq,
    latestSeq: events.at(-1)?.seq ?? current.latestSeq,
  }
}

export function DebugProvider({ initialSession, children }: { initialSession: Session; children: ReactNode }) {
  const queryClient = useQueryClient()
  const [pauseAt, setPauseAt] = useState<number | null>(null)
  const [gap, setGap] = useState(false)
  const [connection, setConnection] = useState<'connecting' | 'live' | 'reconnecting'>('connecting')
  const sourceRef = useRef<EventSource | null>(null)

  const sessionQuery = useQuery({
    queryKey: ['session'],
    initialData: initialSession,
    queryFn: ({ signal }) => api<Session>('/session', { signal }),
    refetchInterval: 2_000,
    staleTime: 500,
  })
  const session = sessionQuery.data
  setCsrfToken(session.csrf_token)

  const feedQuery = useQuery({
    queryKey: ['event-feed'],
    queryFn: async ({ signal }): Promise<FeedState> => {
      let cursor = Math.max(0, (initialSession.latest_seq ?? 0) - EVENT_PAGE_SIZE * MAX_ACTIVE_PAGES)
      let gap = false
      let oldestSeq: number | null = null
      let latestSeq: number | null = null
      const events: EventSummary[] = []
      let droppedTotal = 0
      for (let pageIndex = 0; pageIndex < MAX_ACTIVE_PAGES; pageIndex += 1) {
        const page = await api<EventPage>(`/events?after=${cursor}&limit=${EVENT_PAGE_SIZE}`, { signal })
        events.push(...page.events)
        gap ||= page.gap
        oldestSeq = page.oldest_seq
        latestSeq = page.latest_seq
        droppedTotal = page.dropped_total
        cursor = page.next_after
        if (page.events.length === 0 || latestSeq === null || cursor >= latestSeq) break
      }
      setGap(gap)
      return {
        events: events.slice(-MAX_VISIBLE_EVENTS),
        oldestSeq,
        latestSeq: events.at(-1)?.seq ?? latestSeq,
        droppedTotal,
      }
    },
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
  })

  useEffect(() => {
    if (!feedQuery.isSuccess || sourceRef.current) return
    const after = feedQuery.data.latestSeq ?? 0
    const source = new EventSource(`/api/v1/events/stream?after=${after}`, { withCredentials: true })
    sourceRef.current = source
    source.onopen = () => setConnection('live')
    source.onerror = () => setConnection('reconnecting')
    const onEvent = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as EventSummary
        queryClient.setQueryData<FeedState>(['event-feed'], (current) => appendEvent(current, event))
      } catch {
        setGap(true)
      }
    }
    const onStatus = (message: MessageEvent<string>) => {
      try {
        const next = JSON.parse(message.data) as Session
        queryClient.setQueryData(['session'], next)
      } catch {
        setGap(true)
      }
    }
    source.addEventListener('event', onEvent as EventListener)
    source.addEventListener('status', onStatus as EventListener)
    source.addEventListener('gap', () => {
      setGap(true)
      void queryClient.invalidateQueries({ queryKey: ['event-feed'] })
    })
    return () => {
      source.close()
      sourceRef.current = null
    }
  }, [feedQuery.isSuccess, queryClient])

  useEffect(() => {
    if (pauseAt !== null && session.run_id !== initialSession.run_id) {
      setPauseAt(null)
    }
  }, [initialSession.run_id, pauseAt, session.run_id])

  const allEvents = feedQuery.data?.events ?? []
  const events = pauseAt === null ? allEvents : allEvents.filter((event) => event.seq <= pauseAt)
  const value = useMemo<DebugState>(
    () => ({
      session,
      events,
      live: pauseAt === null,
      connection,
      gap,
      setGap,
      pause: () => setPauseAt(feedQuery.data?.latestSeq ?? 0),
      resume: () => setPauseAt(null),
      restart: async () => {
        await api('/worker/restart', { method: 'POST' })
        await queryClient.invalidateQueries({ queryKey: ['session'] })
      },
    }),
    [connection, events, feedQuery.data?.latestSeq, gap, pauseAt, queryClient, session],
  )

  return <DebugContext.Provider value={value}>{children}</DebugContext.Provider>
}

export function useDebug(): DebugState {
  const value = useContext(DebugContext)
  if (!value) throw new Error('useDebug must be rendered under DebugProvider')
  return value
}
