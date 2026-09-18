import { formDevtoolsPlugin } from '@tanstack/react-form-devtools'
import { TanStackDevtools } from '@tanstack/react-devtools'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { Link, Outlet, useNavigate, useSearch } from '@tanstack/react-router'
import { TanStackRouterDevtools } from '@tanstack/react-router-devtools'
import { type FormEvent, useState } from 'react'
import { t } from 'ttag'
import type { Level, SearchFilters } from '../api/types'
import { useDebug } from '../state/debug-context'


export function AppShell() {
  const tabs = [
    ['/', t`Overview`],
    ['/telegram', t`Telegram`],
    ['/mongo', t`MongoDB`],
    ['/redis', t`Redis`],
    ['/ai-cache', t`AI Cache`],
    ['/performance', t`Performance`],
    ['/logs', t`Logs`],
  ] as const
  const debug = useDebug()
  const search = useSearch({ strict: false }) as SearchFilters
  const navigate = useNavigate()
  const [query, setQuery] = useState(search.q ?? '')
  const statusClass = `status-badge ${debug.session.state}`

  const updateSearch = (patch: Partial<SearchFilters>) => {
    void navigate({ to: '.', search: (previous) => ({ ...previous, ...patch }), replace: true })
  }
  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    updateSearch({ q: query.trim() || undefined })
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-block">
          <strong>{t`Sophie Debugger`}</strong>
          <span>{t`Sensitive development session · memory only`}</span>
        </div>
        <div className="status-strip" aria-live="polite">
          <span className={statusClass}>{debug.session.state}</span>
          <span className={`connection ${debug.connection}`}>{debug.connection === 'reconnecting' ? t`SSE reconnecting` : debug.connection === 'live' ? t`SSE live` : t`SSE connecting`}</span>
          <span>{t`run`} <code>{debug.session.run_id?.slice(0, 10) ?? '—'}</code></span>
          <span>{t`PID`} {debug.session.worker_pid ?? '—'}</span>
          <span>{t`events`} {debug.session.event_count}</span>
          <span>{t`dropped`} {debug.session.dropped_total}</span>
          <span>{t`truncated`} {debug.events.filter((event) => event.truncated).length}</span>
          <button type="button" onClick={() => void debug.restart()} disabled={debug.session.state === 'reloading'}>
            {t`Restart worker`}
          </button>
        </div>
      </header>

      {debug.session.restart_required && (
        <div className="notice warning" role="alert">
          {t`Parent debugger code changed. Restart make dev to load it; worker reload cannot update the collector.`}
        </div>
      )}
      {debug.session.detail && <div className="notice error" role="alert">{debug.session.detail}</div>}
      {debug.gap && (
        <div className="notice warning" role="alert">
          {t`Some events were evicted or skipped. The retained event window was reloaded.`}
          <button type="button" onClick={() => debug.setGap(false)}>{t`Dismiss`}</button>
        </div>
      )}

      <nav className="tabs" aria-label={t`Debugger views`}>
        {tabs.map(([to, label]) => (
          <Link key={to} to={to} search={search} activeOptions={{ exact: to === '/' }}>
            {label}
          </Link>
        ))}
      </nav>

      <form className="filter-toolbar" onSubmit={submitSearch}>
        <label>
          <span>{t`Search summaries`}</span>
          <input value={query} onChange={(event) => setQuery(event.currentTarget.value)} maxLength={256} />
        </label>
        <label>
          <span>{t`Run`}</span>
          <select value={search.run ?? ''} onChange={(event) => updateSearch({ run: event.currentTarget.value || undefined })}>
            <option value="">{t`All runs`}</option>
            {[...new Set(debug.events.map((event) => event.run_id))].map((run) => <option key={run} value={run}>{run.slice(0, 12)}</option>)}
          </select>
        </label>
        <label>
          <span>{t`Level`}</span>
          <select value={search.level ?? ''} onChange={(event) => updateSearch({ level: (event.currentTarget.value || undefined) as Level | undefined })}>
            <option value="">{t`All levels`}</option>
            <option value="debug">{t`Debug`}</option>
            <option value="info">{t`Info`}</option>
            <option value="warning">{t`Warning`}</option>
            <option value="error">{t`Error`}</option>
          </select>
        </label>
        <label>
          <span>{t`Chat ID`}</span>
          <input
            key={`chat-${search.chat ?? ''}`}
            inputMode="numeric"
            defaultValue={search.chat ?? ''}
            onBlur={(event) => {
              const value = Number(event.currentTarget.value)
              updateSearch({ chat: Number.isSafeInteger(value) && event.currentTarget.value.trim() ? value : undefined })
            }}
          />
        </label>
        <label>
          <span>{t`From (UTC RFC3339)`}</span>
          <input key={`from-${search.from ?? ''}`} defaultValue={search.from ?? ''} placeholder="2026-09-13T12:00:00Z" onBlur={(event) => updateSearch({ from: event.currentTarget.value || undefined })} />
        </label>
        <label>
          <span>{t`To (UTC RFC3339)`}</span>
          <input key={`to-${search.to ?? ''}`} defaultValue={search.to ?? ''} placeholder="2026-09-13T13:00:00Z" onBlur={(event) => updateSearch({ to: event.currentTarget.value || undefined })} />
        </label>
        <label className="check-label">
          <input type="checkbox" checked={search.errors ?? false} onChange={(event) => updateSearch({ errors: event.currentTarget.checked || undefined })} />
          <span>{t`Errors only`}</span>
        </label>
        <button type="button" className={debug.live ? '' : 'primary'} onClick={debug.live ? debug.pause : debug.resume}>
          {debug.live ? t`Pause following` : t`Resume to latest`}
        </button>
        {search.trace && (
          <button type="button" onClick={() => updateSearch({ trace: undefined, event: undefined })}>
            {t`Clear trace`} <code>{search.trace.slice(0, 8)}</code>
          </button>
        )}
      </form>

      <main className="workspace"><Outlet /></main>

      {import.meta.env.DEV && import.meta.env.MODE !== 'test' && (
        <>
          <ReactQueryDevtools initialIsOpen={false} />
          <TanStackRouterDevtools initialIsOpen={false} />
          <TanStackDevtools plugins={[formDevtoolsPlugin()]} config={{ defaultOpen: false }} />
        </>
      )}
    </div>
  )
}
