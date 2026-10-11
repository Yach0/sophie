import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useMemo, useRef } from 'react'
import { t } from 'ttag'
import { stringifyRedacted } from '../api/client'
import type { JsonValue } from '../api/types'

interface ResultRow {
  index: number
  value: JsonValue
}

const column = createColumnHelper<ResultRow>()
const columns = [
  column.accessor('index', { header: '#', size: 64 }),
  column.accessor('value', {
    header: t`Redacted value`,
    cell: (info) => stringifyRedacted(info.getValue()),
    size: 760,
  }),
]

export function ResultGrid({ values, label = t`Inspector results` }: { values: JsonValue[]; label?: string }) {
  const parentRef = useRef<HTMLDivElement>(null)
  const rows = useMemo(() => values.map((value, index) => ({ index, value })), [values])
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() })
  const tableRows = table.getRowModel().rows
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 76,
    overscan: 6,
  })

  return (
    <div className="result-grid" ref={parentRef} role="region" aria-label={label} tabIndex={0}>
      <div className="table-header" role="row">
        {table.getHeaderGroups()[0]?.headers.map((header) => (
          <span key={header.id} role="columnheader" style={{ width: header.getSize() }}>
            {flexRender(header.column.columnDef.header, header.getContext())}
          </span>
        ))}
      </div>
      <div className="virtual-space" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const row = tableRows[virtualRow.index]
          if (!row) return null
          return (
            <div className="result-row" role="row" key={row.id} style={{ height: virtualRow.size, transform: `translateY(${virtualRow.start}px)` }}>
              {row.getVisibleCells().map((cell) => (
                <pre key={cell.id} role="cell" style={{ width: cell.column.getSize() }}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</pre>
              ))}
            </div>
          )
        })}
      </div>
      {values.length === 0 && <div className="empty-state">{t`The inspector returned no values.`}</div>}
    </div>
  )
}
