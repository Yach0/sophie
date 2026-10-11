import { t } from 'ttag'
import { useDebug } from '../state/debug-context'
import { EventWorkspace } from './EventWorkspace'

export function Overview() {
  const { events, session } = useDebug()
  const errors = events.filter((event) => event.level === 'error' || event.outcome === 'error')
  const slow = events.filter((event) => (event.duration_ms ?? 0) >= 500)
  const dispatches = events.filter((event) => event.category === 'telegram' && event.origin === 'update')
  const targets = Object.entries(session.sanitized_targets)
  const capabilities = Object.entries(session.capabilities)

  return (
    <div className="overview-stack">
      <section className="overview-grid" aria-label={t`Session overview`}>
        <article><span>{t`Worker state`}</span><strong>{session.state}</strong><small>{session.detail ?? t`No worker diagnostic`}</small></article>
        <article><span>{t`Errors retained`}</span><strong>{errors.length}</strong><small>{errors.at(-1)?.summary ?? t`No retained errors`}</small></article>
        <article><span>{t`Slow operations`}</span><strong>{slow.length}</strong><small>{t`500 ms or slower`}</small></article>
        <article><span>{t`Recent dispatches`}</span><strong>{dispatches.length}</strong><small>{dispatches.at(-1)?.summary ?? t`No incoming updates yet`}</small></article>
      </section>
      <section className="target-grid">
        <div><h2>{t`Sanitized targets`}</h2><dl>{targets.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl></div>
        <div><h2>{t`Capture coverage`}</h2><dl>{capabilities.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl></div>
      </section>
      <div className="notice warning">{t`Captured development payloads can contain personal or unknown secret content. History stays in collector memory, but Sophie's existing console, security, and runtime logs are separate and unchanged.`}</div>
      <EventWorkspace title={t`Recent correlated activity`} description={t`Select a summary to fetch its payload, then follow its trace through every view.`} />
    </div>
  )
}
