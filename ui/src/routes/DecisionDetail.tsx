import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { RankScatter } from '@/components/charts'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Card,
  Chip,
  CountText,
  EmptyState,
  ErrorState,
  IdentLabel,
  ModelKey,
  RangeMeter,
  Section,
  Skeleton,
} from '@/components/primitives'
import { useApi, type DecisionDetail as Detail, type Schemas } from '@/lib/api'
import { clock, ms, n } from '@/lib/format'
import { cn } from '@/lib/utils'

type OfferRow = Schemas['OfferRow']

const PHASE_COLOR: Record<string, string> = {
  collect: 'bg-cat',
  queue: 'bg-gnn',
  score: 'bg-warn',
  verify: 'bg-ok',
}

const offerCols: Col<OfferRow>[] = [
  {
    key: 'rank',
    label: 'rank',
    align: 'right',
    group: 'offer',
    value: (r) => r.rank ?? 9999,
    render: (r) => (r.rank === null || r.rank === undefined ? '—' : r.rank),
  },
  { key: 'entity', label: 'entity', group: 'offer', value: (r) => r.entity ?? '', render: (r) => <span className="num text-2xs">{r.entity}</span> },
  {
    key: 'type',
    label: 'action',
    group: 'offer',
    value: (r) => r.action_type?.label ?? '',
    render: (r) => r.action_type?.label ?? '—',
  },
  {
    key: 'key',
    label: 'key',
    group: 'offer',
    value: (r) => r.action_key ?? '',
    render: (r) => <span className="num text-dim text-2xs">{r.action_key}</span>,
  },
  {
    key: 'exploit',
    label: 'exploit',
    align: 'right',
    group: 'tree model',
    value: (r) => r.exploit ?? 0,
    render: (r) => n(r.exploit, 3),
  },
  {
    key: 'global',
    label: 'global',
    unit: 'pct',
    align: 'right',
    group: 'tree model',
    value: (r) => r.pct_global ?? 0,
    render: (r) => n(r.pct_global, 1),
  },
  {
    key: 'local',
    label: 'local',
    unit: 'pct',
    align: 'right',
    group: 'tree model',
    optional: true,
    value: (r) => r.pct_local ?? 0,
    render: (r) => n(r.pct_local, 1),
  },
  {
    key: 'gnn_impact',
    label: 'impact',
    align: 'right',
    group: 'graph model',
    value: (r) => r.gnn_impact ?? 0,
    render: (r) => n(r.gnn_impact, 4),
  },
  {
    key: 'gnn_rank',
    label: 'rank',
    align: 'right',
    group: 'graph model',
    value: (r) => r.gnn_rank ?? 9999,
    render: (r) => (r.gnn_rank === null || r.gnn_rank === undefined ? '—' : r.gnn_rank),
  },
  {
    key: 'taken',
    label: '',
    group: 'graph model',
    value: (r) => (r.taken ? 1 : 0),
    render: (r) => (r.taken ? <Chip state="ok">taken</Chip> : null),
  },
]

