import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  CountText,
  EntityLink,
  ErrorState,
  MetricTile,
  RateText,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import {
  useApi,
  type ActionsPage,
  type DecisionRow,
  type DecisionsPage,
  type MenusPage,
  type Schemas,
  type TimelinePage,
} from '@/lib/api'
import { isRetiredArm } from '@/lib/arms'
import { clock, ms, n, stateFill } from '@/lib/format'
import { cn } from '@/lib/utils'

const VIEWS = [
  { key: 'log', label: 'log', asks: 'what did it choose' },
  { key: 'actions', label: 'by action type', asks: 'is the game accepting what it picks' },
  { key: 'diplomacy', label: 'diplomacy', asks: 'what each source picks, per model version' },
  { key: 'menus', label: 'blocking screens', asks: 'how does it answer menus' },
  { key: 'timeline', label: 'timeline', asks: 'where did the time go' },
]

const logCols: Col<DecisionRow>[] = [
  {
    key: 'id',
    label: '#',
    align: 'right',
    group: 'decision',
    value: (r) => r.decision_id,
    render: (r) => <span className="text-dim">{r.decision_id}</span>,
  },
  { key: 'time', label: 'time', group: 'decision', value: (r) => r.ts ?? 0, render: (r) => clock(r.ts) },
  {
    key: 'campaign',
    label: 'campaign',
    group: 'decision',
    value: (r) => r.campaign?.label ?? '',
    render: (r) =>
      r.campaign ? (
        <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw}>
          {r.campaign.label}
        </EntityLink>
      ) : (
        <span className="text-dim">—</span>
      ),
  },
  {
    key: 'turn',
    label: 'turn',
    align: 'right',
    group: 'decision',
    value: (r) => r.turn ?? 0,
    render: (r) => r.turn ?? '—',
  },
  {
    key: 'action',
    label: 'action',
    group: 'what it chose',
    value: (r) => r.action_type?.label ?? '',
    render: (r) => r.action_type?.label ?? '—',
  },
  {
    key: 'key',
    label: 'key',
    group: 'what it chose',
    value: (r) => r.action_key ?? '',
    render: (r) => <span className="num text-dim text-2xs">{r.action_key ?? '—'}</span>,
  },
  {
    key: 'offers',
    label: 'offers',
    align: 'right',
    group: 'what it chose',
    optional: true,
    value: (r) => r.offers ?? 0,
    render: (r) => n(r.offers),
  },
  {
    key: 'result',
    label: 'result',
    group: 'outcome',
    value: (r) => r.result?.label ?? '',
    render: (r) => (r.result ? <Chip state={r.result_state ?? 'neutral'}>{r.result.label}</Chip> : '—'),
  },
  {
    key: 'refusal',
    label: 'refusal',
    group: 'outcome',
    optional: true,
    value: (r) => r.refusal?.label ?? '',
    render: (r) => <span className="text-dim">{r.refusal?.label ?? '—'}</span>,
  },
  {
    key: 'cat_rank',
    label: 'rank',
    align: 'right',
    group: 'greedy_catboost',
    value: (r) => r.cat_rank ?? 0,
    render: (r) => (r.cat_rank === null || r.cat_rank === undefined ? '—' : r.cat_rank),
  },
  {
    key: 'gnn_rank',
    label: 'rank',
    align: 'right',
    group: 'marwil_gnn',
    optional: true,
    value: (r) => r.gnn_rank ?? 0,
    render: (r) => (r.gnn_rank === null || r.gnn_rank === undefined ? '—' : r.gnn_rank),
  },
  {
    key: 'ggnn_rank',
    label: 'rank',
    align: 'right',
    group: 'greedy_gnn',
    value: (r) => r.ggnn_rank ?? 0,
    render: (r) => (r.ggnn_rank === null || r.ggnn_rank === undefined ? '—' : r.ggnn_rank),
  },
  {
    key: 'ggnn_score',
    label: 'reward',
    align: 'right',
    group: 'greedy_gnn',
    optional: true,
    value: (r) => r.ggnn_score ?? 0,
    render: (r) => n(r.ggnn_score, 2),
  },
  {
    key: 'policy',
    label: 'picked by',
    group: 'picked by',
    value: (r) => r.policy?.label ?? '',
    render: (r) => r.policy?.label ?? '—',
  },
  {
    key: 'latency',
    label: 'latency',
    unit: 'ms',
    align: 'right',
    group: 'picked by',
    optional: true,
    value: (r) => r.latency_ms ?? 0,
    render: (r) => ms(r.latency_ms),
  },
]

