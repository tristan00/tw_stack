import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Chip,
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
import { clock, n, stateText } from '@/lib/format'

const VIEWS = [
  { key: 'all', label: 'every campaign', asks: 'which campaign ended how' },
  { key: 'starts', label: 'starts', asks: 'which starting factions produce good campaigns' },
  { key: 'matrix', label: 'action types', asks: 'which action types fail' },
  { key: 'picks', label: 'picks', asks: 'why the selector played this start' },
]


const campaignCols: Col<CampaignRow>[] = [
  {
    key: 'campaign',
    label: 'lord',
    group: 'campaign',
    value: (r) => r.leader ?? r.campaign.label,
    render: (r) => (
      <IdentLabel
        ident={{ ...r.campaign, label: r.leader ?? r.campaign.label, culture: null }}
      />
    ),
  },
  {
    key: 'race',
    label: 'race',
    group: 'campaign',
    value: (r) => r.campaign.culture ?? '',
    render: (r) => <span className="text-dim">{r.campaign.culture ?? '—'}</span>,
  },
  {
    key: 'map',
    label: 'map',
    group: 'campaign',
    value: (r) => r.campaign_map?.label ?? '',
    render: (r) => <span className="text-dim">{r.campaign_map?.label ?? '—'}</span>,
  },
  {
    key: 'outcome',
    label: 'outcome',
    group: 'campaign',
    optional: true,
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
    key: 'settlements_start',
    label: 'starting',
    unit: 'settlements',
    align: 'right',
    group: 'growth (first → best)',
    value: (r) => r.first_settlements ?? undefined,
    sortUndefined: 'last',
    render: (r) =>
      r.first_settlements === null || r.first_settlements === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num">{n(r.first_settlements)}</span>
      ),
  },
  {
    key: 'settlements_gained',
    label: 'gained',
    unit: 'settlements',
    align: 'right',
    group: 'growth (first → best)',
    value: (r) => r.settlements_growth ?? undefined,
    sortUndefined: 'last',
    render: (r) =>
      r.settlements_growth === null || r.settlements_growth === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num">
          {r.settlements_growth > 0 ? '+' : ''}
          {n(r.settlements_growth)}
        </span>
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
    key: 'lord_level',
    label: 'lord level',
    unit: 'reached',
    align: 'right',
    group: 'growth (first → best)',
    value: (r) => r.peak_lord_level ?? undefined,
    sortUndefined: 'last',
    render: (r) =>
      r.peak_lord_level === null || r.peak_lord_level === undefined ? (
        <span className="text-dim">—</span>
      ) : (
        <span className="num">{n(r.peak_lord_level)}</span>
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
    label: 'lord',
    value: (r) => r.leader ?? r.faction.label,
    render: (r) => (
      <IdentLabel
        ident={{ ...r.faction, label: r.leader ?? r.faction.label, culture: null }}
      />
    ),
  },
  {
    key: 'culture',
    label: 'race',
    value: (r) => r.faction.culture ?? '',
    render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span>,
  },
  {
    key: 'map',
    label: 'map',
    value: (r) => r.campaign_map?.label ?? '',
    render: (r) => <span className="text-dim">{r.campaign_map?.label ?? '—'}</span>,
  },
  {
    key: 'n',
    label: 'campaigns',
    align: 'right',
    value: (r) => r.n,
    render: (r) => <span className="num">{r.n}</span>,
  },
  {
    key: 'avg_turns',
    label: 'avg turns',
    align: 'right',
    value: (r) => r.avg_turns ?? 0,
    render: (r) => n(r.avg_turns, 1),
  },
  {
    key: 'sec_per_turn',
    label: 's/turn',
    align: 'right',
    value: (r) => r.sec_per_turn ?? 0,
    render: (r) => n(r.sec_per_turn, 1),
  },
  {
    key: 'sett_best',
    label: 'best',
    align: 'right',
    group: 'settlements gained',
    value: (r) => r.settlements_gained_best ?? 0,
    render: (r) => n(r.settlements_gained_best),
  },
  {
    key: 'sett_avg',
    label: 'avg',
    align: 'right',
    group: 'settlements gained',
    value: (r) => r.settlements_gained_avg ?? 0,
    render: (r) => n(r.settlements_gained_avg, 1),
  },
  {
    key: 'levels_best',
    label: 'best',
    align: 'right',
    group: 'levels gained',
    value: (r) => r.levels_gained_best ?? 0,
    render: (r) => n(r.levels_gained_best),
  },
  {
    key: 'levels_avg',
    label: 'avg',
    align: 'right',
    group: 'levels gained',
    value: (r) => r.levels_gained_avg ?? 0,
    render: (r) => n(r.levels_gained_avg, 1),
  },
  {
    key: 'allies_best',
    label: 'best',
    align: 'right',
    optional: true,
    group: 'allies gained',
    value: (r) => r.allies_gained_best ?? 0,
    render: (r) => n(r.allies_gained_best),
  },
  {
    key: 'allies_avg',
    label: 'avg',
    align: 'right',
    optional: true,
    group: 'allies gained',
    value: (r) => r.allies_gained_avg ?? 0,
    render: (r) => n(r.allies_gained_avg, 1),
  },
  {
    key: 'vassals_best',
    label: 'best',
    align: 'right',
    optional: true,
    group: 'vassals gained',
    value: (r) => r.vassals_gained_best ?? 0,
    render: (r) => n(r.vassals_gained_best),
  },
  {
    key: 'vassals_avg',
    label: 'avg',
    align: 'right',
    optional: true,
    group: 'vassals gained',
    value: (r) => r.vassals_gained_avg ?? 0,
    render: (r) => n(r.vassals_gained_avg, 1),
  },
  {
    key: 'total_best',
    label: 'best',
    align: 'right',
    group: 'total gained',
    value: (r) => r.total_gained_best ?? 0,
    render: (r) => n(r.total_gained_best),
  },
  {
    key: 'total_avg',
    label: 'avg',
    align: 'right',
    group: 'total gained',
    value: (r) => r.total_gained_avg ?? 0,
    render: (r) => n(r.total_gained_avg, 1),
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
    optional: true,
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
      <DataTable
        rows={data.rows}
        cols={startCols}
        rowId={(r) => `${r.campaign_map?.raw ?? ''}|${r.faction.raw}`}
        initialSort={{ key: 'total_avg', desc: true }}
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

type UcbPick = Schemas['UcbPick']
type UcbRow = Schemas['UcbRow']
type UcbPicksPage = Schemas['UcbPicksPage']
type UcbPickPage = Schemas['UcbPickPage']

const num = (v: number | null | undefined, digits = 3) => (v == null ? 'inf' : n(v, digits))

const pickCols: Col<UcbPick>[] = [
  {
    key: 'when',
    label: 'when',
    value: (r) => r.ts ?? 0,
    render: (r) => <span className="tabular">{clock(r.ts)}</span>,
  },
  {
    key: 'lord',
    label: 'legendary lord',
    value: (r) => r.leader ?? r.faction.label,
    render: (r) => r.leader ?? r.faction.label,
  },
  {
    key: 'faction',
    label: 'faction',
    value: (r) => r.faction.label,
    render: (r) => <IdentLabel ident={r.faction} />,
  },
  {
    key: 'map',
    label: 'map',
    value: (r) => r.campaign_map?.label ?? '',
    render: (r) => (r.campaign_map ? <IdentLabel ident={r.campaign_map} /> : '-'),
  },
  { key: 'c', label: 'C', align: 'right', value: (r) => r.c ?? 0, render: (r) => n(r.c, 2) },
  {
    key: 'plays',
    label: 'plays',
    align: 'right',
    help: 'total campaigns the selector divided by at this pick',
    value: (r) => r.total_plays,
    render: (r) => n(r.total_plays),
  },
  { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => n(r.n) },
  {
    key: 'mean',
    label: 'mean',
    align: 'right',
    group: 'winning score',
    value: (r) => r.mean ?? 0,
    render: (r) => num(r.mean),
  },
  {
    key: 'explore',
    label: 'explore',
    align: 'right',
    group: 'winning score',
    value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER,
    render: (r) => num(r.explore),
  },
  {
    key: 'score',
    label: 'score',
    align: 'right',
    group: 'winning score',
    value: (r) => r.score ?? Number.MAX_SAFE_INTEGER,
    render: (r) => <strong>{num(r.score)}</strong>,
  },
  {
    key: 'tied',
    label: 'tied',
    align: 'right',
    help: 'how many starts shared the top score; the winner was drawn at random among them',
    value: (r) => r.tied,
    render: (r) => (r.tied > 1 ? <Chip state="warn">{n(r.tied)}</Chip> : n(r.tied)),
  },
  {
    key: 'ranked',
    label: 'ranked',
    align: 'right',
    optional: true,
    value: (r) => r.starts,
    render: (r) => n(r.starts),
  },
]

const rankCols: Col<UcbRow>[] = [
  {
    key: 'rank',
    label: '#',
    align: 'right',
    value: (r) => r.rank,
    render: (r) => (r.chosen ? <strong>{n(r.rank)}</strong> : n(r.rank)),
  },
  {
    key: 'lord',
    label: 'legendary lord',
    value: (r) => r.leader ?? r.faction.label,
    render: (r) => r.leader ?? r.faction.label,
  },
  {
    key: 'faction',
    label: 'faction',
    value: (r) => r.faction.label,
    render: (r) => <IdentLabel ident={r.faction} />,
  },
  {
    key: 'map',
    label: 'map',
    value: (r) => r.campaign_map?.label ?? '',
    render: (r) => (r.campaign_map ? <IdentLabel ident={r.campaign_map} /> : '-'),
  },
  {
    key: 'n',
    label: 'n',
    align: 'right',
    help: 'campaigns this start has recorded, counting only those with two or more decisions',
    value: (r) => r.n,
    render: (r) => n(r.n),
  },
  {
    key: 'mean',
    label: 'mean',
    align: 'right',
    help: 'average reward: settlements gained plus lord levels gained',
    value: (r) => r.mean ?? 0,
    render: (r) => num(r.mean),
  },
  {
    key: 'explore',
    label: 'explore',
    align: 'right',
    help: 'C * sqrt(ln(total plays) / n); infinite for a start never played',
    value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER,
    render: (r) => num(r.explore),
  },
  {
    key: 'score',
    label: 'score',
    align: 'right',
    value: (r) => r.score ?? Number.MAX_SAFE_INTEGER,
    render: (r) => (r.chosen ? <strong>{num(r.score)}</strong> : num(r.score)),
  },
]

function Picks() {
  const { data, error, loading, reload } = useApi<UcbPicksPage>('/api/campaigns/picks?limit=200')
  const [sel, setSel] = useState<number | null>(null)
  const picks = data?.picks ?? []
  const pickId = sel ?? picks[0]?.pick_id ?? null
  const detail = useApi<UcbPickPage>(
    pickId == null ? null : `/api/campaigns/picks/${pickId}`,
    [pickId],
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  return (
    <div>
      <Section title="ucb picks" scope={data.scope}>
        <DataTable
          rows={picks}
          cols={pickCols}
          rowId={(r) => String(r.pick_id)}
          onRowClick={(r) => setSel(r.pick_id)}
          initialSort={{ key: 'when', desc: true }}
          searchPlaceholder="search pick…"
          pageSize={25}
          emptyWhat="no UCB pick has been recorded yet"
          emptyWhy="only runs started with --ucb record them, from the next launch onward"
        />
      </Section>
      <Section title="the ranking at that pick" scope={detail.data?.scope}>
        {detail.error && <ErrorState error={detail.error} onRetry={detail.reload} />}
        {!detail.error && (detail.loading || !detail.data) && <Skeleton rows={8} />}
        {!detail.error && detail.data && (
          <DataTable
            rows={detail.data.rows ?? []}
            cols={rankCols}
            rowId={(r) => String(r.rank)}
            initialSort={{ key: 'rank', desc: false }}
            searchPlaceholder="search start…"
            pageSize={25}
            emptyWhat="no ranking stored for this pick"
          />
        )}
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
      {view === 'picks' && <Picks />}
    </div>
  )
}
