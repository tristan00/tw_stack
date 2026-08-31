import {
  columnFilteringFeature,
  columnVisibilityFeature,
  createCoreRowModel,
  createFilteredRowModel,
  createSortedRowModel,
  globalFilteringFeature,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type ColumnDef,
  type RowData,
} from '@tanstack/react-table'
import { useVirtualizer, type Virtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, ChevronsUpDown, Columns3, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Card, EmptyState, Help } from '@/components/primitives'
import { cn } from '@/lib/utils'


const SEARCH_FROM = 12


const features = tableFeatures({
  rowSortingFeature,
  columnFilteringFeature,
  globalFilteringFeature,
  columnVisibilityFeature,
  coreRowModel: createCoreRowModel(),
  sortedRowModel: createSortedRowModel(),
  filteredRowModel: createFilteredRowModel(),
})

type Features = typeof features

export interface Col<T extends RowData> {

  key: string
  label: string

  unit?: string
  align?: 'left' | 'right'

  direction?: 'up' | 'down'

  help?: ReactNode

  group?: string

  optional?: boolean
  width?: number

  value?: (row: T) => string | number | null | undefined

  sortUndefined?: false | -1 | 1 | 'first' | 'last'
  render: (row: T) => ReactNode
}

export interface ServerTable {
  total: number
  page: number
  pageSize: number
  sort: { key: string; desc: boolean } | null
  onPage: (page: number) => void
  onSort: (sort: { key: string; desc: boolean } | null) => void
  onSearch: (q: string) => void
}

export interface DataTableProps<T extends RowData> {
  rows: T[]
  cols: Col<T>[]

  rowId: (row: T, index: number) => string
  onRowClick?: (row: T) => void

  initialSort?: { key: string; desc: boolean }
  searchPlaceholder?: string
  emptyWhat?: string
  emptyWhy?: string

  virtualizeOver?: number
  maxHeight?: number
  dense?: boolean
  pageSize?: number

  server?: ServerTable

  pinnedTop?: ReactNode
}

export function useServerTable(pageSize = 25) {
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null)
  const [q, setQ] = useState('')
  const qs = (extra?: Record<string, string>) => {
    const p = new URLSearchParams(extra)
    p.set('page', String(page))
    p.set('page_size', String(pageSize))
    if (sort) {
      p.set('sort', sort.key)
      p.set('desc', sort.desc ? 'true' : 'false')
    }
    if (q) p.set('q', q)
    return p.toString()
  }
  const bind = (total: number): ServerTable => ({
    total,
    page,
    pageSize,
    sort,
    onPage: setPage,
    onSort: (s) => {
      setSort(s)
      setPage(0)
    },
    onSearch: (v) => {
      setQ(v)
      setPage(0)
    },
  })
  return { qs, bind, deps: [page, sort?.key ?? '', sort?.desc ?? false, q] }
}

