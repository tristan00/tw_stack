import { ArrowLeft } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { Bar, Chip, ErrorState, IdentLabel, MetricTile, Section, Skeleton } from '@/components/primitives'
import { ChartFrame } from '@/components/charts'
import { RewardBars, ShareHistogram, Trajectory, mapColor, mapShort } from '@/components/startcharts'
import { useApi, type MatrixCell, type StartCampaign, type StartDetail as Detail, type StartPickPoint } from '@/lib/api'
import { clock, n } from '@/lib/format'

const dash = (v: number | null | undefined, digits = 0) => (v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>)
const signed = (v: number | null | undefined, digits = 3) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{v >= 0 ? '+' : ''}{n(v, digits)}</span>
const inf = (v: number | null | undefined, digits = 3) => (v == null ? '∞' : n(v, digits))

const campaignCols: Col<StartCampaign>[] = [
  { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
  { key: 'tag', label: 'campaign', value: (r) => r.campaign.tag ?? '', render: (r) => <span className="num text-dim text-2xs">{r.campaign.tag}</span> },
  { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => <strong className="num">{n(r.reward)}</strong> },
  { key: 'sett', label: 'settlements', unit: 'gained', align: 'right', value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', align: 'right', value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
  { key: 'outcome', label: 'outcome', value: (r) => r.outcome?.label ?? '', render: (r) => (r.outcome ? <Chip state={r.outcome_state ?? 'neutral'}>{r.outcome.label}</Chip> : <span className="text-dim">running</span>) },
  { key: 'why', label: 'why it ended', optional: true, value: (r) => r.ended_because ?? '', render: (r) => (r.ended_because ? <span className="text-2xs">{r.ended_because}</span> : <span className="text-dim">—</span>) },
  { key: 'decisions', label: 'decisions', align: 'right', value: (r) => r.decisions, render: (r) => n(r.decisions) },
  { key: 'confirm', label: 'confirmed', value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1), render: (r) => <Bar rate={r.confirm_rate ?? null} /> },
  { key: 'pick', label: 'UCB pick', align: 'right', value: (r) => r.pick_id ?? undefined, sortUndefined: 'last', render: (r) => dash(r.pick_id) },
  { key: 'window', label: 'in window', optional: true, value: (r) => (r.in_window ? 1 : 0), render: (r) => (r.in_window ? <Chip state="ok">yes</Chip> : <span className="text-dim">no</span>) },
]

const trajCols: Col<StartPickPoint>[] = [
  { key: 'pick', label: 'pick', align: 'right', value: (r) => r.pick_id, render: (r) => <span className="num">{r.pick_id}</span> },
  { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
  { key: 'rank', label: 'rank', align: 'right', value: (r) => r.rank, render: (r) => (r.chosen ? <strong className="num">#{r.rank}</strong> : <span className="num">#{r.rank}</span>) },
  { key: 'c', label: 'C', align: 'right', value: (r) => r.c ?? 0, render: (r) => n(r.c, 2) },
  { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'mean', label: 'mean', align: 'right', value: (r) => r.mean ?? 0, render: (r) => dash(r.mean, 2) },
  { key: 'blend', label: 'blend', align: 'right', value: (r) => r.blend ?? undefined, sortUndefined: 'last', render: (r) => signed(r.blend) },
  { key: 'explore', label: 'explore', align: 'right', value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{inf(r.explore)}</span> },
  { key: 'score', label: 'score', align: 'right', value: (r) => r.score ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{inf(r.score)}</span> },
  { key: 'chosen', label: 'chosen', value: (r) => (r.chosen ? 1 : 0), render: (r) => (r.chosen ? <Chip state="ok">chosen</Chip> : <span className="text-dim">—</span>) },
]

const actionCols: Col<MatrixCell>[] = [
  { key: 'type', label: 'action type', value: (r) => r.action_type.label, render: (r) => r.action_type.label },
  { key: 'rate', label: 'confirmed', value: (r) => (r.rate.of ? r.rate.n / r.rate.of : 2), render: (r) => <Bar rate={r.rate} width={140} /> },
  { key: 'tried', label: 'attempted', align: 'right', value: (r) => r.rate.of, render: (r) => n(r.rate.of) },
  { key: 'per_try', label: 'per try', unit: 'ms', align: 'right', value: (r) => r.per_try_ms ?? 0, render: (r) => n(r.per_try_ms) },
]

export function StartDetail() {
  const { campaignMap = '', faction = '' } = useParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<Detail>(`/api/campaigns/starts/${encodeURIComponent(campaignMap)}/${encodeURIComponent(faction)}`, [campaignMap, faction])
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const s = data.start
  const lord = s.leader ?? s.faction.label
  const campaigns = data.campaigns ?? []
  const trajectory = data.trajectory ?? []
  const popBins = data.population_bins ?? []
  const actions = data.actions ?? []
  const asc = [...campaigns].reverse()
  const bars = asc.map((c, i) => ({
    id: c.campaign.raw,
    label: `campaign ${i + 1}`,
    settlements: c.settlements_gained ?? 0,
    levels: c.levels_gained ?? 0,
    picked: c.pick_id != null,
    sub: `${clock(c.ts)} · ${c.turns ?? '—'} turns${c.outcome ? ` · ${c.outcome.label}` : ''}`,
  }))
  const traj = trajectory.map((p) => ({ id: p.pick_id, rank: p.rank, ranked: p.ranked, chosen: p.chosen, c: p.c, sub: `n ${p.n} · score ${inf(p.score)}` }))
  const tiles = [
    { label: 'plays in window', value: s.n_window, sub: `${s.n} all time` },
    { label: 'mean reward', value: s.mean == null ? null : n(s.mean, 2), sub: s.std == null ? undefined : `± ${n(s.std, 2)} std` },
    { label: 'entropy', value: s.entropy == null ? null : n(s.entropy, 2), unit: 'bits' },
    { label: 'best reward', value: s.best, sub: s.zero_rate?.of ? `${((100 * s.zero_rate.n) / s.zero_rate.of).toFixed(0)}% zero` : undefined },
    { label: 'blend', value: s.blend == null ? null : `${s.blend >= 0 ? '+' : ''}${n(s.blend, 3)}` },
    { label: 'explore', value: s.n_window ? inf(s.explore) : '∞' },
    { label: 'score', value: s.in_pool ? inf(s.score) : null, sub: s.rank == null ? undefined : `rank #${s.rank}` },
    { label: 'UCB picks', value: s.picks, sub: s.picks_ago == null ? 'never chosen' : `last ${s.picks_ago} picks ago` },
    { label: 'avg turns', value: s.avg_turns == null ? null : n(s.avg_turns, 1), sub: s.sec_per_turn == null ? undefined : `${n(s.sec_per_turn, 1)} s/turn` },
    { label: 'confirmed', value: s.confirm_rate?.of ? `${((100 * s.confirm_rate.n) / s.confirm_rate.of).toFixed(0)}%` : null, sub: s.confirm_rate ? `${n(s.confirm_rate.n)}/${n(s.confirm_rate.of)} actions` : undefined },
  ]
  const thisBins = s.reward_bins ?? []
  return (
    <div className="space-y-7">
      <div>
        <Link to="/campaigns?view=starts" className="text-dim hover:text-fg inline-flex items-center gap-1 text-xs">
          <ArrowLeft className="size-3.5" /> starts
        </Link>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <span className="text-lg font-semibold">{lord}</span>
          <IdentLabel ident={s.faction} className="text-dim" />
          {s.campaign_map && (
            <span className="inline-flex items-center gap-1.5 text-xs">
              <span className="inline-block size-2 rounded-full" style={{ background: mapColor(s.campaign_map.raw) }} />
              {s.campaign_map.label}
            </span>
          )}
          {!s.in_pool && <Chip state="warn">not in the presave pool</Chip>}
        </h1>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value ?? null, unit: t.unit ?? null, sub: t.sub ?? null, state: 'neutral', spark: [] }} />
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="reward per campaign" scope={{ text: 'every campaign of this start in play order', detail: 'ring under a bar: booted by a UCB pick' }}>
          <ChartFrame table={<DataTable rows={campaigns} cols={campaignCols} rowId={(r) => r.campaign.raw} onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)} dense pageSize={10} emptyWhat="no campaign recorded" />}>
            <div className="p-2">
              <RewardBars items={bars} onSelect={(id) => navigate(`/campaigns/${encodeURIComponent(id)}`)} />
            </div>
          </ChartFrame>
        </Section>

        <Section title="reward distribution vs the pool" scope={{ text: `share of campaigns at each integer reward, window of ${data.window}` }}>
          <ChartFrame
            table={
              <DataTable
                rows={thisBins.map((c, i) => ({ x: i, a: c, b: popBins[i] ?? 0 }))}
                cols={[
                  { key: 'x', label: 'reward', align: 'right', value: (r) => r.x, render: (r) => r.x },
                  { key: 'a', label: lord, align: 'right', value: (r) => r.a, render: (r) => n(r.a) },
                  { key: 'b', label: 'pool', align: 'right', value: (r) => r.b, render: (r) => n(r.b) },
                ]}
                rowId={(r) => String(r.x)}
                dense
                pageSize={10}
                emptyWhat="no campaign in the window"
              />
            }
          >
            <div className="p-2">
              <ShareHistogram a={thisBins} b={popBins} aLabel={lord} bLabel="every start" />
            </div>
          </ChartFrame>
        </Section>
      </div>

      <Section title="rank over picks" scope={{ text: 'where the selector ranked this start at every pick it scored', detail: 'click a point to open that pick' }}>
        <ChartFrame table={<DataTable rows={trajectory} cols={trajCols} rowId={(r) => String(r.pick_id)} onRowClick={(r) => navigate(`/campaigns?view=selector&pick=${r.pick_id}`)} initialSort={{ key: 'pick', desc: true }} dense pageSize={10} emptyWhat="no pick has ranked this start" />}>
          <div className="p-2">
            <Trajectory points={traj} onSelect={(id) => navigate(`/campaigns?view=selector&pick=${id}`)} />
          </div>
        </ChartFrame>
      </Section>

      <Section title="its campaigns" scope={{ text: 'newest first' }}>
        <DataTable rows={campaigns} cols={campaignCols} rowId={(r) => r.campaign.raw} onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)} pageSize={10} emptyWhat="no campaign recorded for this start" />
      </Section>

      <Section title="action types for this faction" scope={{ text: `every ${s.faction.label} attempt in this run dir, worst confirmed first`, detail: mapShort(s.campaign_map?.raw, s.campaign_map?.label) }}>
        <DataTable rows={actions} cols={actionCols} rowId={(r) => r.action_type.raw} pageSize={10} emptyWhat="no action attempted by this faction" />
      </Section>
    </div>
  )
}