function Log() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const offset = Number(params.get('offset') ?? 0)
  const actionType = params.get('action_type') ?? ''
  const policy = params.get('policy') ?? ''
  const result = params.get('result') ?? ''

  const qs = new URLSearchParams({ offset: String(offset), limit: '200' })
  if (actionType) qs.set('action_type', actionType)
  if (policy) qs.set('policy', policy)
  if (result) qs.set('result', result)

  const { data, error, loading, reload } = useApi<DecisionsPage>(`/api/decisions?${qs}`)
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={12} />

  const setFilter = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    next.delete('offset')
    setParams(next, { replace: true })
  }

  return (
    <Section title="every action" scope={data.scope}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Card className="px-3 py-1.5">
          <CountText count={data.total} />
        </Card>
        <select
          value={actionType}
          onChange={(e) => setFilter('action_type', e.target.value)}
          className="border-line bg-surface rounded-md border px-2 py-1 text-xs"
        >
          <option value="">every action type</option>
          {data.action_types.map((a) => (
            <option key={a.raw} value={a.raw}>
              {a.label}
            </option>
          ))}
        </select>
        <select
          value={policy}
          onChange={(e) => setFilter('policy', e.target.value)}
          className="border-line bg-surface rounded-md border px-2 py-1 text-xs"
        >
          <option value="">every policy</option>
          {data.policies.map((p) => (
            <option key={p.raw} value={p.raw}>
              {p.label}
            </option>
          ))}
        </select>
        <select
          value={result}
          onChange={(e) => setFilter('result', e.target.value)}
          className="border-line bg-surface rounded-md border px-2 py-1 text-xs"
        >
          <option value="">every result</option>
          <option value="confirmed">confirmed</option>
          <option value="refused">refused</option>
          <option value="awaiting">awaiting execution</option>
        </select>
        <div className="ml-auto flex items-center gap-1">
          <button
            disabled={offset <= 0}
            onClick={() => setFilter('offset', String(Math.max(0, offset - 200)))}
            className="border-line rounded-md border px-2 py-1 text-2xs disabled:opacity-40"
          >
            newer
          </button>
          <span className="text-dim num text-2xs">
            {offset + 1}–{Math.min(offset + data.rows.length, data.total.value)} of{' '}
            {n(data.total.value)}
          </span>
          <button
            disabled={offset + data.rows.length >= data.total.value}
            onClick={() => setFilter('offset', String(offset + 200))}
            className="border-line rounded-md border px-2 py-1 text-2xs disabled:opacity-40"
          >
            older
          </button>
        </div>
      </div>
      <DataTable
        rows={data.rows}
        cols={logCols}
        rowId={(r) => String(r.decision_id)}
        onRowClick={(r) => navigate(`/decisions/${r.decision_id}`)}
        dense
        searchPlaceholder="search action key, campaign…"
        emptyWhat="no action matches this filter"
        emptyWhy="widen the filters above, or the run has not taken an action yet"
      />
    </Section>
  )
}

type ActionTypeRow = Schemas['ActionTypeRow']
type PolicyRow = Schemas['PolicyRow']

