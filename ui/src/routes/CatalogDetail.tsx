import { useParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { ByStartTable, RecentTable, dashNum } from '@/components/catalog'
import { Chip, EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { useApi, type CatalogKeyPage, type ChainLevel, type SkillCharacterRow } from '@/lib/api'
import { n, pct } from '@/lib/format'

type Family = 'buildings' | 'research' | 'skills'

const VERB: Record<Family, string> = {
  buildings: 'constructed',
  research: 'started',
  skills: 'ranked',
}

function facts(family: Family, d: CatalogKeyPage): string | null {
  if (family === 'buildings') {
    const parts = []
    if (d.level != null) parts.push(`level ${d.level}`)
    if (d.cost != null) parts.push(`cost ${n(d.cost)}`)
    if (d.upkeep != null) parts.push(`upkeep ${n(d.upkeep)}`)
    if (d.turns_to_build != null) parts.push(`builds in ${d.turns_to_build} turn${d.turns_to_build === 1 ? '' : 's'}`)
    return parts.join(' · ') || null
  }
  if (family === 'research') {
    const parts = []
    if (d.tier != null) parts.push(`tier ${d.tier}`)
    if (d.points != null) parts.push(`${n(d.points)} points`)
    if (d.parent) parts.push(`after ${d.parent.label}`)
    return parts.join(' · ') || null
  }
  return d.unlock_rank != null && d.unlock_rank > 0 ? `unlocks at rank ${d.unlock_rank}` : null
}

function ChainTable({ chain }: { chain: ChainLevel[] }) {
  const cols: Col<ChainLevel>[] = [
    { key: 'level', label: 'level', align: 'right', value: (r) => r.level ?? 0, render: (r) => dashNum(r.level) },
    {
      key: 'name',
      label: 'building',
      value: (r) => r.label ?? r.key,
      render: (r) =>
        r.this ? (
          <strong title={r.key}>{r.label ?? r.key}</strong>
        ) : r.constructed_in ? (
          <EntityLink to={`/buildings/${encodeURIComponent(r.key)}`} title={r.key}>
            {r.label ?? r.key}
          </EntityLink>
        ) : (
          <span title={r.key} className="text-dim">
            {r.label ?? r.key}
          </span>
        ),
    },
    { key: 'cost', label: 'cost', align: 'right', value: (r) => r.cost ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.cost) },
    { key: 'n', label: 'constructed in', unit: 'campaigns', align: 'right', value: (r) => r.constructed_in, render: (r) => dashNum(r.constructed_in) },
  ]
  return <DataTable rows={chain} cols={cols} rowId={(r) => r.key} dense emptyWhat="no chain in the reference schema" />
}

function CharacterTable({ rows }: { rows: SkillCharacterRow[] }) {
  const cols: Col<SkillCharacterRow>[] = [
    { key: 'char', label: 'character', value: (r) => r.label ?? r.subtype, render: (r) => <span title={r.subtype}>{r.label ?? r.subtype}</span> },
    { key: 'kind', label: 'kind', value: (r) => r.kind, render: (r) => <span className="text-dim">{r.kind}</span> },
    { key: 'n', label: 'ranked in', unit: 'campaigns', align: 'right', value: (r) => r.campaigns, render: (r) => <span className="num">{n(r.campaigns)}</span> },
    { key: 'ranks', label: 'avg ranks', align: 'right', value: (r) => r.avg_ranks ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_ranks, 1) },
    { key: 'turn', label: 'avg turn', unit: 'first', align: 'right', value: (r) => r.avg_turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_turn, 1) },
  ]
  return <DataTable rows={rows} cols={cols} rowId={(r) => r.subtype} dense pageSize={10} emptyWhat="no character snapshot attributes this skill" />
}

export function CatalogDetail({ family }: { family: Family }) {
  const { key = '' } = useParams()
  const { data, error, loading, reload } = useApi<CatalogKeyPage>(
    `/api/${family}/${encodeURIComponent(key)}`,
    [family, key],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const verb = VERB[family]
  const factLine = facts(family, data)
  const tiles = [
    {
      label: `${verb} in`,
      value: n(data.took_in),
      sub: `campaigns · ${n(data.starts)} start${data.starts === 1 ? '' : 's'}`,
      state: 'neutral' as const,
    },
    {
      label: 'take rate',
      value: pct(data.took ?? null) ?? '—',
      sub: data.took?.of ? `${verb} in ${n(data.took.n)} of ${n(data.took.of)} campaigns offered it` : 'never on a recorded offer',
      state: 'neutral' as const,
    },
    {
      label: `avg ${verb} turn`,
      value: data.avg_turn == null ? '—' : n(data.avg_turn, 1),
      sub: `first ${verb} per campaign`,
      state: 'neutral' as const,
    },
    {
      label: 'Δ reward, took − passed',
      value: data.delta == null ? '—' : `${data.delta > 0 ? '+' : ''}${n(data.delta, 2)}`,
      sub:
        data.delta == null
          ? 'needs 5+ campaigns on each side'
          : `took ${n(data.avg_reward_took, 2)} vs passed ${n(data.avg_reward_passed, 2)} · pooled across starts`,
      state: data.delta == null ? ('neutral' as const) : data.delta > 0 ? ('ok' as const) : ('bad' as const),
    },
  ]
  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
          <span>
            <EntityLink to={`/${family}`} className="text-dim">{family}</EntityLink>
            <span className="text-dim"> / {data.label ?? data.key}</span>
          </span>
          <span className="text-dim num text-2xs">{data.key}</span>
        </div>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <span className="text-lg font-semibold">{data.label ?? data.key}</span>
          {data.category && <Chip state="neutral">{data.category}</Chip>}
          {data.line && <Chip state="neutral">{data.line}</Chip>}
          {factLine && <span className="text-dim text-xs">{factLine}</span>}
        </h1>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub ?? null, state: t.state, spark: [] }} />
        ))}
      </div>

      {family === 'buildings' && (data.chain ?? []).length > 1 && (
        <Section title="its chain" scope={{ text: 'every level of the same building chain' }}>
          <ChainTable chain={data.chain ?? []} />
        </Section>
      )}

      {family === 'skills' && (
        <Section title="by character" scope={{ text: 'which characters put points in it, from live snapshots' }}>
          <CharacterTable rows={data.by_character ?? []} />
        </Section>
      )}

      <Section
        title="by start"
        scope={{ text: `${verb} rate per start it was offered to · Δ = avg reward of campaigns that ${verb} it minus that start's mean` }}
      >
        <ByStartTable rows={data.by_start ?? []} verb={verb} />
      </Section>

      <Section title={`recent campaigns that ${verb} it`} scope={{ text: 'newest first' }}>
        <RecentTable rows={data.recent ?? []} verb={verb} />
      </Section>
    </div>
  )
}
