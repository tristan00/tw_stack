import { useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { Chip, EntityLink, ErrorState, Section, Skeleton } from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { mapShort } from '@/components/startcharts'
import { MapCell, lordOf, startId, startUrl } from '@/routes/Campaigns'
import { useApi, type UcbPick, type UcbPickPage, type UcbPicksPage, type UcbRow, type WindowEdgeRow } from '@/lib/api'
import { clock, n } from '@/lib/format'

const VIEWS = [
  { key: 'picks', label: 'picks', asks: 'why the selector played this start' },
  { key: 'window', label: 'window', asks: 'which plays are aging out of the lookback window' },
]

const inf = (v: number | null | undefined, digits = 3) => (v == null ? '∞' : n(v, digits))
const dash = (v: number | null | undefined, digits = 0) => (v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>)
const signed = (v: number | null | undefined, digits = 3) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{v >= 0 ? '+' : ''}{n(v, digits)}</span>

const pickCols: Col<UcbPick>[] = [
  { key: 'pick', label: 'pick', align: 'right', value: (r) => r.pick_id, render: (r) => <span className="num">{r.pick_id}</span> },
  { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => <EntityLink to={startUrl(startId(r))} title={r.faction.raw}>{lordOf(r)}</EntityLink> },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'c', label: 'C', align: 'right', value: (r) => r.c ?? 0, render: (r) => n(r.c, 2) },
  { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'mean', label: 'mean', group: 'blend', align: 'right', value: (r) => r.mean ?? 0, render: (r) => dash(r.mean, 2) },
  { key: 'entropy', label: 'H', group: 'blend', align: 'right', value: (r) => r.entropy ?? undefined, sortUndefined: 'last', render: (r) => dash(r.entropy, 2) },
  { key: 'std', label: 'std', group: 'blend', align: 'right', value: (r) => r.std ?? undefined, sortUndefined: 'last', render: (r) => dash(r.std, 2) },
  { key: 'blend', label: 'blend', group: 'winning score', align: 'right', value: (r) => r.blend ?? undefined, sortUndefined: 'last', render: (r) => dash(r.blend, 3) },
  { key: 'explore', label: 'explore', group: 'winning score', align: 'right', value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{inf(r.explore)}</span> },
  { key: 'adjust', label: 'adj', group: 'winning score', align: 'right', value: (r) => r.adjust ?? 0, render: (r) => (r.adjust ? signed(r.adjust, 1) : <span className="text-dim">—</span>) },
  { key: 'score', label: 'score', group: 'winning score', align: 'right', value: (r) => r.score ?? Number.MAX_SAFE_INTEGER, render: (r) => <strong className="num">{inf(r.score)}</strong> },
  { key: 'margin', label: 'margin', unit: 'to #2', group: 'winning score', align: 'right', value: (r) => r.margin ?? undefined, sortUndefined: 'last', render: (r) => dash(r.margin, 3) },
  { key: 'tied', label: 'tied', align: 'right', value: (r) => r.tied, render: (r) => (r.tied > 1 ? <Chip state="warn">{n(r.tied)}</Chip> : <span className="num">{r.tied}</span>) },
  { key: 'repeat', label: 'repeat', optional: true, value: (r) => (r.repeat ? 1 : 0), render: (r) => (r.repeat ? <Chip state="neutral">same as previous</Chip> : <span className="text-dim">—</span>) },
  { key: 'plays', label: 'plays', unit: 'window', optional: true, align: 'right', value: (r) => r.total_plays, render: (r) => n(r.total_plays) },
  { key: 'ranked', label: 'ranked', optional: true, align: 'right', value: (r) => r.starts, render: (r) => n(r.starts) },
  { key: 'distinct', label: 'distinct', unit: 'last 50', optional: true, align: 'right', value: (r) => r.distinct_50, render: (r) => n(r.distinct_50) },
  { key: 'gini', label: 'gini of n', optional: true, align: 'right', value: (r) => r.gini ?? undefined, sortUndefined: 'last', render: (r) => dash(r.gini, 3) },
]

const rankCols: Col<UcbRow>[] = [
  { key: 'rank', label: '#', align: 'right', value: (r) => r.rank, render: (r) => (r.chosen ? <strong className="num">{r.rank}</strong> : <span className="num">{r.rank}</span>) },
  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => <EntityLink to={startUrl(startId(r))} title={r.faction.raw}>{lordOf(r)}</EntityLink> },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'mean', label: 'mean', align: 'right', value: (r) => r.mean ?? 0, render: (r) => dash(r.mean, 2) },
  { key: 'entropy', label: 'H', align: 'right', value: (r) => r.entropy ?? undefined, sortUndefined: 'last', render: (r) => dash(r.entropy, 2) },
  { key: 'std', label: 'std', align: 'right', value: (r) => r.std ?? undefined, sortUndefined: 'last', render: (r) => dash(r.std, 2) },
  { key: 'blend', label: 'blend', align: 'right', value: (r) => r.blend ?? undefined, sortUndefined: 'last', render: (r) => dash(r.blend, 3) },
  { key: 'explore', label: 'explore', align: 'right', value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{inf(r.explore)}</span> },
  { key: 'adjust', label: 'adj', align: 'right', value: (r) => r.adjust ?? 0, render: (r) => (r.adjust ? signed(r.adjust, 1) : <span className="text-dim">—</span>) },
  { key: 'score', label: 'score', align: 'right', value: (r) => r.score ?? Number.MAX_SAFE_INTEGER, render: (r) => (r.chosen ? <strong className="num">{inf(r.score)}</strong> : <span className="num">{inf(r.score)}</span>) },
  { key: 'delta', label: 'Δ to #1', align: 'right', value: (r) => r.delta ?? undefined, sortUndefined: 'last', render: (r) => signed(r.delta) },
]

