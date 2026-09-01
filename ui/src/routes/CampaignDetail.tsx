import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
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
  type CampaignBuildingsPage,
  type CampaignCharacter,
  type CampaignDetail as Detail,
  type CampaignStatePage,
  type Current,
  type CampaignItemsPage,
  type CampaignResearchPage,
  type CampaignSkillsPage,
  type Schemas,
} from '@/lib/api'
import { ago, n } from '@/lib/format'

type RewardPoint = Schemas['RewardPoint']
type DiploEvent = Schemas['DiploEvent']

const TABS = [
  { key: 'overview', label: 'overview', asks: 'what happened and why it ended' },
  { key: 'buildings', label: 'buildings', asks: 'what it built, where and for how much' },
  { key: 'research', label: 'research', asks: 'what it researched and when it finished' },
  { key: 'skills', label: 'skills', asks: 'every point its characters spent' },
  { key: 'items', label: 'items', asks: 'who wore what' },
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

function StateCards({ campaignKey }: { campaignKey: string }) {
  const { data } = useApi<CampaignStatePage>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}/state`,
    [campaignKey],
  )
  if (!data) return null
  const lord = data.lord
  const worn = data.equipped ?? []
  const pool = (data.pool ?? []).filter((p) => !worn.some((w) => w.raw === p.raw))
  return (
    <Section title="state" scope={{ text: 'from the latest recorded snapshot' }}>
      <div className="grid gap-3 lg:grid-cols-3">
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">lord</div>
          {lord ? (
            <>
              <div className="mt-1 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
                <span className="text-dim">
                  rank <b className="num text-fg text-sm">{lord.rank ?? '—'}</b>
                </span>
                {lord.hp != null && (
                  <span className="text-dim">
                    hp <b className="num text-fg text-sm">{n(lord.hp)}%</b>
                  </span>
                )}
                {lord.skill_points != null && lord.skill_points > 0 && (
                  <span className="text-dim">
                    unspent points <b className="num text-fg text-sm">{n(lord.skill_points)}</b>
                  </span>
                )}
                {lord.wounded && <Chip state="bad">wounded</Chip>}
              </div>
              {lord.region && <div className="text-dim mt-1.5 text-xs">at {lord.region}</div>}
            </>
          ) : (
            <div className="text-dim mt-1.5 text-xs">no lord snapshot</div>
          )}
        </Card>
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">progress</div>
          <div className="mt-1 text-xs">
            researching{' '}
            {data.research ? (
              <EntityLink to={`/research/${encodeURIComponent(data.research.raw)}`} title={data.research.raw}>
                {data.research.label}
              </EntityLink>
            ) : (
              <span className="text-dim">nothing</span>
            )}
          </div>
          <div className="text-dim mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span>
              researched <b className="num text-fg">{n(data.researched_n)}</b>
            </span>
            <span>
              built <b className="num text-fg">{n(data.built_n)}</b>
            </span>
            <span>
              skill ranks <b className="num text-fg">{n(data.ranked_n)}</b>
            </span>
          </div>
        </Card>
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">items</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {worn.length === 0 && <span className="text-dim">nothing equipped</span>}
            {worn.map((it) => (
              <EntityLink key={it.raw} to={`/items/${encodeURIComponent(it.raw)}`} title={it.raw}>
                {it.label}
              </EntityLink>
            ))}
          </div>
          {pool.length > 0 && (
            <div className="text-dim mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-2xs">
              <span className="uppercase tracking-wide">in the pool</span>
              {pool.map((it) => (
                <EntityLink key={it.raw} to={`/items/${encodeURIComponent(it.raw)}`} title={it.raw} className="text-dim">
                  {it.label}
                </EntityLink>
              ))}
            </div>
          )}
        </Card>
      </div>
    </Section>
  )
}

function OverviewTab({ data, campaignKey }: { data: Detail; campaignKey: string }) {
  const row = data.row
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
      render: (r) => <span className="num">{r.turn}</span>,
    },
    { key: 'income', label: 'income', align: 'right', value: (r) => byTurn.get(r.turn)?.income ?? 0, render: (r) => <span className="num">{n(byTurn.get(r.turn)?.income)}</span> },
    { key: 'setts', label: 'setts', align: 'right', value: (r) => byTurn.get(r.turn)?.settlements ?? 0, render: (r) => <span className="num">{n(byTurn.get(r.turn)?.settlements)}</span> },
    { key: 'rank', label: 'rank', align: 'right', value: (r) => byTurn.get(r.turn)?.power_rank ?? 0, render: (r) => <span className="num">{n(byTurn.get(r.turn)?.power_rank)}</span> },
  ]
  return (
    <div className="space-y-7">
      <div className="grid gap-3 lg:grid-cols-2">
        <Card className="px-3.5 py-3">
          <div className="text-dim text-2xs uppercase tracking-wide">reward</div>
          <div className="num mt-0.5 text-xl">{row.reward == null ? '—' : n(row.reward)}</div>
          <div className="text-dim mt-1 text-2xs">
            {row.reward == null ? 'not measured' : `${n(row.settlements_gained)} settlements + ${n(row.levels_gained)} levels`}
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

      <StateCards campaignKey={campaignKey} />

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
        <Section
          title="turn ledger"
          scope={{ text: 'one row per turn, newest first' }}
        >
          <DataTable
            rows={turns}
            cols={turnCols}
            rowId={(r) => String(r.turn)}
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

function BuildingsTab({ campaignKey }: { campaignKey: string }) {
  const { data, error, loading, reload } = useApi<CampaignBuildingsPage>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}/buildings`,
    [campaignKey],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={5} />
  return (
    <Section
      title="construction ledger"
      scope={{
        text: `every construction, upgrade, repair and dismantle, in order · ${n(data.constructed)} built, ${n(data.total_cost)} gold in total`,
        detail: 'a dismantle’s cost is its refund, negative',
      }}
    >
      <Card className="overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-line text-dim border-b text-left text-2xs">
              <th className="px-3 py-1.5 text-right font-normal">turn</th>
              <th className="px-3 py-1.5 font-normal">action</th>
              <th className="px-3 py-1.5 font-normal">building</th>
              <th className="px-3 py-1.5 font-normal">category</th>
              <th className="px-3 py-1.5 text-right font-normal">level</th>
              <th className="px-3 py-1.5 font-normal">region</th>
              <th className="px-3 py-1.5 text-right font-normal">cost</th>
            </tr>
          </thead>
          <tbody>
            {(data.rows ?? []).map((r, i) => (
              <tr key={i} className="border-line border-b last:border-0">
                <td className="num px-3 py-1.5 text-right">{r.turn ?? '—'}</td>
                <td className="px-3 py-1.5">
                  <Chip state={r.kind === 'dismantle' ? 'warn' : 'neutral'}>{r.kind}</Chip>
                </td>
                <td className="px-3 py-1.5">
                  <EntityLink to={`/buildings/${encodeURIComponent(r.key)}`} title={r.key}>
                    {r.label ?? r.key}
                  </EntityLink>
                </td>
                <td className="text-dim px-3 py-1.5">{r.category ?? '—'}</td>
                <td className="num px-3 py-1.5 text-right">{r.level ?? '—'}</td>
                <td className="text-dim px-3 py-1.5">{r.region ?? '—'}</td>
                <td className="num px-3 py-1.5 text-right">{r.cost == null ? '—' : n(r.cost)}</td>
              </tr>
            ))}
            {!(data.rows ?? []).length && (
              <tr>
                <td colSpan={7} className="text-dim px-3 py-4 text-center">nothing was built</td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </Section>
  )
}

function ResearchTab({ campaignKey }: { campaignKey: string }) {
  const { data, error, loading, reload } = useApi<CampaignResearchPage>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}/research`,
    [campaignKey],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={5} />
  return (
    <Section
      title="research timeline"
      scope={{
        text: `one start per turn (faction-wide cap) · ${n(data.completed)} completed of ${n((data.rows ?? []).length)} started, tree of ${n(data.universe)}`,
        detail: 'completion is inferred from the next turn’s offer set',
      }}
    >
      <Card className="overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-line text-dim border-b text-left text-2xs">
              <th className="px-3 py-1.5 text-right font-normal">started turn</th>
              <th className="px-3 py-1.5 font-normal">tech</th>
              <th className="px-3 py-1.5 font-normal">parent</th>
              <th className="px-3 py-1.5 text-right font-normal">tier</th>
              <th className="px-3 py-1.5 text-right font-normal">points</th>
              <th className="px-3 py-1.5 text-right font-normal">completed turn</th>
              <th className="px-3 py-1.5 text-right font-normal">turns to complete</th>
            </tr>
          </thead>
          <tbody>
            {(data.rows ?? []).map((r, i) => (
              <tr key={`${r.key}-${i}`} className="border-line border-b last:border-0">
                <td className="num px-3 py-1.5 text-right">{r.turn ?? '—'}</td>
                <td className="px-3 py-1.5">
                  <EntityLink to={`/research/${encodeURIComponent(r.key)}`} title={r.key}>
                    {r.label ?? r.key}
                  </EntityLink>
                </td>
                <td className="px-3 py-1.5">
                  {r.parent ? (
                    <EntityLink to={`/research/${encodeURIComponent(r.parent.raw)}`} title={r.parent.raw} className="text-dim">
                      {r.parent.label}
                    </EntityLink>
                  ) : (
                    <span className="text-dim">—</span>
                  )}
                </td>
                <td className="num px-3 py-1.5 text-right">{r.tier ?? '—'}</td>
                <td className="num px-3 py-1.5 text-right">{r.points == null ? '—' : n(r.points)}</td>
                <td className="px-3 py-1.5 text-right">
                  {r.completed_turn != null ? (
                    <span className="num">{r.completed_turn}</span>
                  ) : r.in_progress ? (
                    <span className="text-dim">in progress at end</span>
                  ) : (
                    <span className="text-dim">—</span>
                  )}
                </td>
                <td className="num text-dim px-3 py-1.5 text-right">
                  {r.completed_turn != null && r.turn != null ? r.completed_turn - r.turn : '—'}
                </td>
              </tr>
            ))}
            {!(data.rows ?? []).length && (
              <tr>
                <td colSpan={7} className="text-dim px-3 py-4 text-center">no research was started</td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </Section>
  )
}

function CharacterChips({ chars }: { chars: CampaignCharacter[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-2xs">
      {chars.map((c) => (
        <span key={c.cqi} className="bg-surface border-line rounded-full border px-2 py-0.5">
          {c.label ?? c.kind} · {c.kind}
          {c.rank != null && <span className="num"> · rank {c.rank}</span>}
          {c.points_unspent != null && c.points_unspent > 0 && (
            <span className="text-dim num"> · {c.points_unspent} pts unspent</span>
          )}
        </span>
      ))}
    </div>
  )
}

function SkillsTab({ campaignKey }: { campaignKey: string }) {
  const { data, error, loading, reload } = useApi<CampaignSkillsPage>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}/skills`,
    [campaignKey],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={5} />
  return (
    <div className="space-y-4">
      <CharacterChips chars={data.characters ?? []} />
      <Section title="skill ledger" scope={{ text: 'every point spent, in order · ranks read from live snapshots' }}>
        <Card className="overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-line text-dim border-b text-left text-2xs">
                <th className="px-3 py-1.5 text-right font-normal">turn</th>
                <th className="px-3 py-1.5 font-normal">character</th>
                <th className="px-3 py-1.5 font-normal">skill</th>
                <th className="px-3 py-1.5 text-right font-normal">rank</th>
                <th className="px-3 py-1.5 text-right font-normal">of max</th>
              </tr>
            </thead>
            <tbody>
              {(data.rows ?? []).map((r, i) => (
                <tr key={i} className="border-line border-b last:border-0">
                  <td className="num px-3 py-1.5 text-right">{r.turn ?? '—'}</td>
                  <td className="text-dim px-3 py-1.5">{r.character ?? '—'}</td>
                  <td className="px-3 py-1.5">
                    <EntityLink to={`/skills/${encodeURIComponent(r.key)}`} title={r.key}>
                      {r.label ?? r.key}
                    </EntityLink>
                  </td>
                  <td className="num px-3 py-1.5 text-right">{r.rank ?? '—'}</td>
                  <td className="num text-dim px-3 py-1.5 text-right">{r.max_ranks ?? '—'}</td>
                </tr>
              ))}
              {!(data.rows ?? []).length && (
                <tr>
                  <td colSpan={5} className="text-dim px-3 py-4 text-center">no skill point was spent</td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </Section>
    </div>
  )
}

function ItemsTab({ campaignKey }: { campaignKey: string }) {
  const { data, error, loading, reload } = useApi<CampaignItemsPage>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}/items`,
    [campaignKey],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={5} />
  const chars = data.characters ?? []
  return (
    <div className="space-y-7">
      <Section title="item ledger" scope={{ text: 'every equip and unequip, in order' }}>
        <Card className="overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-line text-dim border-b text-left text-2xs">
                <th className="px-3 py-1.5 text-right font-normal">turn</th>
                <th className="px-3 py-1.5 font-normal">character</th>
                <th className="px-3 py-1.5 font-normal">action</th>
                <th className="px-3 py-1.5 font-normal">item</th>
                <th className="px-3 py-1.5 font-normal">category</th>
              </tr>
            </thead>
            <tbody>
              {(data.events ?? []).map((r, i) => (
                <tr key={i} className="border-line border-b last:border-0">
                  <td className="num px-3 py-1.5 text-right">{r.turn ?? '—'}</td>
                  <td className="text-dim px-3 py-1.5">{r.character ?? '—'}</td>
                  <td className="px-3 py-1.5">
                    <Chip state={r.action === 'unequip' ? 'warn' : 'neutral'}>{r.action}</Chip>
                  </td>
                  <td className="px-3 py-1.5">
                    <EntityLink to={`/items/${encodeURIComponent(r.key)}`} title={r.key}>
                      {r.label ?? r.key}
                    </EntityLink>
                  </td>
                  <td className="text-dim px-3 py-1.5">{r.category ?? '—'}</td>
                </tr>
              ))}
              {!(data.events ?? []).length && (
                <tr>
                  <td colSpan={5} className="text-dim px-3 py-4 text-center">no item was equipped or unequipped</td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </Section>
      <Section title="at campaign end" scope={{ text: 'who wore what, and what stayed benched · the pool is a lower bound' }}>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="overflow-hidden">
            <table className="w-full text-xs">
              <tbody>
                {chars.map((c) => (
                  <tr key={c.cqi} className="border-line border-b last:border-0 align-top">
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      {c.label ?? c.kind}
                      <span className="num text-dim ml-1.5 text-2xs">{c.slots} slots</span>
                    </td>
                    <td className="px-3 py-1.5">
                      {(c.wearing ?? []).length ? (
                        (c.wearing ?? []).map((w, i) => (
                          <span key={w.raw}>
                            {i > 0 && <span className="text-dim"> · </span>}
                            <EntityLink to={`/items/${encodeURIComponent(w.raw)}`} title={w.raw} className="text-2xs">
                              {w.label}
                            </EntityLink>
                          </span>
                        ))
                      ) : (
                        <span className="text-dim">nothing equipped</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card className="px-3 py-2">
            <div className="text-dim text-2xs uppercase tracking-wide">benched in the pool · ≥ {(data.pool ?? []).length}</div>
            <div className="mt-1.5 text-xs">
              {(data.pool ?? []).length ? (
                (data.pool ?? []).map((w, i) => (
                  <span key={w.raw}>
                    {i > 0 && <span className="text-dim"> · </span>}
                    <EntityLink to={`/items/${encodeURIComponent(w.raw)}`} title={w.raw} className="text-2xs">
                      {w.label}
                    </EntityLink>
                  </span>
                ))
              ) : (
                <span className="text-dim">no free item in the pool</span>
              )}
            </div>
          </Card>
        </div>
      </Section>
    </div>
  )
}

export function CampaignView({ campaignKey, playingNow }: { campaignKey: string; playingNow?: Current }) {
  const tab = useSubView(TABS, 'tab')
  const [seen, setSeen] = useState<Record<string, boolean>>({})
  useEffect(() => {
    setSeen((s) => (s[tab] ? s : { ...s, [tab]: true }))
  }, [tab])
  const { data, error, loading, reload } = useApi<Detail>(
    `/api/campaigns/${encodeURIComponent(campaignKey)}`,
    [campaignKey],
  )
  if (error && playingNow) {
    return (
      <Card className="text-dim px-4 py-6 text-center text-sm">
        the campaign just started — its first snapshot is still being recorded
      </Card>
    )
  }
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
          {playingNow ? (
            <span className="text-ok text-2xs">
              playing now{playingNow.age_seconds != null && <span className="text-dim"> · state {ago(playingNow.age_seconds)}</span>}
            </span>
          ) : row.ended_when ? (
            <span className="text-dim num">{row.ended_when}</span>
          ) : null}
        </div>
        <h1 className="mt-1 flex flex-wrap items-baseline gap-3">
          <IdentLabel ident={row.campaign} className="text-lg font-semibold" />
        </h1>
        <div className="text-dim mt-1 flex flex-wrap gap-4 text-2xs">
          <span>
            turn <b className="num text-fg">{n(row.turns)}</b>
          </span>
          {row.reward != null && (
            <span>
              reward <b className="num text-fg">{n(row.reward)}</b>
            </span>
          )}
        </div>
      </div>

      <SubNav views={TABS} param="tab" />
      <div className={tab === 'overview' ? '' : 'hidden'}>
        {seen.overview && <OverviewTab data={data} campaignKey={campaignKey} />}
      </div>
      <div className={tab === 'buildings' ? '' : 'hidden'}>
        {seen.buildings && <BuildingsTab campaignKey={campaignKey} />}
      </div>
      <div className={tab === 'research' ? '' : 'hidden'}>
        {seen.research && <ResearchTab campaignKey={campaignKey} />}
      </div>
      <div className={tab === 'skills' ? '' : 'hidden'}>
        {seen.skills && <SkillsTab campaignKey={campaignKey} />}
      </div>
      <div className={tab === 'items' ? '' : 'hidden'}>
        {seen.items && <ItemsTab campaignKey={campaignKey} />}
      </div>
    </div>
  )
}

export function CampaignDetail() {
  const { campaignKey = '' } = useParams()
  return <CampaignView campaignKey={campaignKey} />
}
