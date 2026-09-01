import { useNavigate, useParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { Card, Chip, EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { itemDelta } from '@/routes/Items'
import { useApi, type ItemCampaignRow, type ItemEffect, type ItemPage, type ItemStartRow } from '@/lib/api'
import { clock, n, stateText } from '@/lib/format'
import { cn } from '@/lib/utils'

function EffectsTable({ effects, description }: { effects: ItemEffect[]; description?: string | null }) {
  if (!effects.length) {
    return (
      <Card className="px-4 py-3 text-xs">
        {description ? <span>{description}</span> : <span className="text-dim">no effect recorded for it</span>}
      </Card>
    )
  }
  return (
    <Card className="overflow-hidden">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-line text-dim border-b text-left text-2xs">
            <th className="px-3 py-1.5 font-normal">effect</th>
            <th className="px-3 py-1.5 text-right font-normal">value</th>
            <th className="px-3 py-1.5 font-normal">applies to</th>
          </tr>
        </thead>
        <tbody>
          {effects.map((e, i) => (
            <tr key={i} className="border-line border-b last:border-0">
              <td className="px-3 py-1.5">{e.name}</td>
              <td className={cn('num px-3 py-1.5 text-right', stateText[e.state ?? 'neutral'])}>{e.value ?? '\u2014'}</td>
              <td className="text-dim px-3 py-1.5">{e.scope}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

export function ItemDetail() {
  const { itemKey = '' } = useParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<ItemPage>(
    `/api/items/${encodeURIComponent(itemKey)}`,
    [itemKey],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const startCols: Col<ItemStartRow>[] = [
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
    { key: 'held', label: 'held in', align: 'right', value: (r) => r.held_in, render: (r) => <span className="num">{n(r.held_in)}</span> },
    { key: 'eq', label: 'worn in', align: 'right', value: (r) => r.equipped_in, render: (r) => <span className="num">{n(r.equipped_in)}</span> },
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
    { key: 'delta', label: 'Δ reward', unit: 'worn − benched', align: 'right', value: (r) => r.delta ?? undefined, sortUndefined: 'last', render: (r) => itemDelta(r.delta) },
  ]
  const recentCols: Col<ItemCampaignRow>[] = [
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
    { key: 'start', label: 'start', value: (r) => r.leader ?? '', render: (r) => <span className="text-dim">{r.leader ?? '—'}</span> },
    { key: 'et', label: 'equip turn', align: 'right', value: (r) => r.equip_turn ?? 0, render: (r) => (r.equip_turn == null ? <span className="text-dim">—</span> : <span className="num">{r.equip_turn}</span>) },
    { key: 'worn', label: 'turns worn', align: 'right', value: (r) => r.turns_worn ?? 0, render: (r) => (r.turns_worn == null ? <span className="text-dim">—</span> : <span className="num">{r.turns_worn}</span>) },
    { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => <strong className="num">{n(r.reward)}</strong> },
  ]
  const tiles = [
    { label: 'held in', value: n(data.held_in), sub: `campaigns · ${n(data.starts)} start${data.starts === 1 ? '' : 's'}`, state: 'neutral' as const },
    {
      label: 'worn in',
      value: data.equip_rate ? n(data.equip_rate.n) : '—',
      sub: data.equip_rate?.of ? `${((100 * data.equip_rate.n) / data.equip_rate.of).toFixed(0)}% of campaigns that held it` : undefined,
      state: 'neutral' as const,
    },
    {
      label: 'Δ reward, worn − benched',
      value: data.delta == null ? '—' : `${data.delta > 0 ? '+' : ''}${n(data.delta, 2)}`,
      sub: data.delta == null ? 'needs 5+ campaigns worn and benched' : 'avg campaign reward, all starts',
      state: data.delta == null ? ('neutral' as const) : data.delta > 0 ? ('ok' as const) : ('bad' as const),
    },
    { label: 'avg equip turn', value: data.avg_equip_turn == null ? '—' : n(data.avg_equip_turn, 1), sub: 'first equip', state: 'neutral' as const },
    { label: 'churned in', value: n(data.churned_in), sub: 'campaigns with a cycle', state: 'neutral' as const },
  ]
  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
          <span>
            <EntityLink to="/items" className="text-dim">items</EntityLink>
            <span className="text-dim"> / {data.label ?? data.key}</span>
          </span>
          <span className="text-dim num text-2xs">{data.key}</span>
        </div>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <span className="text-lg font-semibold">{data.label ?? data.key}</span>
          {data.category && (
            <EntityLink to={`/items?cat=${encodeURIComponent(data.category)}`} title={`every ${data.category} item`}>
              <Chip state="neutral">{data.category}</Chip>
            </EntityLink>
          )}
          {data.lord_share != null && (
            <span className="text-dim text-xs">
              worn by lords {n(data.lord_share)}% · heroes {n(100 - data.lord_share)}%
            </span>
          )}
        </h1>
        {data.acquisition && <div className="text-dim mt-1 text-2xs">acquired: {data.acquisition.toLowerCase()}</div>}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub ?? null, state: t.state, spark: [] }} />
        ))}
      </div>

      <Section title="what it does" scope={{ text: 'green helps, red hurts' }}>
        <EffectsTable effects={data.effects ?? []} description={data.description} />
      </Section>

      <Section title="by start" scope={{ text: 'the same worn-vs-benched comparison, per start that held it' }}>
        <DataTable
          rows={data.by_start ?? []}
          cols={startCols}
          rowId={(r) => `${r.campaign_map?.raw ?? ''}|${r.faction.raw}`}
          onRowClick={(r) => navigate(`/starts/${encodeURIComponent(r.campaign_map?.raw ?? '')}/${encodeURIComponent(r.faction.raw)}`)}
          pageSize={10}
          emptyWhat="no start held this item"
        />
      </Section>

      <Section title="recent campaigns that equipped it" scope={{ text: 'newest first' }}>
        <DataTable
          rows={data.recent ?? []}
          cols={recentCols}
          rowId={(r) => r.campaign.raw}
          onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
          dense
          emptyWhat="no campaign equipped this item"
        />
      </Section>
    </div>
  )
}
