import { useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { CatalogNav, TookCell } from '@/components/catalog'
import { Bar, EntityLink, ErrorState, Section, Skeleton } from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { useApi, type ItemRow, type ItemsPage, type ItemSwapRow, type SwapsPage } from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

const TABS = [
  { key: 'items', label: 'index', asks: 'does wearing an item pay' },
  { key: 'swaps', label: 'kept swaps', asks: 'which kept item swaps happen, and how those campaigns go' },
]

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
    help: 'avg campaign reward, worn − benched · needs 5+5 campaigns',
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

const swapCols: Col<ItemSwapRow>[] = [
  {
    key: 'removed',
    label: 'took off',
    value: (r) => r.removed.label,
    render: (r) => (
      <EntityLink to={`/items/${encodeURIComponent(r.removed.raw)}`} title={r.removed.raw}>
        {r.removed.label}
      </EntityLink>
    ),
  },
  {
    key: 'equipped',
    label: 'put on instead',
    value: (r) => r.equipped.label,
    render: (r) => (
      <EntityLink to={`/items/${encodeURIComponent(r.equipped.raw)}`} title={r.equipped.raw}>
        {r.equipped.label}
      </EntityLink>
    ),
  },
  { key: 'cat', label: 'category', value: (r) => r.category ?? '', render: (r) => <span className="text-dim">{r.category ?? '—'}</span> },
  { key: 'n', label: 'campaigns', align: 'right', value: (r) => r.campaigns, render: (r) => <span className="num">{n(r.campaigns)}</span> },
  { key: 'events', label: 'swaps', align: 'right', optional: true, value: (r) => r.events, render: (r) => <span className="num">{n(r.events)}</span> },
  { key: 'turn', label: 'avg swap turn', align: 'right', value: (r) => r.avg_turn ?? undefined, sortUndefined: 'last', render: (r) => (r.avg_turn == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_turn, 1)}</span>) },
  { key: 'gap', label: 'avg gap', unit: 'turns', align: 'right', optional: true, value: (r) => r.avg_gap ?? undefined, sortUndefined: 'last', render: (r) => (r.avg_gap == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_gap, 1)}</span>) },
  { key: 'kept', label: 'kept to end', value: (r) => (r.kept_rate?.of ? r.kept_rate.n / r.kept_rate.of : -1), render: (r) => <Bar rate={r.kept_rate ?? null} /> },
  { key: 'kept_turns', label: 'avg kept', unit: 'turns', align: 'right', optional: true, value: (r) => r.avg_kept_turns ?? undefined, sortUndefined: 'last', render: (r) => (r.avg_kept_turns == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_kept_turns, 1)}</span>) },
  { key: 'reward', label: 'avg reward', align: 'right', value: (r) => r.avg_reward ?? undefined, sortUndefined: 'last', render: (r) => (r.avg_reward == null ? <span className="text-dim">—</span> : <span className="num">{n(r.avg_reward, 2)}</span>) },
  {
    key: 'delta',
    label: 'Δ reward',
    unit: 'vs start mean',
    align: 'right',
    help: 'mean of (campaign reward − its start’s mean) over the swapping campaigns',
    value: (r) => r.delta_mean ?? undefined,
    sortUndefined: 'last',
    render: (r) => itemDelta(r.delta_mean),
  },
]

function ItemsIndex({ data }: { data: ItemsPage }) {
  const [params, setParams] = useSearchParams()
  const cat = params.get('cat') ?? ''
  const setCat = (v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set('cat', v)
    else next.delete('cat')
    setParams(next, { replace: true })
  }
  const rows = (data.rows ?? []).filter((r) => !cat || r.category === cat)
  return (
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
  )
}

function SwapsView() {
  const { data, error, loading, reload } = useApi<SwapsPage>('/api/items/swaps', [], { live: false })
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  return (
    <Section
      title="kept swaps"
      scope={{
        text: `${data.scope.text} · ${n(data.events)} swap${data.events === 1 ? '' : 's'} across ${n((data.rows ?? []).length)} pairs`,
      }}
    >
      <DataTable
        rows={data.rows ?? []}
        cols={swapCols}
        rowId={(r) => `${r.removed.raw}>${r.equipped.raw}`}
        searchPlaceholder="search swap…"
        pageSize={25}
        emptyWhat="no character ever swapped one item for another"
      />
    </Section>
  )
}

export function Items() {
  const { data, error, loading, reload } = useApi<ItemsPage>('/api/items', [], { live: false })
  const tab = useSubView(TABS, 'tab')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <div>
      <CatalogNav active="/items" />
      <SubNav views={TABS} param="tab" />
      {tab === 'items' && <ItemsIndex data={data} />}
      {tab === 'swaps' && <SwapsView />}
    </div>
  )
}
