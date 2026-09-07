import { useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minus,
  Plus,
} from 'lucide-react'
import { Card, ErrorState, Skeleton } from '@/components/primitives'
import { DataTable, type Col } from '@/components/DataTable'
import { Steps } from '@/components/startcharts'
import { useApi } from '@/lib/api'
import {
  mapName,
  useGame,
  type Character,
  type GamePage,
  type Item,
  type Region,
} from '@/lib/game'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

type LedgerRow = {
  turn: number
  subject: string
  kind: string
  key: string | null
  label: string
  detail: string
  character: number | null
}
const pretty = (s: string) => s.replaceAll('_', ' ')
const value = (v: number | null | undefined) => (v == null ? '—' : n(v))
const health = (v: number | null, units: number | null) =>
  v == null || !units ? '—' : `${Math.round((v / units) * 100)}%`
function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-panel">{children}</div>
}
function Thing({
  family,
  item,
}: {
  family: string
  item: { key: string; label: string | null }
}) {
  const { campaignKey } = useGame()
  const [params] = useSearchParams()
  const context = new URLSearchParams({ campaign: campaignKey })
  if (params.has('turn')) context.set('turn', params.get('turn')!)
  return (
    <Link
      className="thing-link"
      to={`/${family}/${encodeURIComponent(item.key)}?${context}`}
    >
      {item.label || pretty(item.key)}
    </Link>
  )
}
function ItemsList({ items }: { items: Item[] }) {
  return items.length ? (
    <span>
      {items.map((item, i) => (
        <span key={`${item.key}-${i}`}>
          {i > 0 && ' · '}
          <Thing family="items" item={item} />
        </span>
      ))}
    </span>
  ) : (
    <span className="text-dim">No equipment recorded</span>
  )
}
function useSubjectLink() {
  const [params] = useSearchParams()
  const { campaignKey } = useGame()
  return (subject: string, extra: Record<string, string> = {}) => {
    const p = new URLSearchParams(params)
    p.set('campaign', campaignKey)
    p.set('subject', subject)
    p.delete('view')
    for (const [k, v] of Object.entries(extra)) p.set(k, v)
    return `/?${p}`
  }
}

