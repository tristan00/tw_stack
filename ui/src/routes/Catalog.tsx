import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { CatalogNav, TookCell, dashNum, signedNum } from '@/components/catalog'
import { Card, EntityLink, ErrorState, Help, Section, Skeleton } from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { useApi, type CatalogIndexPage, type CatalogIndexRow, type ChoicesPage, type ForkArmRow, type ForkRow } from '@/lib/api'
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
      { key: 'race', label: 'race', value: (r) => r.race ?? '', render: (r) => (r.race ? <span className="text-dim">{r.race}</span> : <span className="text-dim">—</span>) },
      { key: 'tier', label: 'tier', align: 'right', value: (r) => r.tier ?? 0, render: (r) => dashNum(r.tier) },
      { key: 'points', label: 'points', align: 'right', value: (r) => r.points ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.points) },
    )
  }
  if (family === 'skills') {
    cols.push(
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

type ArmRow = { cohort: number; arm: ForkArmRow }

const armCols = (family: Family): Col<ArmRow>[] => [
  {
    key: 'arm',
    label: family === 'buildings' ? 'built first' : 'picked first',
    value: (r) => r.arm.label,
    render: (r) =>
      r.arm.key == null ? (
        <span className="text-dim">didn’t continue</span>
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
    value: (r) => r.arm.n / Math.max(1, r.cohort),
    render: (r) => <span className="num">{Math.round((100 * r.arm.n) / Math.max(1, r.cohort))}%</span>,
  },
  { key: 'picked', label: 'picked', unit: 'avg turn', align: 'right', value: (r) => r.arm.avg_picked_turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.arm.avg_picked_turn, 1) },
  {
    key: 'reward',
    label: 'avg reward',
    unit: 'campaign',
    align: 'right',
    help: 'the whole campaign’s reward, averaged over this path',
    value: (r) => r.arm.avg_reward ?? undefined,
    sortUndefined: 'last',
    render: (r) => dashNum(r.arm.avg_reward, 2),
  },
  {
    key: 'future',
    label: 'avg future reward',
    align: 'right',
    help: 'gains made after the fork was reached — same anchor for every path',
    value: (r) => r.arm.avg_future ?? undefined,
    sortUndefined: 'last',
    render: (r) => dashNum(r.arm.avg_future, 2),
  },
  {
    key: 'delta',
    label: 'Δ future',
    unit: 'vs other paths',
    align: 'right',
    help: 'this path’s avg future reward minus every other path’s, didn’t-continue included',
    value: (r) => r.arm.delta_future ?? undefined,
    sortUndefined: 'last',
    render: (r) => signedNum(r.arm.delta_future),
  },
]

function topPick(fk: ForkRow): ForkArmRow | null {
  const picks = (fk.arms ?? []).filter((a) => a.key != null)
  return picks.length ? picks.reduce((a, b) => (b.n > a.n ? b : a)) : null
}

function bestDelta(fk: ForkRow): number | null {
  const ds = (fk.arms ?? []).filter((a) => a.key != null && a.delta_future != null)
  return ds.length ? Math.max(...ds.map((a) => a.delta_future!)) : null
}

const SHOW_STEP = 50

function ChoicesView({ family }: { family: Family }) {
  const { data, error, loading, reload } = useApi<ChoicesPage>(
    `/api/choices/${CHOICE_FAMILY[family]}`,
    [family],
    { live: false },
  )
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [q, setQ] = useState('')
  const [shown, setShown] = useState(SHOW_STEP)
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const needle = q.toLowerCase()
  const forks = (data.forks ?? []).filter(
    (fk) =>
      !needle ||
      fk.label.toLowerCase().includes(needle) ||
      (fk.race ?? '').toLowerCase().includes(needle) ||
      (fk.starts ?? '').toLowerCase().includes(needle) ||
      (fk.arms ?? []).some((a) => a.label.toLowerCase().includes(needle)),
  )
  const visible = forks.slice(0, shown)
  const toggle = (key: string) => {
    const next = new Set(open)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setOpen(next)
  }
  const cols = armCols(family)
  return (
    <Section
      title={`${FAMILY[family].noun} choices`}
      scope={{
        text: `${data.scope.text} · ${n((data.forks ?? []).length)} forks`,
      }}
    >
      <div className="mb-2 flex items-center gap-2">
        <label className="border-line bg-surface flex min-w-0 flex-1 items-center gap-1.5 rounded-md border px-2 py-1">
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setShown(SHOW_STEP)
            }}
            placeholder="search fork, path…"
            className="min-w-0 flex-1 bg-transparent text-xs outline-none"
          />
        </label>
        <span className="text-dim text-2xs whitespace-nowrap num">{forks.length} forks</span>
      </div>
      <Card className="overflow-hidden">
        <div className="tablewrap">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-line text-dim border-b">
                <th className="px-3 py-1.5 text-left font-medium">{family === 'buildings' ? 'settlement' : 'fork'}</th>
                <th className="px-3 py-1.5 text-left font-medium">race</th>
                <th className="px-3 py-1.5 text-right font-medium">paths</th>
                <th className="px-3 py-1.5 text-right font-medium">campaigns</th>
                <th className="px-3 py-1.5 text-left font-medium">most picked</th>
                <th className="px-3 py-1.5 text-right font-medium">
                  <span className="inline-flex items-center gap-1">
                    best Δ future
                    <Help>the strongest path’s avg future reward minus the rest — expand for every path</Help>
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((fk) => {
                const isOpen = open.has(fk.fork)
                const top = topPick(fk)
                const picks = (fk.arms ?? []).filter((a) => a.key != null).length
                return [
                  <tr
                    key={fk.fork}
                    onClick={() => toggle(fk.fork)}
                    className="border-line/60 hover:bg-raised cursor-pointer border-b"
                  >
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        <span className="text-dim text-2xs">{isOpen ? '▾' : '▸'}</span>
                        {family === 'buildings' ? (
                          <span>{fk.label}</span>
                        ) : (
                          <span onClick={(e) => e.stopPropagation()}>
                            <EntityLink
                              to={`/${family}/${encodeURIComponent(fk.fork.split('@')[0])}`}
                              title={fk.fork}
                            >
                              {fk.label}
                            </EntityLink>
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="text-dim px-3 py-1.5">{fk.race ?? '—'}</td>
                    <td className="num px-3 py-1.5 text-right">{picks}</td>
                    <td className="num px-3 py-1.5 text-right">{n(fk.cohort)}</td>
                    <td className="px-3 py-1.5">
                      {top ? (
                        <span>
                          {top.label}{' '}
                          <span className="text-dim num text-2xs">
                            {Math.round((100 * top.n) / Math.max(1, fk.cohort))}%
                          </span>
                        </span>
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                    <td className="num px-3 py-1.5 text-right">{signedNum(bestDelta(fk))}</td>
                  </tr>,
                  isOpen ? (
                    <tr key={`${fk.fork}#arms`} className="border-line/60 border-b">
                      <td colSpan={6} className="bg-raised/40 px-3 py-2">
                        <DataTable
                          rows={(fk.arms ?? []).map((arm) => ({ cohort: fk.cohort, arm }))}
                          cols={cols}
                          rowId={(r) => r.arm.key ?? 'neither'}
                          dense
                          emptyWhat="no path recorded"
                        />
                      </td>
                    </tr>
                  ) : null,
                ]
              })}
            </tbody>
          </table>
        </div>
      </Card>
      {forks.length > shown && (
        <div className="mt-2 flex justify-center">
          <button
            onClick={() => setShown(shown + SHOW_STEP)}
            className="border-line bg-surface text-dim hover:text-fg rounded-md border px-3 py-1 text-2xs"
          >
            show {Math.min(SHOW_STEP, forks.length - shown)} more of {forks.length}
          </button>
        </div>
      )}
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
