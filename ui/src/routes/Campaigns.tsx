import { Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, useServerTable, type Col } from '@/components/DataTable'
import {
  Card,
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
} from '@/lib/api'
import { n } from '@/lib/format'

const VIEWS = [
  { key: 'campaigns', label: 'campaigns', asks: 'which campaign ended how' },
  { key: 'starts', label: 'starts', asks: 'what the pool of starts looks like' },
]
const LEGACY: Record<string, string> = { all: 'campaigns', matrix: 'starts' }

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
  return { map: get('map'), race: get('race'), set }
}

export function Select({ value, onChange, children }: { value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1 text-xs">
      {children}
    </select>
  )
}

export const startId = (r: { campaign_map?: { raw: string } | null; faction: { raw: string } }) => `${r.campaign_map?.raw ?? ''}|${r.faction.raw}`
export const startUrl = (id: string) => {
  const [m, f] = id.split('|')
  return `/starts/${encodeURIComponent(m)}/${encodeURIComponent(f)}`
}
export const lordOf = (r: { leader?: string | null; faction: { label: string } }) => r.leader ?? r.faction.label

export function MapCell({ ident }: { ident?: { raw: string; label: string } | null }) {
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
  { key: 'turns', label: 'turns', group: 'campaign', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'when', label: 'when', group: 'campaign', value: (r) => r.ended_when ?? '', render: (r) => <span className="text-dim text-2xs">{r.ended_when ?? '—'}</span> },
  { key: 'reward', label: 'reward', group: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => (r.reward == null ? <span className="text-dim">—</span> : <strong className="num">{n(r.reward)}</strong>) },
  { key: 'sett', label: 'settlements', unit: 'gained', group: 'reward', align: 'right', value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', group: 'reward', align: 'right', value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
  { key: 'settlements_start', label: 'starting', unit: 'settlements', group: 'growth', align: 'right', optional: true, value: (r) => r.first_settlements ?? undefined, sortUndefined: 'last', render: (r) => dash(r.first_settlements) },
  { key: 'settlements_per_turn', label: 'settlements', unit: 'per turn', group: 'growth', align: 'right', optional: true, value: (r) => r.settlements_per_turn ?? undefined, sortUndefined: 'last', render: (r) => signed(r.settlements_per_turn, 2) },
  { key: 'lord_level', label: 'lord level', unit: 'reached', group: 'growth', align: 'right', optional: true, value: (r) => r.peak_lord_level ?? undefined, sortUndefined: 'last', render: (r) => dash(r.peak_lord_level) },
  { key: 'lord_per_turn', label: 'lord level', unit: 'per turn', group: 'growth', align: 'right', optional: true, value: (r) => r.lord_per_turn ?? undefined, sortUndefined: 'last', render: (r) => signed(r.lord_per_turn, 2) },
  { key: 'peak_setts', label: 'peak settlements', group: 'growth', align: 'right', optional: true, value: (r) => r.peak_settlements ?? 0, render: (r) => dash(r.peak_settlements) },
  { key: 'peak_rank', label: 'peak power rank', group: 'growth', align: 'right', direction: 'down', optional: true, value: (r) => r.peak_power_rank ?? 0, render: (r) => dash(r.peak_power_rank) },
  { key: 'final_rank', label: 'final power rank', group: 'growth', align: 'right', direction: 'down', optional: true, value: (r) => r.final_power_rank ?? 0, render: (r) => dash(r.final_power_rank) },
]

function AllCampaigns() {
  const st = useServerTable(25)
  const f = useFilters()
  const extra: Record<string, string> = {}
  if (f.map) extra.map = f.map
  if (f.race) extra.race = f.race
  const { data, error, loading, reload } = useApi<CampaignsPage>(
    `/api/campaigns?${st.qs(extra)}`,
    [...st.deps, f.map, f.race],
  )
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <Section title="campaigns" scope={data.scope}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select value={f.map} onChange={(v) => f.set('map', v)}>
          <option value="">every map</option>
          {(data.maps ?? []).map((m) => (
            <option key={m.raw} value={m.raw}>{m.label}</option>
          ))}
        </Select>
        <Select value={f.race} onChange={(v) => f.set('race', v)}>
          <option value="">every race</option>
          {(data.races ?? []).map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </Select>
        <Card className="text-dim ml-auto px-2 py-1 text-2xs">
          <b className="num text-fg">{n(data.total)}</b> campaigns
        </Card>
      </div>
      <DataTable
        rows={data.rows}
        cols={campaignCols}
        rowId={(r) => r.campaign.raw}
        onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
        searchPlaceholder="search lord, race…"
        server={st.bind(data.total ?? data.rows.length)}
        emptyWhat="no campaign matches"
      />
    </Section>
  )
}

export function Campaigns() {
  const raw = useSubView(VIEWS)
  const [params] = useSearchParams()
  if (raw === 'selector' || raw === 'picks' || raw === 'window') {
    const pick = params.get('pick')
    const view = raw === 'window' ? '?view=window' : pick ? `?pick=${pick}` : ''
    return <Navigate to={`/selector${view}`} replace />
  }
  const view = LEGACY[raw] ?? raw
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'starts' && <Starts />}
      {view === 'campaigns' && <AllCampaigns />}
    </div>
  )
}