function Actions() {
  const { data, error, loading, reload } = useApi<ActionsPage>('/api/decisions/actions')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />

  const typeCols: Col<ActionTypeRow>[] = [
    {
      key: 'type',
      label: 'action type',
      value: (r) => r.action_type.label,
      render: (r) => r.action_type.label,
    },
    {
      key: 'rate',
      label: 'confirmed',
      value: (r) => (r.rate.of ? r.rate.n / r.rate.of : 2),
      render: (r) => <Bar rate={r.rate} width={150} />,
    },
    {
      key: 'refusals',
      label: 'most common refusals',
      value: (r) => r.refusals?.length ?? 0,
      render: (r) => (
        <span className="flex flex-wrap gap-1">
          {(r.refusals ?? []).map((x) => (
            <Chip key={x.raw} state="neutral">
              {x.label}
            </Chip>
          ))}
          {!r.refusals?.length && <span className="text-dim">none</span>}
        </span>
      ),
    },
  ]

  const polCols: Col<PolicyRow>[] = [
    { key: 'policy', label: 'policy', value: (r) => r.policy.label, render: (r) => r.policy.label },
    { key: 'picks', label: 'picks', align: 'right', value: (r) => r.picks, render: (r) => n(r.picks) },
    {
      key: 'share',
      label: 'share of the mix',
      value: (r) => (r.share.of ? r.share.n / r.share.of : 0),
      render: (r) => <Bar rate={r.share} />,
    },
    {
      key: 'note',
      label: '',
      value: (r) => r.note ?? '',


      render: (r) => (r.note ? <span className="text-dim text-2xs">{r.note}</span> : null),
    },
  ]

  return (
    <div className="space-y-6">
      <Section title="confirm rate" scope={data.scope}>
        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          {data.tiles.map((m) => (
            <MetricTile key={m.label} metric={m} />
          ))}
        </div>
        <DataTable
          rows={data.by_type}
          cols={typeCols}
          rowId={(r) => r.action_type.raw}
          emptyWhat="no action attempted yet"
        />
      </Section>

      <Section
        title="who is picking"
        scope={{ text: 'every pick recorded in this run dir, by policy' }}
      >
        <DataTable rows={data.policies} cols={polCols} rowId={(r) => r.policy.raw} dense emptyWhat="no pick recorded" />
      </Section>

      {}
      <Section
        title="denominators on this page"
        scope={{ text: 'three counts of actions that are not the same count' }}
      >
        <div className="grid gap-2 sm:grid-cols-3">
          {(data.denominators ?? []).map((c) => (
            <Card key={c.population} className="px-3 py-2">
              <CountText count={c} />
            </Card>
          ))}
        </div>
      </Section>
    </div>
  )
}

type InterruptRow = Schemas['InterruptRow']
type ArmCoverage = Schemas['ArmCoverage']

