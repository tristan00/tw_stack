import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ConditionBar, useConditionQuery } from '@/components/conditions'
import { useUiMode } from '@/components/Layout'
import { DataTable, type Col } from '@/components/DataTable'
import { Card, Chip, EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { post, useApi, type CampaignLookupPage, type LookupCampaignRow, type RewardWeightsPage } from '@/lib/api'
import { clock, n } from '@/lib/format'

const TABS = [
  { key: 'lookup', label: 'lookup', asks: 'which campaigns passed through situations like this, and how they went' },
  { key: 'weights', label: 'reward weights', asks: 'what one unit of each gain is worth in the analytics reward' },
]

const dash = (v: number | null | undefined, digits = 0) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>

const cols: Col<LookupCampaignRow>[] = [
  { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
  {
    key: 'campaign',
    label: 'campaign',
    value: (r) => r.campaign.tag ?? '',
    render: (r) => (
      <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw} className="num text-2xs">
        {r.campaign.tag ?? r.campaign.raw.slice(-6)}
      </EntityLink>
    ),
  },
  {
    key: 'start',
    label: 'start',
    value: (r) => r.leader ?? r.faction.label,
    render: (r) => (
      <EntityLink
        to={`/starts/${encodeURIComponent(r.campaign_map?.raw ?? '')}/${encodeURIComponent(r.faction.raw)}`}
        title={r.faction.raw}
      >
        {r.leader ?? r.faction.label}
      </EntityLink>
    ),
  },
  { key: 'race', label: 'race', optional: true, value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'first', label: 'first match', unit: 'turn', align: 'right', value: (r) => r.first_turn ?? undefined, sortUndefined: 'last', render: (r) => dash(r.first_turn) },
  { key: 'matched', label: 'matching positions', align: 'right', value: (r) => r.matched, render: (r) => <span className="num">{n(r.matched)}</span> },
  { key: 'turns', label: 'turns', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => <strong className="num">{n(r.reward, 2)}</strong> },
  { key: 'sett', label: 'settlements', unit: 'gained', align: 'right', optional: true, value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', align: 'right', optional: true, value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
  {
    key: 'outcome',
    label: 'outcome',
    value: (r) => r.outcome?.label ?? '',
    render: (r) => (r.outcome ? <Chip state={r.outcome_state ?? 'neutral'}>{r.outcome.label}</Chip> : <span className="text-dim">—</span>),
  },
]

function LookupView() {
  const navigate = useNavigate()
  const dev = useUiMode() === 'full'
  const qs = useConditionQuery()
  const { data, error, loading, reload } = useApi<CampaignLookupPage>(
    `/api/lookup${qs ? `?${qs}` : ''}`,
    [qs],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data && loading) return <Skeleton rows={10} />
  if (!data) return null
  const tiles = [
    { label: 'campaigns', value: n(data.campaigns), sub: 'ever in a matching situation' },
    { label: 'matching positions', value: n(data.decisions), sub: 'recorded moments matching every condition' },
    { label: 'mean reward', value: data.mean_reward == null ? '—' : n(data.mean_reward, 2), sub: 'of those campaigns, analytics weights' },
    { label: 'mean turns', value: data.mean_turns == null ? '—' : n(data.mean_turns, 1), sub: 'they reached' },
  ]
  return (
    <div className="space-y-5">
      <ConditionBar facets={data} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub, state: 'neutral', spark: [] }} />
        ))}
      </div>
      <Section title="matching campaigns" scope={data.scope}>
        <DataTable
          rows={data.rows ?? []}
          cols={dev ? cols : cols.filter((c) => c.key !== 'outcome')}
          rowId={(r) => r.campaign.raw}
          onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
          searchPlaceholder="search campaign, start, outcome…"
          pageSize={25}
          emptyWhat="no campaign ever passed through a matching situation"
        />
      </Section>
    </div>
  )
}

function WeightsTab() {
  const { data, error, loading, reload } = useApi<RewardWeightsPage>('/api/reward-weights', [], { live: false })
  const [draft, setDraft] = useState<Record<string, string> | null>(null)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={4} />
  const weights = data.weights ?? {}
  const current = draft ?? Object.fromEntries((data.components ?? []).map((c) => [c.key, String(weights[c.key] ?? c.default)]))
  const dirty = (data.components ?? []).some((c) => Number(current[c.key]) !== (weights[c.key] ?? c.default))
  const save = async () => {
    setSaving(true)
    setNote('')
    try {
      const body = Object.fromEntries(Object.entries(current).map(([k, v]) => [k, Number(v) || 0]))
      await post('/api/reward-weights', body)
      setDraft(null)
      setNote('saved — caches are rebuilding in the background; reward numbers refresh over the next minute or two')
      reload()
    } catch (e) {
      setNote(`could not save: ${(e as Error).message}`)
    } finally {
      setSaving(false)
    }
  }
  return (
    <Section title="reward weights" scope={data.scope}>
      <Card className="max-w-xl space-y-3 px-4 py-3">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-line text-dim border-b text-left text-2xs">
              <th className="py-1.5 font-normal">counted gain</th>
              <th className="py-1.5 text-right font-normal">weight</th>
              <th className="py-1.5 text-right font-normal">default</th>
            </tr>
          </thead>
          <tbody>
            {(data.components ?? []).map((c) => (
              <tr key={c.key} className="border-line border-b last:border-0">
                <td className="py-1.5">{c.label}</td>
                <td className="py-1.5 text-right">
                  <input
                    value={current[c.key]}
                    onChange={(e) => setDraft({ ...current, [c.key]: e.target.value.replace(/[^\d.]/g, '') })}
                    className="border-line bg-surface num w-20 rounded-md border px-2 py-1 text-right"
                  />
                </td>
                <td className="num text-dim py-1.5 text-right">{n(c.default, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex items-center gap-3">
          <button
            onClick={() => void save()}
            disabled={!dirty || saving}
            className="border-line bg-surface hover:text-fg rounded-md border px-3 py-1 text-xs disabled:opacity-40"
          >
            {saving ? 'saving…' : 'save weights'}
          </button>
          {!data.is_default && (
            <button
              onClick={() => setDraft(Object.fromEntries((data.components ?? []).map((c) => [c.key, String(c.default)])))}
              className="text-dim hover:text-fg text-2xs"
            >
              reset to defaults
            </button>
          )}
          {note && <span className="text-dim text-2xs">{note}</span>}
        </div>
        <p className="text-dim text-2xs">
          analytics reward = Σ weight × gain, per campaign (first → peak). This is not the UCB selector’s reward — the
          selector, the starts pool and the campaigns page keep the official settlements + lord levels reward.
        </p>
      </Card>
    </Section>
  )
}

export function Lookup() {
  const tab = useSubView(TABS, 'tab')
  return (
    <div>
      <SubNav views={TABS} param="tab" />
      {tab === 'lookup' && <LookupView />}
      {tab === 'weights' && <WeightsTab />}
    </div>
  )
}