export function DecisionDetail() {
  const { decisionId = '' } = useParams()
  const { data, error, loading, reload } = useApi<Detail>(`/api/decisions/${decisionId}`)

  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />

  const row = data.row
  const total = data.phases.reduce((a, p) => a + p.ms, 0) || 1

  return (
    <div className="space-y-7">
      <div>
        <Link to="/decisions" className="text-dim hover:text-fg inline-flex items-center gap-1 text-xs">
          <ArrowLeft className="size-3.5" /> every action
        </Link>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <span className="text-lg font-semibold">decision {row.decision_id}</span>
          {row.result && <Chip state={row.result_state ?? 'neutral'}>{row.result.label}</Chip>}
          {row.refusal && <Chip state="bad">{row.refusal.label}</Chip>}
        </h1>
        <div className="text-dim mt-1 flex flex-wrap gap-4 text-2xs">
          <span>
            <IdentLabel ident={row.campaign} showCulture={false} />
          </span>
          <span>turn <b className="num text-fg">{row.turn ?? '—'}</b></span>
          <span>{clock(row.ts)}</span>
          <span>picked by <b className="text-fg">{row.policy?.label ?? '—'}</b></span>
          <span>offers <b className="num text-fg">{n(row.offers)}</b></span>
        </div>
      </div>

      <Section
        title="where the time went"
        scope={{ text: 'the four phases of this one action', detail: `${ms(total)} in total` }}
      >
        <Card className="px-3 py-3">
          <div className="flex items-center gap-px">
            {data.phases.map((p) => (
              <span
                key={p.phase}
                className={cn('h-3 rounded-sm', PHASE_COLOR[p.phase] ?? 'bg-dim')}
                style={{ width: `${(p.ms / total) * 100}%` }}
              />
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-4">
            {data.phases.map((p) => (
              <span key={p.phase} className="flex items-center gap-1.5 text-2xs">
                <span className={cn('inline-block size-2.5 rounded-sm', PHASE_COLOR[p.phase])} />
                {p.phase} <b className="num">{ms(p.ms)}</b>
              </span>
            ))}
            {!data.phases.length && <span className="text-dim text-2xs">no timing recorded</span>}
          </div>
        </Card>
      </Section>

      <Section
        title="how alike the two rankings were"
        scope={{
          text: "both models' orderings of this decision's offers, over the offers both scored",
          detail: 'every dot is a row in the ranking below',
        }}
      >
        {!data.agreement || data.agreement.rho === null || data.agreement.rho === undefined ? (
          <EmptyState
            what="this decision has no rank correlation"
            why={
              data.agreement?.note ??
              'fewer than three offers carry both ranks, and a rho over two points can only be +1 or -1'
            }
          />
        ) : (
          <Card className="flex flex-wrap items-start gap-6 p-3.5">
            <div className="min-w-0 flex-1 space-y-2">
              <div>
                <div className="text-dim text-2xs uppercase tracking-wide">spearman rho</div>
                <div className="num text-xl leading-tight">
                  {data.agreement.rho >= 0 ? '+' : ''}
                  {data.agreement.rho.toFixed(3)}
                </div>
                <RangeMeter value={data.agreement.rho} width={180} />
              </div>
              <CountText count={data.agreement.n} />
              <div className="text-2xs">
                {data.agreement.top1_same
                  ? 'both models put the same offer first'
                  : 'the two models put different offers first'}
              </div>
              {(data.agreement.cat_top_in_gnn || data.agreement.gnn_top_in_cat) && (
                <div className="text-dim text-2xs">
                  the tree model's first choice was the graph model's{' '}
                  <b className="num text-fg">#{data.agreement.cat_top_in_gnn}</b>; the graph
                  model's was the tree model's{' '}
                  <b className="num text-fg">#{data.agreement.gnn_top_in_cat}</b>
                </div>
              )}
              <div className="text-dim flex flex-wrap gap-3 text-2xs">
                <ModelKey model="cat">tree model rank →</ModelKey>
                <ModelKey model="gnn">graph model rank ↓</ModelKey>
              </div>
            </div>
            <RankScatter
              pairs={data.offers
                .filter((o) => o.rank !== null && o.rank !== undefined && o.gnn_rank !== null && o.gnn_rank !== undefined)
                .map((o) => ({
                  cat: o.rank as number,
                  gnn: o.gnn_rank as number,
                  taken: Boolean(o.taken),
                  label: o.action_key ?? '',
                }))}
            />
          </Card>
        )}
      </Section>

      <Section
        title="the ranking it produced"
        scope={{
          text: 'every offer both models scored for this decision',
          detail: 'the row the game accepted is marked',
        }}
      >
        <DataTable
          rows={data.offers}
          cols={offerCols}
          rowId={(r, i) => `${r.action_key ?? ''}-${i}`}
          initialSort={{ key: 'rank', desc: false }}
          dense
          searchPlaceholder="search offer…"
          emptyWhat="no offer was recorded for this decision"
          emptyWhy="a decision with no offers is one where nothing was eligible"
        />
      </Section>

      <Section
        title="entity state"
        scope={{ text: 'what the features were built from, at that instant' }}
      >
        <div className="space-y-2">
          {data.entities.map((e) => (
            <Card key={`${e.context_kind}-${e.context_id}`} className="overflow-hidden">
              <div className="border-line bg-raised/40 border-b px-3 py-1.5 text-2xs">
                <span className="font-semibold">{e.context_kind}</span>{' '}
                <span className="num text-dim">{e.context_id}</span>
              </div>
              <pre className="num max-h-60 overflow-auto px-3 py-2 text-2xs">
                {JSON.stringify(e.features, null, 1)}
              </pre>
            </Card>
          ))}
          {!data.entities.length && (
            <Card className="text-dim px-4 py-6 text-center text-sm">
              no entity snapshot stored for this decision
            </Card>
          )}
        </div>
      </Section>
    </div>
  )
}