function Menus() {
  const { data, error, loading, reload } = useApi<MenusPage>('/api/decisions/menus')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />

  const cols: Col<InterruptRow>[] = [
    { key: 'time', label: 'time', value: (r) => r.ts ?? 0, render: (r) => clock(r.ts) },
    { key: 'kind', label: 'screen', value: (r) => r.kind.label, render: (r) => r.kind.label },
    {
      key: 'campaign',
      label: 'campaign',
      value: (r) => r.campaign?.label ?? '',
      render: (r) =>
        r.campaign ? (
          <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw}>
            {r.campaign.label}
          </EntityLink>
        ) : (
          <span className="text-dim">—</span>
        ),
    },
    {
      key: 'result',
      label: 'result',
      value: (r) => r.result?.label ?? '',
      render: (r) => (r.result ? <Chip state={r.result_state ?? 'neutral'}>{r.result.label}</Chip> : '—'),
    },
    {
      key: 'chosen',
      label: 'chosen',
      value: (r) => r.chosen?.label ?? '',
      render: (r) => r.chosen?.label ?? '—',
    },
    {
      key: 'options',
      label: 'options and their scores',


      value: (r) => (r.options ?? []).length,
      render: (r) => (
        <div className="flex flex-wrap gap-1.5">
          {(r.options ?? []).map((o) => (
            <span
              key={o.label.raw}
              className={cn(
                'border-line rounded border px-1.5 py-0.5 text-2xs',
                o.chosen && 'border-ok text-ok font-semibold',
              )}
            >
              {o.label.label}
              {o.exploit !== null && o.exploit !== undefined && (
                <span className="num text-cat ml-1" title="greedy_catboost exploit score">
                  cat {o.exploit.toFixed(2)}
                </span>
              )}
            </span>
          ))}
          {!(r.options ?? []).length && <span className="text-dim">no options recorded</span>}
        </div>
      ),
    },
    {
      key: 'policy',
      label: 'picked by',
      value: (r) => r.policy?.label ?? '',
      render: (r) => r.policy?.label ?? '—',
    },
  ]

  const scorers = Array.from(
    new Set(data.coverage.flatMap((c) => Object.keys(c.scored ?? {}))),
  )
    .filter((arm) => !isRetiredArm(arm))
    .sort()
  const covCols: Col<ArmCoverage>[] = [
    { key: 'screen', label: 'screen', value: (r) => r.screen.label, render: (r) => r.screen.label },
    { key: 'rows', label: 'seen', align: 'right', value: (r) => r.rows, render: (r) => n(r.rows) },
    ...scorers.map(
      (arm): Col<ArmCoverage> => ({
        key: `scored_${arm}`,
        label: arm,
        unit: 'scored',
        align: 'right',
        value: (r) => r.scored?.[arm] ?? 0,
        render: (r) => n(r.scored?.[arm] ?? 0),
      }),
    ),
    {
      key: 'compared',
      label: 'two or more arms scored',
      align: 'right',
      value: (r) => r.compared ?? 0,
      render: (r) => n(r.compared ?? 0),
    },
    {
      key: 'agree',
      label: 'all of them picked the same',
      value: (r) => (r.agree?.of ? r.agree.n / r.agree.of : -1),
      render: (r) => <Bar rate={r.agree ?? null} />,
    },
  ]

  return (
    <div className="space-y-6">
      <Section title="blocking screens" scope={data.scope}>
        <div className="mb-3 flex flex-wrap gap-2">
          <Card className="px-3 py-1.5">
            <CountText count={data.total} />
          </Card>
          {data.by_screen.map((c) => (
            <Card key={c.noun} className="px-3 py-1.5">
              <span className="num font-semibold">{n(c.value)}</span>{' '}
              <span className="text-dim text-2xs">{c.noun}</span>
            </Card>
          ))}
        </div>
        <DataTable
          rows={data.rows}
          cols={cols}
          rowId={(r) => String(r.interrupt_id)}
          searchPlaceholder="search screen, option…"
          emptyWhat="no blocking screen recorded in this run dir"
        />
      </Section>

      <Section
        title="arm coverage"
        scope={{
          text: 'how often each arm scored a screen, and how often they agreed',
          detail:
            'only greedy_catboost has an interrupt model now; scores from retired arms are from older runs',
        }}
      >
        <DataTable rows={data.coverage} cols={covCols} rowId={(r) => r.screen.raw} dense emptyWhat="no screen scored yet" />
      </Section>
    </div>
  )
}

const PHASE_COLOR: Record<string, string> = {
  collect: 'bg-cat',
  queue: 'bg-gnn',
  score: 'bg-warn',
  verify: 'bg-ok',
}

