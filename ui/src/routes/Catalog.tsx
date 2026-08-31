import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { CatalogNav, TookCell, dashNum, signedNum } from '@/components/catalog'
import { EntityLink, ErrorState, Section, Skeleton } from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { useApi, type CatalogIndexPage, type CatalogIndexRow, type ChoicesPage, type ForkArmRow } from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

type Family = 'buildings' | 'research' | 'skills'

const FAMILY: Record<
  Family,
  { title: string; noun: string; verb: string; filterOn: 'category' | 'line' | null }
> = {
  buildings: { title: 'buildings', noun: 'building', verb: 'constructed', filterOn: 'category' },
  research: { title: 'research', noun: 'tech', verb: 'started', filterOn: 'line' },
  skills: { title: 'skills', noun: 'skill', verb: 'ranked', filterOn: null },
}

function famCols(family: Family): Col<CatalogIndexRow>[] {
  const f = FAMILY[family]
  const cols: Col<CatalogIndexRow>[] = [
    {
      key: 'name',
      label: f.noun,
      value: (r) => r.label ?? r.key,
      render: (r) => (
        <EntityLink to={`/${family}/${encodeURIComponent(r.key)}`} title={r.key}>
          {r.label ?? r.key}
        </EntityLink>
      ),
    },
    { key: 'key', label: 'key', optional: true, value: (r) => r.key, render: (r) => <span className="num text-dim text-2xs">{r.key}</span> },
  ]
  if (family === 'buildings') {
    cols.push(
      { key: 'category', label: 'category', value: (r) => r.category ?? '', render: (r) => <span className="text-dim">{r.category ?? '—'}</span> },
      { key: 'level', label: 'level', align: 'right', value: (r) => r.level ?? 0, render: (r) => dashNum(r.level) },
      { key: 'cost', label: 'cost', align: 'right', value: (r) => r.cost ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.cost) },
    )
  }
  if (family === 'research') {
    cols.push(
      { key: 'tier', label: 'tier', align: 'right', value: (r) => r.tier ?? 0, render: (r) => dashNum(r.tier) },
      { key: 'points', label: 'points', align: 'right', value: (r) => r.points ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.points) },
    )
  }
  if (family === 'skills') {
    cols.push(
      {
        key: 'characters',
        label: 'most ranked by',
        value: (r) => r.characters ?? '',
        render: (r) => <span className="text-dim">{r.characters ?? '—'}</span>,
      },
      { key: 'unlock', label: 'unlocks at', unit: 'rank', align: 'right', optional: true, value: (r) => r.unlock_rank ?? 0, render: (r) => dashNum(r.unlock_rank) },
      { key: 'ranks', label: 'avg ranks', align: 'right', value: (r) => r.avg_ranks ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_ranks, 1) },
    )
  }
  cols.push(
    { key: 'took', label: f.verb, align: 'right', value: (r) => r.took?.n ?? 0, render: (r) => <TookCell rate={r.took} /> },
    { key: 'starts', label: 'starts', align: 'right', value: (r) => r.starts, render: (r) => dashNum(r.starts) },
    { key: 'turn', label: 'avg turn', unit: 'first', align: 'right', value: (r) => r.avg_turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_turn, 1) },
    { key: 'rt', label: 'avg reward', unit: 'took', align: 'right', optional: true, value: (r) => r.avg_reward_took ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_reward_took, 2) },
    { key: 'rp', label: 'avg reward', unit: 'passed', align: 'right', optional: true, value: (r) => r.avg_reward_passed ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_reward_passed, 2) },
    {
      key: 'delta',
      label: 'Δ reward',
      unit: 'took − passed',
      align: 'right',
      help: `avg campaign reward, ${f.verb} − passed on the offer · pooled across starts · needs 5+5 campaigns`,
      value: (r) => r.delta ?? undefined,
      sortUndefined: 'last',
      render: (r) => signedNum(r.delta),
    },
  )
  return cols
}

const FAM_COLS: Record<Family, Col<CatalogIndexRow>[]> = {
  buildings: famCols('buildings'),
  research: famCols('research'),
  skills: famCols('skills'),
}

const CHOICE_FAMILY: Record<Family, string> = {
  buildings: 'building',
  research: 'research',
  skills: 'skills',
}

type ChoiceRow = { fork: string; forkLabel: string; cohort: number; arm: ForkArmRow }

