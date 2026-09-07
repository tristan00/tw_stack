import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Card, ErrorState, Skeleton } from '@/components/primitives'
import { useApi } from '@/lib/api'
import { useGame, type Effect } from '@/lib/game'
import { n } from '@/lib/format'

type Definition = {
  key: string
  label: string | null
  description?: string | null
  category?: string | null
  level?: number | null
  cost?: number | null
  points?: number | null
  unlock_rank?: number | null
  effects?: Effect[]
  related?: { key: string; label: string | null; kind: string }[]
  chain?: { key: string; label: string | null; level: number | null }[]
  trait_levels?: { level: number; name: string | null; effects: Effect[] }[]
}
export function ThingPage({ family }: { family: string }) {
  const params = useParams()
  const key = params.key || params.itemKey || ''
  const [search] = useSearchParams()
  const { data: game, campaignKey } = useGame()
  const { data, error, reload } = useApi<Definition>(
    `/api/${family}/${encodeURIComponent(key)}`,
    [family, key],
    { live: false },
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <Skeleton rows={6} />
  const tech = game?.research.find((t) => t.key === key)
  const skill = game?.characters
    .flatMap((c) => c.skills)
    .find((s) => s.key === key)
  const effects = data.effects || skill?.effects || tech?.effects || []
  const related =
    family === 'skills' && game
      ? data.related?.filter((r) =>
          game.characters.some((c) => c.skills.some((s) => s.key === r.key)),
        )
      : data.related
  const link = (item: string) => {
    const p = new URLSearchParams(search)
    p.set('campaign', campaignKey)
    return `/${family}/${encodeURIComponent(item)}?${p}`
  }
  const where =
    family === 'research'
      ? tech
        ? [
            tech.researched
              ? 'Researched'
              : tech.researching
                ? 'Researching'
                : tech.can_research
                  ? 'Available'
                  : 'Locked',
          ]
        : []
      : family === 'skills'
        ? game?.characters
            .filter((c) => c.skills.some((s) => s.key === key && s.level > 0))
            .map(
              (c) =>
                `${c.label} · rank ${c.skills.find((s) => s.key === key)?.level}`,
            ) || []
        : family === 'items'
          ? game?.characters
              .filter((c) => c.items.some((i) => i.key === key))
              .map((c) => `Equipped by ${c.label}`) || []
          : family === 'traits'
            ? game?.characters
                .filter((c) => c.traits.some((t) => t.key === key))
                .map(
                  (c) =>
                    `${c.label} · level ${c.traits.find((t) => t.key === key)?.level}`,
                ) || []
            : game?.provinces
                .filter((p) => p.slots.some((s) => s.key === key))
                .map((p) => p.label) || []
  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <Link className="text-dim" to={`/${family}`}>
          {family}
        </Link>
        <h1 className="text-xl mt-2">{data.label || key}</h1>
        <p className="text-dim mt-1">
          {data.category || family}
          {data.level != null && ` · level ${data.level}`}
          {data.cost != null && ` · cost ${n(data.cost)}`}
          {data.points != null && ` · ${n(data.points)} research points`}
          {data.unlock_rank != null && ` · unlocks at rank ${data.unlock_rank}`}
        </p>
      </div>
      <Card className="p-5">
        <span className="eyebrow">Definition</span>
        {data.description && <p className="mt-3">{data.description}</p>}
        {effects.length > 0 && (
          <div className="mt-4">
            {effects.map((e, i) => (
              <div className="finance-row" key={i}>
                <span>
                  {e.name} <span className="text-dim">{e.scope}</span>
                </span>
                <b>{e.value}</b>
              </div>
            ))}
          </div>
        )}
        {!data.description && !effects.length && (
          <p className="text-dim mt-3">No description or effects recorded.</p>
        )}
        {data.trait_levels?.map((l) => (
          <div className="mt-4" key={l.level}>
            <b>{l.name || `Level ${l.level}`}</b>
            {l.effects.map((e, i) => (
              <p key={i}>
                {e.value} {e.name} · {e.scope}
              </p>
            ))}
          </div>
        ))}
      </Card>
      {Boolean(related?.length || data.chain?.length) && (
        <Card className="p-5">
          <span className="eyebrow">Chain position</span>
          <div className="mt-3 space-y-2">
            {related?.map((r) => (
              <div key={`${r.kind}-${r.key}`}>
                <span className="text-dim mr-3">{r.kind}</span>
                <Link className="thing-link" to={link(r.key)}>
                  {r.label || r.key}
                </Link>
              </div>
            ))}
            {data.chain?.map((r) => (
              <div key={r.key}>
                <span className="text-dim mr-3">Level {r.level}</span>
                <Link className="thing-link" to={link(r.key)}>
                  {r.label || r.key}
                </Link>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card className="p-5">
        <span className="eyebrow">
          In this campaign · {game?.meta.leader} · turn {game?.selected.turn}
        </span>
        <div className="mt-3">
          {where.length ? (
            where.map((s, i) => <p key={i}>{s}</p>)
          ) : (
            <p className="text-dim">
              Not present in this campaign’s selected snapshot.
            </p>
          )}
        </div>
        <Link
          className="small-link"
          to={`/?campaign=${encodeURIComponent(campaignKey)}&subject=${family === 'skills' || family === 'traits' ? 'characters' : family === 'buildings' ? 'realm' : family}${search.get('turn') ? `&turn=${search.get('turn')}` : ''}`}
        >
          Back to campaign →
        </Link>
      </Card>
    </div>
  )
}
