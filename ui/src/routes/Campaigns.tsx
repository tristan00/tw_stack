import { useNavigate } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  CountText,
  ErrorState,
  IdentLabel,
  RateText,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import {
  useApi,
  type CampaignRow,
  type CampaignsPage,
  type MatrixPage,
  type Schemas,
  type StartsPage,
} from '@/lib/api'
import { n, stateText } from '@/lib/format'

const VIEWS = [
  { key: 'all', label: 'every campaign', asks: 'which campaign ended how' },
  { key: 'starts', label: 'starts', asks: 'which starting factions produce good campaigns' },
  { key: 'matrix', label: 'action types', asks: 'which action types fail' },
]


function Growth({
  row,
  first,
  last,
  delta,
}: {
  row: CampaignRow
  first?: number | null
  last?: number | null
  delta?: number | null
}) {
  if (row.growth_state === 'no_turn_rows')
    return <span className="text-dim text-2xs">no turn recorded</span>
  if (row.growth_state === 'single_turn')
    return <span className="text-dim text-2xs">one turn only — no span</span>
  if (delta === null || delta === undefined)
    return <span className="text-dim text-2xs">not recorded</span>
  return (
    <span className={delta > 0 ? 'text-ok' : delta < 0 ? 'text-bad' : undefined}>
      {n(first)} <span className="text-dim">→</span> {n(last)}
      <span className="num ml-1 text-2xs">{delta > 0 ? `+${n(delta)}` : n(delta)}</span>
    </span>
  )
}

const campaignCols: Col<CampaignRow>[] = [
  {
    key: 'campaign',
    label: 'campaign',
    group: 'campaign',
    value: (r) => r.campaign.label,
    render: (r) => <IdentLabel ident={r.campaign} />,
  },
  {
    key: 'outcome',
    label: 'outcome',
    group: 'campaign',
    value: (r) => r.outcome?.label ?? '',


    render: (r) =>
      r.outcome ? (
        <span className="flex items-center gap-1.5">
          <Chip state={r.outcome_state ?? 'neutral'}>{r.outcome.label}</Chip>
          {r.suspicious && (
            <Chip state="bad" title="the state looked healthy at the point the run gave up">
              suspicious
            </Chip>
          )}
        </span>
      ) : (
        <span className="text-dim">running</span>
      ),
  },
  {
    key: 'turns',
    label: 'turns',
    unit: 'reached',
    align: 'right',
    group: 'campaign',


    value: (r) => r.turns ?? undefined,
    sortUndefined: 'last',
    render: (r) => n(r.turns),
  },
  {
    key: 'ended_because',
    label: 'why it ended',
    group: 'campaign',
    optional: true,
    value: (r) => r.ended_because ?? '',
    render: (r) =>
      r.ended_because ? (
        <span className="text-2xs">{r.ended_because}</span>
      ) : (
        <span className="text-dim">—</span>
      ),
  },
  {
    key: 'settlements_growth',
    label: 'settlements',
    group: 'growth (first → best)',


    value: (r) => r.settlements_growth ?? undefined,
    sortUndefined: 'last',
    render: (r) => (
      <Growth
        row={r}
        first={r.first_settlements}
        last={r.peak_settlements}
        delta={r.settlements_growth}
      />
    ),
  },
  {
    key: 'settlements_per_turn',
    label: 'settlements',
    unit: 'per turn',
    align: 'right',
    group: 'growth (first → best)',


    value: (r) => r.settlements_per_turn ?? undefined,
    sortUndefined: 'last',
    render: (r) =>
      r.settlements_per_turn === null || r.settlements_per_turn === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num">
          {r.settlements_per_turn > 0 ? '+' : ''}
          {r.settlements_per_turn.toFixed(2)}
        </span>
      ),
  },
  {
    key: 'lord_growth',
    label: 'lord level',
    group: 'growth (first → best)',
    value: (r) => r.lord_growth ?? undefined,
    sortUndefined: 'last',
    render: (r) => (
      <Growth
        row={r}
        first={r.first_lord_level}
        last={r.peak_lord_level}
        delta={r.lord_growth}
      />
    ),
  },
  {
    key: 'lord_per_turn',
    label: 'lord level',
    unit: 'per turn',
    align: 'right',
    group: 'growth (first → best)',


    value: (r) => r.lord_per_turn ?? undefined,
    sortUndefined: 'last',
    render: (r) =>
      r.lord_per_turn === null || r.lord_per_turn === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num">
          {r.lord_per_turn > 0 ? '+' : ''}
          {r.lord_per_turn.toFixed(2)}
        </span>
      ),
  },
  {
    key: 'last_measured_turn',
    label: 'last measured turn',
    align: 'right',
    optional: true,
    group: 'growth (first → best)',
    value: (r) => r.last_measured_turn ?? undefined,
    sortUndefined: 'last',
    render: (r) => n(r.last_measured_turn),
  },
  {
    key: 'decisions',
    label: 'decisions',
    align: 'right',
    group: 'volume',
    value: (r) => r.decisions,
    render: (r) => n(r.decisions),
  },
  {
    key: 'confirm',
    label: 'confirmed',
    group: 'volume',
    value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1),
    render: (r) => <Bar rate={r.confirm_rate ?? null} />,
  },
  {
    key: 'no_action',
    label: 'no action',
    align: 'right',
    group: 'volume',
    optional: true,
    value: (r) => r.no_action,
    render: (r) => n(r.no_action),
  },
  {
    key: 'peak_setts',
    label: 'settlements',
    align: 'right',
    group: 'peak',
    optional: true,
    value: (r) => r.peak_settlements ?? 0,
    render: (r) => n(r.peak_settlements),
  },
  {
    key: 'peak_rank',
    label: 'power rank',
    align: 'right',
    group: 'peak',
    direction: 'down',
    optional: true,
    value: (r) => r.peak_power_rank ?? 0,
    render: (r) => n(r.peak_power_rank),
  },
  {
    key: 'final_rank',
    label: 'power rank',
    align: 'right',
    group: 'final',
    direction: 'down',
    optional: true,
    value: (r) => r.final_power_rank ?? 0,
    render: (r) => n(r.final_power_rank),
  },
  {
    key: 'span',
    label: 'span',
    unit: 'min',
    align: 'right',
    group: 'final',
    optional: true,
    value: (r) => r.span_min ?? 0,
    render: (r) => n(r.span_min, 1),
  },
]

