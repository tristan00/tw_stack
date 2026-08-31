import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronRight, Plus, X } from 'lucide-react'
import { signedNum } from '@/components/catalog'
import { Card, EntityLink, ErrorState, Help, MetricTile, Section, Skeleton } from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import {
  post,
  useApi,
  type CatalogIndexPage,
  type ItemsPage,
  type PositionFacetOption,
  type PositionKeyRow,
  type PositionsPage,
  type PositionTypeRow,
  type RewardWeightsPage,
} from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

const TABS = [
  { key: 'lookup', label: 'lookup', asks: 'what gets taken in situations like this' },
  { key: 'weights', label: 'reward weights', asks: 'what one unit of each gain is worth in the analytics reward' },
]

const RANGE_DIMS = [
  { key: 'turn', label: 'turn' },
  { key: 'settlements', label: 'settlements' },
  { key: 'income', label: 'income' },
  { key: 'treasury', label: 'treasury' },
  { key: 'armies', label: 'armies' },
  { key: 'heroes', label: 'heroes' },
  { key: 'lord_level', label: 'lord level' },
  { key: 'power_rank', label: 'power rank' },
  { key: 'allies', label: 'allies' },
  { key: 'vassals', label: 'vassals' },
]

const FLAG_DIMS = [
  { key: 'is_researching', label: 'researching' },
  { key: 'll_wounded', label: 'lord wounded' },
]

const HIST_DIMS = [
  { key: 'settlement', label: 'captured settlement', verb: 'captured' },
  { key: 'building', label: 'constructed building', verb: 'constructed' },
  { key: 'research', label: 'researched tech', verb: 'researched' },
  { key: 'skills', label: 'ranked skill', verb: 'ranked' },
  { key: 'items', label: 'equipped item', verb: 'equipped' },
]

const LINKED: Record<string, string> = {
  items: '/items',
  item_unequip: '/items',
  building: '/buildings',
  research: '/research',
  skills: '/skills',
}

const rangeText = (lo: string, hi: string) =>
  lo && hi ? `${lo}–${hi}` : lo ? `≥ ${lo}` : hi ? `≤ ${hi}` : ''

function condFamilies(conds: string[]): Set<string> {
  const out = new Set<string>()
  for (const c of conds) {
    const p = c.split(':')
    if ((p[0] === 'has' || p[0] === 'not') && p[1]) out.add(p[1])
  }
  return out
}

const num = (v: number | null | undefined, digits = 2) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>

function KeyRows({ row }: { row: PositionTypeRow }) {
  const to = LINKED[row.action_type.raw]
  return (
    <>
      {(row.keys ?? []).map((k: PositionKeyRow, i) => (
        <tr key={k.key || `rest-${i}`} className="border-line/60 bg-raised/30 border-b text-2xs">
          <td className="py-1 pr-3 pl-10">
            {k.key && to ? (
              <EntityLink to={`${to}/${encodeURIComponent(k.key)}`} title={k.key}>
                {k.label ?? k.key}
              </EntityLink>
            ) : (
              <span title={k.key || undefined} className={cn(!k.key && 'text-dim')}>
                {k.label ?? k.key}
              </span>
            )}
          </td>
          <td className="num px-3 py-1 text-right">{n(k.n)}</td>
          <td className="px-3 py-1 text-right" />
          <td className="px-3 py-1 text-right">{num(k.avg_reward)}</td>
          <td className="px-3 py-1 text-right">{num(k.avg_future)}</td>
          <td className="px-3 py-1 text-right">{signedNum(k.delta_future)}</td>
        </tr>
      ))}
    </>
  )
}

function NumInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value.replace(/[^-\d.]/g, ''))}
      placeholder={placeholder}
      className="border-line bg-surface num w-16 rounded-md border px-1.5 py-1 text-right text-xs"
    />
  )
}

function KeySelect({
  options,
  value,
  onChange,
  what,
}: {
  options: { key: string; label: string }[]
  value: string
  onChange: (v: string) => void
  what: string
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="border-line bg-surface max-w-64 rounded-md border px-2 py-1 text-xs">
      <option value="">pick a {what}…</option>
      {options.map((o) => (
        <option key={o.key} value={o.key}>{o.label}</option>
      ))}
    </select>
  )
}