function Timeline() {
  const { data, error, loading, reload } = useApi<TimelinePage>('/api/decisions/timeline')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />

  return (
    <Section title="timeline" scope={data.scope}>
      {}
      <div className="mb-3 flex flex-wrap gap-3">
        {data.phase_legend.map((l) => {
          const key = l.split(' ')[0]
          return (
            <span key={l} className="text-dim flex items-center gap-1.5 text-2xs">
              <span className={cn('inline-block h-2.5 w-4 rounded-sm', PHASE_COLOR[key] ?? 'bg-dim')} />
              {l}
            </span>
          )
        })}
      </div>
      <div className="space-y-4">
        {data.lanes.map((lane) => {
          const max = Math.max(...lane.actions.map((a) => a.total_ms ?? 0), 1)
          return (
            <Card key={`${lane.campaign.raw}-${lane.turn}`} className="overflow-hidden">
              <div className="border-line bg-raised/40 flex flex-wrap items-baseline gap-3 border-b px-3 py-1.5">
                <EntityLink
                  to={`/campaigns/${encodeURIComponent(lane.campaign.raw)}`}
                  title={lane.campaign.raw}
                  className="text-xs font-semibold"
                >
                  {lane.campaign.label}
                </EntityLink>
                <span className="text-dim text-2xs">turn {lane.turn}</span>
                <RateText rate={lane.confirmed} />
                <span className="text-dim ml-auto num text-2xs">{lane.in_turn_s}s in turn</span>
              </div>
              <div className="divide-line/60 divide-y">
                {lane.actions.map((a) => (
                  <div key={a.decision_id} className="flex items-center gap-3 px-3 py-1">
                    <span className="text-dim num w-12 shrink-0 text-2xs">{a.decision_id}</span>
                    <span className="w-36 shrink-0 truncate text-2xs">
                      {a.action_type?.label ?? '—'}
                    </span>
                    <span className="flex min-w-0 flex-1 items-center gap-px">
                      {a.phases.map((p) => (
                        <span
                          key={p.phase}
                          title={`${p.phase} ${Math.round(p.ms)}ms`}
                          className={cn('h-2.5 rounded-sm', PHASE_COLOR[p.phase] ?? 'bg-dim')}
                          style={{ width: `${Math.max(1, (p.ms / max) * 100)}%` }}
                        />
                      ))}
                    </span>
                    <span className="num w-16 shrink-0 text-right text-2xs">{ms(a.total_ms)}</span>
                    <span
                      className={cn(
                        'w-2 shrink-0 rounded-full',
                        stateFill[a.result_state ?? 'neutral'],
                        'h-2',
                      )}
                      title={a.result?.label}
                    />
                  </div>
                ))}
              </div>
            </Card>
          )
        })}
        {!data.lanes.length && (
          <Card className="text-dim px-4 py-8 text-center text-sm">no action recorded yet</Card>
        )}
      </div>
    </Section>
  )
}

type DiplomacyPage = Schemas['DiplomacyPage']
type ModelVersion = Schemas['ModelVersion']

const ARM_COLOR: Record<string, string> = {
  greedy_catboost: 'var(--cat)',
  marwil_gnn: 'var(--gnn)',
  greedy_gnn: 'var(--ggnn)',
  random: 'var(--warn)',
  ruleset: 'var(--ok)',
}

const armColor = (raw: string) => ARM_COLOR[raw] ?? 'var(--dim)'

function versionShort(v: ModelVersion): string {
  if (!v.trained_ts) return v.trained ? v.version : 'pre'
  const d = new Date(v.trained_ts * 1000)
  const mmdd = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return v.trained ? `${mmdd} ${hhmm}` : `pre ${mmdd}`
}

