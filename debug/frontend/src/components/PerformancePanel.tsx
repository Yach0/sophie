import { useQuery } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { t } from 'ttag'
import { api } from '../api/client'
import type { EventSummary, ResourceResponse, ResourceSample, SearchFilters } from '../api/types'
import { useDebug } from '../state/debug-context'

function metric(sample: ResourceSample, key: 'cpu_percent' | 'rss' | 'event_loop_lag_ms'): number | null {
  const value = key === 'rss' ? sample.rss_bytes ?? sample.rss : sample[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function Sparkline({ samples, metricKey, label }: { samples: ResourceSample[]; metricKey: 'cpu_percent' | 'rss' | 'event_loop_lag_ms'; label: string }) {
  const values = samples.map((sample) => metric(sample, metricKey))
  const finite = values.filter((value): value is number => value !== null)
  const maximum = Math.max(...finite, 1)
  const points = values.map((value, index) => {
    const xCoordinate = values.length <= 1 ? 0 : (index / (values.length - 1)) * 300
    const yCoordinate = value === null ? 96 : 96 - (value / maximum) * 90
    return `${xCoordinate},${yCoordinate}`
  }).join(' ')
  return (
    <figure className="chart-card">
      <figcaption>{label} · {finite.at(-1)?.toLocaleString() ?? t`unavailable`}</figcaption>
      <svg viewBox="0 0 300 100" role="img" aria-label={label} preserveAspectRatio="none">
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
    </figure>
  )
}
function TraceWaterfall({ events }: { events: EventSummary[] }) {
  const spans = events.filter((event) => event.duration_ms !== null && event.phase === 'finish').slice(-40)
  if (spans.length === 0) return <div className="empty-state">{t`Select a trace with completed spans to show its waterfall.`}</div>
  const starts = spans.map((event) => Date.parse(event.timestamp) - (event.duration_ms ?? 0))
  const origin = Math.min(...starts)
  const end = Math.max(...spans.map((event) => Date.parse(event.timestamp)))
  const range = Math.max(end - origin, 1)
  return (
    <svg className="waterfall" viewBox={`0 0 800 ${spans.length * 24}`} role="img" aria-label={t`Selected trace waterfall`}>
      {spans.map((event, index) => {
        const duration = event.duration_ms ?? 0
        const start = Date.parse(event.timestamp) - duration
        const xCoordinate = 180 + ((start - origin) / range) * 600
        const width = Math.max(2, (duration / range) * 600)
        return (
          <g key={event.seq}>
            <text x="0" y={index * 24 + 16}>{event.name.slice(0, 26)}</text>
            <rect x={xCoordinate} y={index * 24 + 4} width={width} height="14" rx="2" />
            <title>{event.summary} · {duration.toFixed(2)} ms</title>
          </g>
        )
      })}
    </svg>
  )
}


export function PerformancePanel() {
  const debug = useDebug()
  const search = useSearch({ strict: false }) as SearchFilters
  const resources = useQuery({
    queryKey: ['resources', debug.session.run_id],
    queryFn: ({ signal }) => api<ResourceResponse>(`/resources${debug.session.run_id ? `?run_id=${encodeURIComponent(debug.session.run_id)}` : ''}`, { signal }),
    refetchInterval: 1_000,
    gcTime: 0,
    retry: false,
  })
  const middleware = debug.events
    .filter((event) => event.category === 'middleware' && event.phase === 'finish')
    .slice(-200)
    .sort((left, right) => (right.duration_ms ?? 0) - (left.duration_ms ?? 0))
  const traceEvents = search.trace ? debug.events.filter((event) => event.trace_id === search.trace) : []

  if (resources.error) return <div className="notice error" role="alert">{resources.error.message}</div>
  const data = resources.data ?? { run_id: null, worker: [], collector: [], vite: [] }
  return (
    <section className="view-stack" aria-labelledby="performance-title">
      <div className="view-heading"><div><h1 id="performance-title">{t`Performance`}</h1><p>{t`Bounded process samples and wall-clock middleware timing. RSS changes are not per-handler allocations.`}</p></div></div>
      <div className="chart-grid">
        <Sparkline samples={data.worker} metricKey="cpu_percent" label={t`Worker CPU percent`} />
        <Sparkline samples={data.worker} metricKey="rss" label={t`Worker RSS bytes`} />
        <Sparkline samples={data.worker} metricKey="event_loop_lag_ms" label={t`Worker event-loop lag ms`} />
        <Sparkline samples={data.collector} metricKey="cpu_percent" label={t`Collector CPU percent`} />
        <Sparkline samples={data.collector} metricKey="rss" label={t`Collector RSS bytes`} />
        <Sparkline samples={data.vite} metricKey="rss" label={t`Vite RSS bytes`} />
      </div>
      <section>
        <h2>{t`Selected trace waterfall`}</h2>
        <TraceWaterfall events={traceEvents} />
      </section>
      <section>
        <h2>{t`Slowest retained middleware calls`}</h2>
        <div className="simple-table" role="table" aria-label={t`Middleware wall time`}>
          <div role="row"><strong role="columnheader">{t`Middleware`}</strong><strong role="columnheader">{t`Inclusive wall time`}</strong><strong role="columnheader">{t`Trace`}</strong></div>
          {middleware.map((event) => <div role="row" key={event.seq}><span role="cell">{event.name}</span><span role="cell">{event.duration_ms?.toFixed(2) ?? '—'} ms</span><code role="cell">{event.trace_id?.slice(0, 12) ?? '—'}</code></div>)}
        </div>
      </section>
    </section>
  )
}
