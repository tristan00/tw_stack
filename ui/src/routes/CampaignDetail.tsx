import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  EntityLink,
  ErrorState,
  IdentLabel,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { Steps } from '@/components/startcharts'
import {
  useApi,
  type CampaignDecisions,
  type CampaignDetail as Detail,
  type Schemas,
} from '@/lib/api'
import { clock, n } from '@/lib/format'
import { cn } from '@/lib/utils'

type RewardPoint = Schemas['RewardPoint']
type DecisionRow = Schemas['DecisionRow']
type DiploEvent = Schemas['DiploEvent']

const TABS = [
  { key: 'overview', label: 'overview', asks: 'what happened and why it ended' },
  { key: 'decisions', label: 'decisions', asks: 'every action taken inside it' },
]

const SERIES: { key: keyof RewardPoint & string; label: string }[] = [
  { key: 'settlements', label: 'settlements' },
  { key: 'income', label: 'income' },
  { key: 'power_rank', label: 'power rank' },
  { key: 'allies', label: 'allies' },
  { key: 'vassals', label: 'vassals' },
]

function seriesDelta(pts: RewardPoint[], key: keyof RewardPoint & string) {
  const vals = pts.map((p) => p[key] as number | null | undefined).filter((v): v is number => v != null)
  if (!vals.length) return null
  const a = vals[0]
  const b = vals[vals.length - 1]
  return { a, b, d: b - a }
}