function Diplomacy() {
  const [version, setVersion] = useState<string>('')
  const { data, error, loading, reload } = useApi<DiplomacyPage>(
    `/api/decisions/diplomacy${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    [version],
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />

  const rows = [...(data.rows ?? [])].sort((a, b) => (b.attempted ?? 0) - (a.attempted ?? 0))
  const counts = new Map<string, Map<string, number>>()
  const colTotals = new Map<string, number>()
  for (const r of rows)
    for (const c of r.by_source ?? []) {
      const att = c.attempted ?? 0
      if (!att) continue
      const byTerm = counts.get(c.source.raw) ?? new Map<string, number>()
      byTerm.set(r.term.raw, (byTerm.get(r.term.raw) ?? 0) + att)
      counts.set(c.source.raw, byTerm)
      colTotals.set(c.source.raw, (colTotals.get(c.source.raw) ?? 0) + att)
    }
  const sources = (data.sources ?? []).filter((s) => (colTotals.get(s.raw) ?? 0) > 0)
  const total = rows.reduce((t, r) => t + (r.attempted ?? 0), 0)

  return (
    <Section
      title="diplomacy"
      scope={{
        text: 'what each source picks',
        detail:
          'counts of attempted diplomatic actions by the source that picked them; pick a model version to see the mix while it was in force; the tint is the share of that source\u2019s picks',
      }}
    >
      <Card className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <label className="text-dim flex items-center gap-2 text-2xs">
          model version
          <select
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            className="bg-raised text-fg rounded border border-line px-2 py-0.5 text-2xs"
          >
            <option value="">every version</option>
            {(data.versions ?? []).map((v) => (
              <option key={v.version} value={v.version} title={v.label}>
                {versionShort(v)} · {n(v.campaigns)} campaigns
              </option>
            ))}
          </select>
        </label>
        <CountText count={data.attempts} />
      </Card>
      {rows.length === 0 ? (
        <Card className="text-dim px-3.5 py-6 text-center text-xs">
          no diplomatic action attempted{version ? ' under this model version' : ' yet'}
        </Card>
      ) : (
        <Card className="max-w-3xl overflow-hidden">
          <table className="w-full border-separate border-spacing-0 text-xs">
            <thead>
              <tr className="text-dim border-line text-2xs">
                <th className="px-3 py-1.5 text-left font-normal">proposal</th>
                {sources.map((s) => (
                  <th key={s.raw} className="min-w-20 px-2 py-1.5 text-right font-normal whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="inline-block size-2 rounded-sm" style={{ background: armColor(s.raw) }} />
                      {s.label}
                    </span>
                  </th>
                ))}
                <th className="num min-w-16 px-3 py-1.5 text-right font-normal">all sources</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.term.raw}>
                  <td className="border-line/60 border-t px-3 py-1.5">{r.term.label}</td>
                  {sources.map((s) => {
                    const v = counts.get(s.raw)?.get(r.term.raw) ?? 0
                    const of = colTotals.get(s.raw) ?? 0
                    const share = of ? v / of : 0
                    return (
                      <td
                        key={s.raw}
                        title={`${s.label} picked ${r.term.label} ${n(v)} times — ${(100 * share).toFixed(1)}% of its ${n(of)} diplomacy picks`}
                        className="num border-line/60 border-t px-2 py-1.5 text-right"
                        style={
                          v
                            ? { background: `color-mix(in oklab, ${armColor(s.raw)} ${Math.min(55, Math.round(110 * share))}%, transparent)` }
                            : undefined
                        }
                      >
                        {v ? n(v) : <span className="text-dim opacity-40">·</span>}
                      </td>
                    )
                  })}
                  <td className="num text-dim border-line/60 border-t px-3 py-1.5 text-right">{n(r.attempted)}</td>
                </tr>
              ))}
              <tr className="text-dim text-2xs">
                <td className="border-line border-t px-3 py-1.5">all picks</td>
                {sources.map((s) => (
                  <td key={s.raw} className="num border-line border-t px-2 py-1.5 text-right">
                    {n(colTotals.get(s.raw) ?? 0)}
                  </td>
                ))}
                <td className="num border-line border-t px-3 py-1.5 text-right">{n(total)}</td>
              </tr>
            </tbody>
          </table>
        </Card>
      )}
    </Section>
  )
}

export function Decisions() {
  const view = useSubView(VIEWS)
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'log' && <Log />}
      {view === 'actions' && <Actions />}
      {view === 'diplomacy' && <Diplomacy />}
      {view === 'menus' && <Menus />}
      {view === 'timeline' && <Timeline />}
    </div>
  )
}
