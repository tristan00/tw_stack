import { useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  EntityLink,
  ErrorState,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { mapColor, mapShort } from '@/components/startcharts'
import {
  useApi,
  type CampaignRow,
  type CampaignsPage,
  type StartRow,
  type StartsPage,
  type UcbPick,
  type UcbPickPage,
  type UcbPicksPage,
  type UcbRow,
  type WindowEdgeRow,
} from '@/lib/api'
import { clock, n } from '@/lib/format'
import { cn } from '@/lib/utils'

const VIEWS = [
  { key: 'starts', label: 'starts', asks: 'what the pool of starts looks like' },
  { key: 'selector', label: 'selector', asks: 'why the selector played this start' },
  { key: 'window', label: 'window', asks: 'which plays are aging out of the lookback window' },
  { key: 'campaigns', label: 'campaigns', asks: 'which campaign ended how' },
]
const LEGACY: Record<string, string> = { all: 'campaigns', picks: 'selector', matrix: 'starts' }

const inf = (v: number | null | undefined, digits = 3) => (v == null ? '∞' : n(v, digits))
const dash = (v: number | null | undefined, digits = 0) => (v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>)
const signed = (v: number | null | undefined, digits = 3) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{v >= 0 ? '+' : ''}{n(v, digits)}</span>

function useFilters() {
  const [params, setParams] = useSearchParams()
  const get = (k: string) => params.get(k) ?? ''
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }
  return { map: get('map'), race: get('race'), plays: get('plays'), pick: get('pick'), outcome: get('outcome'), range: get('range'), set }
}

function Select({ value, onChange, children }: { value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1 text-xs">
      {children}
    </select>
  )
}

const startId = (r: { campaign_map?: { raw: string } | null; faction: { raw: string } }) => `${r.campaign_map?.raw ?? ''}|${r.faction.raw}`
const startUrl = (id: string) => {
  const [m, f] = id.split('|')
  return `/starts/${encodeURIComponent(m)}/${encodeURIComponent(f)}`
}
const lordOf = (r: { leader?: string | null; faction: { label: string } }) => r.leader ?? r.faction.label

function MapCell({ ident }: { ident?: { raw: string; label: string } | null }) {
  if (!ident) return <span className="text-dim">—</span>
  return (
    <span className="inline-flex items-center gap-1.5" title={ident.label}>
      <span className="inline-block size-2 rounded-full" style={{ background: mapColor(ident.raw) }} />
      <span className="text-dim">{mapShort(ident.raw, ident.label)}</span>
    </span>
  )
}


const startCols: Col<StartRow>[] = [
  {
    key: 'lord',
    label: 'lord',
    value: (r) => lordOf(r),
    render: (r) => (
      <EntityLink to={startUrl(startId(r))} title={r.faction.raw}>
        {lordOf(r)}
      </EntityLink>
    ),
  },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'n', label: 'campaigns', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'avg_turns', label: 'avg turns', align: 'right', value: (r) => r.avg_turns ?? 0, render: (r) => n(r.avg_turns, 1) },
  { key: 'sec_per_turn', label: 's/turn', align: 'right', value: (r) => r.sec_per_turn ?? 0, render: (r) => n(r.sec_per_turn, 1) },
  { key: 'sett_best', label: 'best', align: 'right', group: 'settlements gained', value: (r) => r.settlements_gained_best ?? 0, render: (r) => n(r.settlements_gained_best) },
  { key: 'sett_avg', label: 'avg', align: 'right', group: 'settlements gained', value: (r) => r.settlements_gained_avg ?? 0, render: (r) => n(r.settlements_gained_avg, 1) },
  { key: 'levels_best', label: 'best', align: 'right', group: 'levels gained', value: (r) => r.levels_gained_best ?? 0, render: (r) => n(r.levels_gained_best) },
  { key: 'levels_avg', label: 'avg', align: 'right', group: 'levels gained', value: (r) => r.levels_gained_avg ?? 0, render: (r) => n(r.levels_gained_avg, 1) },
  { key: 'allies_best', label: 'best', align: 'right', optional: true, group: 'allies gained', value: (r) => r.allies_gained_best ?? 0, render: (r) => n(r.allies_gained_best) },
  { key: 'allies_avg', label: 'avg', align: 'right', optional: true, group: 'allies gained', value: (r) => r.allies_gained_avg ?? 0, render: (r) => n(r.allies_gained_avg, 1) },
  { key: 'vassals_best', label: 'best', align: 'right', optional: true, group: 'vassals gained', value: (r) => r.vassals_gained_best ?? 0, render: (r) => n(r.vassals_gained_best) },
  { key: 'vassals_avg', label: 'avg', align: 'right', optional: true, group: 'vassals gained', value: (r) => r.vassals_gained_avg ?? 0, render: (r) => n(r.vassals_gained_avg, 1) },
  { key: 'total_best', label: 'best', align: 'right', group: 'total gained', value: (r) => r.total_gained_best ?? 0, render: (r) => n(r.total_gained_best) },
  { key: 'total_avg', label: 'avg', align: 'right', group: 'total gained', value: (r) => r.total_gained_avg ?? 0, render: (r) => n(r.total_gained_avg, 1) },
  { key: 'allied', label: 'ever allied', align: 'right', optional: true, value: (r) => r.ever_allied, render: (r) => n(r.ever_allied) },
  { key: 'vassal', label: 'ever vassal', align: 'right', optional: true, value: (r) => r.ever_vassal, render: (r) => n(r.ever_vassal) },
  { key: 'confirm', label: 'confirmed', optional: true, value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1), render: (r) => <Bar rate={r.confirm_rate ?? null} /> },
]