function DiploDigest({ events }: { events: DiploEvent[] }) {
  const [all, setAll] = useState(false)
  const byFaction = useMemo(() => {
    const m = new Map<string, { label: string; culture: string | null; turns: number[]; terms: { term: string; turn: number | null }[] }>()
    for (const e of events) {
      const k = e.faction?.raw ?? '—'
      const cur = m.get(k) ?? { label: e.faction?.label ?? '—', culture: e.faction?.culture ?? null, turns: [], terms: [] }
      if (e.turn != null) cur.turns.push(e.turn)
      if (e.terms) cur.terms.push({ term: e.terms, turn: e.turn ?? null })
      m.set(k, cur)
    }
    return [...m.entries()].sort((a, b) => b[1].turns.length - a[1].turns.length)
  }, [events])
  const withTerms = events.filter((e) => e.terms).length
  const scored = events.filter((e) => e.deal_score != null).length
  const diploCols: Col<DiploEvent>[] = [
    { key: 'turn', label: 'turn', align: 'right', value: (r) => r.turn ?? 0, render: (r) => r.turn ?? '—' },
    { key: 'faction', label: 'with', value: (r) => r.faction?.label ?? '', render: (r) => <IdentLabel ident={r.faction} /> },
    { key: 'outcome', label: 'outcome', optional: true, value: (r) => r.outcome?.label ?? '', render: (r) => (r.outcome ? <Chip state={r.state ?? 'neutral'}>{r.outcome.label}</Chip> : <span className="text-dim">—</span>) },
    {
      key: 'score',
      label: 'deal score',
      align: 'right',
      optional: true,
      value: (r) => r.deal_score ?? 0,
      render: (r) =>
        r.deal_score == null ? <span className="text-dim">—</span> : <span className={r.deal_score < 0 ? 'text-bad' : 'text-ok'}>{n(r.deal_score)}</span>,
    },
    { key: 'terms', label: 'terms', value: (r) => r.terms ?? '', render: (r) => <span className="text-dim">{r.terms?.replace(/_/g, ' ') ?? '—'}</span> },
  ]
  if (!events.length) {
    return <Card className="text-dim px-4 py-6 text-center text-sm">no deal event for this campaign</Card>
  }
  return (
    <Card className="overflow-hidden">
      <div className="text-dim border-line border-b px-3 py-2 text-2xs">
        {n(events.length)} contacts · {n(withTerms)} carried terms{scored === 0 ? ' · no deal scores recorded' : ''}
      </div>
      {all ? (
        <div className="p-2">
          <DataTable rows={events} cols={diploCols} rowId={(r, i) => `${r.turn}-${i}`} dense pageSize={25} emptyWhat="no deal event" />
        </div>
      ) : (
        <table className="w-full text-xs">
          <tbody>
            {byFaction.map(([k, f]) => (
              <tr key={k} className="border-line border-b last:border-0">
                <td className="px-3 py-1.5">
                  <b>{f.label}</b>
                  {f.culture && <span className="text-dim text-2xs ml-1.5">{f.culture}</span>}
                  {f.terms.map((t, i) => (
                    <span key={i} className="ml-2">
                      <Chip state="neutral">{t.term.replace(/_/g, ' ')}</Chip>
                      {t.turn != null && <span className="text-dim text-2xs ml-1">t{t.turn}</span>}
                    </span>
                  ))}
                </td>
                <td className="num text-dim px-3 py-1.5 text-right whitespace-nowrap">
                  {f.turns.length} contact{f.turns.length === 1 ? '' : 's'}
                  {f.turns.length > 0 && ` · t${Math.min(...f.turns)}–${Math.max(...f.turns)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button onClick={() => setAll(!all)} className="text-accent hover:underline w-full px-3 py-1.5 text-left text-2xs">
        {all ? 'back to the digest' : `view all ${events.length} events ▸`}
      </button>
    </Card>
  )
}

function OverviewTab({ data, campaignKey }: { data: Detail; campaignKey: string }) {
  const navigate = useNavigate()
  const row = data.row
  const verdict = data.verdict
  const turns = data.turns ?? []
  const pts = data.reward
  const constant = new Set(data.constant_columns)
  const charted = SERIES.filter((s) => !constant.has(s.key) && pts.some((p) => p[s.key] != null))
  const flat = SERIES.filter((s) => constant.has(s.key) && pts.some((p) => p[s.key] != null))
  const byTurn = new Map(pts.map((p) => [p.turn, p]))
  const turnCols: Col<(typeof turns)[number]>[] = [
    {
      key: 'turn',
      label: 'turn',
      align: 'right',
      value: (r) => r.turn,
      render: (r) => (
        <EntityLink to={`?tab=decisions&turn=${r.turn}`} className="num">
          {r.turn}
        </EntityLink>
      ),
    },
    { key: 'decisions', label: 'decisions', align: 'right', value: (r) => r.decisions, render: (r) => <span className="num">{r.decisions}</span> },
    {
      key: 'refused',
      label: 'refused',
      align: 'right',
      value: (r) => r.refused,
      render: (r) => (r.refused ? <span className="num">{r.refused}</span> : <span className="text-dim">—</span>),
    },
    { key: 'income', label: 'income', align: 'right', value: (r) => byTurn.get(r.turn)?.income ?? 0, render: (r) => <span className="num">{n(byTurn.get(r.turn)?.income)}</span> },
    { key: 'setts', label: 'setts', align: 'right', value: (r) => byTurn.get(r.turn)?.settlements ?? 0, render: (r) => <span className="num">{n(byTurn.get(r.turn)?.settlements)}</span> },
    { key: 'rank', label: 'rank', align: 'right', value: (r) => byTurn.get(r.turn)?.power_rank ?? 0, render: (r) => <span className="num">{n(byTurn.get(r.turn)?.power_rank)}</span> },
  ]
  return (
    <div className="space-y-7">
      <div className="grid gap-3 lg:grid-cols-[1.7fr_1fr_1fr_1.2fr]">
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">why it ended</div>
          {verdict ? (
            <>
              <div className="mt-1.5 text-sm">{verdict.text}</div>
              {verdict.detail && (
                <div className="mt-0.5 flex items-center gap-2.5 text-xs">
                  <span className="text-dim">{verdict.detail}</span>
                  {verdict.pct != null && (
                    <>
                      <span className="bg-raised relative inline-block h-1.5 w-28 rounded">
                        <span
                          className={cn('absolute inset-y-0 left-0 rounded', verdict.pct >= 99 ? 'bg-warn' : 'bg-fg/45')}
                          style={{ width: `${Math.min(100, verdict.pct)}%` }}
                        />
                      </span>
                      <span className={cn('num text-2xs', verdict.pct >= 99 && 'text-warn')}>{n(verdict.pct)}%</span>
                    </>
                  )}
                </div>
              )}
              {(verdict.roots ?? []).length > 0 && (
                <>
                  <div className="text-dim mt-2.5 text-2xs">UI roots open at the stall · {(verdict.roots ?? []).length}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(verdict.roots ?? []).map((r) => (
                      <span key={r} className="num bg-raised text-dim rounded px-1.5 py-0.5 text-2xs">
                        {r}
                      </span>
                    ))}
                  </div>
                </>
              )}
              {row.turns != null && (
                <div className="mt-2 text-2xs">
                  <EntityLink to={`?tab=decisions&turn=${row.turns}`} className="text-dim">
                    open turn {row.turns} in decisions →
                  </EntityLink>
                </div>
              )}
            </>
          ) : (
            <div className="text-dim mt-1.5 text-xs">no ending recorded for this campaign</div>
          )}
        </Card>
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">reward</div>
          <div className="num mt-0.5 text-xl">{row.reward == null ? '—' : n(row.reward)}</div>
          <div className="text-dim mt-1 text-2xs">
            {row.reward == null ? 'not measured' : `${n(row.settlements_gained)} settlements + ${n(row.levels_gained)} levels`}
          </div>
        </Card>
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">confirmed</div>
          <div className="num mt-0.5 text-xl">
            {row.confirm_rate?.of ? `${((100 * row.confirm_rate.n) / row.confirm_rate.of).toFixed(0)}%` : '—'}
          </div>
          <div className="mt-1.5">
            <Bar rate={row.confirm_rate ?? null} width={120} />
          </div>
        </Card>
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">growth</div>
          {row.growth_state === 'measured' ? (
            <>
              <div className="num mt-0.5 text-xl">
                {n(row.first_settlements)} → {n(row.peak_settlements)}
              </div>
              <div className="text-dim mt-1 text-2xs">
                settlements · lord level {n(row.first_lord_level)} → {n(row.peak_lord_level)} · turns {n(row.first_turn)}–{n(row.last_measured_turn)}
              </div>
            </>
          ) : (
            <div className="text-dim mt-1.5 text-2xs">
              {row.growth_state === 'single_turn'
                ? 'only one turn was recorded, so there is no span'
                : 'no turn was recorded for this campaign'}
            </div>
          )}
        </Card>
      </div>

      <Section
        title="turn series"
        scope={{
          text: 'the reward inputs this campaign was judged on',
          detail: pts.length < 4 ? 'fewer than 4 recorded turns — deltas instead of trends' : undefined,
        }}
      >
        {pts.length === 0 ? (
          <Card className="text-dim px-4 py-6 text-center text-sm">
            no turn was recorded for this campaign
          </Card>
        ) : pts.length < 4 ? (
          <Card className="flex flex-wrap gap-x-8 gap-y-2 px-4 py-3">
            {charted.map((s) => {
              const d = seriesDelta(pts, s.key)
              return d ? (
                <span key={s.key} className="text-xs">
                  <span className="text-dim">{s.label} </span>
                  <b className="num">
                    {n(d.a)} → {n(d.b)}
                  </b>
                  {d.d !== 0 && <span className="num text-dim"> ({d.d > 0 ? '+' : ''}{n(d.d)})</span>}
                </span>
              ) : null
            })}
          </Card>
        ) : (
          <div className="flex flex-wrap gap-3">
            {charted.map((s) => {
              const d = seriesDelta(pts, s.key)
              return (
                <Steps
                  key={s.key}
                  label={s.label}
                  delta={d ? `${n(d.a)} → ${n(d.b)}${d.d ? ` (${d.d > 0 ? '+' : ''}${n(d.d)})` : ''}` : '—'}
                  values={pts.map((p) => p[s.key] as number | null | undefined)}
                  turns={pts.map((p) => p.turn)}
                />
              )
            })}
          </div>
        )}
        {flat.length > 0 && (
          <div className="text-dim mt-2 text-2xs">
            {flat.map((s) => `${s.label} ${n(pts[0][s.key] as number)}`).join(' · ')} — unchanged all campaign
          </div>
        )}
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="turn ledger" scope={{ text: 'one row per turn, newest first · a turn links to its decisions' }}>
          <DataTable
            rows={turns}
            cols={turnCols}
            rowId={(r) => String(r.turn)}
            onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(campaignKey)}?tab=decisions&turn=${r.turn}`)}
            dense
            emptyWhat="no turn was recorded"
          />
        </Section>
        <Section title="diplomacy" scope={{ text: 'by counterpart faction · absence rolls up instead of rendering as dashes' }}>
          <DiploDigest events={data.diplomacy} />
        </Section>
      </div>
    </div>
  )
}

function DecisionsTab({ campaignKey }: { campaignKey: string }) {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi<CampaignDecisions>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}/decisions`,
    [campaignKey],
    { live: false },
  )
  const [result, setResult] = useState('')
  const [policy, setPolicy] = useState('')
  const [action, setAction] = useState('')
  const turnParam = params.get('turn')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const rows = data.rows ?? []
  const policies = [...new Map(rows.filter((r) => r.policy).map((r) => [r.policy!.raw, r.policy!])).values()]
  const actions = [...new Map(rows.filter((r) => r.action_type).map((r) => [r.action_type!.raw, r.action_type!])).values()]
  const confirmed = rows.filter((r) => r.result?.raw === 'confirmed').length
  const refused = rows.filter((r) => r.result?.raw === 'refused').length
  const filtered = rows.filter(
    (r) =>
      (!result || r.result?.raw === result) &&
      (!policy || r.policy?.raw === policy) &&
      (!action || r.action_type?.raw === action) &&
      (!turnParam || String(r.turn) === turnParam),
  )
  const byTurn = new Map<number, DecisionRow[]>()
  for (const r of filtered) {
    const t = r.turn ?? 0
    byTurn.set(t, [...(byTurn.get(t) ?? []), r])
  }
  const groups = [...byTurn.entries()].sort((a, b) => b[0] - a[0])
  const seg = (label: string, count: number, val: string) => (
    <button
      key={val || 'all'}
      onClick={() => setResult(val === result ? '' : val)}
      className={cn('px-2 py-0.5', result === val ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg')}
    >
      {label} {n(count)}
    </button>
  )
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-2xs">
        <span className="border-line flex overflow-hidden rounded-md border">
          {seg('all', rows.length, '')}
          {seg('confirmed', confirmed, 'confirmed')}
          {seg('refused', refused, 'refused')}
        </span>
        <span className="text-dim ml-1">policy</span>
        {policies.map((p) => (
          <button
            key={p.raw}
            onClick={() => setPolicy(policy === p.raw ? '' : p.raw)}
            className={cn(
              'rounded-full px-2 py-0.5',
              policy === p.raw ? 'bg-raised text-fg font-semibold' : 'bg-surface border-line border hover:text-fg',
            )}
          >
            {p.label} {n(rows.filter((r) => r.policy?.raw === p.raw).length)}
          </button>
        ))}
        <span className="text-dim ml-1">action</span>
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="border-line bg-surface rounded-md border px-2 py-1"
        >
          <option value="">all types</option>
          {actions.map((a) => (
            <option key={a.raw} value={a.raw}>
              {a.label}
            </option>
          ))}
        </select>
        {turnParam && (
          <button
            onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('turn')
              setParams(next, { replace: true })
            }}
            className="bg-accent-soft text-accent rounded-full px-2 py-0.5"
          >
            turn {turnParam} ×
          </button>
        )}
        <span className="text-dim num ml-auto">
          {filtered.length} of {rows.length}
        </span>
      </div>
      {groups.length === 0 && <Card className="text-dim px-4 py-6 text-center text-sm">no decision matches these filters</Card>}
      {groups.map(([turn, list]) => {
        const ts = list.map((r) => r.ts).filter((t): t is number => t != null)
        const span = ts.length > 1 ? Math.round(Math.max(...ts) - Math.min(...ts)) : null
        const ref = list.filter((r) => r.result?.raw === 'refused').length
        return (
          <Card key={turn} className="overflow-hidden">
            <div className="border-line bg-surface sticky top-0 z-10 border-b px-3 py-1.5 text-2xs">
              <b>turn {turn}</b>
              <span className="text-dim">
                {' '}· {list.length} decision{list.length === 1 ? '' : 's'}
                {ref ? ` · ${ref} refused` : ''}
                {ts.length > 0 && ` · ${clock(Math.min(...ts))} → ${clock(Math.max(...ts))}`}
              </span>
              {span != null && span > 0 && <span className="num text-dim"> · {span}s</span>}
            </div>
            <table className="w-full text-xs">
              <tbody>
                {list.map((r) => (
                  <tr
                    key={r.decision_id}
                    onClick={() => navigate(`/decisions/${r.decision_id}`)}
                    className="border-line hover:bg-raised cursor-pointer border-b last:border-0"
                  >
                    <td className="num text-dim w-20 px-3 py-1.5">{clock(r.ts)}</td>
                    <td className="w-32 px-2 py-1.5">{r.action_type?.label ?? '—'}</td>
                    <td className="px-2 py-1.5">
                      {r.target ? (
                        <span title={r.action_key ?? undefined}>{r.target}</span>
                      ) : (
                        <span className="num text-dim text-2xs">{r.action_key ?? '—'}</span>
                      )}
                    </td>
                    <td className="w-24 px-2 py-1.5">
                      {r.result ? <Chip state={r.result_state ?? 'neutral'}>{r.result.label}</Chip> : '—'}
                    </td>
                    <td className="text-dim w-32 px-2 py-1.5">{r.policy?.label ?? '—'}</td>
                    <td className="text-dim w-6 px-2 py-1.5 text-right">›</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )
      })}
    </div>
  )
}

export function CampaignDetail() {
  const { campaignKey = '' } = useParams()
  const tab = useSubView(TABS, 'tab')
  const [seen, setSeen] = useState<Record<string, boolean>>({})
  useEffect(() => {
    setSeen((s) => (s[tab] ? s : { ...s, [tab]: true }))
  }, [tab])
  const { data, error, loading, reload } = useApi<Detail>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}`,
    [campaignKey],
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const row = data.row
  const startHref = row.faction_key
    ? `/starts/${encodeURIComponent(row.campaign_map?.raw ?? '')}/${encodeURIComponent(row.faction_key)}`
    : '/campaigns?view=starts'
  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
          <span>
            <EntityLink to="/campaigns?view=campaigns" className="text-dim">campaigns</EntityLink>
            <span className="text-dim"> / </span>
            <EntityLink to={startHref} className="text-dim">
              {row.leader ?? row.campaign.label} on {row.campaign_map?.label ?? '—'}
            </EntityLink>
            <span className="text-dim num"> / {row.campaign.tag ?? row.campaign.raw.slice(-12)}</span>
          </span>
          {row.ended_when && <span className="text-dim num">{row.ended_when}</span>}
        </div>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <IdentLabel ident={row.campaign} className="text-lg font-semibold" />
          {row.outcome && <Chip state={row.outcome_state ?? 'neutral'}>{row.outcome.label}</Chip>}
          {row.suspicious && <Chip state="bad">suspicious</Chip>}
          {data.verdict?.kind && <span className="text-dim text-xs">{data.verdict.text.replace(/\.$/, '').toLowerCase()}</span>}
        </h1>
        <div className="text-dim mt-1 flex flex-wrap gap-4 text-2xs">
          <span>
            turn <b className="num text-fg">{n(row.turns)}</b>
          </span>
          <span>
            <b className="num text-fg">{n(row.decisions)}</b> decisions
          </span>
          {row.reward != null && (
            <span>
              reward <b className="num text-fg">{n(row.reward)}</b>
            </span>
          )}
          {row.confirm_rate?.of ? (
            <span>
              confirmed <b className="num text-fg">{((100 * row.confirm_rate.n) / row.confirm_rate.of).toFixed(0)}%</b>{' '}
              ({n(row.confirm_rate.n)}/{n(row.confirm_rate.of)})
            </span>
          ) : null}
          {row.pick_id != null && (
            <EntityLink to={`/campaigns?view=selector&pick=${row.pick_id}`} className="text-dim">
              UCB pick <b className="num text-fg">#{row.pick_id}</b>
            </EntityLink>
          )}
        </div>
      </div>

      <SubNav views={TABS} param="tab" />
      <div className={tab === 'overview' ? '' : 'hidden'}>
        {seen.overview && <OverviewTab data={data} campaignKey={campaignKey} />}
      </div>
      <div className={tab === 'decisions' ? '' : 'hidden'}>
        {seen.decisions && <DecisionsTab campaignKey={campaignKey} />}
      </div>
    </div>
  )
}
