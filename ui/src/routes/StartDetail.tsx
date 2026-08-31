import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  EntityLink,
  ErrorState,
  IdentLabel,
  MetricTile,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { ChartFrame } from '@/components/charts'
import {
  Histogram,
  RewardBars,
  ShareHistogram,
  StackShares,
  mapColor,
} from '@/components/startcharts'
import {
  useApi,
  type ConquestStep,
  type MatrixCell,
  type OpeningBranch,
  type OpeningFamily,
  type OpeningOffer,
  type StartActions,
  type StartCampaign,
  type StartCampaignsPage,
  type StartDetail as Detail,
  type StartOpenings,
  type StartPerformance,
} from '@/lib/api'
import { clock, n } from '@/lib/format'
import { cn } from '@/lib/utils'

const TABS = [
  { key: 'performance', label: 'performance', asks: 'is this start earning when played' },
  { key: 'openings', label: 'openings', asks: 'what do the strategies do with it, and does it matter' },
  { key: 'campaigns', label: 'campaigns', asks: 'the individual runs' },
  { key: 'actions', label: 'actions', asks: 'is the machinery executing cleanly here' },
]

const BANDS = ['all', '1-3', '4-6', '7+']

const dash = (v: number | null | undefined, digits = 0) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>

const signedDelta = (v: number | null | undefined) =>
  v == null ? (
    <span className="text-dim">—</span>
  ) : (
    <span className={cn('num', v > 0 ? 'text-ok' : v < 0 ? 'text-bad' : 'text-dim')}>
      {v > 0 ? '+' : ''}
      {n(v, 2)}
    </span>
  )