function AllCampaigns() {
  const { data, error, loading, reload } = useApi<CampaignsPage>('/api/campaigns')
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <Section title="every campaign" scope={data.scope}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {data.headline.map((h) => (
          <Chip key={h.outcome.raw} state={h.state ?? 'neutral'}>
            <span className="num mr-1 font-semibold">{h.count}</span>
            {h.outcome.label}
          </Chip>
        ))}
      </div>
      <div className="mb-3 grid gap-2 sm:grid-cols-3">
        <Card className="px-3 py-2">
          <CountText count={data.suspicious} />
        </Card>
        <Card className="px-3 py-2">
          <CountText count={data.unjoined} />
        </Card>
        {}
        <Card className="px-3 py-2">
          <div className="text-dim text-2xs">growth measurable</div>
          <Bar rate={data.growth_coverage} />
          <div className="text-dim mt-0.5 text-2xs">{data.growth_coverage.population}</div>
        </Card>
      </div>
      <DataTable
        rows={data.rows}
        cols={campaignCols}
        rowId={(r) => r.campaign.raw}
        onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
        searchPlaceholder="search faction, outcome…"
        emptyWhat="no campaign has recorded a decision in this run dir"
      />
    </Section>
  )
}

type StartRow = Schemas['StartRow']

const startCols: Col<StartRow>[] = [
  {
    key: 'faction',
    label: 'start',
    value: (r) => r.faction.label,
    render: (r) => <IdentLabel ident={r.faction} />,
  },
  {
    key: 'n',
    label: 'campaigns',
    align: 'right',


    value: (r) => r.n,
    render: (r) => (
      <span className="flex items-center justify-end gap-1.5">
        <span className="num">{r.n}</span>
        {r.single_sample && (
          <Chip state="warn" title="one or two campaigns — treat the aggregates as anecdotes">
            low n
          </Chip>
        )}
      </span>
    ),
  },
  {
    key: 'avg_turns',
    label: 'avg turns',
    align: 'right',
    value: (r) => r.avg_turns ?? 0,
    render: (r) => n(r.avg_turns, 1),
  },
  {
    key: 'best_turns',
    label: 'turns',
    align: 'right',
    group: 'best reached',
    value: (r) => r.best_turns ?? 0,
    render: (r) => n(r.best_turns),
  },
  {
    key: 'best_setts',
    label: 'settlements',
    align: 'right',
    group: 'best reached',
    value: (r) => r.best_settlements ?? 0,
    render: (r) => n(r.best_settlements),
  },
  {
    key: 'best_rank',
    label: 'power rank',
    align: 'right',
    group: 'best reached',
    direction: 'down',
    value: (r) => r.best_power_rank ?? 0,
    render: (r) => n(r.best_power_rank),
  },
  {
    key: 'best_lord',
    label: 'lord level',
    align: 'right',
    group: 'best reached',
    value: (r) => r.best_lord_level ?? 0,
    render: (r) => n(r.best_lord_level),
  },
  {
    key: 'allied',
    label: 'ever allied',
    align: 'right',
    optional: true,
    value: (r) => r.ever_allied,
    render: (r) => n(r.ever_allied),
  },
  {
    key: 'vassal',
    label: 'ever vassal',
    align: 'right',
    optional: true,
    value: (r) => r.ever_vassal,
    render: (r) => n(r.ever_vassal),
  },
  {
    key: 'confirm',
    label: 'confirmed',
    value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1),
    render: (r) => <Bar rate={r.confirm_rate ?? null} />,
  },
]

