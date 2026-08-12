import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  CountText,
  EmptyState,
  ErrorState,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import {
  useApi,
  type AgreementPage,
  type CorrelationsPage,
  type ForcingPage,
  type ModelsPage,
  type Schemas,
  type TrainingPage,
} from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

const VIEWS = [
  { key: 'disk', label: 'on disk', asks: 'what is trained right now' },
  { key: 'forcing', label: 'what each wants', asks: 'what does each arm pick' },
  { key: 'agreement', label: 'agreement', asks: 'do the two models agree' },
  { key: 'correlations', label: 'does it help', asks: 'does an arm track how it went' },
  { key: 'training', label: 'trials', asks: 'what has been tried' },
]

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
  const { data, error, loading, reload } = useApi<ForcingPage>('/api/models/forcing')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  return (
    <Section title="what each model wants to do" scope={data.scope}>
      <Card className="mb-3 px-3 py-2">
        <CountText count={data.decisions} />
      </Card>
      {/* A real empty state. The old panel rendered a bare em dash where a chart was
          promised and left the reader to guess whether it was broken or simply had no
          data yet. */}
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
                        {/* The interval is drawn so a 2-of-3 share cannot be read as
                            confidently as a 200-of-300 share. */}
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
  const { data, error, loading, reload } = useApi<AgreementPage>('/api/models/agreement')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />

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
      key: 'cat_rank',
      label: 'rank',
      align: 'right',
      group: 'tree model',
      value: (r) => r.cat_rank ?? 0,
      render: (r) => n(r.cat_rank, 1),
    },
    {
      key: 'cat_pct',
      label: 'percentile',
      align: 'right',
      group: 'tree model',
      value: (r) => r.cat_pct ?? 0,
      render: (r) => n(r.cat_pct, 1),
    },
    {
      key: 'gnn_rank',
      label: 'rank',
      align: 'right',
      group: 'graph model',
      value: (r) => r.gnn_rank ?? 0,
      render: (r) => n(r.gnn_rank, 1),
    },
    {
      key: 'gnn_pct',
      label: 'percentile',
      align: 'right',
      group: 'graph model',
      value: (r) => r.gnn_pct ?? 0,
      render: (r) => n(r.gnn_pct, 1),
    },
    {
      key: 'delta',
      label: 'gap',
      align: 'right',
      group: 'graph model',
      help: 'Graph minus tree, on the percentile. Positive means the graph model rated the taken action higher.',
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

  return (
    <Section title="do the two models agree" scope={data.scope}>
      {data.empty_reason ? (
        <EmptyState what="nothing to compare yet" why={data.empty_reason} />
      ) : (
        <>
          <div className="mb-3 grid gap-2 sm:grid-cols-3">
            {data.summary.map((s) => (
              <Card key={s.measure} className="px-3.5 py-3">
                <div className="text-dim text-2xs uppercase tracking-wide">{s.measure}</div>
                <div className="num mt-0.5 text-xl">{s.value}</div>
                {s.help && <div className="text-dim mt-1 text-2xs">{s.help}</div>}
              </Card>
            ))}
          </div>
          {data.warning && (
            <Card className="border-warn mb-3 px-3 py-2 text-2xs">{data.warning}</Card>
          )}
          <DataTable
            rows={data.rows}
            cols={cols}
            rowId={(r) => r.picked_by.raw}
            dense
            emptyWhat="no decision in the window carries both ranks"
          />
        </>
      )}
    </Section>
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
  { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns, render: (r) => n(r.turns) },
  {
    key: 'share',
    label: 'share',
    value: (r) => (r.share?.of ? r.share.n / r.share.of : -1),
    render: (r) => <Bar rate={r.share ?? null} width={70} />,
  },
  {
    key: 'setts_r',
    label: 'settlements r',
    align: 'right',
    // A correlation over too few points is refused and says why, rather than being
    // printed as though it were a finding.
    help: 'Pearson r between this arm\'s share of a turn and the settlements at that turn. Withheld below 12 paired points — a correlation over eight points is noise wearing a number.',
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
  const { data, error, loading, reload } = useApi<CorrelationsPage>('/api/models/correlations')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={6} />
  return (
    <Section title="does an arm's share track how the campaign went" scope={data.scope}>
      {/* Two tiles, each labelled and each checked against its OWN corpus counts. They
          share arm names, so a single search over a merged page finds the action table
          every time and never inspects the interrupt one. */}
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
    { key: 'trial', label: 'trial', value: (r) => r.trial, render: (r) => r.trial },
    {
      key: 'backend',
      label: 'backend',
      value: (r) => r.backend ?? '',
      render: (r) => r.backend ?? '—',
    },
    {
      key: 'ruleset',
      label: 'ruleset',
      value: (r) => r.ruleset ?? '',
      render: (r) => r.ruleset ?? '—',
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
      value: (r) => r.corpus ?? 0,
      render: (r) => n(r.corpus),
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
      optional: true,
      value: (r) => r.seconds_per_campaign ?? 0,
      render: (r) => n(r.seconds_per_campaign, 1),
    },
    {
      key: 'grew',
      label: 'grew',
      align: 'right',
      group: 'result',
      value: (r) => r.grew ?? '',
      render: (r) => r.grew ?? '—',
    },
    {
      key: 'notes',
      label: 'notes',
      optional: true,
      value: (r) => r.notes ?? '',
      render: (r) => <span className="text-dim text-2xs">{r.notes ?? ''}</span>,
    },
  ]

  return (
    <div className="space-y-6">
      <Section title="experiment ledger" scope={data.scope}>
        <DataTable
          rows={data.trials}
          cols={cols}
          rowId={(r, i) => `${r.trial}-${i}`}
          searchPlaceholder="search trial, ruleset…"
          emptyWhat="no trial recorded yet"
          emptyWhy="the ledger is written when a run completes"
        />
      </Section>
      <TrainingHistory data={data} />
    </div>
  )
}

type TrainingEvent = Schemas['TrainingEvent']

/**
 * One row per retrain: what the corpus was, and what each model's fit produced.
 *
 * Columns are derived from the groups the data actually carries, so a new metric appears
 * without a code change and a metric that stopped being recorded stops occupying a
 * column. The old view hard-coded 27 columns across two header rows, which forced a
 * horizontal scrollbar and left half the numbers off screen; here the corpus and the
 * headline fit numbers are shown and the rest is one click away in the column picker.
 */
function TrainingHistory({ data }: { data: TrainingPage }) {
  const history = data.history ?? []
  const groups = data.group_order ?? []

  // Every metric present anywhere, in group order, so a row missing one renders a dash
  // rather than shifting its neighbours.
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
        // Only the corpus size and the headline fit numbers are shown by default.
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
      {view === 'correlations' && <Correlations />}
      {view === 'training' && <Training />}
    </div>
  )
}