function FamilyBoard({
  fam,
  onBranch,
}: {
  fam: OpeningFamily
  onBranch: (family: string, key: string, label: string) => void
}) {
  const [view, setView] = useState<'branches' | 'offers'>('branches')
  const rows: OpeningBranch[] = [...(fam.branches ?? []), ...(fam.pooled ? [fam.pooled] : [])]
  const branchCols: Col<OpeningBranch>[] = [
    {
      key: 'choice',
      label: 'first choice',
      value: (r) => r.label ?? r.key,
      render: (r) => <span title={r.key || undefined}>{r.label ?? r.key ?? '—'}</span>,
    },
    { key: 'key', label: 'key', optional: true, value: (r) => r.key, render: (r) => <span className="num text-dim text-2xs">{r.key || '—'}</span> },
    { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
    {
      key: 'share',
      label: 'share',
      align: 'right',
      value: (r) => r.share ?? 0,
      render: (r) => (r.share == null ? <span className="text-dim">—</span> : <span className="num text-dim">{n(r.share)}%</span>),
    },
    { key: 'avg', label: 'avg R', align: 'right', value: (r) => r.avg_reward ?? -1, render: (r) => dash(r.avg_reward, 2) },
    { key: 'delta', label: 'Δ mean', align: 'right', value: (r) => r.delta_mean ?? 0, render: (r) => signedDelta(r.delta_mean) },
    { key: 't', label: 'avg turns', align: 'right', value: (r) => r.avg_turns ?? 0, render: (r) => dash(r.avg_turns, 1) },
    { key: 'rpt', label: 'R/turn', align: 'right', value: (r) => r.reward_per_turn ?? 0, render: (r) => dash(r.reward_per_turn, 2) },
    {
      key: 'off',
      label: 'offered',
      align: 'right',
      value: (r) => r.offered,
      render: (r) => (r.offered ? <span className="num text-dim">{n(r.offered)}</span> : <span className="text-dim">—</span>),
    },
  ]
  const offerCols: Col<OpeningOffer>[] = [
    { key: 'k', label: 'on offer at the first decision', value: (r) => r.label ?? r.key, render: (r) => <span title={r.key}>{r.label ?? r.key}</span> },
    { key: 'key', label: 'key', optional: true, value: (r) => r.key, render: (r) => <span className="num text-dim text-2xs">{r.key}</span> },
    { key: 'offered', label: 'offered', align: 'right', value: (r) => r.offered, render: (r) => <span className="num">{n(r.offered)}</span> },
    { key: 'taken', label: 'taken', align: 'right', value: (r) => r.taken, render: (r) => (r.taken ? <span className="num">{n(r.taken)}</span> : <span className="text-warn">never</span>) },
    {
      key: 'rate',
      label: 'take rate',
      value: (r) => (r.offered ? r.taken / r.offered : 0),
      render: (r) => <Bar rate={{ n: r.taken, of: r.offered, noun: 'offers', population: 'of this key at the first decision' }} />,
    },
    { key: 'avg', label: 'avg R taken', align: 'right', value: (r) => r.avg_reward_taken ?? -1, render: (r) => dash(r.avg_reward_taken, 2) },
  ]
  return (
    <Section
      title={fam.label}
      scope={{
        text: `${fam.coverage.n} of ${fam.coverage.of} campaigns took one · ~${n(fam.avg_offers, 1)} on offer at the decision`,
        detail: fam.spread != null ? `best−worst spread ${n(fam.spread, 1)}R among branches n≥10` : 'no two branches reach n≥10',
      }}
      right={
        <div className="border-line flex overflow-hidden rounded-md border text-2xs">
          {(['branches', 'offers'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn('px-2 py-0.5', view === v ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg')}
            >
              {v === 'offers' ? 'on offer' : v}
            </button>
          ))}
        </div>
      }
    >
      {view === 'branches' ? (
        <DataTable
          rows={rows}
          cols={branchCols}
          rowId={(r) => r.key || 'other'}
          onRowClick={(r) => r.key && onBranch(fam.family, r.key, r.label ?? r.key)}
          dense
          pageSize={10}
          emptyWhat={`no campaign took a first ${fam.family}`}
        />
      ) : (
        <DataTable rows={fam.offers ?? []} cols={offerCols} rowId={(r) => r.key} dense pageSize={10} emptyWhat="no key was offered 10+ times at the first decision" />
      )}
    </Section>
  )
}

function OpeningsTab({ base }: { base: string }) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const band = params.get('band') ?? 'all'
  const { data, error, loading, reload } = useApi<StartOpenings>(
    `${base}/openings?band=${encodeURIComponent(band)}`,
    [band],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const setBand = (b: string) => {
    const next = new URLSearchParams(params)
    if (b === 'all') next.delete('band')
    else next.set('band', b)
    setParams(next, { replace: true })
  }
  const onBranch = (family: string, key: string) =>
    navigate(`?tab=campaigns&ff=${encodeURIComponent(family)}&fk=${encodeURIComponent(key)}`)
  const families = data.families ?? []
  const spreads = families.filter((f) => f.spread != null)
  const maxSpread = Math.max(0.1, ...spreads.map((f) => f.spread ?? 0))
  const conquestCols: Col<ConquestStep>[] = [
    { key: 'step', label: 'step', align: 'right', value: (r) => r.step, render: (r) => <span className="num">{r.step}</span> },
    { key: 'sett', label: 'settlement (most common)', value: (r) => r.label ?? r.key, render: (r) => <span title={r.key}>{r.label ?? r.key}</span> },
    {
      key: 'reached',
      label: 'reached',
      value: (r) => (r.of ? r.reached / r.of : 0),
      render: (r) => <Bar rate={{ n: r.reached, of: r.of, noun: 'campaigns', population: 'that attacked at least one settlement' }} width={110} />,
    },
    { key: 'turn', label: 'median turn', align: 'right', value: (r) => r.median_turn ?? 0, render: (r) => dash(r.median_turn) },
  ]
  return (
    <div className="space-y-7">
      {spreads.length > 0 && (
        <Card className="px-3.5 py-2.5">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-dim text-2xs uppercase tracking-wide">where the openings diverge</span>
            <span className="text-dim text-2xs">
              best−worst first choice, branches n≥10 · reward sd {n(data.sd_reward, 1)}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs">
            {spreads.map((f) => (
              <span key={f.family} className="inline-flex items-center gap-2">
                {f.family}
                <span
                  className="bg-accent inline-block h-2 rounded"
                  style={{ width: 8 + (110 * (f.spread ?? 0)) / maxSpread, opacity: 0.4 + (0.6 * (f.spread ?? 0)) / maxSpread }}
                />
                <b className="num">{n(f.spread, 1)}R</b>
              </span>
            ))}
          </div>
        </Card>
      )}
      <div className="text-dim flex flex-wrap items-center gap-2 text-2xs">
        <span>
          openings, not builds — campaigns are short rollouts · {data.campaigns} campaigns · mean R {n(data.mean_reward, 2)} · turn band:
        </span>
        {BANDS.map((b) => (
          <button
            key={b}
            onClick={() => setBand(b)}
            className={cn(
              'rounded-full px-2 py-0.5',
              band === b ? 'bg-raised text-fg font-semibold' : 'bg-surface border-line border hover:text-fg',
            )}
          >
            {b === 'all' ? 'all turns' : b}
          </button>
        ))}
      </div>
      {families.map((f) => (
        <FamilyBoard key={f.family} fam={f} onBranch={onBranch} />
      ))}
      <div className="grid gap-6 lg:grid-cols-2">
        <Section
          title="openings over time"
          scope={{ text: `share of first ${data.ribbon_family} per bucket of campaigns, play order` }}
        >
          <Card className="px-3 py-2">
            <StackShares buckets={(data.ribbon ?? []).map((b) => ({ label: b.label, shares: b.shares ?? [] }))} keys={data.ribbon_labels ?? []} />
          </Card>
        </Section>
        <Section
          title="conquest depth"
          scope={{
            text: 'a sequence, not a choice — targets on offer average ~1 per decision',
            detail: `${data.no_settlement} campaigns took no settlement`,
          }}
        >
          <DataTable rows={data.conquest ?? []} cols={conquestCols} rowId={(r) => String(r.step)} dense emptyWhat="no settlement was attacked" />
        </Section>
      </div>
    </div>
  )
}

function PerformanceTab({ base, lord }: { base: string; lord: string }) {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<StartPerformance>(`${base}/performance`, [], { live: false })
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const rewardBins = data.reward_bins ?? []
  const popBins = data.population_bins ?? []
  const bars = (data.bars ?? []).map((b) => ({
    id: b.id,
    label: b.label,
    settlements: b.settlements,
    levels: b.levels,
    picked: false,
    sub: `${clock(b.ts)}${b.n > 1 ? ` · ${b.n} campaigns · best ${n(b.total_max)}` : ''}`,
  }))
  return (
    <div className="space-y-7">
      <Section
        title="reward per campaign"
        scope={{
          text: `every campaign in play order${data.bucket > 1 ? `, bucketed ${data.bucket} per bar` : ''} · click a bar → its campaign`,
        }}
      >
        <Card className="px-3 py-2">
          <RewardBars items={bars} onSelect={(id) => navigate(`/campaigns/${encodeURIComponent(id)}`)} />
        </Card>
      </Section>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section
          title="reward distribution vs the pool"
          scope={{ text: `share of campaigns at each integer reward, window of ${n(data.window)}`, detail: data.pool_mean != null ? `pool mean ${n(data.pool_mean, 2)}` : undefined }}
        >
          <ChartFrame
            table={
              <DataTable
                rows={rewardBins.map((c, i) => ({ x: i, a: c, b: popBins[i] ?? 0 }))}
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
              <ShareHistogram a={rewardBins} b={popBins} aLabel={lord} bLabel="every start" />
            </div>
          </ChartFrame>
        </Section>
        <Section title="length &amp; outcome" scope={{ text: 'how long its campaigns live and how they end' }}>
          <Card className="space-y-3 px-3.5 py-3">
            <Histogram
              bins={(data.turns_hist ?? []).map((v, i) => ({ x: i + 1, counts: { n: v } }))}
              series={[{ key: 'n', label: 'campaigns', color: 'var(--accent)' }]}
              height={120}
              xLabel="turns reached"
            />
            <div className="grid grid-cols-2 gap-4">
              <table className="w-full text-xs">
                <tbody>
                  {(data.outcomes ?? []).map((o) => (
                    <tr key={o.outcome.raw}>
                      <td className="py-0.5">
                        <Chip state={o.state ?? 'neutral'}>{o.outcome.label}</Chip>
                      </td>
                      <td className="num py-0.5 text-right">{n(o.n)}</td>
                      <td className="num text-dim py-0.5 text-right">{n(o.avg_turns, 1)} t̄</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-dim text-2xs">
                    <td>reward by length</td>
                    <td className="text-right">n</td>
                    <td className="text-right">avg R</td>
                    <td className="text-right">R/turn</td>
                  </tr>
                </thead>
                <tbody>
                  {(data.bands ?? []).map((b) => (
                    <tr key={b.label}>
                      <td className="py-0.5">{b.label}</td>
                      <td className="num py-0.5 text-right">{n(b.n)}</td>
                      <td className="num py-0.5 text-right">{b.avg_reward == null ? '—' : n(b.avg_reward, 1)}</td>
                      <td className="num py-0.5 text-right">{b.reward_per_turn == null ? '—' : n(b.reward_per_turn, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data.reward_turns_r != null && (
              <div className="text-dim text-2xs">reward ↔ turns r ≈ {n(data.reward_turns_r, 2)}</div>
            )}
          </Card>
        </Section>
      </div>
    </div>
  )
}

function CampaignsTab({ base }: { base: string }) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<StartCampaignsPage>(`${base}/campaigns`, [], { live: false })
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const ff = params.get('ff')
  const fk = params.get('fk')
  const firstOf = (r: StartCampaign, fam: string) =>
    fam === 'research' ? r.first_research : fam === 'skills' ? r.first_skill : r.first_building
  const all = data.rows ?? []
  const rows = ff && fk ? all.filter((r) => firstOf(r, ff)?.raw === fk) : all
  const filterLabel = ff && fk ? (rows[0] ? firstOf(rows[0], ff)?.label : fk) : null
  const cols: Col<StartCampaign>[] = [
    { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
    {
      key: 'tag',
      label: 'campaign',
      value: (r) => r.campaign.tag ?? '',
      render: (r) => (
        <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw} className="num text-2xs">
          {r.campaign.tag ?? r.campaign.raw.slice(-6)}
        </EntityLink>
      ),
    },
    { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
    { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => <strong className="num">{n(r.reward)}</strong> },
    { key: 'sett', label: 'settlements', unit: 'gained', align: 'right', value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
    { key: 'lvl', label: 'lord levels', unit: 'gained', align: 'right', value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
    {
      key: 'outcome',
      label: 'outcome',
      value: (r) => r.outcome?.label ?? '',
      render: (r) => (r.outcome ? <Chip state={r.outcome_state ?? 'neutral'}>{r.outcome.label}</Chip> : <span className="text-dim">running</span>),
    },
    { key: 'why', label: 'why it ended', optional: true, value: (r) => r.ended_because ?? '', render: (r) => (r.ended_because ? <span className="text-2xs">{r.ended_because}</span> : <span className="text-dim">—</span>) },
    { key: 'decisions', label: 'decisions', align: 'right', value: (r) => r.decisions, render: (r) => n(r.decisions) },
    { key: 'confirm', label: 'confirmed', value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1), render: (r) => <Bar rate={r.confirm_rate ?? null} /> },
    { key: 'fb', label: 'first building', optional: true, value: (r) => r.first_building?.label ?? '', render: (r) => <span className="text-dim text-2xs">{r.first_building?.label ?? '—'}</span> },
    { key: 'fs', label: 'first skill', optional: true, value: (r) => r.first_skill?.label ?? '', render: (r) => <span className="text-dim text-2xs">{r.first_skill?.label ?? '—'}</span> },
    { key: 'fr', label: 'first research', optional: true, value: (r) => r.first_research?.label ?? '', render: (r) => <span className="text-dim text-2xs">{r.first_research?.label ?? '—'}</span> },
  ]
  return (
    <Section title="its campaigns" scope={{ text: 'newest first · an openings branch click lands here with its filter applied' }}>
      {ff && fk && (
        <div className="mb-2 flex items-center gap-2 text-2xs">
          <span className="text-dim">filter:</span>
          <button
            onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('ff')
              next.delete('fk')
              setParams(next, { replace: true })
            }}
            className="bg-accent-soft text-accent rounded-full px-2 py-0.5"
            title="clear this filter"
          >
            first {ff} = {filterLabel} ×
          </button>
          <span className="text-dim num">{rows.length} of {all.length}</span>
        </div>
      )}
      <DataTable
        rows={rows}
        cols={cols}
        rowId={(r) => r.campaign.raw}
        onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
        searchPlaceholder="search campaign…"
        pageSize={25}
        emptyWhat="no campaign recorded for this start"
      />
    </Section>
  )
}

function ActionsTab({ base, faction }: { base: string; faction: string }) {
  const { data, error, loading, reload } = useApi<StartActions>(`${base}/actions`, [], { live: false })
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const cols: Col<MatrixCell>[] = [
    { key: 'type', label: 'action type', value: (r) => r.action_type.label, render: (r) => r.action_type.label },
    {
      key: 'rate',
      label: 'confirmed',
      value: (r) => (r.rate.of ? r.rate.n / r.rate.of : 2),
      render: (r) => <Bar rate={r.rate} width={140} />,
    },
    { key: 'tried', label: 'attempted', align: 'right', value: (r) => r.rate.of, render: (r) => n(r.rate.of) },
    { key: 'per_try', label: 'per try', unit: 'ms', align: 'right', value: (r) => r.per_try_ms ?? 0, render: (r) => n(r.per_try_ms) },
    {
      key: 'counted',
      label: 'counted',
      value: (r) => (r.counted?.of ? r.counted.n / r.counted.of : 2),
      render: (r) => <Bar rate={r.counted ?? null} />,
    },
  ]
  return (
    <Section title="action types for this faction" scope={{ text: `every ${faction} attempt in this run dir, worst confirmed first` }}>
      <DataTable rows={data.cells ?? []} cols={cols} rowId={(r) => r.action_type.raw} pageSize={25} emptyWhat="no action attempted by this faction" />
    </Section>
  )
}

export function StartDetail() {
  const { campaignMap = '', faction = '' } = useParams()
  const base = `/api/campaigns/starts/${encodeURIComponent(campaignMap)}/${encodeURIComponent(faction)}`
  const tab = useSubView(TABS, 'tab')
  const [seen, setSeen] = useState<Record<string, boolean>>({})
  useEffect(() => {
    setSeen((s) => (s[tab] ? s : { ...s, [tab]: true }))
  }, [tab])
  const { data, error, loading, reload } = useApi<Detail>(base, [campaignMap, faction])
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const s = data.start
  const lord = s.leader ?? s.faction.label
  const last = data.last_played
  const tiles = [
    { label: 'plays', value: s.n, sub: `${s.n_window} in window` },
    { label: 'mean reward', value: s.mean == null ? null : n(s.mean, 2), sub: s.std == null ? undefined : `± ${n(s.std, 2)} std` },
    { label: 'best reward', value: s.best, sub: s.zero_rate?.of ? `${((100 * s.zero_rate.n) / s.zero_rate.of).toFixed(0)}% zero` : undefined },
    { label: 'avg turns', value: s.avg_turns == null ? null : n(s.avg_turns, 1), sub: s.sec_per_turn == null ? undefined : `${n(s.sec_per_turn, 1)} s/turn` },
    {
      label: 'confirmed',
      value: s.confirm_rate?.of ? `${((100 * s.confirm_rate.n) / s.confirm_rate.of).toFixed(0)}%` : null,
      sub: s.confirm_rate ? `${n(s.confirm_rate.n)}/${n(s.confirm_rate.of)} actions` : undefined,
    },
  ]
  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
          <span>
            <EntityLink to="/campaigns" className="text-dim">campaigns</EntityLink>
            <span className="text-dim"> / </span>
            <EntityLink to="/campaigns?view=starts" className="text-dim">starts</EntityLink>
            <span className="text-dim"> / {lord}</span>
          </span>
          <EntityLink to="/campaigns?view=selector" className="text-dim">selector view →</EntityLink>
        </div>
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
          {last && (
            <span className="text-dim ml-auto text-2xs">
              last played {clock(last.ts)}
              {last.outcome ? ` · ${last.outcome.label.toLowerCase()}` : ''}
              {last.reward != null ? `, reward ${n(last.reward)}` : ''}
            </span>
          )}
        </h1>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value ?? null, unit: null, sub: t.sub ?? null, state: 'neutral', spark: [] }} />
        ))}
      </div>

      <SubNav views={TABS} param="tab" />
      <div className={tab === 'performance' ? '' : 'hidden'}>{seen.performance && <PerformanceTab base={base} lord={lord} />}</div>
      <div className={tab === 'openings' ? '' : 'hidden'}>{seen.openings && <OpeningsTab base={base} />}</div>
      <div className={tab === 'campaigns' ? '' : 'hidden'}>{seen.campaigns && <CampaignsTab base={base} />}</div>
      <div className={tab === 'actions' ? '' : 'hidden'}>{seen.actions && <ActionsTab base={base} faction={s.faction.label} />}</div>
    </div>
  )
}