function Starts() {
  const { data, error, loading, reload } = useApi<StartsPage>('/api/campaigns/starts')
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <Section title="starts" scope={data.scope}>
      <DataTable
        rows={data.rows.filter((r) => r.n > 0)}
        cols={startCols}
        rowId={(r) => startId(r)}
        onRowClick={(r) => navigate(startUrl(startId(r)))}
        initialSort={{ key: 'total_avg', desc: true }}
        searchPlaceholder="search start…"
        pageSize={10}
        emptyWhat="no start has recorded a campaign yet"
      />
    </Section>
  )
}


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

function Selector() {
  const { data, error, loading, reload } = useApi<UcbPicksPage>('/api/campaigns/picks')
  const f = useFilters()
  const navigate = useNavigate()
  const desc = data?.picks ?? []
  const pickId = f.pick ? Number(f.pick) : (desc[0]?.pick_id ?? null)
  const detail = useApi<UcbPickPage>(pickId == null ? null : `/api/campaigns/picks/${pickId}`, [pickId])
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const select = (id: number) => f.set('pick', String(id))
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


const campaignCols: Col<CampaignRow>[] = [
  {
    key: 'campaign',
    label: 'lord',
    group: 'campaign',
    value: (r) => r.leader ?? r.campaign.label,
    render: (r) => (
      <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw}>
        {r.leader ?? r.campaign.label}
      </EntityLink>
    ),
  },
  { key: 'race', label: 'race', group: 'campaign', value: (r) => r.campaign.culture ?? '', render: (r) => <span className="text-dim">{r.campaign.culture ?? '—'}</span> },
  { key: 'map', label: 'map', group: 'campaign', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  {
    key: 'outcome',
    label: 'outcome',
    group: 'campaign',
    value: (r) => r.outcome?.label ?? '',
    render: (r) =>
      r.outcome ? (
        <span className="flex items-center gap-1.5">
          <Chip state={r.outcome_state ?? 'neutral'}>{r.outcome.label}</Chip>
          {r.suspicious && <Chip state="bad">suspicious</Chip>}
        </span>
      ) : (
        <span className="text-dim">running</span>
      ),
  },
  { key: 'turns', label: 'turns', group: 'campaign', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'when', label: 'when', group: 'campaign', value: (r) => r.ended_when ?? '', render: (r) => <span className="text-dim text-2xs">{r.ended_when ?? '—'}</span> },
  { key: 'ended_because', label: 'why it ended', group: 'campaign', optional: true, value: (r) => r.ended_because ?? '', render: (r) => (r.ended_because ? <span className="text-2xs">{r.ended_because}</span> : <span className="text-dim">—</span>) },
  { key: 'reward', label: 'reward', group: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => (r.reward == null ? <span className="text-dim">—</span> : <strong className="num">{n(r.reward)}</strong>) },
  { key: 'sett', label: 'settlements', unit: 'gained', group: 'reward', align: 'right', value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', group: 'reward', align: 'right', value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
  { key: 'decisions', label: 'decisions', group: 'volume', align: 'right', value: (r) => r.decisions, render: (r) => n(r.decisions) },
  { key: 'confirm', label: 'confirmed', group: 'volume', value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1), render: (r) => <Bar rate={r.confirm_rate ?? null} /> },
  { key: 'no_action', label: 'no action', group: 'volume', align: 'right', optional: true, value: (r) => r.no_action, render: (r) => n(r.no_action) },
  { key: 'pick', label: 'UCB pick', group: 'volume', align: 'right', optional: true, value: (r) => r.pick_id ?? undefined, sortUndefined: 'last', render: (r) => dash(r.pick_id) },
  { key: 'settlements_start', label: 'starting', unit: 'settlements', group: 'growth', align: 'right', optional: true, value: (r) => r.first_settlements ?? undefined, sortUndefined: 'last', render: (r) => dash(r.first_settlements) },
  { key: 'settlements_per_turn', label: 'settlements', unit: 'per turn', group: 'growth', align: 'right', optional: true, value: (r) => r.settlements_per_turn ?? undefined, sortUndefined: 'last', render: (r) => signed(r.settlements_per_turn, 2) },
  { key: 'lord_level', label: 'lord level', unit: 'reached', group: 'growth', align: 'right', optional: true, value: (r) => r.peak_lord_level ?? undefined, sortUndefined: 'last', render: (r) => dash(r.peak_lord_level) },
  { key: 'lord_per_turn', label: 'lord level', unit: 'per turn', group: 'growth', align: 'right', optional: true, value: (r) => r.lord_per_turn ?? undefined, sortUndefined: 'last', render: (r) => signed(r.lord_per_turn, 2) },
  { key: 'peak_setts', label: 'peak settlements', group: 'growth', align: 'right', optional: true, value: (r) => r.peak_settlements ?? 0, render: (r) => dash(r.peak_settlements) },
  { key: 'peak_rank', label: 'peak power rank', group: 'growth', align: 'right', direction: 'down', optional: true, value: (r) => r.peak_power_rank ?? 0, render: (r) => dash(r.peak_power_rank) },
  { key: 'final_rank', label: 'final power rank', group: 'growth', align: 'right', direction: 'down', optional: true, value: (r) => r.final_power_rank ?? 0, render: (r) => dash(r.final_power_rank) },
  { key: 'span', label: 'span', unit: 'min', group: 'growth', align: 'right', optional: true, value: (r) => r.span_min ?? 0, render: (r) => dash(r.span_min, 1) },
]

function AllCampaigns() {
  const { data, error, loading, reload } = useApi<CampaignsPage>('/api/campaigns')
  const f = useFilters()
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const maps = [...new Map(data.rows.filter((r) => r.campaign_map).map((r) => [r.campaign_map!.raw, r.campaign_map!])).values()]
  const races = [...new Set(data.rows.map((r) => r.campaign.culture ?? ''))].filter(Boolean).sort()
  const rows = data.rows.filter(
    (r) => (!f.map || r.campaign_map?.raw === f.map) && (!f.race || (r.campaign.culture ?? '') === f.race) && (!f.outcome || r.outcome?.raw === f.outcome),
  )
  return (
    <Section title="campaigns" scope={data.scope}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select value={f.map} onChange={(v) => f.set('map', v)}>
          <option value="">every map</option>
          {maps.map((m) => (
            <option key={m.raw} value={m.raw}>{m.label}</option>
          ))}
        </Select>
        <Select value={f.race} onChange={(v) => f.set('race', v)}>
          <option value="">every race</option>
          {races.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </Select>
        {data.headline.map((h) => (
          <button key={h.outcome.raw} onClick={() => f.set('outcome', f.outcome === h.outcome.raw ? '' : h.outcome.raw)} className={cn('rounded-full', f.outcome === h.outcome.raw && 'ring-accent ring-2')}>
            <Chip state={h.state ?? 'neutral'}>
              <span className="num mr-1 font-semibold">{h.count}</span>
              {h.outcome.label}
            </Chip>
          </button>
        ))}
        <Card className="text-dim ml-auto px-2 py-1 text-2xs">
          <b className="num text-fg">{rows.length}</b> of {data.rows.length}
        </Card>
      </div>
      <DataTable rows={rows} cols={campaignCols} rowId={(r) => r.campaign.raw} onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)} searchPlaceholder="search lord, race, outcome…" pageSize={10} emptyWhat="no campaign matches" />
    </Section>
  )
}

export function Campaigns() {
  const raw = useSubView(VIEWS)
  const view = LEGACY[raw] ?? raw
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'starts' && <Starts />}
      {view === 'selector' && <Selector />}
      {view === 'window' && <WindowChurn />}
      {view === 'campaigns' && <AllCampaigns />}
    </div>
  )
}