type EdgeRow = WindowEdgeRow & { kind: 'dropped' | 'next' }

const edgeCols: Col<EdgeRow>[] = [
  {
    key: 'edge',
    label: 'edge',
    value: (r) => (r.kind === 'dropped' ? -r.campaigns_away : r.campaigns_away),
    render: (r) => (r.kind === 'dropped' ? <Chip state="neutral">dropped</Chip> : <Chip state="warn">next out</Chip>),
  },
  {
    key: 'away',
    label: 'campaigns',
    unit: 'since / until',
    align: 'right',
    value: (r) => (r.kind === 'dropped' ? -r.campaigns_away : r.campaigns_away),
    render: (r) => <span className="num">{r.kind === 'dropped' ? `−${r.campaigns_away}` : `+${r.campaigns_away}`}</span>,
  },
  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw}>{lordOf(r)}</EntityLink> },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'played', label: 'played', value: (r) => r.played_ts ?? 0, render: (r) => <span className="num">{clock(r.played_ts)}</span> },
  { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => dash(r.reward) },
  { key: 'n', label: 'start n', unit: 'in window', align: 'right', value: (r) => r.start_n, render: (r) => <span className="num">{r.start_n}</span> },
]

function Picks() {
  const [params, setParams] = useSearchParams()
  const { data, error, loading, reload } = useApi<UcbPicksPage>('/api/campaigns/picks')
  const navigate = useNavigate()
  const desc = data?.picks ?? []
  const pickParam = params.get('pick')
  const pickId = pickParam ? Number(pickParam) : (desc[0]?.pick_id ?? null)
  const detail = useApi<UcbPickPage>(pickId == null ? null : `/api/campaigns/picks/${pickId}`, [pickId])
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const select = (id: number) => {
    const next = new URLSearchParams(params)
    next.set('pick', String(id))
    setParams(next, { replace: true })
  }
  const sub = (p: UcbPick) => `${p.faction.culture ?? ''} · ${mapShort(p.campaign_map?.raw, p.campaign_map?.label)}`
  const rows = detail.data?.rows ?? []
  const head = detail.data?.pick

  return (
    <div className="space-y-6">
      <Section title="pick log" scope={data.scope}>
        <DataTable rows={desc} cols={pickCols} rowId={(r) => String(r.pick_id)} onRowClick={(r) => select(r.pick_id)} initialSort={{ key: 'pick', desc: true }} searchPlaceholder="search lord, race, map…" pageSize={10} emptyWhat="no UCB pick has been recorded yet" emptyWhy="only runs started with --ucb record them" />
      </Section>

      <Section title={head ? `ranking at pick ${head.pick_id}` : 'ranking at the selected pick'} scope={detail.data?.scope}>
        {detail.error && <ErrorState error={detail.error} onRetry={detail.reload} />}
        {!detail.error && (detail.loading || !detail.data) && <Skeleton rows={8} />}
        {!detail.error && detail.data && (
          <div className="space-y-2">
            {head && (
              <div className="text-dim flex flex-wrap gap-3 px-1 text-2xs">
                <span><b className="text-fg">{lordOf(head)}</b> · {sub(head)}</span>
                <span>C <b className="num text-fg">{n(head.c, 2)}</b></span>
                <span>plays <b className="num text-fg">{n(head.total_plays)}</b></span>
                <span>tied <b className="num text-fg">{head.tied}</b></span>
                <span>under min plays <b className="num text-fg">{detail.data.under_min}</b></span>
              </div>
            )}
            <DataTable rows={rows} cols={rankCols} rowId={(r) => String(r.rank)} onRowClick={(r) => navigate(startUrl(startId(r)))} initialSort={{ key: 'rank', desc: false }} pageSize={25} emptyWhat="no ranking stored for this pick" />
          </div>
        )}
      </Section>
    </div>
  )
}

function WindowChurn() {
  const { data, error, loading, reload } = useApi<UcbPicksPage>('/api/campaigns/picks')
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const edges: EdgeRow[] = [
    ...(data.dropped_out ?? []).map((r) => ({ ...r, kind: 'dropped' as const })),
    ...(data.next_out ?? []).map((r) => ({ ...r, kind: 'next' as const })),
  ]
  return (
    <Section
      title="window churn"
      scope={{ text: `oldest campaigns at the edge of the trailing ${data.window}-campaign window, last out and next out` }}
    >
      <DataTable rows={edges} cols={edgeCols} rowId={(r) => `${r.kind}:${r.campaign.raw}`} onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)} initialSort={{ key: 'away', desc: false }} pageSize={20} emptyWhat="the window is not full yet, so nothing ages out" />
    </Section>
  )
}

export function Selector() {
  const view = useSubView(VIEWS)
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'picks' && <Picks />}
      {view === 'window' && <WindowChurn />}
    </div>
  )
}