export function RealmMap({
  data,
  onSelect,
  selected,
}: {
  data: GamePage
  onSelect?: (id: number) => void
  selected?: number
}) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [whole, setWhole] = useState(
    () => localStorage.getItem('map-frame') === 'world',
  )
  const [hover, setHover] = useState<Region | null>(null)
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(
    null,
  )
  const moved = useRef(false)
  const mapped = data.regions.filter(
    (r) => r.outline.length && r.cx != null && r.cy != null,
  )
  const owned = mapped.filter((r) => r.owned)
  const adjacent = new Set(owned.flatMap((r) => r.adjacent))
  const framed = whole
    ? mapped
    : mapped.filter((r) => r.owned || adjacent.has(r.region_id))
  const points = (framed.length ? framed : mapped).flatMap((r) =>
    r.outline.flatMap((poly) =>
      Array.from({ length: poly.length / 2 }, (_, i) => [
        poly[i * 2],
        poly[i * 2 + 1],
      ]),
    ),
  )
  const minX = points.length ? Math.min(...points.map((p) => p[0])) : 0
  const minY = points.length ? Math.min(...points.map((p) => p[1])) : 0
  const width = points.length
    ? Math.max(...points.map((p) => p[0])) - minX + 80
    : 800
  const height = points.length
    ? Math.max(...points.map((p) => p[1])) - minY + 80
    : 400
  const reset = () => {
    setPan({ x: 0, y: 0 })
    setZoom(1)
  }
  if (!mapped.length)
    return <Empty>No region outlines recorded for this campaign.</Empty>
  return (
    <div className="realm-map">
      <div className="map-heading">
        <span className="eyebrow">
          {whole ? 'Known world' : 'Your realm & neighbours'}
        </span>
        <span>
          <i className="map-dot" /> owned <i className="map-dot neighbour" />{' '}
          known
        </span>
      </div>
      <svg
        role="img"
        aria-label="Campaign realm map. Select a region to inspect its settlement."
        viewBox={`${minX - 40 + pan.x} ${minY - 40 + pan.y} ${width / zoom} ${height / zoom}`}
        onPointerDown={(e) => {
          moved.current = false
          drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
        }}
        onPointerMove={(e) => {
          if (drag.current) {
            const d = drag.current
            if (
              Math.hypot(e.clientX - d.x, e.clientY - d.y) < 4 &&
              !moved.current
            )
              return
            moved.current = true
            e.currentTarget.setPointerCapture(e.pointerId)
            const scale = width / zoom / e.currentTarget.clientWidth
            setPan({
              x: d.px - (e.clientX - d.x) * scale,
              y: d.py - (e.clientY - d.y) * scale,
            })
          }
        }}
        onPointerUp={() => {
          drag.current = null
        }}
        onPointerCancel={() => {
          drag.current = null
        }}
      >
        {mapped.map((r) => (
          <g
            key={r.region_id}
            className="map-region"
            role="button"
            tabIndex={0}
            aria-label={`${r.label}, ${r.owned ? 'owned' : pretty(r.owner || 'unknown owner')}`}
            onMouseEnter={() => setHover(r)}
            onFocus={() => setHover(r)}
            onClick={() => {
              if (!moved.current) onSelect?.(r.region_id)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect?.(r.region_id)
              }
            }}
          >
            {r.outline.map((poly, i) => (
              <polygon
                key={i}
                points={Array.from(
                  { length: poly.length / 2 },
                  (_, j) => `${poly[j * 2]},${poly[j * 2 + 1]}`,
                ).join(' ')}
                className={cn(
                  r.owned ? 'owned' : 'known',
                  selected === r.region_id && 'selected',
                )}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <circle
              cx={r.cx!}
              cy={r.cy!}
              r={r.capital ? 3 : 2}
              fill="var(--fg)"
            />
            {(r.owned || zoom > 1.5) && (
              <text
                x={r.cx!}
                y={r.cy! - 8}
                textAnchor="middle"
                fontSize={Math.max(10, width / 60)}
              >
                {r.label}
              </text>
            )}
          </g>
        ))}
      </svg>
      <div className="map-footer">
        <span>
          {hover
            ? `${hover.label} · ${hover.owned ? 'Owned' : pretty(hover.owner || 'Unknown owner')}`
            : `${owned.length} owned · ${mapped.length} known regions`}
        </span>
        <div className="map-controls">
          <button
            aria-label="Zoom out"
            onClick={() => setZoom((z) => Math.max(0.5, z / 1.3))}
          >
            <Minus size={14} />
          </button>
          <button
            aria-label="Zoom in"
            onClick={() => setZoom((z) => Math.min(8, z * 1.3))}
          >
            <Plus size={14} />
          </button>
          <button aria-label="Reset map" onClick={reset}>
            <Maximize2 size={14} />
          </button>
          <button
            onClick={() => {
              setWhole((v) => !v)
              reset()
            }}
          >
            {whole ? 'Frame realm' : 'Whole known world'}
          </button>
        </div>
      </div>
    </div>
  )
}

function CharacterCard({
  character: c,
  featured = false,
}: {
  character: Character
  featured?: boolean
}) {
  const link = useSubjectLink()
  return (
    <Card className={cn('character-card', featured && 'featured')}>
      <div>
        <span className="eyebrow">
          {c.is_leader ? 'Faction leader' : c.is_hero ? 'Hero' : 'Lord'}
        </span>
        <Link
          className="character-name"
          to={link('characters', { character: String(c.cqi) })}
        >
          {c.label || pretty(c.subtype)}{' '}
          <span className="text-dim">· {c.rank}</span>
          <ArrowRight size={14} />
        </Link>
        <div className="card-section">
          <span className="eyebrow">Equipped</span>
          <ItemsList items={c.items} />
        </div>
        <div className="card-section">
          <span className="eyebrow">
            {c.units != null ? 'Army' : 'Location'}
          </span>
          {c.units != null &&
            `${c.units} units · ${health(c.hp, c.units)} strength · `}
          {c.region || (c.wounded ? 'Wounded' : 'Not recorded')}
          <div className="text-dim">{pretty(c.stance)}</div>
        </div>
      </div>
      {featured && (
        <div className="character-bonuses">
          <span className="eyebrow">
            Summed bonuses · skills, traits & items
          </span>
          {(c.bonuses || []).slice(0, 6).map((e, i) => (
            <div key={i}>
              <b className="text-accent">{e.value}</b> {e.name}
            </div>
          ))}
          {!c.bonuses?.length && (
            <span className="text-dim">No bonuses recorded</span>
          )}
          <Link
            className="text-dim small-link"
            to={link('characters', { character: String(c.cqi) })}
          >
            Skills & equipment →
          </Link>
        </div>
      )}
    </Card>
  )
}

function HomeView({ data }: { data: GamePage }) {
  const navigate = useNavigate()
  const link = useSubjectLink()
  const p = data.selected
  const previous = data.series.filter((s) => s.turn < p.turn).at(-1)
  const researching = data.research.find((t) => t.researching)
  const changes = data.events.filter((e) => e.turn === p.turn)
  const metrics = [
    ['Score', 'score', 'home'],
    ['Settlements', 'settlements', 'realm'],
    ['Treasury', 'treasury', 'realm'],
    ['Income / turn', 'income', 'realm'],
    ['Armies', 'armies', 'forces'],
    ['Allies', 'allies', 'diplomacy'],
  ] as const
  return (
    <div className="space-y-4">
      <div className="character-grid">
        {data.characters
          .slice(
            0,
            localStorage.getItem('home-characters') === 'all'
              ? undefined
              : Number(localStorage.getItem('home-characters') || 4),
          )
          .map((c, i) => (
            <CharacterCard key={c.cqi} character={c} featured={i === 0} />
          ))}
        {!data.characters.length && (
          <Empty>No character state recorded at this turn.</Empty>
        )}
      </div>
      <div className="home-middle">
        <RealmMap
          data={data}
          onSelect={(id) => navigate(link('realm', { region: String(id) }))}
        />
        <div className="metric-grid">
          {metrics.map(([label, key, subject]) => {
            const delta =
              p[key] != null && previous?.[key] != null
                ? p[key]! - previous[key]!
                : null
            return (
              <Link to={link(subject)} key={key} className="game-metric">
                <span className="eyebrow">{label}</span>
                <strong>{value(p[key])}</strong>
                <span className="metric-delta">
                  {delta == null
                    ? 'No previous observation'
                    : delta === 0
                      ? 'Unchanged'
                      : `${delta > 0 ? '+' : ''}${n(delta)} since turn ${previous?.turn}`}
                </span>
              </Link>
            )
          })}
        </div>
      </div>
      <div className="home-bottom">
        <Card className="p-4">
          <span className="eyebrow">Researching</span>
          <div className="mt-2 text-base">
            {researching ? (
              <Thing family="research" item={researching} />
            ) : data.research.length ? (
              'Nothing researching'
            ) : (
              'Research state not recorded'
            )}
          </div>
          <p className="text-dim mt-2">
            {data.research.filter((t) => t.researched).length} /{' '}
            {data.research.length} recorded technologies researched
          </p>
          <Link className="small-link" to={link('research')}>
            Open research →
          </Link>
        </Card>
        <Card className="p-4">
          <span className="eyebrow">Recorded events · turn {p.turn}</span>
          <div className="mt-2">
            {changes.length
              ? changes
                  .slice(0, 4)
                  .map((e) => e.label)
                  .join(' · ')
              : 'No events recorded this turn.'}
          </div>
          <Link className="small-link" to={link('events', { view: 'history' })}>
            All changes →
          </Link>
        </Card>
      </div>
    </div>
  )
}

function CharacterView({ data }: { data: GamePage }) {
  const [params, setParams] = useSearchParams()
  const [show, setShow] = useState('all')
  const c =
    data.characters.find((c) => String(c.cqi) === params.get('character')) ||
    data.characters[0]
  const skills = useMemo(
    () =>
      c?.skills
        .filter(
          (s) =>
            show === 'all' ||
            (show === 'taken' && s.level > 0) ||
            (show === 'available' &&
              s.status === 'active' &&
              s.level < s.total_levels),
        )
        .sort((a, b) => a.line.localeCompare(b.line)) || [],
    [c, show],
  )
  if (!c) return <Empty>No character state recorded at this turn.</Empty>
  const cols: Col<(typeof skills)[number]>[] = [
    {
      key: 'line',
      label: 'Line',
      value: (r) => r.line,
      render: (r) => <span className="eyebrow">{r.line}</span>,
    },
    {
      key: 'skill',
      label: 'Skill',
      value: (r) => r.label,
      render: (r) => <Thing family="skills" item={r} />,
    },
    {
      key: 'rank',
      label: 'Ranks',
      value: (r) => r.level,
      render: (r) => (
        <span className="rank-pips">
          {Array.from({ length: r.total_levels }, (_, i) => (
            <i key={i} className={i < r.level ? 'filled' : ''} />
          ))}{' '}
          {r.level}/{r.total_levels}
        </span>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      value: (r) => r.status,
      render: (r) => pretty(r.status),
    },
    {
      key: 'unlock',
      label: 'Unlocks',
      value: (r) => r.unlock_rank ?? undefined,
      render: (r) => (r.unlock_rank != null ? 'Rank ' + r.unlock_rank : '—'),
    },
    {
      key: 'requires',
      label: 'Requires',
      value: (r) => r.parents.join(' '),
      render: (r) =>
        r.parents.length
          ? r.parents.map((key) => (
              <span key={key}>
                <Thing
                  family="skills"
                  item={{
                    key,
                    label: c.skills.find((s) => s.key === key)?.label || key,
                  }}
                />{' '}
              </span>
            ))
          : '—',
    },
    {
      key: 'effect',
      label: 'Effect at rank',
      value: (r) => r.effect || '',
      render: (r) => (
        <span className="text-dim">
          {r.effects
            ?.map((e) => [e.value, e.name, e.scope].filter(Boolean).join(' '))
            .join(' · ') || '—'}
        </span>
      ),
    },
  ]
  return (
    <div className="space-y-5">
      <div className="roster-row" aria-label="Character roster">
        {data.characters.map((ch) => (
          <button
            key={ch.cqi}
            className={cn('roster-button', ch.cqi === c.cqi && 'active')}
            onClick={() => {
              const p = new URLSearchParams(params)
              p.set('character', String(ch.cqi))
              setParams(p)
            }}
          >
            {ch.label || pretty(ch.subtype)}{' '}
            <span className="num">{ch.rank}</span>
          </button>
        ))}
      </div>
      <div>
        <h2 className="text-xl font-semibold">{c.label}</h2>
        <p className="text-dim mt-1">
          Rank {c.rank} · {c.skill_points} skill points unspent ·{' '}
          {c.region || 'Location not recorded'} ·{' '}
          {c.wounded ? 'wounded' : pretty(c.stance)}
        </p>
      </div>
      <div className="detail-cards">
        <Card className="p-4">
          <span className="eyebrow">Equipped · {c.items.length}</span>
          <div className="mt-2">
            <ItemsList items={c.items} />
          </div>
          <span className="eyebrow mt-5">Traits</span>
          <div className="mt-2">
            {c.traits?.length ? (
              c.traits.map((trait) => (
                <p key={trait.key}>
                  <Thing family="traits" item={trait} /> · level {trait.level}
                </p>
              ))
            ) : (
              <span className="text-dim">No traits recorded</span>
            )}
          </div>
        </Card>
        <Card className="p-4">
          <span className="eyebrow">
            Summed bonuses · skills, traits & items
          </span>
          {(c.bonuses || []).map((e, i) => (
            <div key={i} className="mt-1">
              <b>{e.value}</b> {e.name}{' '}
              <span className="text-dim">{e.scope}</span>
            </div>
          ))}
        </Card>
        <Card className="p-4">
          <span className="eyebrow">
            Army · {value(c.units)} units · {health(c.hp, c.units)}
          </span>
          <div className="mt-2">
            {c.units_list.length
              ? Object.entries(
                  c.units_list.reduce<Record<string, number>>((acc, u) => {
                    acc[u.label] = (acc[u.label] || 0) + 1
                    return acc
                  }, {}),
                )
                  .map(([label, count]) => `${count}× ${label}`)
                  .join(' · ')
              : 'No unit composition recorded'}
          </div>
        </Card>
      </div>
      <div className="section-toolbar">
        <h3>Skills</h3>
        <Filter
          value={show}
          onChange={setShow}
          options={['all', 'taken', 'available']}
        />
      </div>
      <DataTable
        rows={skills}
        cols={cols}
        rowId={(r) => r.key}
        pageSize={50}
        emptyWhat="No skills match this filter"
      />
    </div>
  )
}
function Filter({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: string[]
}) {
  return (
    <div className="filter-row">
      {options.map((o) => (
        <button
          key={o}
          aria-pressed={o === value}
          onClick={() => onChange(o)}
          className={cn(o === value && 'active')}
        >
          {o}
        </button>
      ))}
    </div>
  )
}
function ResearchView({ data }: { data: GamePage }) {
  const [show, setShow] = useState('all')
  const rows = data.research
    .filter(
      (t) =>
        show === 'all' ||
        (show === 'researched' && t.researched) ||
        (show === 'available' && t.can_research),
    )
    .sort((a, b) => a.branch.localeCompare(b.branch))
  const cols: Col<(typeof rows)[number]>[] = [
    {
      key: 'branch',
      label: 'Branch',
      value: (r) => r.branch,
      render: (r) => <span className="eyebrow">{r.branch}</span>,
    },
    {
      key: 'tech',
      label: 'Technology',
      value: (r) => r.label,
      render: (r) => <Thing family="research" item={r} />,
    },
    {
      key: 'status',
      label: 'Status',
      value: (r) =>
        r.researched
          ? 'researched'
          : r.researching
            ? 'researching'
            : r.can_research
              ? 'available'
              : 'locked',
      render: (r) => (
        <span
          className={
            r.researching
              ? 'text-accent'
              : r.researched
                ? 'text-ok'
                : 'text-dim'
          }
        >
          {r.researched
            ? 'Researched'
            : r.researching
              ? 'Researching'
              : r.can_research
                ? 'Available'
                : 'Locked'}
        </span>
      ),
    },
    {
      key: 'cost',
      label: 'Cost',
      value: (r) => r.cost,
      render: (r) => n(r.cost),
    },
    {
      key: 'requires',
      label: 'Requires',
      value: (r) => r.parent?.join(' ') || '',
      render: (r) =>
        r.parent?.map((k) => (
          <span key={k}>
            <Thing
              family="research"
              item={{
                key: k,
                label: data.research.find((t) => t.key === k)?.label || k,
              }}
            />{' '}
          </span>
        )) || '—',
    },
    {
      key: 'description',
      label: 'Effect',
      value: (r) => r.effect || '',
      render: (r) => (
        <span className="text-dim">
          {r.effects
            ?.map((e) => [e.value, e.name, e.scope].filter(Boolean).join(' '))
            .join(' · ') || '—'}
        </span>
      ),
    },
  ]
  return (
    <div className="space-y-4">
      <div className="section-toolbar">
        <p>
          {data.research.filter((t) => t.researched).length} /{' '}
          {data.research.length} researched
        </p>
        <Filter
          value={show}
          onChange={setShow}
          options={['all', 'researched', 'available']}
        />
      </div>
      <DataTable
        rows={rows}
        cols={cols}
        rowId={(r) => r.key}
        pageSize={50}
        emptyWhat="No research recorded for this filter"
      />
    </div>
  )
}
function RealmView({ data }: { data: GamePage }) {
  const [params, setParams] = useSearchParams()
  const selected = Number(params.get('region'))
  const select = (id: number) => {
    const p = new URLSearchParams(params)
    p.set('region', String(id))
    setParams(p, { replace: true })
  }
  const rows = selected
    ? data.provinces.filter((r) => r.region_id === selected)
    : data.provinces
  const cols: Col<(typeof rows)[number]>[] = [
    {
      key: 'province',
      label: 'Province',
      value: (r) =>
        data.regions.find((g) => g.region_id === r.region_id)?.province || '',
      render: (r) => (
        <span className="eyebrow">
          {data.regions.find((g) => g.region_id === r.region_id)?.province ||
            '—'}
        </span>
      ),
    },
    {
      key: 'settlement',
      label: 'Settlement · slots',
      value: (r) => r.label,
      render: (r) => (
        <div>
          <b>{r.label}</b>
          <div className="text-dim mt-1">
            Level {value(r.settlement_level)} ·{' '}
            {r.slots.map((s, i) => (
              <span key={i}>
                <Thing family="buildings" item={s} /> ·{' '}
              </span>
            ))}
            {value(r.free_slots)} empty
          </div>
        </div>
      ),
    },
    {
      key: 'order',
      label: 'Order',
      value: (r) => r.public_order ?? undefined,
      render: (r) => value(r.public_order),
    },
    {
      key: 'growth',
      label: 'Growth',
      value: (r) => r.growth_per_turn ?? undefined,
      render: (r) => value(r.growth_per_turn),
    },
    {
      key: 'income',
      label: 'Income',
      value: (r) => r.income ?? undefined,
      render: (r) => value(r.income),
    },
  ]
  return (
    <div className="space-y-5">
      <div className="realm-layout">
        <RealmMap data={data} selected={selected} onSelect={select} />
        <Card className="p-4">
          <span className="eyebrow">
            Income by source · turn {data.selected.turn}
          </span>
          {data.finance.length ? (
            data.finance.map((f) => (
              <div className="finance-row" key={f.component_id}>
                <span>{f.label || pretty(f.component_id)}</span>
                <b className="num">{f.value || '—'}</b>
              </div>
            ))
          ) : (
            <p className="text-dim mt-4">
              No income breakdown recorded at this turn.
            </p>
          )}
          <div className="finance-row">
            <span>Treasury</span>
            <b>{value(data.selected.treasury)}</b>
          </div>
          <div className="finance-row">
            <span>Income / turn</span>
            <b>{value(data.selected.income)}</b>
          </div>
        </Card>
      </div>
      <div className="section-toolbar">
        <h3>Settlements</h3>
        {selected > 0 && (
          <button
            onClick={() => {
              const p = new URLSearchParams(params)
              p.delete('region')
              setParams(p, { replace: true })
            }}
          >
            Show all settlements
          </button>
        )}
      </div>
      <DataTable
        rows={rows}
        cols={cols}
        rowId={(r) => String(r.region_id)}
        emptyWhat="No settlement state recorded for this selection"
      />
    </div>
  )
}
function DiplomacyView({ data }: { data: GamePage }) {
  const check = (b: boolean) => (b ? '✓' : '—')
  const cols: Col<GamePage['diplomacy'][number]>[] = [
    {
      key: 'faction',
      label: 'Faction',
      value: (r) => r.label,
      render: (r) => <b>{r.label}</b>,
    },
    {
      key: 'standing',
      label: 'Standing',
      value: (r) => r.standing ?? undefined,
      render: (r) => (
        <span
          className={
            r.standing != null && r.standing < 0 ? 'text-bad' : 'text-ok'
          }
        >
          {value(r.standing)}
        </span>
      ),
    },
    {
      key: 'relation',
      label: 'Relation',
      value: (r) => (r.at_war ? 'war' : r.allied ? 'allied' : 'peace'),
      render: (r) => (
        <span
          className={r.at_war ? 'text-bad' : r.allied ? 'text-ok' : 'text-dim'}
        >
          {r.at_war ? 'War' : r.allied ? 'Ally' : 'Peace'}
        </span>
      ),
    },
    ...(['trade', 'nap', 'mil_access'] as const).map((key) => ({
      key,
      label: key === 'nap' ? 'Non-aggression' : pretty(key),
      value: (r: GamePage['diplomacy'][number]) => Number(r[key]),
      render: (r: GamePage['diplomacy'][number]) => check(r[key]),
    })),
  ]
  return (
    <div className="space-y-4">
      <p className="text-dim">
        {data.diplomacy.length} known factions ·{' '}
        {data.diplomacy.filter((r) => r.allied).length} allies ·{' '}
        {data.diplomacy.filter((r) => r.at_war).length} wars
      </p>
      <DataTable
        rows={data.diplomacy}
        cols={cols}
        rowId={(r) => r.key}
        emptyWhat="No diplomacy recorded at this turn"
      />
    </div>
  )
}
function ForcesView({ data }: { data: GamePage }) {
  const cols: Col<GamePage['armies'][number]>[] = [
    {
      key: 'army',
      label: 'Army',
      value: (r) => r.label,
      render: (r) => <b>{r.label}</b>,
    },
    {
      key: 'units',
      label: 'Units',
      value: (r) => r.units ?? undefined,
      render: (r) => value(r.units),
    },
    {
      key: 'strength',
      label: 'Strength',
      value: (r) => r.hp ?? undefined,
      render: (r) => health(r.hp, r.units),
    },
    {
      key: 'where',
      label: 'Where',
      value: (r) => r.region || '',
      render: (r) => (
        <span>
          {r.region || '—'} · {pretty(r.stance || '')}
        </span>
      ),
    },
    {
      key: 'composition',
      label: 'Composition',
      value: (r) => r.cqi,
      render: (r) => {
        const units = data.characters.find((c) => c.cqi === r.cqi)?.units_list
        return units?.length
          ? Object.entries(
              units.reduce<Record<string, number>>((acc, u) => {
                acc[u.label] = (acc[u.label] || 0) + 1
                return acc
              }, {}),
            )
              .map(([label, count]) => `${count}× ${label}`)
              .join(' · ')
          : 'Not recorded'
      },
    },
  ]
  return (
    <div className="space-y-5">
      <DataTable
        rows={data.armies}
        cols={cols}
        rowId={(r, i) => `${r.cqi}-${i}`}
        emptyWhat="No armies recorded at this turn"
      />
      <h3>Hostiles visible · {data.hostiles.length}</h3>
      <div className="detail-cards">
        {data.hostiles.map((h, i) => (
          <Card key={i} className="p-4">
            <b>{pretty(h.faction)}</b>
            <p className="text-dim mt-2">
              {value(h.units)} units · {health(h.hp, h.units)} strength
            </p>
            <p className="num text-dim mt-1">
              {h.x}, {h.y}
            </p>
          </Card>
        ))}
      </div>
    </div>
  )
}
function ItemsView({ data }: { data: GamePage }) {
  const rows = [
    ...data.characters.flatMap((c) =>
      c.items.map((i) => ({ ...i, on: c.label || pretty(c.subtype) })),
    ),
    ...data.pool
      .filter(
        (i) =>
          !data.characters.some((c) => c.items.some((w) => w.key === i.key)),
      )
      .map((i) => ({ ...i, on: 'Benched' })),
  ]
  const cols: Col<(typeof rows)[number]>[] = [
    {
      key: 'item',
      label: 'Item',
      value: (r) => r.label,
      render: (r) => <Thing family="items" item={r} />,
    },
    {
      key: 'on',
      label: 'On',
      value: (r) => r.on,
      render: (r) => (
        <span className={r.on === 'Benched' ? 'text-dim' : ''}>{r.on}</span>
      ),
    },
    {
      key: 'effects',
      label: 'Equipment effects',
      value: (r) => r.effects?.length || 0,
      render: (r) =>
        r.effects?.map((e) => `${e.value || ''} ${e.name}`).join(' · ') ||
        'Open item for its definition',
    },
  ]
  return (
    <DataTable
      rows={rows}
      cols={cols}
      rowId={(r, i) => `${r.key}-${i}`}
      emptyWhat="No items recorded at this turn"
    />
  )
}
function HistoryView({ data, subject }: { data: GamePage; subject: string }) {
  const { campaignKey } = useGame()
  const [params, setParams] = useSearchParams()
  const character = subject === 'characters' ? params.get('character') : null
  const ledger = useApi<LedgerRow[]>(
    `/api/game/campaigns/${encodeURIComponent(campaignKey)}/history`,
    [campaignKey],
  )
  const [filter, setFilter] = useState('all')
  const events: LedgerRow[] = data.events.map((e) => ({
    turn: e.turn,
    subject: 'events',
    kind: e.kind,
    key: null,
    label: e.label,
    detail: e.choice ? `Choice ${e.choice}` : '',
    character: e.character_cqi,
  }))
  const rows = [...(ledger.data || []), ...events]
    .filter(
      (r) =>
        r.turn <= data.selected.turn &&
        (subject === 'home' ||
          subject === 'events' ||
          r.subject === subject ||
          (subject === 'characters' && r.subject === 'items')),
    )
    .filter((r) => filter === 'all' || r.subject === filter)
    .filter((r) => !character || String(r.character) === character)
    .sort((a, b) => b.turn - a.turn)
  const cols: Col<LedgerRow>[] = [
    {
      key: 'turn',
      label: 'Turn',
      value: (r) => r.turn,
      render: (r) => <b className="num">{r.turn}</b>,
    },
    {
      key: 'subject',
      label: 'Subject',
      value: (r) => r.subject,
      render: (r) => r.subject,
    },
    {
      key: 'kind',
      label: 'Kind',
      value: (r) => r.kind,
      render: (r) => <span className="text-dim">{pretty(r.kind)}</span>,
    },
    {
      key: 'what',
      label: 'What',
      value: (r) => r.label,
      render: (r) =>
        r.key && ['characters', 'research', 'items'].includes(r.subject) ? (
          <Thing
            family={r.subject === 'characters' ? 'skills' : r.subject}
            item={{ key: r.key, label: r.label }}
          />
        ) : (
          r.label
        ),
    },
    {
      key: 'detail',
      label: 'Detail',
      value: (r) => r.detail,
      render: (r) => <span className="text-dim">{r.detail}</span>,
    },
  ]
  if (ledger.error)
    return <ErrorState error={ledger.error} onRetry={ledger.reload} />
  if (!ledger.data) return <Skeleton rows={6} />
  return (
    <div className="space-y-4">
      <p className="text-dim">
        Changes between recorded observations. First observations are marked
        separately.
      </p>
      {subject === 'characters' && (
        <select
          className="workspace-input"
          aria-label="History character"
          value={character || ''}
          onChange={(e) => {
            const p = new URLSearchParams(params)
            if (e.target.value) p.set('character', e.target.value)
            else p.delete('character')
            setParams(p)
          }}
        >
          <option value="">All characters</option>
          {data.characters.map((c) => (
            <option key={c.cqi} value={c.cqi}>
              {c.label || c.subtype}
            </option>
          ))}
        </select>
      )}
      {(subject === 'events' || subject === 'home') && (
        <Filter
          value={filter}
          onChange={setFilter}
          options={[
            'all',
            'research',
            'characters',
            'realm',
            'diplomacy',
            'forces',
            'items',
            'events',
          ]}
        />
      )}
      <DataTable
        rows={rows}
        cols={cols}
        rowId={(_, i) => String(i)}
        pageSize={50}
        emptyWhat="No changes recorded for this selection"
      />
      {subject === 'realm' && (
        <div className="flex flex-wrap gap-3">
          {(['income', 'treasury', 'settlements'] as const).map((k) => (
            <Steps
              key={k}
              label={k}
              delta="by recorded turn"
              values={data.series
                .filter((p) => p.turn <= data.selected.turn)
                .map((p) => p[k])}
              turns={data.series
                .filter((p) => p.turn <= data.selected.turn)
                .map((p) => p.turn)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
export function CampaignWorkspace() {
  const { data, error, reload, campaignKey } = useGame()
  const [params, setParams] = useSearchParams()
  const subject =
    params.get('subject') ||
    { overview: 'home', skills: 'characters', buildings: 'realm' }[
      params.get('tab') || ''
    ] ||
    params.get('tab') ||
    'home'
  const history = params.get('view') === 'history'
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!campaignKey) return <Empty>No campaign snapshots recorded yet.</Empty>
  if (!data) return <Skeleton rows={9} />
  const set = (key: string, val: string) => {
    const p = new URLSearchParams(params)
    p.set('campaign', campaignKey)
    if (val) p.set(key, val)
    else p.delete(key)
    setParams(p)
  }
  const index = data.series.findIndex((p) => p.turn === data.selected.turn)
  return (
    <div className="campaign-workspace">
      <div className="campaign-title">
        <div>
          <div className="eyebrow">
            {mapName(data.meta.map)} · turn {data.selected.turn}
          </div>
          <h1>
            {subject === 'home'
              ? data.meta.leader || pretty(data.meta.faction)
              : subject.charAt(0).toUpperCase() + subject.slice(1)}
          </h1>
          {subject !== 'home' && (
            <p className="text-dim">
              {data.meta.leader} · {pretty(data.meta.faction)}
            </p>
          )}
        </div>
        <div className="turn-tools">
          {subject !== 'starts' && (
            <div className="view-switch">
              <button
                className={!history ? 'active' : ''}
                onClick={() => set('view', '')}
              >
                At turn
              </button>
              <button
                className={history ? 'active' : ''}
                onClick={() => set('view', 'history')}
              >
                History
              </button>
            </div>
          )}
          <label className="turn-label">
            Turn{' '}
            <select
              aria-label="Recorded turn"
              value={data.selected.turn}
              onChange={(e) => set('turn', e.target.value)}
            >
              {data.series.map((p) => (
                <option key={p.turn} value={p.turn}>
                  {p.turn}
                </option>
              ))}
            </select>
          </label>
          <button
            aria-label="Previous recorded turn"
            disabled={index <= 0}
            onClick={() => set('turn', String(data.series[index - 1].turn))}
          >
            <ChevronLeft size={16} />
          </button>
          <button
            aria-label="Next recorded turn"
            disabled={index === data.series.length - 1}
            onClick={() => set('turn', String(data.series[index + 1].turn))}
          >
            <ChevronRight size={16} />
          </button>
          {params.has('turn') && (
            <button onClick={() => set('turn', '')}>Latest</button>
          )}
        </div>
      </div>
      {history ? (
        <HistoryView
          key={`${campaignKey}-${subject}`}
          data={data}
          subject={subject}
        />
      ) : subject === 'home' ? (
        <HomeView data={data} />
      ) : subject === 'characters' ? (
        <CharacterView data={data} />
      ) : subject === 'research' ? (
        <ResearchView data={data} />
      ) : subject === 'realm' ? (
        <RealmView data={data} />
      ) : subject === 'diplomacy' ? (
        <DiplomacyView data={data} />
      ) : subject === 'forces' ? (
        <ForcesView data={data} />
      ) : subject === 'items' ? (
        <ItemsView data={data} />
      ) : subject === 'events' ? (
        <>
          <p className="text-dim mb-4">
            Events recorded at turn {data.selected.turn}. Open mission state is
            not included in these records.
          </p>
          {data.events
            .filter((e) => e.turn === data.selected.turn)
            .map((e) => (
              <Card key={e.event_id} className="p-4 mb-2">
                <span className="eyebrow">{pretty(e.kind)}</span>
                <p className="mt-1">{e.label}</p>
                {e.choice && <p className="text-dim">Choice {e.choice}</p>}
              </Card>
            ))}
        </>
      ) : subject === 'starts' ? (
        <Card className="p-5">
          <h2 className="text-lg">
            {data.meta.leader} · {mapName(data.meta.map)}
          </h2>
          <Link
            className="small-link"
            to={`/starts/${encodeURIComponent(data.meta.map || '')}/${encodeURIComponent(data.meta.faction)}`}
          >
            Compare this start’s campaigns →
          </Link>
        </Card>
      ) : (
        <Empty>Unknown campaign subject.</Empty>
      )}
    </div>
  )
}
