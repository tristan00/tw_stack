import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { signedNum } from '@/components/catalog'
import { ConditionBar, useConditionQuery } from '@/components/conditions'
import { Card, EntityLink, ErrorState, Help, MetricTile, Section, Skeleton } from '@/components/primitives'
import { useApi, type PositionKeyRow, type PositionsPage, type PositionTypeRow } from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

const LINKED: Record<string, string> = {
  items: '/items',
  item_unequip: '/items',
  building: '/buildings',
  research: '/research',
  skills: '/skills',
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

export function Positions() {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const qs = useConditionQuery()
  const { data, error, loading, reload } = useApi<PositionsPage>(
    `/api/positions${qs ? `?${qs}` : ''}`,
    [qs],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data && loading) return <Skeleton rows={10} />
  if (!data) return null
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
      <Section
        title="positions"
        scope={{
          text: 'what the advisor took in situations like this, by action type',
          detail: 'the takes are the programmatic runner’s moves — campaign-only views live on lookup',
        }}
      >
        <ConditionBar />
      </Section>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((t) => (
          <MetricTile key={t.label} metric={{ label: t.label, value: t.value, unit: null, sub: t.sub, state: 'neutral', spark: [] }} />
        ))}
      </div>

      <Section
        title="what gets taken"
        scope={{
          text: `one row per action type, biggest share first · ${n(data.takes)} confirmed takes · expand a row for its keys`,
          detail: 'rewards use the analytics weights from the lookup page’s reward weights tab',
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
                  <Help>final campaign reward, mean over these takes — hindsight; use the future column to compare</Help>
                </th>
                <th className="px-3 py-1.5 text-right font-normal">
                  avg future reward
                  <Help>Σ weight × (peak − at the take), mean over these takes</Help>
                </th>
                <th className="px-3 py-1.5 text-right font-normal">
                  Δ future vs situation
                  <Help>this type's avg future − avg future over every matching position</Help>
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