function Composer({
  data,
  keyOptions,
  onAdd,
  onFamily,
}: {
  data: PositionsPage
  keyOptions: (family: string) => { key: string; label: string }[]
  onAdd: (cond: string) => void
  onFamily: (family: string) => void
}) {
  const [dim, setDim] = useState('')
  const [lo, setLo] = useState('')
  const [hi, setHi] = useState('')
  const [key, setKey] = useState('')
  const [has, setHas] = useState(true)
  const reset = () => {
    setDim('')
    setLo('')
    setHi('')
    setKey('')
    setHas(true)
  }
  const kind = dim.startsWith('hist:') ? 'hist' : dim.startsWith('flag:') ? 'flag' : dim === 'res' || dim === 'hero' ? 'sparse' : dim ? 'range' : ''
  const family = kind === 'hist' ? dim.slice(5) : ''
  const canAdd =
    kind === 'range' ? Boolean(lo || hi)
    : kind === 'sparse' ? Boolean(key && (lo || hi))
    : kind === 'hist' ? Boolean(key)
    : false
  const add = (cond: string) => {
    onAdd(cond)
    reset()
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <select
        value={dim}
        onChange={(e) => {
          setDim(e.target.value)
          setLo('')
          setHi('')
          setKey('')
          if (e.target.value.startsWith('hist:')) onFamily(e.target.value.slice(5))
        }}
        className="border-line bg-surface rounded-md border px-2 py-1"
      >
        <option value="">add a condition…</option>
        <optgroup label="situation">
          {RANGE_DIMS.map((d) => (
            <option key={d.key} value={d.key}>{d.label}</option>
          ))}
          {(data.resources ?? []).length > 0 && <option value="res">faction resource…</option>}
          {(data.hero_types ?? []).length > 0 && <option value="hero">hero type count…</option>}
          {FLAG_DIMS.map((d) => (
            <option key={d.key} value={`flag:${d.key}`}>{d.label} (yes/no)</option>
          ))}
        </optgroup>
        <optgroup label="history — has it done this yet">
          {HIST_DIMS.map((d) => (
            <option key={d.key} value={`hist:${d.key}`}>{d.label}</option>
          ))}
        </optgroup>
      </select>
      {kind === 'range' && (
        <>
          <NumInput value={lo} onChange={setLo} placeholder="min" />
          <span className="text-dim">–</span>
          <NumInput value={hi} onChange={setHi} placeholder="max" />
        </>
      )}
      {kind === 'sparse' && (
        <>
          <KeySelect
            options={(dim === 'res' ? data.resources : data.hero_types) ?? []}
            value={key}
            onChange={setKey}
            what={dim === 'res' ? 'resource' : 'hero type'}
          />
          <NumInput value={lo} onChange={setLo} placeholder="min" />
          <span className="text-dim">–</span>
          <NumInput value={hi} onChange={setHi} placeholder="max" />
        </>
      )}
      {kind === 'flag' && (
        <>
          <button onClick={() => add(`flag:${dim.slice(5)}:1`)} className="border-line bg-surface hover:text-fg rounded-md border px-2 py-1">yes</button>
          <button onClick={() => add(`flag:${dim.slice(5)}:0`)} className="border-line bg-surface hover:text-fg rounded-md border px-2 py-1">no</button>
        </>
      )}
      {kind === 'hist' && (
        <>
          <span className="border-line flex overflow-hidden rounded-md border text-2xs">
            <button onClick={() => setHas(true)} className={cn('px-2 py-1', has ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg')}>has</button>
            <button onClick={() => setHas(false)} className={cn('px-2 py-1', !has ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg')}>has not</button>
          </span>
          <KeySelect options={keyOptions(family)} value={key} onChange={setKey} what={HIST_DIMS.find((d) => d.key === family)?.label ?? 'key'} />
        </>
      )}
      {(kind === 'range' || kind === 'sparse' || kind === 'hist') && (
        <button
          disabled={!canAdd}
          onClick={() =>
            add(
              kind === 'range'
                ? `${dim}:${lo}:${hi}`
                : kind === 'sparse'
                  ? `${dim}:${key}:${lo}:${hi}`
                  : `${has ? 'has' : 'not'}:${family}:${key}`,
            )
          }
          className="border-line bg-surface hover:text-fg inline-flex items-center gap-1 rounded-md border px-2 py-1 disabled:opacity-40"
        >
          <Plus className="size-3" /> add
        </button>
      )}
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

function LookupTab() {
  const [params, setParams] = useSearchParams()
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const qs = useMemo(() => {
    const next = new URLSearchParams(params)
    next.delete('tab')
    return next.toString()
  }, [params])
  const conds = params.getAll('c')
  const { data, error, loading, reload } = useApi<PositionsPage>(
    `/api/positions${qs ? `?${qs}` : ''}`,
    [qs],
    { live: false },
  )
  const famNeeded = useMemo(() => condFamilies(conds), [qs])
  const [composerFamily, setComposerFamily] = useState('')
  const need = (f: string) => famNeeded.has(f) || composerFamily === f
  const bIdx = useApi<CatalogIndexPage>(need('building') ? '/api/buildings' : null, [need('building')], { live: false })
  const rIdx = useApi<CatalogIndexPage>(need('research') ? '/api/research' : null, [need('research')], { live: false })
  const sIdx = useApi<CatalogIndexPage>(need('skills') ? '/api/skills' : null, [need('skills')], { live: false })
  const iIdx = useApi<ItemsPage>(need('items') ? '/api/items' : null, [need('items')], { live: false })
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }
  const addCond = (cond: string) => {
    const next = new URLSearchParams(params)
    next.append('c', cond)
    setParams(next, { replace: true })
  }
  const delCond = (at: number) => {
    const next = new URLSearchParams(params)
    next.delete('c')
    conds.forEach((c, i) => i !== at && next.append('c', c))
    setParams(next, { replace: true })
  }
  const keyOptions = (family: string): { key: string; label: string }[] => {
    if (family === 'settlement') return (data?.settlements ?? []).map((s) => ({ key: s.key, label: `${s.label} · ${s.campaigns}` }))
    const idx = family === 'building' ? bIdx.data : family === 'research' ? rIdx.data : family === 'skills' ? sIdx.data : family === 'items' ? iIdx.data : null
    return (idx?.rows ?? []).map((r: { key: string; label?: string | null }) => ({ key: r.key, label: r.label ?? r.key }))
  }
  const keyLabel = (family: string, key: string) => {
    if (family === 'settlement') return (data?.settlements ?? []).find((s) => s.key === key)?.label ?? key
    return keyOptions(family).find((o) => o.key === key)?.label ?? key
  }
  const facet = (list: PositionFacetOption[] | undefined, key: string) => (list ?? []).find((o) => o.key === key)?.label ?? key
  const condChip = (c: string): string => {
    const p = c.split(':')
    if (p[0] === 'has' || p[0] === 'not') {
      const d = HIST_DIMS.find((x) => x.key === p[1])
      return `${p[0] === 'has' ? 'has' : 'never'} ${d?.verb ?? p[1]} ${keyLabel(p[1], p.slice(2).join(':'))}`
    }
    if (p[0] === 'flag') {
      const d = FLAG_DIMS.find((x) => x.key === p[1])
      return `${p[2] === '1' ? '' : 'not '}${d?.label ?? p[1]}`
    }
    if (p[0] === 'res') return `${facet(data?.resources, p[1])} ${rangeText(p[2] ?? '', p[3] ?? '')}`
    if (p[0] === 'hero') return `${facet(data?.hero_types, p[1])} ${rangeText(p[2] ?? '', p[3] ?? '')}`
    const d = RANGE_DIMS.find((x) => x.key === p[0])
    return `${d?.label ?? p[0]} ${rangeText(p[1] ?? '', p[2] ?? '')}`
  }
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data && loading) return <Skeleton rows={10} />
  if (!data) return null
  const sel = (k: string) => params.get(k) ?? ''
  const tiles = [
    { label: 'positions', value: n(data.decisions), sub: 'decisions matching every condition' },
    { label: 'campaigns', value: n(data.campaigns), sub: 'they belong to' },
    {
      label: 'mean campaign reward',
      value: data.mean_reward == null ? '—' : n(data.mean_reward, 2),
      sub: 'over the matching campaigns, analytics weights',
    },
    {
      label: 'mean future reward',
      value: data.mean_future == null ? '—' : n(data.mean_future, 2),
      sub: 'still to come after a matching decision',
    },
  ]
  return (
    <div className="space-y-5">
      <Card className="space-y-2 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select value={sel('faction')} onChange={(e) => set('faction', e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1">
            <option value="">every start</option>
            {(data.factions ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.label} · {f.campaigns}
              </option>
            ))}
          </select>
          <select value={sel('culture')} onChange={(e) => set('culture', e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1">
            <option value="">every race</option>
            {(data.cultures ?? []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          {(data.maps ?? []).length > 1 && (
            <select value={sel('map')} onChange={(e) => set('map', e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1">
              <option value="">every map</option>
              {(data.maps ?? []).map((m) => (
                <option key={m.raw} value={m.raw}>{m.label}</option>
              ))}
            </select>
          )}
          {conds.map((c, i) => (
            <span key={`${c}-${i}`} className="bg-accent-soft text-accent inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs">
              {condChip(c)}
              <button onClick={() => delCond(i)} title="remove this condition" className="hover:opacity-70">
                <X className="size-3" />
              </button>
            </span>
          ))}
          {qs.length > 0 && (
            <button onClick={() => setParams(new URLSearchParams(), { replace: true })} className="text-accent text-2xs hover:underline">
              clear all
            </button>
          )}
        </div>
        <Composer data={data} keyOptions={keyOptions} onAdd={addCond} onFamily={setComposerFamily} />
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub, state: 'neutral', spark: [] }} />
        ))}
      </div>

      <Section
        title="what gets taken"
        scope={{
          text: `one row per action type, biggest share first · ${n(data.takes)} confirmed takes · expand a row for its keys`,
          detail: 'rewards use the analytics weights from the reward weights tab',
        }}
      >
        <Card className="overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-line text-dim border-b text-left text-2xs">
                <th className="px-3 py-1.5 font-normal">action type</th>
                <th className="px-3 py-1.5 text-right font-normal">taken</th>
                <th className="px-3 py-1.5 text-right font-normal">share</th>
                <th className="px-3 py-1.5 text-right font-normal">
                  avg campaign reward
                  <Help>
                    The final analytics reward of the campaigns these takes belong to, averaged over the takes of this
                    type. Whole-campaign hindsight — the past is baked in, so compare types with the future column.
                  </Help>
                </th>
                <th className="px-3 py-1.5 text-right font-normal">
                  avg future reward
                  <Help>
                    What the campaign still gained after the decision: weighted (peak − current) settlements, lord
                    levels, allies and vassals at that moment, averaged over the takes of this type.
                  </Help>
                </th>
                <th className="px-3 py-1.5 text-right font-normal">
                  Δ future vs situation
                  <Help>
                    Avg future reward after takes of this type, minus the mean future reward over every matching
                    decision: did campaigns choosing this here go on to gain more than the average position?
                  </Help>
                </th>
              </tr>
            </thead>
            <tbody>
              {(data.rows ?? []).map((row) => {
                const isOpen = !!open[row.action_type.raw]
                return [
                  <tr
                    key={row.action_type.raw}
                    onClick={() => setOpen((o) => ({ ...o, [row.action_type.raw]: !isOpen }))}
                    className="border-line hover:bg-raised cursor-pointer border-b"
                  >
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                        {row.action_type.label}
                      </span>
                    </td>
                    <td className="num px-3 py-1.5 text-right">{n(row.n)}</td>
                    <td className="num text-dim px-3 py-1.5 text-right">{row.share == null ? '—' : `${n(row.share, 1)}%`}</td>
                    <td className="px-3 py-1.5 text-right">{num(row.avg_reward)}</td>
                    <td className="px-3 py-1.5 text-right">{num(row.avg_future)}</td>
                    <td className="px-3 py-1.5 text-right">{signedNum(row.delta_future)}</td>
                  </tr>,
                  isOpen ? <KeyRows key={`${row.action_type.raw}-keys`} row={row} /> : null,
                ]
              })}
              {!(data.rows ?? []).length && (
                <tr>
                  <td colSpan={6} className="text-dim px-3 py-5 text-center">no decision matches these conditions</td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </Section>
    </div>
  )
}

export function Positions() {
  const tab = useSubView(TABS, 'tab')
  return (
    <div>
      <SubNav views={TABS} param="tab" />
      {tab === 'lookup' && <LookupTab />}
      {tab === 'weights' && <WeightsTab />}
    </div>
  )
}
