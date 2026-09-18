import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { t } from 'ttag'
import { api, ApiError, stringifyRedacted } from '../api/client'
import type { SearchFilters, StoredEvent } from '../api/types'

export function EventDetail({ seq }: { seq: number | undefined }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as SearchFilters
  const [copied, setCopied] = useState(false)
  const detail = useQuery({
    queryKey: ['event-detail', seq],
    queryFn: ({ signal }) => api<StoredEvent>(`/events/${seq}`, { signal }),
    enabled: seq !== undefined,
    gcTime: 0,
    retry: false,
  })

  useEffect(() => {
    setCopied(false)
    return () => {
      void queryClient.cancelQueries({ queryKey: ['event-detail', seq] })
      queryClient.removeQueries({ queryKey: ['event-detail', seq], exact: true })
    }
  }, [queryClient, seq])

  if (seq === undefined) {
    return <aside className="detail-pane empty-state">{t`Select an event to fetch its redacted payload.`}</aside>
  }
  if (detail.isPending) return <aside className="detail-pane" role="status">{t`Loading event…`}</aside>
  if (detail.error) {
    const evicted = detail.error instanceof ApiError && detail.error.code === 'event_evicted'
    return (
      <aside className="detail-pane notice error" role="alert">
        {evicted ? t`This event was evicted from the in-memory session history.` : detail.error.message}
      </aside>
    )
  }
  if (!detail.data) return null

  const event = detail.data
  return (
    <aside className="detail-pane" aria-label={t`Selected event detail`}>
      <div className="detail-actions">
        {event.trace_id && event.trace_id !== search.trace && (
          <button
            type="button"
            onClick={() => void navigate({ to: '.', search: (previous) => ({ ...previous, trace: event.trace_id ?? undefined }), replace: true })}
          >
            {t`Follow trace across tabs`}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(stringifyRedacted(event)).then(() => setCopied(true))
          }}
        >
          {copied ? t`Copied` : t`Copy redacted JSON`}
        </button>
      </div>
      <dl className="fact-grid">
        <div><dt>{t`Sequence`}</dt><dd>{event.seq}</dd></div>
        <div><dt>{t`Outcome`}</dt><dd>{event.outcome ?? '—'}</dd></div>
        <div><dt>{t`Trace`}</dt><dd><code>{event.trace_id ?? '—'}</code></dd></div>
        <div><dt>{t`Span`}</dt><dd><code>{event.span_id ?? '—'}</code></dd></div>
        <div><dt>{t`Origin`}</dt><dd>{event.origin}</dd></div>
        <div><dt>{t`Chat`}</dt><dd>{event.chat_tid ?? '—'}</dd></div>
      </dl>
      {(event.redacted || event.truncated) && (
        <div className="notice warning">
          {event.redacted && <span>{t`Credential-shaped values were redacted. `}</span>}
          {event.truncated && <span>{t`Payload exceeded a capture limit and was truncated.`}</span>}
        </div>
      )}
      <h2>{event.summary}</h2>
      <pre className="json-view" data-testid="event-json">{stringifyRedacted(event.payload)}</pre>
    </aside>
  )
}
