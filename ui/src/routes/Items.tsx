import { useState } from 'react'
import { DataTable, type Col } from '@/components/DataTable'
import { EntityLink, ErrorState, Section, Skeleton } from '@/components/primitives'
import { useApi, type ItemRow, type ItemsPage } from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

export const itemDelta = (v: number | null | undefined) =>
  v == null ? (
    <span className="text-dim">—</span>
  ) : (
    <span className={cn('num', v > 0 ? 'text-ok' : v < 0 ? 'text-bad' : 'text-dim')}>
      {v > 0 ? '+' : ''}
      {n(v, 2)}
    </span>
  )

export const itemCols = (withEffects: boolean, resources: string[] = []): Col<ItemRow>[] => [
  {
    key: 'item',
    label: 'item',
    value: (r) => r.label ?? r.key,
    render: (r) => (
      <EntityLink to={`/items/${encodeURIComponent(r.key)}`} title={r.key}>
        {r.label ?? r.key}
      </EntityLink>
    ),
  },
  { key: 'key', label: 'key', optional: true, value: (r) => r.key, render: (r) => <span className="num text-dim text-2xs">{r.key}</span> },
  { key: 'cat', label: 'category', value: (r) => r.category ?? '', render: (r) => <span className="text-dim">{r.category ?? '—'}</span> },
  ...(withEffects
    ? [
        {
          key: 'fx',
          label: 'effects',
          value: (r: ItemRow) => r.effects ?? '',
          render: (r: ItemRow) => <span className="text-dim text-2xs">{r.effects ?? '—'}</span>,
        } as Col<ItemRow>,
      ]
    : []),
  { key: 'held', label: 'held in', align: 'right', value: (r) => r.held_in, render: (r) => <span className="num">{n(r.held_in)}</span> },
  { key: 'eq', label: 'equipped in', align: 'right', value: (r) => r.equipped_in, render: (r) => <span className="num">{n(r.equipped_in)}</span> },
  {
    key: 'req',
    label: 'avg reward equipped',
    align: 'right',
    value: (r) => r.avg_reward_equipped ?? undefined,
    sortUndefined: 'last',
    render: (r) => (r.avg_reward_equipped == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_reward_equipped, 2)}</span>),
  },
  {
    key: 'rb',
    label: 'avg reward benched',
    align: 'right',
    value: (r) => r.avg_reward_benched ?? undefined,
    sortUndefined: 'last',
    render: (r) => (r.avg_reward_benched == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_reward_benched, 2)}</span>),
  },
  {
    key: 'delta',
    label: 'Δ equipped − benched',
    align: 'right',
    value: (r) => r.delta ?? undefined,
    sortUndefined: 'last',
    render: (r) => itemDelta(r.delta),
  },
  ...resources.map(
    (name): Col<ItemRow> => ({
      key: `res:${name}`,
      label: name,
      align: 'right',
      optional: true,
      value: (r) => r.resources?.[name] ?? undefined,
      sortUndefined: 'last',
      render: (r) => {
        const v = r.resources?.[name]
        return v == null ? (
          <span className="text-dim">—</span>
        ) : (
          <span className="num">
            {v > 0 ? '+' : ''}
            {n(v, Number.isInteger(v) ? 0 : 1)}
          </span>
        )
      },
    }),
  ),
]

export function Items() {
  const { data, error, loading, reload } = useApi<ItemsPage>('/api/items', [], { live: false })
  const [cat, setCat] = useState('')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const rows = (data.rows ?? []).filter((r) => !cat || r.category === cat)
  return (
    <Section
      title="items"
      scope={{ text: 'every item this run dir ever held, one row each · equipped vs benched compares campaigns that held the same item' }}
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-2xs">
        <button
          onClick={() => setCat('')}
          className={cn('rounded-full px-2 py-0.5', !cat ? 'bg-raised text-fg font-semibold' : 'bg-surface border-line border hover:text-fg')}
        >
          all
        </button>
        {(data.categories ?? []).map((c) => (
          <button
            key={c}
            onClick={() => setCat(cat === c ? '' : c)}
            className={cn('rounded-full px-2 py-0.5', cat === c ? 'bg-raised text-fg font-semibold' : 'bg-surface border-line border hover:text-fg')}
          >
            {c}
          </button>
        ))}
        <span className="text-dim num ml-auto">{rows.length} of {n(data.total)}</span>
      </div>
      <DataTable rows={rows} cols={itemCols(true, data.resources ?? [])} rowId={(r) => r.key} searchPlaceholder="search item…" pageSize={25} emptyWhat="no item matches" />
    </Section>
  )
}
