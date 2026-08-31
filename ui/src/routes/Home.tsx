import { useNavigate } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { Card, EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { mapShort } from '@/components/startcharts'
import { useApi, type CampaignRow, type CampaignsPage, type RunPage } from '@/lib/api'
import { ago, n } from '@/lib/format'

const dash = (v: number | null | undefined, d = 0) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, d)}</span>

const recentCols: Col<CampaignRow>[] = [
  { key: 'when', label: 'when', value: (r) => r.ended_when ?? '', render: (r) => <span className="text-dim text-2xs">{r.ended_when ?? '—'}</span> },
  {
    key: 'campaign',
    label: 'lord',
    value: (r) => r.leader ?? r.campaign.label,
    render: (r) => (
      <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw}>
        {r.leader ?? r.campaign.label}
      </EntityLink>
    ),
  },
  { key: 'race', label: 'race', value: (r) => r.campaign.culture ?? '', render: (r) => <span className="text-dim">{r.campaign.culture ?? '—'}</span> },
  { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => (r.reward == null ? <span className="text-dim">—</span> : <strong className="num">{n(r.reward)}</strong>) },
  { key: 'sett', label: 'settlements', unit: 'gained', align: 'right', value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', align: 'right', value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
]

function CurrentCard({ run }: { run: RunPage }) {
  const cur = run.current
  if (!cur?.campaign) {
    return (
      <Card className="flex flex-col justify-center px-4 py-3">
        <span className="text-dim">no campaign playing right now</span>
      </Card>
    )
  }
  const facts: { label: string; value: React.ReactNode }[] = [
    { label: 'turn', value: dash(cur.turn) },
    { label: 'settlements', value: dash(cur.settlements) },
    { label: 'lord level', value: dash(cur.lord_level) },
  ]
  return (
    <Card className="px-4 py-3">
      <div className="text-dim text-2xs uppercase tracking-wide">playing now</div>
      <div className="mt-1 flex items-baseline gap-2">
        <EntityLink
          to={`/campaigns/${encodeURIComponent(cur.campaign.raw)}`}
          title={cur.campaign.raw}
          className="text-lg font-semibold tracking-tight"
        >
          {cur.leader ?? cur.campaign.label}
        </EntityLink>
        {cur.campaign.culture && <span className="text-dim text-2xs">{cur.campaign.culture}</span>}
        {cur.faction_key && cur.campaign_map && (
          <EntityLink
            to={`/starts/${encodeURIComponent(cur.campaign_map.raw)}/${encodeURIComponent(cur.faction_key)}`}
            className="text-dim text-2xs"
            title="open this start"
          >
            {mapShort(cur.campaign_map.raw, cur.campaign_map.label)}
          </EntityLink>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        {facts.map((f) => (
          <span key={f.label} className="text-dim text-2xs uppercase tracking-wide">
            {f.label} <b className="num text-fg text-sm">{f.value}</b>
          </span>
        ))}
        {cur.age_seconds != null && (
          <span className="text-dim text-2xs">state {ago(cur.age_seconds)}</span>
        )}
      </div>
    </Card>
  )
}

export function Home() {
  const run = useApi<RunPage>('/api/run')
  const recent = useApi<CampaignsPage>('/api/campaigns?page_size=8')
  const navigate = useNavigate()
  if (run.error) return <ErrorState error={run.error} onRetry={run.reload} />
  if (!run.data || (!recent.data && recent.loading)) return <Skeleton rows={8} />
  const campaigns = (run.data.totals ?? []).find((t) => t.noun === 'campaigns')
  const tiles = [
    ...(campaigns
      ? [{ label: 'campaigns recorded', value: String(n(campaigns.value)), sub: 'in this run dir', spark: [] as number[] }]
      : []),
    ...(run.data.throughput ?? [])
      .filter((m) => m.label === 'campaigns/hr' || m.label === 'turns/hr')
      .map((m) => ({ label: m.label, value: m.value == null ? '—' : String(m.value), sub: m.sub ?? '', spark: m.spark ?? [] })),
  ]
  return (
    <div className="space-y-5">
      <div className="grid gap-3 lg:grid-cols-2">
        <CurrentCard run={run.data} />
        <div className="grid grid-cols-3 gap-3">
          {tiles.map((t) => (
            <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub, state: 'neutral', spark: t.spark }} />
          ))}
        </div>
      </div>
      <Section
        title="latest campaigns"
        scope={{ text: 'the newest finished campaigns, how long they ran and what they gained' }}
        right={
          <button onClick={() => navigate('/campaigns?view=campaigns')} className="text-accent text-2xs hover:underline">
            every campaign
          </button>
        }
      >
        <DataTable
          rows={recent.data?.rows ?? []}
          cols={recentCols}
          rowId={(r) => r.campaign.raw}
          onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
          emptyWhat="no campaign recorded yet"
        />
      </Section>
    </div>
  )
}
