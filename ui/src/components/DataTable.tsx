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
import { useMemo, useRef, useState, type ReactNode } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Card, EmptyState, Help } from '@/components/primitives'
import { cn } from '@/lib/utils'

/**
 * One table component, used by every table in the app.
 *
 * A column is DECLARED, not hand-written: its label, unit, alignment, direction and
 * caveat travel with it, so the same column looks and behaves the same everywhere and
 * there is one place to fix it. The previous dashboard hand-wrote every `<tr><th>` at
 * its call site, which is why 27 columns could disagree about alignment and why "power
 * rank counts downwards" had to be explained in a paragraph on each page that showed it.
 *
 * Three rules the component enforces rather than documents:
 *
 *   `unit` and `direction` live in the HEADER. A column of milliseconds says so once, at
 *   the top, instead of repeating "ms" in every cell or omitting it entirely.
 *
 *   `help` renders a visible affordance, never a bare tooltip. A caveat reachable only by
 *   hover is invisible on a glanceable dashboard and absent on touch.
 *
 *   Long tables virtualize. Rendering 500 rows x 27 columns into the DOM is what made
 *   the old fragments 147 KB and made every refresh a full re-layout.
 */

// Below this many rows a search box is noise -- every row is already on screen.
const SEARCH_FROM = 12

// In v9 one object registers the feature modules AND the row-model factories they need.
// Sorting, filtering and visibility are opt-in, so the bundle carries only what this app
// actually uses.
//
// globalFilteringFeature is built on columnFilteringFeature and the library encodes that
// dependency in the type -- omitting it is a compile error rather than a search box that
// silently does nothing.
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
  /** Stable id; also the sort key. */
  key: string
  label: string
  /** Rendered small and dim in the header, after the label. */
  unit?: string
  align?: 'left' | 'right'
  /** `down` renders ↓, meaning a lower value is better. */
  direction?: 'up' | 'down'
  /** A caveat, shown behind a visible ? in the header. */
  help?: ReactNode
  /** Column group label; adjacent columns sharing one are bracketed together. */
  group?: string
  /** Hidden until the reader turns it on. Density without deletion. */
  optional?: boolean
  width?: number
  /** The value used for sorting and searching. Defaults to the rendered text. */
  value?: (row: T) => string | number | null | undefined
  /**
   * Where rows with no value sort. Return `undefined` from `value` to mean "no value".
   *
   * This exists because the campaign growth column mapped missing data to 0 and sorted
   * 117 blank rows into the middle of the genuinely-flat ones -- and, when only one
   * endpoint was present, ranked a row with no delta at all as the biggest grower on the
   * page. "No data" and "no change" are different facts and must not sort as one.
   */
  sortUndefined?: false | -1 | 1 | 'first' | 'last'
  render: (row: T) => ReactNode
}

export interface DataTableProps<T extends RowData> {
  rows: T[]
  cols: Col<T>[]
  /** Row identity, so React and the virtualizer keep rows stable across refreshes. */
  rowId: (row: T, index: number) => string
  onRowClick?: (row: T) => void
  /** Sort key applied on first render. */
  initialSort?: { key: string; desc: boolean }
  searchPlaceholder?: string
  emptyWhat?: string
  emptyWhy?: string
  /** Above this many rows the body virtualizes. */
  virtualizeOver?: number
  maxHeight?: number
  dense?: boolean
  /** Rendered above the header row and never scrolled away -- e.g. a totals row. */
  pinnedTop?: ReactNode
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
  pinnedTop,
}: DataTableProps<T>) {
  const [globalFilter, setGlobalFilter] = useState('')
  const [sorting, setSorting] = useState(
    initialSort ? [{ id: initialSort.key, desc: initialSort.desc }] : [],
  )
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

  // The two casts below are the entire cost of wrapping a generic component around
  // useTable. Inside a generic function T is opaque, so the library cannot prove that
  // ColumnDef<Features, T> is assignable to its own ColumnDef<TableFeatures, RowData>,
  // even though `columns` was authored against the precise type on the line above --
  // which is where the real checking happens and where a wrong field is caught.
  // Confining the widening to this one call keeps every caller of DataTable fully typed.
  const table = useTable({
    features,
    columns: columns as never,
    data: rows as never,
    state: { sorting, globalFilter, columnVisibility },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
  })

  const visible = cols.filter((c) => columnVisibility[c.key] !== false)
  const modelRows = table.getRowModel().rows as unknown as { original: T }[]
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldVirtualize = modelRows.length > virtualizeOver

  const virtualizer = useVirtualizer({
    count: modelRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => (dense ? 28 : 34),
    overscan: 12,
    enabled: shouldVirtualize,
  })

  const groups = useMemo(() => buildGroups(visible), [visible])
  const hasGroups = groups.some((g) => g.label)

  if (!rows.length) return <EmptyState what={emptyWhat} why={emptyWhy} />

  const pad = dense ? 'px-2.5 py-1' : 'px-3 py-1.5'

  // A search box over eight rows is chrome, not a feature: you can already see all of
  // them. It appears once the table is long enough that finding a row is a real task.
  const searchable = rows.length >= SEARCH_FROM
  const hasControls = searchable || cols.some((c) => c.optional)

  return (
    <div className="min-w-0">
      {hasControls && (
        <div className="mb-2 flex items-center gap-2">
          {searchable && (
            <label className="border-line bg-surface flex min-w-0 flex-1 items-center gap-1.5 rounded-md border px-2 py-1">
              <Search className="text-dim size-3.5 shrink-0" />
              <input
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                placeholder={searchPlaceholder}
                className="min-w-0 flex-1 bg-transparent text-xs outline-none"
              />
              {globalFilter && (
                <button className="text-dim text-2xs" onClick={() => setGlobalFilter('')}>
                  clear
                </button>
              )}
            </label>
          )}
          {searchable && (
            <span className="text-dim text-2xs whitespace-nowrap num">
              {modelRows.length === rows.length
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
                  modelRows={modelRows}
                  visible={visible}
                  rowId={rowId}
                  onRowClick={onRowClick}
                  pad={pad}
                />
              ) : (
                modelRows.map((r, i) => (
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

/**
 * Column groups, derived from adjacent columns sharing a `group`.
 *
 * Derived rather than declared separately, because a group row whose spans do not sum to
 * the column count silently shifts every vertical rule and labels the wrong columns --
 * a defect the previous dashboard needed a dedicated linter rule to catch. Here the sum
 * is correct by construction.
 */
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
