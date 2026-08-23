import { useState } from 'react'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  CountText,
  EmptyState,
  ErrorState,
  Help,
  ModelKey,
  PairPicker,
  RangeMeter,
  armTag,
  RateText,
  Section,
  Skeleton,
} from '@/components/primitives'
import { ChartFrame, RewardTrend, RhoHistogram, RhoMatrix, RhoTrend } from '@/components/charts'
import { SubNav, useSubView } from '@/components/SubNav'
import {
  useApi,
  type AgreementPage,
  type AgreementSeriesPage,
  type AgreementSeriesPoint,
  type CorrelationsPage,
  type ForcingPage,
  type CampaignReward,
  type ModelsPage,
  type Schemas,
  type TrainingPage,
} from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

const VIEWS = [
  { key: 'disk', label: 'on disk', asks: 'what is trained right now' },
  { key: 'forcing', label: 'what each wants', asks: 'what does each arm pick' },
  { key: 'agreement', label: 'agreement', asks: 'do any two ranking arms rank alike' },
  { key: 'drift', label: 'over time', asks: 'has that agreement changed as they retrained' },
  { key: 'correlations', label: 'does it help', asks: 'does an arm track how it went' },
  { key: 'training', label: 'training', asks: 'what has been tried, and did the retrain help' },
]


const CURRENT_GROWTH_BASELINE = 'first_decision_snapshot->peak'


const MIX_ARMS: { key: string; model: boolean; cls: string }[] = [
  { key: 'greedy_catboost', model: true, cls: 'bg-cat' },
  { key: 'marwil_gnn', model: true, cls: 'bg-gnn' },
  { key: 'greedy_gnn', model: true, cls: 'bg-ggnn' },
  { key: 'random', model: false, cls: 'bg-dim' },
  { key: 'ruleset', model: false, cls: 'bg-warn' },
]

const INTERRUPT_ARMS = MIX_ARMS.filter((a) => ['greedy_catboost', 'random', 'ruleset'].includes(a.key))

function MixBar({ mix, arms = MIX_ARMS }: { mix: Record<string, unknown> | undefined; arms?: typeof MIX_ARMS }) {
  const vals = arms.map((a) => {
    const v = mix?.[a.key]
    return typeof v === 'number' ? v : 0
  })
  const total = vals.reduce((a, b) => a + b, 0)
  if (!total) return <span className="text-dim">—</span>
  return (
    <span className="flex items-center gap-2">
      <span className="flex h-2.5 w-24 overflow-hidden rounded">
        {arms.map((a, i) => (
          <span
            key={a.key}
            className={a.cls}
            style={{ width: `${(vals[i] / total) * 100}%` }}
            title={`${a.key} ${vals[i]}`}
          />
        ))}
      </span>
      <span className="num text-2xs text-dim whitespace-nowrap">
        {vals.map((v) => Math.round((v / total) * 100)).join('/')}
      </span>
    </span>
  )
}

function OnDisk() {
  const { data, error, loading, reload } = useApi<ModelsPage>('/api/models')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  return (
    <div className="space-y-6">
      <Section title="models on disk" scope={data.scope}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.cards.map((c) => (
            <Card key={c.name} className={cn('p-3.5', c.state === 'bad' && 'border-bad')}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold">{c.name}</span>
                <Chip state={c.state ?? 'neutral'}>{c.status}</Chip>
              </div>
              <p className="text-dim mt-1 text-2xs leading-relaxed">{c.role}</p>
              <dl className="divide-line/60 mt-2 divide-y">
                {(c.rows ?? []).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 py-1 text-2xs">
                    <dt className="text-dim">{k}</dt>
                    <dd className="num text-right">{v}</dd>
                  </div>
                ))}
              </dl>
              {c.trained_at && <div className="text-dim mt-2 text-2xs">written {c.trained_at}</div>}
              {c.note && <div className="text-bad mt-1 text-2xs">{c.note}</div>}
            </Card>
          ))}
        </div>
      </Section>

      <Section title="fit configuration" scope={{ text: 'read from the model metadata on disk' }}>
        <div className="grid gap-3 md:grid-cols-2">
          {data.fit.map((f) => (
            <Card key={f.family} className="p-3.5">
              <div className="text-sm font-semibold">{f.family}</div>
              <div className="text-dim text-2xs">{f.role}</div>
              <dl className="divide-line/60 mt-2 divide-y">
                {Object.entries(f.hyperparameters).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 py-1 text-2xs">
                    <dt className="text-dim">{k}</dt>
                    <dd className="num text-right">{String(v)}</dd>
                  </div>
                ))}
                {Object.entries(f.compute).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 py-1 text-2xs">
                    <dt className="text-dim">{k}</dt>
                    <dd className="num text-right">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          ))}
        </div>
      </Section>
    </div>
  )
}