export function DataTable<T extends RowData>({
  rows,
  cols,
  rowId,
  onRowClick,
  initialSort,
  searchPlaceholder = 'search',
  emptyWhat = 'nothing to show',
  emptyWhy,
  virtualizeOver = 120,
  maxHeight = 620,
  dense = false,
  pageSize,
  server,
  pinnedTop,
}: DataTableProps<T>) {
  const [searchText, setSearchText] = useState('')
  const [globalFilter, setGlobalFilter] = useState('')
  const [page, setPage] = useState(0)

  useEffect(() => {
    const id = setTimeout(() => {
      if (server) server.onSearch(searchText)
      else setGlobalFilter(searchText)
    }, 250)
    return () => clearTimeout(id)
  }, [searchText])
  const [clientSorting, setClientSorting] = useState(
    initialSort ? [{ id: initialSort.key, desc: initialSort.desc }] : [],
  )
  const sorting = server
    ? server.sort
      ? [{ id: server.sort.key, desc: server.sort.desc }]
      : []
    : clientSorting
  const setSorting = (next: unknown) => {
    const resolved =
      typeof next === 'function'
        ? (next as (s: typeof clientSorting) => typeof clientSorting)(sorting)
        : (next as { id: string; desc: boolean }[])
    if (server) server.onSort(resolved.length ? { key: resolved[0].id, desc: resolved[0].desc } : null)
    else setClientSorting(resolved)
  }
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(cols.filter((c) => c.optional).map((c) => [c.key, false])),
  )

  const columns = useMemo<ColumnDef<Features, T, unknown>[]>(
    () =>
      cols.map((c) => ({
        id: c.key,
        header: c.label,
        accessorFn: (row: T) => {
          if (c.value) return c.value(row)
          const v = c.render(row)
          return typeof v === 'string' || typeof v === 'number' ? v : ''
        },
        cell: ({ row }) => c.render(row.original),
        enableSorting: true,
        enableGlobalFilter: true,
        sortUndefined: c.sortUndefined,
      })),
    [cols],
  )


  const table = useTable({
    features,
    columns: columns as never,
    data: rows as never,
    state: {
      sorting: server ? [] : sorting,
      globalFilter: server ? '' : globalFilter,
      columnVisibility,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
  })

  const visible = cols.filter((c) => columnVisibility[c.key] !== false)
  const modelRows = table.getRowModel().rows as unknown as { original: T }[]
  const effSize = server ? server.pageSize : pageSize
  const totalRows = server ? server.total : modelRows.length
  const pageCount = effSize ? Math.max(1, Math.ceil(totalRows / effSize)) : 1
  const cur = server ? Math.min(server.page, pageCount - 1) : Math.min(page, pageCount - 1)
  const from = effSize ? cur * effSize : 0
  const shownRows = server || !pageSize ? modelRows : modelRows.slice(from, from + pageSize)
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldVirtualize = !server && !pageSize && modelRows.length > virtualizeOver

  const virtualizer = useVirtualizer({
    count: shownRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => (dense ? 28 : 34),
    overscan: 12,
    enabled: shouldVirtualize,
  })

  const groups = useMemo(() => buildGroups(visible), [visible])
  const hasGroups = groups.some((g) => g.label)

  if (!rows.length && !server) return <EmptyState what={emptyWhat} why={emptyWhy} />
  if (!rows.length && server && !searchText && cur === 0)
    return <EmptyState what={emptyWhat} why={emptyWhy} />

  const pad = dense ? 'px-2.5 py-1' : 'px-3 py-1.5'


  const searchable = Boolean(server) || rows.length >= SEARCH_FROM
  const hasControls = searchable || cols.some((c) => c.optional)

  return (
    <div className="min-w-0">
      {hasControls && (
        <div className="mb-2 flex items-center gap-2">
          {searchable && (
            <label className="border-line bg-surface flex min-w-0 flex-1 items-center gap-1.5 rounded-md border px-2 py-1">
              <Search className="text-dim size-3.5 shrink-0" />
              <input
                value={searchText}
                onChange={(e) => {
                  setSearchText(e.target.value)
                  setPage(0)
                }}
                placeholder={searchPlaceholder}
                className="min-w-0 flex-1 bg-transparent text-xs outline-none"
              />
              {searchText && (
                <button
                  className="text-dim text-2xs"
                  onClick={() => {
                    setSearchText('')
                    if (server) server.onSearch('')
                    else setGlobalFilter('')
                  }}
                >
                  clear
                </button>
              )}
            </label>
          )}
          {searchable && (
            <span className="text-dim text-2xs whitespace-nowrap num">
              {server
                ? `${totalRows} rows`
                : modelRows.length === rows.length
                  ? `${rows.length} rows`
                  : `${modelRows.length} of ${rows.length} rows`}
            </span>
          )}
          {cols.some((c) => c.optional) && (
            <ColumnPicker cols={cols} visibility={columnVisibility} onChange={setColumnVisibility} />
          )}
        </div>
      )}

      <Card className="overflow-hidden">
        {pinnedTop}
        <div
          ref={scrollRef}
          className="tablewrap"
          style={{ maxHeight: shouldVirtualize ? maxHeight : undefined }}
        >
          <table className="w-full border-collapse text-xs">
            <thead>
              {hasGroups && (
                <tr className="border-line border-b">
                  {groups.map((g, i) => (
                    <th
                      key={i}
                      colSpan={g.span}
                      className={cn(
                        'text-dim text-2xs px-3 py-1 text-center font-medium uppercase tracking-wide',
                        i > 0 && 'border-line border-l',
                      )}
                    >
                      {g.label ?? ''}
                    </th>
                  ))}
                </tr>
              )}
              <tr className="border-line border-b">
                {visible.map((c) => {
                  const sorted = sorting.find((s) => s.id === c.key)
                  return (
                    <th
                      key={c.key}
                      style={{ width: c.width }}
                      className={cn(
                        'text-dim font-medium whitespace-nowrap',
                        pad,
                        c.align === 'right' ? 'text-right' : 'text-left',
                      )}
                    >
                      <span className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() =>
                            setSorting(
                              sorted?.desc
                                ? []
                                : [{ id: c.key, desc: sorted ? true : false }],
                            )
                          }
                          className="hover:text-fg inline-flex items-center gap-1"
                          title={`sort by ${c.label}`}
                        >
                          <span>{c.label}</span>
                          {c.direction === 'down' && (
                            <span title="lower is better" className="text-2xs">
                              ↓
                            </span>
                          )}
                          {c.unit && <span className="text-2xs opacity-70">({c.unit})</span>}
                          {sorted ? (
                            sorted.desc ? (
                              <ArrowDown className="size-3" />
                            ) : (
                              <ArrowUp className="size-3" />
                            )
                          ) : (
                            <ChevronsUpDown className="size-3 opacity-30" />
                          )}
                        </button>
                        {c.help && <Help>{c.help}</Help>}
                      </span>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {shouldVirtualize ? (
                <VirtualBody
                  virtualizer={virtualizer}
                  modelRows={shownRows}
                  visible={visible}
                  rowId={rowId}
                  onRowClick={onRowClick}
                  pad={pad}
                />
              ) : (
                shownRows.map((r, i) => (
                  <tr
                    key={rowId(r.original, i)}
                    onClick={onRowClick ? () => onRowClick(r.original) : undefined}
                    className={cn(
                      'border-line/60 border-b last:border-0',
                      onRowClick && 'hover:bg-raised cursor-pointer',
                    )}
                  >
                    {visible.map((c) => (
                      <td
                        key={c.key}
                        className={cn(
                          pad,
                          'align-top',
                          c.align === 'right' ? 'num text-right' : 'text-left',
                        )}
                      >
                        {c.render(r.original)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {effSize != null && pageCount > 1 && (
        <div className="text-dim text-2xs mt-2 flex items-center justify-between gap-2">
          <span className="num">
            {from + 1}-{from + shownRows.length} of {totalRows}
          </span>
          <span className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => (server ? server.onPage(cur - 1) : setPage(cur - 1))}
              disabled={cur === 0}
              className="border-line bg-surface hover:text-fg rounded-md border px-2 py-0.5 disabled:opacity-40"
            >
              prev
            </button>
            <span className="num">
              page {cur + 1} of {pageCount}
            </span>
            <button
              type="button"
              onClick={() => (server ? server.onPage(cur + 1) : setPage(cur + 1))}
              disabled={cur >= pageCount - 1}
              className="border-line bg-surface hover:text-fg rounded-md border px-2 py-0.5 disabled:opacity-40"
            >
              next
            </button>
          </span>
        </div>
      )}
    </div>
  )
}

function VirtualBody<T extends RowData>({
  virtualizer,
  modelRows,
  visible,
  rowId,
  onRowClick,
  pad,
}: {
  virtualizer: Virtualizer<HTMLDivElement, Element>
  modelRows: { original: T }[]
  visible: Col<T>[]
  rowId: (row: T, index: number) => string
  onRowClick?: (row: T) => void
  pad: string
}) {
  const items = virtualizer.getVirtualItems()
  const before = items.length ? items[0].start : 0
  const after = items.length ? virtualizer.getTotalSize() - items[items.length - 1].end : 0
  return (
    <>
      {before > 0 && (
        <tr>
          <td colSpan={visible.length} style={{ height: before }} />
        </tr>
      )}
      {items.map((v) => {
        const r = modelRows[v.index]
        if (!r) return null
        return (
          <tr
            key={rowId(r.original, v.index)}
            onClick={onRowClick ? () => onRowClick(r.original) : undefined}
            className={cn(
              'border-line/60 border-b',
              onRowClick && 'hover:bg-raised cursor-pointer',
            )}
          >
            {visible.map((c) => (
              <td
                key={c.key}
                className={cn(pad, 'align-top', c.align === 'right' ? 'num text-right' : 'text-left')}
              >
                {c.render(r.original)}
              </td>
            ))}
          </tr>
        )
      })}
      {after > 0 && (
        <tr>
          <td colSpan={visible.length} style={{ height: after }} />
        </tr>
      )}
    </>
  )
}


function buildGroups<T extends RowData>(cols: Col<T>[]): { label?: string; span: number }[] {
  const out: { label?: string; span: number }[] = []
  for (const c of cols) {
    const last = out[out.length - 1]
    if (last && last.label === c.group) last.span += 1
    else out.push({ label: c.group, span: 1 })
  }
  return out
}

function ColumnPicker<T extends RowData>({
  cols,
  visibility,
  onChange,
}: {
  cols: Col<T>[]
  visibility: Record<string, boolean>
  onChange: (v: Record<string, boolean>) => void
}) {
  const hidden = cols.filter((c) => visibility[c.key] === false).length
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button className="border-line bg-surface text-dim hover:text-fg flex items-center gap-1 rounded-md border px-2 py-1 text-2xs whitespace-nowrap">
          <Columns3 className="size-3.5" />
          columns{hidden ? ` (${hidden} hidden)` : ''}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          className="bg-surface border-line z-50 max-h-80 overflow-auto rounded-lg border p-2 shadow-lg"
        >
          <div className="text-dim text-2xs mb-1 px-1">
            hidden columns are still in the data — nothing is deleted
          </div>
          {cols.map((c) => (
            <label
              key={c.key}
              className="hover:bg-raised flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs"
            >
              <input
                type="checkbox"
                checked={visibility[c.key] !== false}
                onChange={(e) => onChange({ ...visibility, [c.key]: e.target.checked })}
              />
              <span>{c.label}</span>
              {c.group && <span className="text-dim text-2xs">{c.group}</span>}
            </label>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
