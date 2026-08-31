import { useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { CatalogNav, TookCell, dashNum, signedNum } from '@/components/catalog'
import { EntityLink, ErrorState, Section, Skeleton } from '@/components/primitives'
import { useApi, type CatalogIndexPage, type CatalogIndexRow } from '@/lib/api'
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

export function Catalog({ family }: { family: Family }) {
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
      <CatalogNav active={`/${family}`} />
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
