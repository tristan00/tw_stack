import { ArrowLeft } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  ErrorState,
  IdentLabel,
  Section,
  Skeleton,
} from '@/components/primitives'
import { useApi, type CampaignDetail as Detail, type Schemas } from '@/lib/api'
import { clock, n } from '@/lib/format'

type RewardPoint = Schemas['RewardPoint']
type DiploEvent = Schemas['DiploEvent']
type DecisionRow = Schemas['DecisionRow']

export function CampaignDetail() {
  const { campaignKey = '' } = useParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<Detail>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}`,
  )

  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />

  const row = data.row
  const constant = new Set(data.constant_columns)

  // A column holding one distinct value across the whole series carries no signal, so it
  // is hidden by default rather than shipped as a wall of zeroes. It is still in the data
  // and still one click away -- allies and vassals are zero on every recorded turn today,
  // and a check that only looked for blank cells never noticed, because a column full of
  // real zeroes is full.
  const rewardCols: Col<RewardPoint>[] = [
    { key: 'turn', label: 'turn', align: 'right', value: (r) => r.turn, render: (r) => r.turn },
    {
      key: 'settlements',
      label: 'settlements',
      align: 'right',
      optional: constant.has('settlements'),
      value: (r) => r.settlements ?? 0,
      render: (r) => n(r.settlements),
    },
    {
      key: 'income',
      label: 'income',
      align: 'right',
      optional: constant.has('income'),
      value: (r) => r.income ?? 0,
      render: (r) => n(r.income),
    },
    {
      key: 'power_rank',
      label: 'power rank',
      align: 'right',
      direction: 'down',
      optional: constant.has('power_rank'),
      value: (r) => r.power_rank ?? 0,
      render: (r) => n(r.power_rank),
    },
    {
      key: 'allies',
      label: 'allies',
      align: 'right',
      optional: constant.has('allies'),
      value: (r) => r.allies ?? 0,
      render: (r) => n(r.allies),
    },
    {
      key: 'vassals',
      label: 'vassals',
      align: 'right',
      optional: constant.has('vassals'),
      value: (r) => r.vassals ?? 0,
      render: (r) => n(r.vassals),
    },
  ]

  const diploCols: Col<DiploEvent>[] = [
    { key: 'turn', label: 'turn', align: 'right', value: (r) => r.turn ?? 0, render: (r) => r.turn ?? '—' },
    {
      key: 'outcome',
      label: 'outcome',
      value: (r) => r.outcome?.label ?? '',
      // The taxonomy separates their refusal from our failure to build the deal. Reading
      // those as one number is how a refusal rate becomes meaningless.
      help: 'accepted / declined are the AI answering a deal we sent. "ai would refuse" means the game told us up front, so nothing was sent. "not staged" is our own failure to build the deal — not a diplomatic outcome.',
      render: (r) =>
        r.outcome ? <Chip state={r.state ?? 'neutral'}>{r.outcome.label}</Chip> : '—',
    },
    {
      key: 'faction',
      label: 'with',
      value: (r) => r.faction?.label ?? '',
      render: (r) => <IdentLabel ident={r.faction} />,
    },
    {
      key: 'score',
      label: 'deal score',
      align: 'right',
      // Named success_chance by the game and not a percentage: it runs past -1000. The
      // sign is what carries, so the sign is what gets the colour.
      help: "The game's own success_chance readout. Despite the name it is not a percentage — it runs past −1000. Its sign is what carries: negative and the AI refuses.",
      value: (r) => r.deal_score ?? 0,
      render: (r) =>
        r.deal_score === null || r.deal_score === undefined ? (
          <span className="text-dim">—</span>
        ) : (
          <span className={r.deal_score < 0 ? 'text-bad' : 'text-ok'}>{n(r.deal_score)}</span>
        ),
    },
    {
      key: 'terms',
      label: 'terms',
      value: (r) => r.terms ?? '',
      render: (r) => <span className="text-dim">{r.terms ?? '—'}</span>,
    },
  ]

  const decisionCols: Col<DecisionRow>[] = [
    { key: 'turn', label: 'turn', align: 'right', value: (r) => r.turn ?? 0, render: (r) => r.turn ?? '—' },
    { key: 'time', label: 'time', value: (r) => r.ts ?? 0, render: (r) => clock(r.ts) },
    {
      key: 'action',
      label: 'action',
      value: (r) => r.action_type?.label ?? '',
      render: (r) => r.action_type?.label ?? '—',
    },
    {
      key: 'key',
      label: 'key',
      value: (r) => r.action_key ?? '',
      render: (r) => <span className="num text-dim text-2xs">{r.action_key ?? '—'}</span>,
    },
    {
      key: 'result',
      label: 'result',
      value: (r) => r.result?.label ?? '',
      render: (r) => (r.result ? <Chip state={r.result_state ?? 'neutral'}>{r.result.label}</Chip> : '—'),
    },
    {
      key: 'policy',
      label: 'picked by',
      value: (r) => r.policy?.label ?? '',
      render: (r) => r.policy?.label ?? '—',
    },
  ]

  return (
    <div className="space-y-7">
      <div>
        <Link to="/campaigns" className="text-dim hover:text-fg inline-flex items-center gap-1 text-xs">
          <ArrowLeft className="size-3.5" /> every campaign
        </Link>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <IdentLabel ident={row.campaign} className="text-lg font-semibold" />
          {row.outcome && <Chip state={row.outcome_state ?? 'neutral'}>{row.outcome.label}</Chip>}
          {row.suspicious && <Chip state="bad">suspicious</Chip>}
        </h1>
        <div className="text-dim mt-1 flex flex-wrap gap-4 text-2xs">
          <span>turns <b className="num text-fg">{n(row.turns)}</b></span>
          <span>decisions <b className="num text-fg">{n(row.decisions)}</b></span>
          <span>ended at turn <b className="num text-fg">{row.ended_at_turn ?? '—'}</b></span>
          {row.ended_when && <span>{row.ended_when}</span>}
        </div>
        <div className="mt-2 max-w-md">
          <Bar rate={row.confirm_rate ?? null} width={160} />
        </div>
      </div>

      <Section
        title="turn series"
        scope={{
          text: 'the reward inputs this campaign was judged on',
          detail: constant.size
            ? `${[...constant].join(', ')} carried one value the whole way and ${
                constant.size > 1 ? 'are' : 'is'
              } hidden — turn back on with the column picker`
            : undefined,
        }}
      >
        <DataTable
          rows={data.reward}
          cols={rewardCols}
          rowId={(r) => String(r.turn)}
          dense
          emptyWhat="no turn was recorded for this campaign"
          emptyWhy="a campaign that ended before its first turn has no series"
        />
      </Section>

      <Section title="diplomacy" scope={{ text: 'deal events recorded for this campaign' }}>
        <DataTable
          rows={data.diplomacy}
          cols={diploCols}
          rowId={(r, i) => `${r.turn}-${i}`}
          dense
          emptyWhat="no deal event for this campaign"
          emptyWhy="the run either sent no deals or the stream has rolled past them"
        />
      </Section>

      <Section title="its decisions" scope={{ text: 'the actions taken inside this campaign' }}>
        <DataTable
          rows={data.decisions}
          cols={decisionCols}
          rowId={(r) => String(r.decision_id)}
          onRowClick={(r) => navigate(`/decisions/${r.decision_id}`)}
          dense
          emptyWhat="no action recorded for this campaign"
        />
      </Section>

      <Card className="text-dim px-3 py-2 text-2xs">
        raw campaign key <span className="num">{row.campaign.raw}</span>
      </Card>
    </div>
  )
}
