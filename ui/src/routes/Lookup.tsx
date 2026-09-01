import { useNavigate } from 'react-router-dom'
import { ConditionBar, useConditionQuery } from '@/components/conditions'
import { DataTable, useServerTable, type Col } from '@/components/DataTable'
import { EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { useApi, type CampaignLookupPage, type LookupCampaignRow } from '@/lib/api'
import { clock, n } from '@/lib/format'

const dash = (v: number | null | undefined, digits = 0) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>

const cols: Col<LookupCampaignRow>[] = [
  { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
  {
    key: 'campaign',
    label: 'campaign',
    value: (r) => r.campaign.tag ?? '',
    render: (r) => (
      <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw} className="num text-2xs">
        {r.campaign.tag ?? r.campaign.raw.slice(-6)}
      </EntityLink>
    ),
  },
  {
    key: 'start',
    label: 'start',
    value: (r) => r.leader ?? r.faction.label,
    render: (r) => (
      <EntityLink
        to={`/starts/${encodeURIComponent(r.campaign_map?.raw ?? '')}/${encodeURIComponent(r.faction.raw)}`}
        title={r.faction.raw}
      >
        {r.leader ?? r.faction.label}
      </EntityLink>
    ),
  },
  { key: 'race', label: 'race', optional: true, value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'first', label: 'first match', unit: 'turn', align: 'right', value: (r) => r.first_turn ?? undefined, sortUndefined: 'last', render: (r) => dash(r.first_turn) },
  { key: 'matched', label: 'matching positions', align: 'right', value: (r) => r.matched, render: (r) => <span className="num">{n(r.matched)}</span> },
  { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => <strong className="num">{n(r.reward, 2)}</strong> },
  { key: 'sett', label: 'settlements', unit: 'gained', align: 'right', optional: true, value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', align: 'right', optional: true, value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
]

export function Lookup() {
  const navigate = useNavigate()
  const qs = useConditionQuery()
  const st = useServerTable(25)
  const { data, error, loading, reload } = useApi<CampaignLookupPage>(
    `/api/lookup?${qs ? `${qs}&` : ''}${st.qs()}`,
    [qs, ...st.deps],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data && loading) return <Skeleton rows={10} />
  if (!data) return null
  const tiles = [
    { label: 'campaigns', value: n(data.campaigns), sub: 'ever in a matching situation' },
    { label: 'matching positions', value: n(data.decisions), sub: 'recorded moments matching every condition' },
    { label: 'mean reward', value: data.mean_reward == null ? '—' : n(data.mean_reward, 2), sub: 'of those campaigns' },
    { label: 'mean turns', value: data.mean_turns == null ? '—' : n(data.mean_turns, 1), sub: 'they reached' },
  ]
  return (
    <div className="space-y-5">
      <ConditionBar />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub, state: 'neutral', spark: [] }} />
        ))}
      </div>
      <Section title="matching campaigns" scope={data.scope}>
        <DataTable
          rows={data.rows ?? []}
          cols={cols}
          rowId={(r) => r.campaign.raw}
          onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
          searchPlaceholder="search campaign, start…"
          server={st.bind(data.total ?? 0)}
          emptyWhat="no campaign ever passed through a matching situation"
        />
      </Section>
    </div>
  )
}