function Starts() {
  const { data, error, loading, reload } = useApi<StartsPage>('/api/campaigns/starts')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <Section title="starts" scope={data.scope}>
      <Card className="mb-3 px-3 py-2">
        <CountText count={data.low_sample} />
      </Card>
      <DataTable
        rows={data.rows}
        cols={startCols}
        rowId={(r) => r.faction.raw}
        initialSort={{ key: 'n', desc: true }}
        searchPlaceholder="search start…"
        emptyWhat="no start has recorded a campaign yet"
      />
    </Section>
  )
}

type MatrixTotal = Schemas['MatrixTotal']

const totalCols: Col<MatrixTotal>[] = [
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
    render: (r) => <Bar rate={r.rate} width={140} />,
  },
  {
    key: 'tried',
    label: 'attempted',
    align: 'right',
    value: (r) => r.rate.of,
    render: (r) => n(r.rate.of),
  },
  {
    key: 'per_try',
    label: 'per try',
    unit: 'ms',
    align: 'right',
    value: (r) => r.per_try_ms ?? 0,
    render: (r) => n(r.per_try_ms),
  },
]

function Matrix() {
  const { data, error, loading, reload } = useApi<MatrixPage>('/api/campaigns/matrix?kind=action')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <div className="space-y-6">
      {}
      <Section title="by action type" scope={data.scope}>
        <DataTable
          rows={data.totals}
          cols={totalCols}
          rowId={(r) => r.action_type.raw}
          emptyWhat="no action has been attempted in this run dir"
        />
      </Section>

      <Section
        title="by faction"
        scope={{
          text: 'the same attempts split by faction',
          detail: 'a faction only shows the types it actually attempted',
        }}
      >
        <DataTable
          rows={data.rows}
          cols={[
            {
              key: 'faction',
              label: 'faction',
              value: (r) => r.faction.label,
              render: (r) => <IdentLabel ident={r.faction} />,
            },
            {
              key: 'worst',
              label: 'worst type',
              value: (r) => {
                const w = [...r.cells].sort(
                  (a, b) =>
                    (a.rate.of ? a.rate.n / a.rate.of : 2) - (b.rate.of ? b.rate.n / b.rate.of : 2),
                )[0]
                return w?.action_type.label ?? ''
              },
              render: (r) => {
                const w = [...r.cells].sort(
                  (a, b) =>
                    (a.rate.of ? a.rate.n / a.rate.of : 2) - (b.rate.of ? b.rate.n / b.rate.of : 2),
                )[0]
                if (!w) return <span className="text-dim">—</span>
                return (
                  <span className="flex items-center gap-2">
                    <span className={stateText[w.state ?? 'neutral']}>{w.action_type.label}</span>
                    <RateText rate={w.rate} />
                  </span>
                )
              },
            },
            {
              key: 'types',
              label: 'types attempted',
              align: 'right',
              value: (r) => r.cells.length,
              render: (r) => n(r.cells.length),
            },
            {
              key: 'attempts',
              label: 'attempts',
              align: 'right',
              value: (r) => r.cells.reduce((a, c) => a + c.rate.of, 0),
              render: (r) => n(r.cells.reduce((a, c) => a + c.rate.of, 0)),
            },
          ]}
          rowId={(r) => r.faction.raw}
          searchPlaceholder="search faction…"
          emptyWhat="no faction has attempted an action yet"
        />
      </Section>
    </div>
  )
}

export function Campaigns() {
  const view = useSubView(VIEWS)
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'all' && <AllCampaigns />}
      {view === 'starts' && <Starts />}
      {view === 'matrix' && <Matrix />}
    </div>
  )
}