function Forcing() {
  const [version, setVersion] = useState<string>('')
  const { data, error, loading, reload } = useApi<ForcingPage>(
    `/api/models/forcing${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    [version],
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  return (
    <Section title="what each model wants to do" scope={data.scope}>
      <Card className="mb-3 flex flex-wrap items-center gap-3 px-3 py-2">
        <label className="text-dim flex items-center gap-2 text-2xs">
          model version
          <select
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            className="bg-raised text-fg rounded border border-line px-2 py-0.5 text-2xs"
          >
            <option value="">every version</option>
            {(data.versions ?? []).map((v) => (
              <option key={v.version} value={v.version}>{v.label}</option>
            ))}
          </select>
        </label>
        <CountText count={data.decisions} />
      </Card>
      {}
      {!data.tiles.length ? (
        <EmptyState what="no model arm has picked anything yet" why={data.empty_reason} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {data.tiles.map((t) => (
            <Card key={t.model} className="p-3.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold">{t.model}</span>
                {t.favours && (
                  <span className="text-dim text-2xs">favours {t.favours.label}</span>
                )}
              </div>
              {!t.bars.length && (
                <div className="mt-3">
                  <EmptyState
                    what="no decision drawn by this arm yet"
                    why="its first pick in this run dir puts a bar here"
                  />
                </div>
              )}
              <div className="mt-3 space-y-1.5">
                {t.bars.map((b) => {
                  const pctv = b.share.of ? (100 * b.share.n) / b.share.of : 0
                  return (
                    <div key={b.action_type.raw} className="flex items-center gap-2">
                      <span className="w-32 shrink-0 truncate text-2xs">{b.action_type.label}</span>
                      <span className="bg-raised relative h-3 flex-1 rounded">
                        <span
                          className="bg-accent absolute inset-y-0 left-0 rounded"
                          style={{ width: `${pctv}%` }}
                        />
                        {}
                        {b.ci_lo !== null && b.ci_hi !== null && (
                          <span
                            className="bg-fg/50 absolute inset-y-0"
                            style={{
                              left: `${b.ci_lo}%`,
                              width: `${Math.max(0.5, (b.ci_hi ?? 0) - (b.ci_lo ?? 0))}%`,
                              opacity: 0.35,
                            }}
                            title={`95% interval ${b.ci_lo?.toFixed(1)}–${b.ci_hi?.toFixed(1)}%`}
                          />
                        )}
                      </span>
                      <span className="num w-24 shrink-0 text-right text-2xs">
                        {pctv.toFixed(0)}%{' '}
                        <span className="text-dim">
                          {b.share.n}/{b.share.of}
                        </span>
                      </span>
                    </div>
                  )
                })}
              </div>
            </Card>
          ))}
        </div>
      )}
    </Section>
  )
}

type AgreementRankRow = Schemas['AgreementRankRow']

function Agreement() {
  const [pair, setPair] = useState<string>('')
  const { data, error, loading, reload } = useApi<AgreementPage>(
    `/api/models/agreement${pair ? `?pair=${encodeURIComponent(pair)}` : ''}`,
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  const a = data.a
  const b = data.b

  const cols: Col<AgreementRankRow>[] = [
    {
      key: 'policy',
      label: 'picked by',
      value: (r) => r.picked_by.label,
      render: (r) => r.picked_by.label,
    },
    {
      key: 'decisions',
      label: 'decisions',
      align: 'right',
      value: (r) => r.decisions,
      render: (r) => n(r.decisions),
    },
    {
      key: 'a_rank',
      label: 'rank',
      align: 'right',
      group: a,
      value: (r) => r.a_rank ?? 0,
      render: (r) => n(r.a_rank, 1),
    },
    {
      key: 'a_pct',
      label: 'percentile',
      align: 'right',
      group: a,
      value: (r) => r.a_pct ?? 0,
      render: (r) => n(r.a_pct, 1),
    },
    {
      key: 'b_rank',
      label: 'rank',
      align: 'right',
      group: b,
      value: (r) => r.b_rank ?? 0,
      render: (r) => n(r.b_rank, 1),
    },
    {
      key: 'b_pct',
      label: 'percentile',
      align: 'right',
      group: b,
      value: (r) => r.b_pct ?? 0,
      render: (r) => n(r.b_pct, 1),
    },
    {
      key: 'rho',
      label: 'rho',
      align: 'right',
      group: 'agree',


      value: (r) => r.rho_median ?? undefined,
      sortUndefined: 'last',
      render: (r) =>
        r.rho_median === null || r.rho_median === undefined ? (
          <span className="text-dim">—</span>
        ) : (


          <span className="num">
            {r.rho_median >= 0 ? '+' : ''}
            {r.rho_median.toFixed(3)}
          </span>
        ),
    },
    {
      key: 'fell_back',
      label: 'fell back',
      align: 'right',
      optional: true,
      value: (r) => r.fell_back,
      render: (r) => (r.fell_back ? n(r.fell_back) : <span className="text-dim">—</span>),
    },
    {
      key: 'delta',
      label: 'gap',
      align: 'right',
      group: 'agree',
      value: (r) => r.delta_pct ?? 0,
      render: (r) =>
        r.delta_pct === null || r.delta_pct === undefined ? (
          '—'
        ) : (
          <span className={r.delta_pct > 0 ? 'text-ok' : r.delta_pct < 0 ? 'text-bad' : undefined}>
            {r.delta_pct > 0 ? '+' : ''}
            {r.delta_pct.toFixed(1)}
          </span>
        ),
    },
  ]

  const c = data.correlation


  const bins = data.rho_bins ?? []
  const secondary = data.secondary ?? []
  const excluded = c?.excluded ?? []
  const matrices = data.matrices ?? []
  return (
    <div className="space-y-7">
      {matrices.length > 0 && (
        <Section
          title="how alike the ranking arms are, pair by pair"
          scope={{
            text: 'median Spearman rho of each pair, over the decisions both ranked',
            detail: 'click a cell to open that pair below; blue is alike, amber is reversed, n is how many decisions the median stands on',
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            {matrices.map((m) => (
              <Card key={m.key} className="p-3.5">
                <div className="text-sm font-semibold">{m.title}</div>
                {m.detail && <div className="text-dim mb-2 text-2xs">{m.detail}</div>}
                <RhoMatrix arms={m.arms} cells={m.cells ?? []} selected={data.pair} onSelect={setPair} />
              </Card>
            ))}
          </div>
        </Section>
      )}
      <Section
        title={`do ${a} and ${b} rank alike`}
        scope={data.scope}
        right={
          <div className="flex items-center gap-3">
            <PairPicker pairs={data.pairs ?? []} value={data.pair} onChange={setPair} />
            <Help>
              Spearman rho compares the two arms' whole orderings of one decision, not just the
              action that was taken. Near +1 they are ranking on the same thing and the second
              is close to redundant; near 0 they are ranking on effectively unrelated criteria;
              near −1 one is the other reversed. None of the three is a fault by itself — which
              one you want is a question about the run. Every arm that stores a per-offer
              ranking can be paired with every other; a decision one of the pair never scored
              cannot appear.
            </Help>
          </div>
        }
      >
        <Freshness f={data.freshness} />
        {data.empty_reason || !c ? (
          <EmptyState what="nothing to compare yet" why={data.empty_reason} />
        ) : (
          <>
            <div className="mb-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              <Card className="px-3.5 py-3 sm:col-span-2">
                <div className="text-dim text-2xs uppercase tracking-wide">
                  spearman rho, median
                </div>
                <div className="num mt-0.5 text-xl leading-tight">
                  {c.rho_median === null || c.rho_median === undefined
                    ? '—'
                    : `${c.rho_median >= 0 ? '+' : ''}${c.rho_median.toFixed(3)}`}
                </div>
                <div className="mt-1">
                  <RangeMeter value={c.rho_median} band={[c.rho_q1, c.rho_q3]} width={180} />
                </div>
                <div className="text-dim mt-1 text-2xs">
                  over <span className="num">{n(c.compared.value)}</span> decisions · the
                  shaded part is the middle half of them
                </div>
              </Card>
              <Card className="px-3.5 py-3">
                <div className="text-dim text-2xs uppercase tracking-wide">rho, mean</div>
                <div className="num mt-0.5 text-xl leading-tight">
                  {c.rho_mean === null || c.rho_mean === undefined
                    ? '—'
                    : `${c.rho_mean >= 0 ? '+' : ''}${c.rho_mean.toFixed(3)}`}
                </div>
                <div className="text-dim mt-1 text-2xs">
                  pulled by the tails; the median beside it is not
                </div>
              </Card>
              <Card className="px-3.5 py-3">
                <div className="text-dim text-2xs uppercase tracking-wide">
                  both picked the same best action
                </div>
                <div className="mt-1">
                  <RateText rate={c.same_best} />
                </div>
                <div className="mt-1">
                  <Bar rate={c.same_best} />
                </div>
              </Card>
            </div>
            <div className="mb-3 flex flex-wrap gap-2">
              {excluded.map((e) => (
                <Card key={e.population} className="px-3 py-2">
                  <CountText count={e} />
                </Card>
              ))}
            </div>
          </>
        )}
      </Section>

      {!data.empty_reason && (
        <Section
          title="where each model ranked the action that was taken"
          scope={{
            text: 'grouped by the strategy that actually chose',
            detail:
              "rank is the taken action's position in that model's ordering; percentile is the same as a share, so decisions of different sizes compare",
          }}
          right={
            <div className="text-dim flex items-center gap-3 text-2xs">
              <ModelKey model={armTag(a)}>{a}</ModelKey>
              <ModelKey model={armTag(b)}>{b}</ModelKey>
            </div>
          }
        >
          <DataTable
            rows={data.rows}
            cols={cols}
            rowId={(r) => r.picked_by.raw}
            dense
            emptyWhat="no decision carries both ranks"
            emptyWhy={`a decision is only comparable once both ${a} and ${b} have scored the same offers`}
          />
        </Section>
      )}

      {bins.length > 0 && (
        <Section
          title="how alike, decision by decision"
          scope={{
            text: `one bar per 0.1 of rho, over the ${n(c?.compared.value ?? 0)} decisions compared`,
          }}
        >
          <ChartFrame
            table={
              <DataTable
                rows={bins}
                cols={binCols}
                rowId={(b) => String(b.lo)}
                dense
                emptyWhat="no distribution yet"
              />
            }
          >
            <RhoHistogram bins={bins} median={c?.rho_median ?? null} />
          </ChartFrame>
        </Section>
      )}

      {secondary.length > 0 && (
        <Section
          title="other ways of measuring the same thing"
          scope={{ text: 'secondary' }}
        >
          <DataTable
            rows={secondary}
            cols={secondaryCols}
            rowId={(r) => r.measure}
            dense
            emptyWhat="no secondary measures"
          />
        </Section>
      )}
    </div>
  )
}

function Freshness({ f }: { f: Schemas['AnalyticsFreshness'] }) {
  if (f.state === 'ok') return null
  return (
    <Card className={cn('mb-3 px-3 py-2 text-2xs', f.state === 'bad' ? 'border-bad' : 'border-warn')}>
      <Chip state={f.state ?? 'neutral'}>analytics {f.state}</Chip>{' '}
      {f.detail ?? `${f.behind.value} decisions behind`}
    </Card>
  )
}

const binCols: Col<Schemas['RhoBin']>[] = [
  { key: 'from', label: 'from', align: 'right', value: (b) => b.lo, render: (b) => b.lo.toFixed(1) },
  { key: 'to', label: 'to', align: 'right', value: (b) => b.hi, render: (b) => b.hi.toFixed(1) },
  {
    key: 'decisions',
    label: 'decisions',
    align: 'right',
    value: (b) => b.decisions,
    render: (b) => n(b.decisions),
  },
]

const secondaryCols: Col<Schemas['SecondaryMeasure']>[] = [
  { key: 'measure', label: 'measure', value: (r) => r.measure, render: (r) => r.measure },
  {
    key: 'value',
    label: 'value',
    align: 'right',
    value: (r) => r.value,
    render: (r) => (r.rate ? <Bar rate={r.rate} width={90} /> : <span className="num">{r.value}</span>),
  },
]

const trendCols: Col<AgreementSeriesPoint>[] = [
  { key: 'label', label: 'bucket', value: (p) => p.seq, render: (p) => p.label },
  {
    key: 'decisions',
    label: 'decisions',
    align: 'right',
    value: (p) => p.decisions.value,
    render: (p) => n(p.decisions.value),
  },
  {
    key: 'rho',
    label: 'rho',
    align: 'right',
    value: (p) => p.rho_median ?? undefined,
    sortUndefined: 'last',
    render: (p) =>
      p.rho_median === null || p.rho_median === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num">
          {p.rho_median >= 0 ? '+' : ''}
          {p.rho_median.toFixed(3)}
        </span>
      ),
  },
  {
    key: 'spread',
    label: 'middle half',
    align: 'right',
    value: (p) => p.rho_q1 ?? undefined,
    sortUndefined: 'last',
    render: (p) =>
      p.rho_q1 === null || p.rho_q1 === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num text-2xs">
          {p.rho_q1.toFixed(2)} … {(p.rho_q3 ?? 0).toFixed(2)}
        </span>
      ),
  },
  {
    key: 'gate',
    label: 'note',
    value: (p) => p.gate ?? '',
    render: (p) => (p.gate ? <span className="text-warn text-2xs">{p.gate}</span> : <span className="text-dim">—</span>),
  },
]

function Drift() {
  const [axis, setAxis] = useState<'window' | 'generation'>('window')
  const [pair, setPair] = useState<string>('')
  const { data, error, loading, reload } = useApi<AgreementSeriesPage>(
    `/api/models/agreement/series?axis=${axis}${pair ? `&pair=${encodeURIComponent(pair)}` : ''}`,
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  const points = data.points ?? []
  const generations = data.generations ?? []
  const drawable = points.some((p) => p.rho_median !== null && p.rho_median !== undefined)
  return (
    <div className="space-y-7">
      <Section
        title={`has the agreement of ${data.a} and ${data.b} changed`}
        scope={data.scope}
        right={
          <div className="flex flex-wrap items-center gap-3">
            <PairPicker pairs={data.pairs ?? []} value={data.pair} onChange={setPair} />
            <div className="flex items-center gap-1">
            {(['window', 'generation'] as const).map((a) => (
              <button
                key={a}
                onClick={() => setAxis(a)}
                className={cn(
                  'text-2xs rounded px-1.5 py-0.5',
                  axis === a ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg',
                )}
              >
                {a === 'window' ? 'over the run' : 'by generation'}
              </button>
            ))}
            </div>
          </div>
        }
      >
        <Freshness f={data.freshness} />
        {data.is_alignment && data.caveat && (
          <Card className="border-warn mb-3 px-3 py-2 text-2xs">
            {data.caveat}
            {data.ambiguous.value > 0 && (
              <div className="mt-1">
                <CountText count={data.ambiguous} />
              </div>
            )}
          </Card>
        )}
        {!drawable ? (
          <EmptyState
            what="no bucket has enough comparable decisions to draw"
            why={data.empty_reason}
          />
        ) : (
          <>
            <ChartFrame
              table={
                <DataTable
                  rows={points}
                  cols={trendCols}
                  rowId={(p) => String(p.seq)}
                  dense
                  emptyWhat="no bucket in this window"
                />
              }
              note={
                data.bucket_decisions
                  ? `one point per ${data.bucket_decisions} comparable decisions`
                  : undefined
              }
            >
              <RhoTrend points={points} marks={generations} />
            </ChartFrame>
            <div className="text-dim mt-2 flex flex-wrap items-center gap-4 text-2xs">
              <span className="flex items-center gap-1.5">
                <span className="bg-accent inline-block h-0.5 w-4 rounded-full" /> median rho per
                bucket
              </span>
              <span className="flex items-center gap-1.5">
                <span className="bg-accent/20 inline-block h-2.5 w-4 rounded-sm" /> the middle half
                of that bucket
              </span>
              <span className="flex items-center gap-1.5">
                <span className="text-dim">▲</span> a retrain finished near here
              </span>
            </div>
          </>
        )}
      </Section>
    </div>
  )
}

type CorrelationRow = Schemas['CorrelationRow']

const corrCols: Col<CorrelationRow>[] = [
  { key: 'arm', label: 'arm', value: (r) => r.arm.label, render: (r) => r.arm.label },
  {
    key: 'campaigns',
    label: 'campaigns',
    align: 'right',
    value: (r) => r.campaigns,
    render: (r) => n(r.campaigns),
  },
  {
    key: 'share',
    label: 'share',
    value: (r) => (r.share?.of ? r.share.n / r.share.of : -1),
    render: (r) => <Bar rate={r.share ?? null} width={70} />,
  },
  {
    key: 'reward_r',
    label: 'reward r',
    align: 'right',
    value: (r) => r.reward_r ?? -2,
    render: (r) =>
      r.reward_r === null || r.reward_r === undefined ? (
        <span className="text-dim text-2xs">{r.reward_gate ?? '—'}</span>
      ) : (
        <strong className="num">{n(r.reward_r, 3)}</strong>
      ),
  },
  {
    key: 'setts_r',
    label: 'settlements r',
    align: 'right',
    value: (r) => r.settlements_r ?? -2,
    render: (r) =>
      r.settlements_r === null || r.settlements_r === undefined ? (
        <span className="text-dim text-2xs">{r.settlements_gate ?? '—'}</span>
      ) : (
        n(r.settlements_r, 3)
      ),
  },
  {
    key: 'lord_r',
    label: 'lord level r',
    align: 'right',
    value: (r) => r.lord_r ?? -2,
    render: (r) =>
      r.lord_r === null || r.lord_r === undefined ? (
        <span className="text-dim text-2xs">{r.lord_gate ?? '—'}</span>
      ) : (
        n(r.lord_r, 3)
      ),
  },
]

function Correlations() {
  const [version, setVersion] = useState<string>('')
  const { data, error, loading, reload } = useApi<CorrelationsPage>(
    `/api/models/correlations${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    [version],
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  return (
    <Section title="does an arm's share track how the campaign went" scope={data.scope}>
      <Card className="mb-3 flex flex-wrap items-center gap-3 px-3 py-2">
        <label className="text-dim flex items-center gap-2 text-2xs">
          model version
          <select
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            className="bg-raised text-fg rounded border border-line px-2 py-0.5 text-2xs"
          >
            <option value="">every version</option>
            {(data.versions ?? []).map((v) => (
              <option key={v.version} value={v.version}>{v.label}</option>
            ))}
          </select>
        </label>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        {data.tiles.map((t) => (
          <div key={t.label}>
            <h3 className="mb-1.5 text-sm font-semibold">{t.label}</h3>
            <DataTable
              rows={t.rows}
              cols={corrCols}
              rowId={(r) => `${t.label}-${r.arm.raw}`}
              dense
              emptyWhat={`no arm has played a ${t.label} decision yet`}
            />
          </div>
        ))}
      </div>
    </Section>
  )
}

type TrialRow = Schemas['TrialRow']

function Training() {
  const { data, error, loading, reload } = useApi<TrainingPage>('/api/models/training')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />

  const cols: Col<TrialRow>[] = [
    {
      key: 'trial',
      label: 'trial',
      value: (r) => r.trial,
      render: (r) => (
        <span className="flex items-center gap-1.5 whitespace-nowrap">
          <span className="num text-2xs">{r.trial}</span>
          {r.live && <Chip state="ok">running</Chip>}
        </span>
      ),
    },
    {
      key: 'snapshots',
      label: 'updates',
      align: 'right',
      optional: true,
      value: (r) => r.snapshots ?? 1,
      render: (r) => n(r.snapshots ?? 1),
    },
    {
      key: 'ruleset',
      label: 'ruleset',
      optional: true,
      value: (r) => r.ruleset ?? '',
      render: (r) => r.ruleset ?? '—',
    },
    {
      key: 'mix',
      label: 'action mix',
      value: (r) => JSON.stringify(r.mix ?? {}),
      render: (r) => <MixBar mix={r.mix as Record<string, unknown> | undefined} />,
    },
    {
      key: 'interrupt_mix',
      label: 'interrupt mix',
      value: (r) => JSON.stringify(r.interrupt_mix ?? {}),
      render: (r) => (
        <MixBar mix={r.interrupt_mix as Record<string, unknown> | undefined} arms={INTERRUPT_ARMS} />
      ),
    },
    {
      key: 'campaigns',
      label: 'campaigns',
      align: 'right',
      group: 'what it played',
      value: (r) => r.campaigns ?? 0,
      render: (r) => n(r.campaigns),
    },
    {
      key: 'corpus',
      label: 'corpus rows',
      align: 'right',
      group: 'what it played',
      optional: true,
      value: (r) => r.corpus ?? 0,
      render: (r) => n(r.corpus),
    },
    {
      key: 'reward_camp',
      label: 'reward',
      unit: 'per campaign',
      align: 'right',
      group: 'result',
      value: (r) => r.reward_per_campaign ?? undefined,
      sortUndefined: 'last',
      render: (r) => (
        <span title="settlements gained + legendary lord levels gained, per campaign — the UCB reward">
          {n(r.reward_per_campaign, 2)}
        </span>
      ),
    },
    {
      key: 'sett_camp',
      label: 'settlements',
      unit: 'per campaign',
      align: 'right',
      group: 'result',
      value: (r) => r.settlements_per_campaign ?? 0,
      render: (r) => n(r.settlements_per_campaign, 2),
    },
    {
      key: 'lord_camp',
      label: 'lord levels',
      unit: 'per campaign',
      align: 'right',
      group: 'result',
      value: (r) => r.lord_per_campaign ?? 0,
      render: (r) => n(r.lord_per_campaign, 2),
    },
    {
      key: 'turns_camp',
      label: 'turns',
      unit: 'per campaign',
      align: 'right',
      group: 'result',
      value: (r) => r.turns_per_campaign ?? 0,
      render: (r) => n(r.turns_per_campaign, 1),
    },
    {
      key: 's_camp',
      label: 'seconds',
      unit: 'per campaign',
      align: 'right',
      group: 'result',
      value: (r) => r.seconds_per_campaign ?? 0,
      render: (r) => n(r.seconds_per_campaign, 1),
    },
    {
      key: 'grew',
      label: 'grew',
      group: 'result',
      value: (r) => r.grew?.n ?? undefined,
      sortUndefined: 'last',
      render: (r) => <Bar rate={r.grew ?? null} width={80} />,
    },
    {
      key: 'shrank',
      label: 'shrank',
      group: 'result',
      optional: true,
      value: (r) => r.shrank?.n ?? undefined,
      sortUndefined: 'last',
      render: (r) => <Bar rate={r.shrank ?? null} width={80} />,
    },
    {
      key: 'growth_baseline',
      label: 'baseline',
      group: 'result',
      optional: true,
      value: (r) => r.growth_baseline ?? '',
      render: (r) =>
        r.growth_baseline === CURRENT_GROWTH_BASELINE ? (
          <span className="text-dim text-2xs">snapshots → peak</span>
        ) : r.growth_baseline ? (
          <Chip state="warn" title="an older growth definition — not comparable with the rest">
            {r.growth_baseline}
          </Chip>
        ) : (
          <span className="text-dim">—</span>
        ),
    },
    ...MIX_ARMS.map(
      (a): Col<TrialRow> => ({
        key: `corr_${a.key}`,
        label: a.key,
        unit: 'r vs growth',
        align: 'right',
        group: 'share tracks growth',
        optional: true,
        value: (r) => r.growth_corr?.[a.key]?.r ?? undefined,
        sortUndefined: 'last',
        render: (r) => {
          const c = r.growth_corr?.[a.key]
          if (c?.r === null || c?.r === undefined)
            return (
              <span className="text-dim" title={c?.gate ?? 'not measured'}>
                —
              </span>
            )
          return (
            <span
              className={cn('num text-2xs', c.r > 0 ? 'text-ok' : c.r < 0 ? 'text-bad' : '')}
              title={`over ${c.over.value} ${c.over.noun} ${c.over.population}`}
            >
              {c.r.toFixed(2)}
            </span>
          )
        },
      }),
    ),
    {
      key: 'notes',
      label: 'outcomes',
      group: 'result',
      optional: true,
      value: (r) => r.notes ?? '',
      render: (r) => <span className="text-dim text-2xs">{r.notes ?? '—'}</span>,
    },
  ]

  const num = (v: number) => <span className="num text-2xs">{v}</span>
  const rewardCols: Col<CampaignReward>[] = [
    { key: 'seq', label: 'campaign', value: (r) => r.seq, render: (r) => num(r.seq) },
    {
      key: 'settlements',
      label: 'settlements',
      value: (r) => r.settlements,
      render: (r) => num(r.settlements),
    },
    {
      key: 'lord_level',
      label: 'lord level',
      value: (r) => r.lord_level,
      render: (r) => num(r.lord_level),
    },
    { key: 'vassals', label: 'vassals', value: (r) => r.vassals, render: (r) => num(r.vassals) },
    { key: 'allies', label: 'allies', value: (r) => r.allies, render: (r) => num(r.allies) },
    { key: 'total', label: 'total', value: (r) => r.total, render: (r) => num(r.total) },
  ]

  return (
    <div className="space-y-6">
      <div className="text-dim flex flex-wrap items-center gap-3 text-2xs">
        <span>mix bars, left to right:</span>
        {MIX_ARMS.map((a) => (
          <span key={a.key} className="flex items-center gap-1.5">
            <span className={cn('inline-block h-2.5 w-4 rounded-sm', a.cls)} />
            {a.key}
          </span>
        ))}
      </div>
      <Section title="reward per campaign">
        <ChartFrame
          table={
            <DataTable
              rows={data.reward}
              cols={rewardCols}
              rowId={(r) => String(r.seq)}
              emptyWhat="no campaign measured yet"
              emptyWhy="a campaign needs more than one reward row to show a gain"
            />
          }
        >
          <RewardTrend points={data.reward} />
        </ChartFrame>
      </Section>
      <Section title="experiment ledger" scope={data.scope}>
        <DataTable
          rows={data.trials}
          cols={cols}
          rowId={(r, i) => `${r.trial}-${i}`}
          searchPlaceholder="search trial, ruleset…"
          pageSize={10}
          emptyWhat="no trial recorded yet"
          emptyWhy="the ledger is written when a run completes"
        />
      </Section>
      <TrainingHistory data={data} />
    </div>
  )
}

type TrainingEvent = Schemas['TrainingEvent']

function TrainingHistory({ data }: { data: TrainingPage }) {
  const history = data.history ?? []
  const groups = data.group_order ?? []

  const metricCols: Col<TrainingEvent>[] = []
  for (const g of groups) {
    const keys: string[] = []
    for (const ev of history) {
      const bag = (ev.groups ?? {})[g] as Record<string, unknown> | undefined
      for (const k of Object.keys(bag ?? {})) if (!keys.includes(k)) keys.push(k)
    }
    for (const k of keys) {
      metricCols.push({
        key: `${g}.${k}`,
        label: k,
        group: g,
        align: 'right',
        optional: !(g === 'corpus' || k.includes('rmse') || k.includes('NLL')),
        value: (r) => {
          const v = ((r.groups ?? {})[g] as Record<string, unknown> | undefined)?.[k]
          return typeof v === 'number' ? v : String(v ?? '')
        },
        render: (r) => {
          const v = ((r.groups ?? {})[g] as Record<string, unknown> | undefined)?.[k]
          if (v === undefined || v === null) return <span className="text-dim">—</span>
          return typeof v === 'number' ? n(v, Number.isInteger(v) ? 0 : 4) : String(v)
        },
      })
    }
  }

  const cols: Col<TrainingEvent>[] = [
    { key: 'when', label: 'when', group: 'retrain', value: (r) => r.when ?? '', render: (r) => r.when ?? '—' },
    {
      key: 'trial',
      label: 'trial',
      group: 'retrain',
      value: (r) => r.trial ?? '',
      render: (r) => <span className="num text-2xs">{r.trial}</span>,
    },
    ...metricCols,
  ]

  return (
    <Section
      title="training history"
      scope={{
        text: 'one row per retrain, newest first',
        detail: 'read from the session reports; open the column picker for the full fit',
      }}
    >
      <DataTable
        rows={history}
        cols={cols}
        rowId={(r, i) => `${r.trial}-${i}`}
        dense
        emptyWhat="no retrain recorded yet"
        emptyWhy="a retrain is written when a run crosses its retrain interval"
      />
    </Section>
  )
}

export function Models() {
  const view = useSubView(VIEWS)
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'disk' && <OnDisk />}
      {view === 'forcing' && <Forcing />}
      {view === 'agreement' && <Agreement />}
      {view === 'drift' && <Drift />}
      {view === 'correlations' && <Correlations />}
      {view === 'training' && <Training />}
    </div>
  )
}