const choiceCols = (family: Family): Col<ChoiceRow>[] => [
  {
    key: 'fork',
    label: family === 'buildings' ? 'settlement' : 'fork',
    value: (r) => r.forkLabel,
    render: (r) =>
      family !== 'buildings' && !r.fork.startsWith('root:') ? (
        <EntityLink to={`/${family}/${encodeURIComponent(r.fork)}`} title={r.fork}>
          {r.forkLabel}
        </EntityLink>
      ) : (
        <span>{r.forkLabel}</span>
      ),
  },
  {
    key: 'arm',
    label: family === 'buildings' ? 'first commitment' : 'picked first',
    value: (r) => r.arm.label,
    render: (r) =>
      r.arm.key == null ? (
        <span className="text-dim">neither</span>
      ) : family === 'buildings' ? (
        <span>{r.arm.label}</span>
      ) : (
        <EntityLink to={`/${family}/${encodeURIComponent(r.arm.key)}`} title={r.arm.key}>
          {r.arm.label}
        </EntityLink>
      ),
  },
  { key: 'n', label: 'campaigns', align: 'right', value: (r) => r.arm.n, render: (r) => <span className="num">{n(r.arm.n)}</span> },
  {
    key: 'share',
    label: 'share',
    unit: 'of cohort',
    align: 'right',
    help: 'this arm’s campaigns over every campaign that reached the fork',
    value: (r) => r.arm.n / Math.max(1, r.cohort),
    render: (r) => <span className="num">{Math.round((100 * r.arm.n) / Math.max(1, r.cohort))}%</span>,
  },
  { key: 'reached', label: 'reached', unit: 'avg turn', align: 'right', optional: true, value: (r) => r.arm.avg_reached_turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.arm.avg_reached_turn, 1) },
  { key: 'picked', label: 'picked', unit: 'avg turn', align: 'right', value: (r) => r.arm.avg_picked_turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.arm.avg_picked_turn, 1) },
  {
    key: 'reward',
    label: 'avg reward',
    unit: 'campaign',
    align: 'right',
    help: 'the whole campaign’s analytics reward, averaged over this arm',
    value: (r) => r.arm.avg_reward ?? undefined,
    sortUndefined: 'last',
    render: (r) => dashNum(r.arm.avg_reward, 2),
  },
  {
    key: 'future',
    label: 'avg future reward',
    align: 'right',
    help: 'gains made after the fork was reached — peak minus state at the fork, analytics weights, same anchor for every arm of a fork',
    value: (r) => r.arm.avg_future ?? undefined,
    sortUndefined: 'last',
    render: (r) => dashNum(r.arm.avg_future, 2),
  },
]

function ChoicesView({ family }: { family: Family }) {
  const [censor, setCensor] = useState('3')
  const [minN, setMinN] = useState('20')
  const { data, error, loading, reload } = useApi<ChoicesPage>(
    `/api/choices/${CHOICE_FAMILY[family]}?censor=${censor}&min_n=${minN}`,
    [family, censor, minN],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const rows: ChoiceRow[] = (data.forks ?? []).flatMap((fk) =>
    (fk.arms ?? []).map((arm) => ({ fork: fk.fork, forkLabel: fk.label, cohort: fk.cohort, arm })),
  )
  return (
    <Section
      title={`${FAMILY[family].noun} choices`}
      scope={{
        text: `${data.scope.text} · ${n((data.forks ?? []).length)} forks`,
        detail: data.scope.detail ?? undefined,
      }}
    >
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1.5">
          <span className="text-dim">neither needs</span>
          <select value={censor} onChange={(e) => setCensor(e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1">
            <option value="0">any survival</option>
            <option value="3">3+ turns past the fork</option>
            <option value="5">5+ turns past the fork</option>
            <option value="10">10+ turns past the fork</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="text-dim">cohort at least</span>
          <select value={minN} onChange={(e) => setMinN(e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1">
            <option value="0">anything</option>
            <option value="10">10 campaigns</option>
            <option value="20">20 campaigns</option>
            <option value="50">50 campaigns</option>
          </select>
        </label>
      </div>
      <DataTable
        rows={rows}
        cols={choiceCols(family)}
        rowId={(r) => `${r.fork}>${r.arm.key ?? 'neither'}`}
        searchPlaceholder="search fork, choice…"
        pageSize={25}
        emptyWhat="no fork clears these knobs"
      />
    </Section>
  )
}

const CATALOG_TABS = [
  { key: 'all', label: 'index', asks: 'every key, take rates and reward deltas' },
  { key: 'choices', label: 'choices', asks: 'at each fork, what did picking one path over the others go on to gain' },
]

export function Catalog({ family }: { family: Family }) {
  const tab = useSubView(CATALOG_TABS, 'tab')
  return (
    <div>
      <CatalogNav active={`/${family}`} />
      <SubNav views={CATALOG_TABS} param="tab" />
      {tab === 'all' && <CatalogIndex family={family} />}
      {tab === 'choices' && <ChoicesView family={family} />}
    </div>
  )
}

function CatalogIndex({ family }: { family: Family }) {
  const { data, error, loading, reload } = useApi<CatalogIndexPage>(`/api/${family}`, [family], { live: false })
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
  const f = FAMILY[family]
  const rows = (data.rows ?? []).filter(
    (r) => !cat || (f.filterOn === 'category' ? r.category === cat : r.line === cat),
  )
  return (
    <div>
      <Section
        title={f.title}
        scope={{
          text: `${data.scope.text} · ${n(data.campaigns)} campaigns`,
          detail: data.scope.detail ?? undefined,
        }}
      >
        {(data.categories ?? []).length > 0 && (
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
        )}
        <DataTable
          rows={rows}
          cols={FAM_COLS[family]}
          rowId={(r) => r.key}
          searchPlaceholder={`search ${f.noun}…`}
          pageSize={25}
          emptyWhat={`no ${f.noun} matches`}
        />
      </Section>
    </div>
  )
}
