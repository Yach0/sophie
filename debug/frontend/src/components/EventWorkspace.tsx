import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { t } from 'ttag'
import type { Category, EventSummary, SearchFilters } from '../api/types'
import { useDebug } from '../state/debug-context'
import { EventDetail } from './EventDetail'

const column = createColumnHelper<EventSummary>()
const columns = [
  column.accessor('timestamp', {
    header: t`Time`,
    cell: (info) => new Date(info.getValue()).toLocaleTimeString(),
    size: 104,
  }),
  column.accessor('category', { header: t`Category`, size: 96 }),
  column.accessor('name', { header: t`Operation`, size: 180 }),
  column.accessor('origin', { header: t`Origin`, size: 104 }),
  column.accessor('chat_tid', { header: t`Chat`, size: 106 }),
  column.accessor('summary', { header: t`Summary`, size: 460 }),
  column.accessor('duration_ms', {
    header: t`Duration`,
    cell: (info) => info.getValue() === null ? '—' : `${info.getValue()!.toFixed(1)} ms`,
    size: 100,
  }),
  column.accessor('level', { header: t`Level`, size: 84 }),
]

interface EventWorkspaceProps {
  title: string
  description: string
  categories?: Category[]
  defaultErrors?: boolean
  telegram?: boolean
}

export function EventWorkspace({ title, description, categories, defaultErrors = false, telegram = false }: EventWorkspaceProps) {
  const debug = useDebug()
  const search = useSearch({ strict: false }) as SearchFilters
  const navigate = useNavigate()
  const [sorting, setSorting] = useState<SortingState>([])
  const [detailWidth, setDetailWidth] = useState(42)
  const parentRef = useRef<HTMLDivElement>(null)

  const events = useMemo(() => {
    const query = search.q?.toLocaleLowerCase()
    return debug.events.filter((event) => {
      if (categories && !search.trace && !categories.includes(event.category)) return false
      if (search.run && event.run_id !== search.run) return false
      if (search.trace && event.trace_id !== search.trace) return false
      if (search.chat !== undefined && event.chat_tid !== search.chat) return false
      if (search.level && event.level !== search.level) return false
      if ((search.errors || (defaultErrors && !search.level && !search.trace && !search.q)) && event.level !== 'error' && event.outcome !== 'error') return false
      if (query && !event.summary.toLocaleLowerCase().includes(query) && !event.name.toLocaleLowerCase().includes(query)) return false
      if (telegram && !search.polling && event.name.toLocaleLowerCase().includes('getupdates') && event.level !== 'error') return false
      if (search.from && event.timestamp < search.from) return false
      if (search.to && event.timestamp > search.to) return false
      return true
    })
  }, [categories, debug.events, defaultErrors, search, telegram])

  const table = useReactTable({
    data: events,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })
  const rows = table.getRowModel().rows
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 36,
    overscan: 12,
  })

  useEffect(() => {
    if (debug.live && search.event === undefined && rows.length > 0) {
      virtualizer.scrollToIndex(rows.length - 1, { align: 'end' })
    }
  }, [debug.live, rows.length, search.event, virtualizer])

  const selectEvent = (event: EventSummary) => {
    void navigate({ to: '.', search: (previous) => ({ ...previous, event: event.seq }), replace: true })
  }
  const resizeByKeyboard = (delta: number) => setDetailWidth((width) => Math.max(25, Math.min(70, width + delta)))

  return (
    <section className="view-stack" aria-labelledby="view-title">
      <div className="view-heading">
        <div><h1 id="view-title">{title}</h1><p>{description}</p></div>
        <span>{events.length} {t`retained summaries`}</span>
      </div>
      {telegram && (
        <label className="check-label inline-control">
          <input
            type="checkbox"
            checked={search.polling ?? false}
            onChange={(event) => void navigate({ to: '.', search: (previous) => ({ ...previous, polling: event.currentTarget.checked || undefined }), replace: true })}
          />
          <span>{t`Reveal getUpdates polling`}</span>
        </label>
      )}
      <div className="split-pane" style={{ gridTemplateColumns: `minmax(0, ${100 - detailWidth}fr) 6px minmax(19rem, ${detailWidth}fr)` }}>
        <div className="event-table" ref={parentRef} role="region" aria-label={t`Event summaries`} tabIndex={0}>
          <div className="table-header" role="row">
            {table.getHeaderGroups()[0]?.headers.map((header) => (
              <button
                key={header.id}
                type="button"
                role="columnheader"
                style={{ width: header.getSize() }}
                onClick={header.column.getToggleSortingHandler()}
              >
                {flexRender(header.column.columnDef.header, header.getContext())}
                {header.column.getIsSorted() === 'asc' ? ' ↑' : header.column.getIsSorted() === 'desc' ? ' ↓' : ''}
              </button>
            ))}
          </div>
          <div className="virtual-space" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const row = rows[virtualRow.index]
              if (!row) return null
              const selected = row.original.seq === search.event
              return (
                <button
                  key={row.id}
                  type="button"
                  className={`table-row level-${row.original.level}${selected ? ' selected' : ''}`}
                  style={{ height: virtualRow.size, transform: `translateY(${virtualRow.start}px)` }}
                  onClick={() => selectEvent(row.original)}
                  aria-pressed={selected}
                >
                  {row.getVisibleCells().map((cell) => (
                    <span key={cell.id} style={{ width: cell.column.getSize() }} title={String(cell.getValue() ?? '')}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </span>
                  ))}
                </button>
              )
            })}
          </div>
          {rows.length === 0 && <div className="empty-state">{t`No retained events match these filters.`}</div>}
        </div>
        <div
          className="resize-handle"
          role="separator"
          aria-label={t`Resize event detail`}
          aria-orientation="vertical"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') resizeByKeyboard(3)
            if (event.key === 'ArrowRight') resizeByKeyboard(-3)
          }}
          onPointerDown={(event) => {
            const startX = event.clientX
            const startWidth = detailWidth
            event.currentTarget.setPointerCapture(event.pointerId)
            const move = (moveEvent: PointerEvent) => {
              const percent = ((startX - moveEvent.clientX) / window.innerWidth) * 100
              setDetailWidth(Math.max(25, Math.min(70, startWidth + percent)))
            }
            const stop = () => {
              window.removeEventListener('pointermove', move)
              window.removeEventListener('pointerup', stop)
            }
            window.addEventListener('pointermove', move)
            window.addEventListener('pointerup', stop)
          }}
        />
        <EventDetail seq={search.event} />
      </div>
    </section>
  )
}
