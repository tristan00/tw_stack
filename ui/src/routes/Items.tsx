import { useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { CatalogNav, TookCell } from '@/components/catalog'
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

export const itemCols = (resources: string[] = []): Col<ItemRow>[] => [
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
  { key: 'held', label: 'held in', align: 'right', value: (r) => r.held_in, render: (r) => <span className="num">{n(r.held_in)}</span> },
  {
    key: 'eq',
    label: 'worn',
    align: 'right',
    value: (r) => (r.held_in ? r.equipped_in / r.held_in : 0),
    render: (r) => <TookCell rate={{ n: r.equipped_in, of: r.held_in, noun: 'campaigns', population: 'that held it and wore it' }} />,
  },
  { key: 'bench', label: 'benched in', align: 'right', value: (r) => r.benched_in, render: (r) => (r.benched_in ? <span className="num">{n(r.benched_in)}</span> : <span className="text-dim">—</span>) },
  {
    key: 'req',
    label: 'avg reward',
    unit: 'worn',
    align: 'right',
    value: (r) => r.avg_reward_equipped ?? undefined,
    sortUndefined: 'last',
    render: (r) => (r.avg_reward_equipped == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_reward_equipped, 2)}</span>),
  },
  {
    key: 'rb',
    label: 'avg reward',
    unit: 'benched',
    align: 'right',
    value: (r) => r.avg_reward_benched ?? undefined,
    sortUndefined: 'last',
    render: (r) => (r.avg_reward_benched == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_reward_benched, 2)}</span>),
  },
  {
    key: 'delta',
    label: 'Δ reward',
    unit: 'worn − benched',
    align: 'right',
    help: 'average campaign reward when a campaign wore it, minus when a campaign held it but left it benched. Shown once both sides have 5+ campaigns.',
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
  const [params, setParams] = useSearchParams()
  const cat = params.get('cat') ?? ''
  const setCat = (v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set('cat', v)
    else next.delete('cat')
    setParams(next, { replace: true })
  }
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const rows = (data.rows ?? []).filter((r) => !cat || r.category === cat)
  return (
    <div>
      <CatalogNav active="/items" />
      <Section
        title="items"
        scope={{
          text: 'every item this run dir ever held, one row each',
          detail: 'worn vs benched compares campaigns that held the same item: worn = equipped at least once, benched = held in the pool and never worn',
        }}
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
      <DataTable rows={rows} cols={itemCols(data.resources ?? [])} rowId={(r) => r.key} searchPlaceholder="search item…" pageSize={25} emptyWhat="no item matches" />
      </Section>
    </div>
  )
}
