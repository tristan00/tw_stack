import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { signedNum } from '@/components/catalog'
import { useUiMode } from '@/components/Layout'
import { Card, EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { useApi, type PositionKeyRow, type PositionsPage, type PositionTypeRow } from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

const RANGES = [
  { key: 'turn', label: 'turn' },
  { key: 'settlements', label: 'settlements' },
  { key: 'income', label: 'income' },
  { key: 'power_rank', label: 'power rank' },
  { key: 'lord_level', label: 'lord level' },
]

const LINKED: Record<string, string> = {
  items: '/items',
  item_unequip: '/items',
  building: '/buildings',
  research: '/research',
  skills: '/skills',
}

function RangeInput({
  value,
  placeholder,
  onCommit,
}: {
  value: string
  placeholder: string
  onCommit: (v: string) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  return (
    <input
      value={draft}
      onChange={(e) => setDraft(e.target.value.replace(/[^-\d.]/g, ''))}
      onBlur={() => draft !== value && onCommit(draft)}
      onKeyDown={(e) => e.key === 'Enter' && draft !== value && onCommit(draft)}
      placeholder={placeholder}
      className="border-line bg-surface num w-14 rounded-md border px-1.5 py-1 text-right text-xs"
    />
  )
}

function score(v: number | null | undefined) {
  return v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, 3)}</span>
}

function KeyRows({ row, scores }: { row: PositionTypeRow; scores: boolean }) {
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
          {scores && <td className="px-3 py-1 text-right">{score(k.mean_score)}</td>}
          {scores && <td className="px-3 py-1 text-right">{signedNum(k.delta, 3)}</td>}
        </tr>
      ))}
    </>
  )
}

export function Positions() {
  const [params, setParams] = useSearchParams()
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const scores = useUiMode() === 'full'
  const qs = params.toString()
  const { data, error, loading, reload } = useApi<PositionsPage>(
    `/api/positions${qs ? `?${qs}` : ''}`,
    [qs],
    { live: false },
  )
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data && loading) return <Skeleton rows={10} />
  if (!data) return null
  const sel = (k: string) => params.get(k) ?? ''
  const tiles = [
    { label: 'positions', value: n(data.decisions), sub: 'decisions matching the filter' },
    { label: 'campaigns', value: n(data.campaigns), sub: 'they belong to' },
    { label: 'actions taken', value: n(data.takes), sub: 'confirmed takes at those decisions' },
    ...(scores
      ? [
          {
            label: 'situation mean score',
            value: data.situation_mean == null ? '—' : n(data.situation_mean, 3),
            sub: 'the acting policy over everything on offer',
          },
        ]
      : []),
  ]
  return (
    <div className="space-y-5">
      <Section
        title="positions"
        scope={{
          text: scores
            ? 'filter to situations, see what gets taken there and how it scored'
            : 'filter to situations, see what gets taken there',
          detail: scores ? (data.scope.detail ?? undefined) : undefined,
        }}
      >
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
            <span className="text-dim text-2xs">holds</span>
            <select value={sel('holds')} onChange={(e) => set('holds', e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1">
              <option value="">any settlement</option>
              {(data.settlements ?? []).map((st) => (
                <option key={st.key} value={st.key}>
                  {st.label} · {st.campaigns}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-2xs">
            {RANGES.map((r) => (
              <span key={r.key} className="inline-flex items-center gap-1">
                <span className="text-dim">{r.label}</span>
                <RangeInput value={sel(`${r.key}_min`)} placeholder="min" onCommit={(v) => set(`${r.key}_min`, v)} />
                <span className="text-dim">–</span>
                <RangeInput value={sel(`${r.key}_max`)} placeholder="max" onCommit={(v) => set(`${r.key}_max`, v)} />
              </span>
            ))}
            {qs && (
              <button onClick={() => setParams(new URLSearchParams(), { replace: true })} className="text-accent hover:underline">
                clear all
              </button>
            )}
          </div>
        </Card>
      </Section>

      <div className={cn('grid gap-3 sm:grid-cols-2', scores ? 'lg:grid-cols-4' : 'lg:grid-cols-3', loading && 'opacity-50')}>
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub, state: 'neutral', spark: [] }} />
        ))}
      </div>

      <Section
        title="what gets taken"
        scope={{
          text: 'one row per action type, biggest share first · expand a row for its keys',
          detail: scores ? 'Δ = score of the taken action minus the mean score of everything on offer at that decision' : undefined,
        }}
      >
        <Card className={cn('overflow-hidden', loading && 'opacity-50')}>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-line text-dim border-b text-left text-2xs">
                <th className="px-3 py-1.5 font-normal">action type</th>
                <th className="px-3 py-1.5 text-right font-normal">taken</th>
                <th className="px-3 py-1.5 text-right font-normal">share</th>
                {scores && <th className="px-3 py-1.5 text-right font-normal">mean score</th>}
                {scores && <th className="px-3 py-1.5 text-right font-normal">Δ vs situation</th>}
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
                    {scores && <td className="px-3 py-1.5 text-right">{score(row.mean_score)}</td>}
                    {scores && <td className="px-3 py-1.5 text-right">{signedNum(row.delta, 3)}</td>}
                  </tr>,
                  isOpen ? <KeyRows key={`${row.action_type.raw}-keys`} row={row} scores={scores} /> : null,
                ]
              })}
              {!(data.rows ?? []).length && (
                <tr>
                  <td colSpan={scores ? 5 : 3} className="text-dim px-3 py-5 text-center">no decision matches these filters</td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </Section>
    </div>
  )
}
