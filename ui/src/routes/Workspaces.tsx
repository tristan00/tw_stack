import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Plus, X } from 'lucide-react'
import { DataTable, type Col } from '@/components/DataTable'
import { Card, ErrorState, Section, Skeleton } from '@/components/primitives'
import { RewardWeightsCard } from '@/components/reward'
import { ChoicesView } from '@/routes/Catalog'
import { useApi } from '@/lib/api'
import { n } from '@/lib/format'
import { cn } from '@/lib/utils'

type RecordRow = Record<string, string | number | boolean | null>
type RecordsPage = {
  subject: string
  rows: RecordRow[]
  before: number | null
  scope: string
}
const SUBJECTS = [
  'turns',
  'research',
  'skills',
  'items',
  'regions',
  'buildings',
  'diplomacy',
  'armies',
  'events',
]
const pretty = (s: string) => s.replaceAll('_', ' ')
const display = (v: RecordRow[string], key: string) =>
  v == null
    ? '—'
    : key === 'recorded_at'
      ? new Date(Number(v) * 1000).toLocaleString()
      : typeof v === 'boolean'
        ? v
          ? 'Yes'
          : 'No'
        : typeof v === 'number'
          ? n(v)
          : v
function columns(rows: RecordRow[]): Col<RecordRow>[] {
  return Object.keys(rows[0] || {})
    .filter((k) => k !== 'id')
    .map((key) => ({
      key,
      label: pretty(key),
      value: (r) =>
        typeof r[key] === 'boolean' ? Number(r[key]) : (r[key] ?? undefined),
      render: (r) =>
        key === 'campaign' ? (
          <Link
            className="thing-link num"
            to={`/?campaign=${encodeURIComponent(String(r[key]))}`}
          >
            {String(r[key]).slice(-12)}
          </Link>
        ) : (
          <span className={typeof r[key] === 'number' ? 'num' : ''}>
            {display(r[key], key)}
          </span>
        ),
    }))
}

export function Explore() {
  const [params, setParams] = useSearchParams()
  const subject = SUBJECTS.includes(params.get('records') || '')
    ? params.get('records')!
    : 'turns'
  const before = params.get('before')
  const search = params.get('search') || ''
  const [draft, setDraft] = useState(search)
  const { data, error, reload } = useApi<RecordsPage>(
    `/api/game/records/${subject}?limit=100${before ? `&before=${before}` : ''}${search ? `&search=${encodeURIComponent(search)}` : ''}`,
    [subject, before, search],
  )
  const [distribution, setDistribution] = useState('turn')
  const [hidden, setHidden] = useState<string[]>([])
  const set = (key: string, val: string) => {
    const p = new URLSearchParams(params)
    if (val) p.set(key, val)
    else p.delete(key)
    if (key !== 'before') p.delete('before')
    setParams(p)
  }
  const numeric = Object.keys(data?.rows[0] || {}).filter(
    (k) =>
      k !== 'id' &&
      k !== 'recorded_at' &&
      data?.rows.some((r) => typeof r[k] === 'number'),
  )
  const field = numeric.includes(distribution) ? distribution : numeric[0]
  const histogram = new Map<number, number>()
  for (const row of data?.rows || []) {
    const v = row[field]
    if (typeof v === 'number') histogram.set(v, (histogram.get(v) || 0) + 1)
  }
  const bins = [...histogram].sort((a, b) => a[0] - b[0]).slice(0, 60)
  return (
    <div className="space-y-5">
      <div className="section-toolbar">
        <div>
          <span className="eyebrow">Explore</span>
          <h1 className="text-xl mt-1">Raw records</h1>
        </div>
        <Link
          to={`/analytics?builder=1&records=${subject}`}
          className="small-link"
        >
          Pivot recent records →
        </Link>
      </div>
      <div className="roster-row">
        {SUBJECTS.map((s) => (
          <button
            key={s}
            className={cn('roster-button', subject === s && 'active')}
            onClick={() => {
              set('records', s)
              setHidden([])
            }}
          >
            {s}
          </button>
        ))}
      </div>
      <div className="section-toolbar">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            set('search', draft)
          }}
        >
          <input
            className="workspace-input"
            aria-label="Filter campaign key"
            placeholder="Filter campaign key…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="workspace-button" type="submit">
            Apply filter
          </button>
        </form>
        <details className="relative">
          <summary className="workspace-button">Columns</summary>
          <div className="column-picker">
            {Object.keys(data?.rows[0] || {})
              .filter((k) => k !== 'id')
              .map((k) => (
                <label key={k}>
                  <input
                    type="checkbox"
                    checked={!hidden.includes(k)}
                    onChange={() =>
                      setHidden((h) =>
                        h.includes(k) ? h.filter((x) => x !== k) : [...h, k],
                      )
                    }
                  />{' '}
                  {pretty(k)}
                </label>
              ))}
          </div>
        </details>
      </div>
      {error ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? (
        <Skeleton rows={8} />
      ) : (
        <>
          <Card className="p-4">
            <div className="section-toolbar">
              <span className="eyebrow">Distribution · visible records</span>
              <select
                aria-label="Distribution field"
                className="workspace-input"
                value={field || ''}
                onChange={(e) => setDistribution(e.target.value)}
              >
                {numeric.map((k) => (
                  <option key={k} value={k}>
                    {pretty(k)}
                  </option>
                ))}
              </select>
            </div>
            <div className="histogram">
              {bins.map(([v, count]) => (
                <div
                  key={v}
                  title={`${v}: ${count} observations`}
                  style={{
                    height: `${Math.max(3, (count / Math.max(...bins.map((b) => b[1]))) * 100)}%`,
                  }}
                >
                  <span>{v}</span>
                </div>
              ))}
            </div>
          </Card>
          <p className="text-dim text-xs">{data.scope}</p>
          <DataTable
            rows={data.rows}
            cols={columns(data.rows).filter((c) => !hidden.includes(c.key))}
            rowId={(_, i) => String(i)}
            pageSize={100}
            emptyWhat="No records match this selection"
          />
          <div className="flex justify-between">
            <button
              className="workspace-button"
              disabled={!before}
              onClick={() => set('before', '')}
            >
              Newest records
            </button>
            <button
              className="workspace-button"
              disabled={!data.before}
              onClick={() => set('before', String(data.before))}
            >
              Older snapshots →
            </button>
          </div>
        </>
      )}
    </div>
  )
}

type BoardView = {
  id: string
  name: string
  subject: string
  row: string
  metric: string
  operation: string
  chart: string
}
function loadViews(): BoardView[] {
  try {
    return JSON.parse(localStorage.getItem('analytics-views') || '[]')
  } catch {
    return []
  }
}
function PivotView({ view }: { view: BoardView }) {
  const { data, error, reload } = useApi<RecordsPage>(
    `/api/game/records/${view.subject}?limit=500`,
    [view.subject],
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <Skeleton rows={4} />
  const groups = new Map<string, { count: number; values: number[] }>()
  for (const row of data.rows) {
    const key = String(row[view.row] ?? 'Not recorded')
    const g = groups.get(key) || { count: 0, values: [] }
    g.count++
    if (typeof row[view.metric] === 'number')
      g.values.push(row[view.metric] as number)
    groups.set(key, g)
  }
  const rows = [...groups]
    .map(([label, g]) => ({
      label,
      count: g.count,
      value:
        view.operation === 'count'
          ? g.count
          : g.values.length
            ? view.operation === 'sum'
              ? g.values.reduce((a, b) => a + b, 0)
              : g.values.reduce((a, b) => a + b, 0) / g.values.length
            : null,
    }))
    .sort((a, b) => b.count - a.count)
  const cols: Col<(typeof rows)[number]>[] = [
    {
      key: 'row',
      label: pretty(view.row),
      value: (r) => r.label,
      render: (r) => r.label,
    },
    {
      key: 'n',
      label: 'Observations',
      value: (r) => r.count,
      render: (r) => n(r.count),
    },
    {
      key: 'value',
      label:
        view.operation === 'count'
          ? 'Share'
          : `${view.operation} ${pretty(view.metric)}`,
      value: (r) => r.value ?? undefined,
      render: (r) =>
        view.operation === 'count'
          ? `${((r.count / data.rows.length) * 100).toFixed(1)}%`
          : r.value == null
            ? '—'
            : n(r.value, 2),
    },
  ]
  return (
    <div className="space-y-3">
      <p className="text-dim text-xs">
        {n(data.rows.length)} recent observations ·{' '}
        {new Date(
          Number(data.rows.at(-1)?.recorded_at) * 1000,
        ).toLocaleString()}{' '}
        – {new Date(Number(data.rows[0]?.recorded_at) * 1000).toLocaleString()}{' '}
        · repeated snapshots included
      </p>
      {view.chart === 'bars' ? (
        <div className="pivot-bars">
          {rows.slice(0, 20).map((r) => (
            <div key={r.label}>
              <span>{r.label}</span>
              <div>
                <i
                  style={{
                    width: `${(Math.abs(r.value || 0) / Math.max(1, ...rows.map((r) => Math.abs(r.value || 0)))) * 100}%`,
                  }}
                />
              </div>
              <b>{r.value == null ? '—' : n(r.value, 1)}</b>
            </div>
          ))}
        </div>
      ) : (
        <DataTable
          rows={rows}
          cols={cols}
          rowId={(r) => r.label}
          pageSize={20}
        />
      )}
    </div>
  )
}
export function Analytics() {
  const [params, setParams] = useSearchParams()
  const [views, setViews] = useState(loadViews)
  const [draft, setDraft] = useState<BoardView>({
    id: '',
    name: 'Recent records',
    subject: params.get('records') || 'research',
    row: 'leader',
    metric: 'turn',
    operation: 'count',
    chart: 'table',
  })
  const builder = params.get('builder') === '1'
  const [family, setFamily] = useState<'research' | 'skills' | 'buildings'>(
    'research',
  )
  const fields = useApi<RecordsPage>(
    builder ? `/api/game/records/${draft.subject}?limit=1` : null,
    [builder, draft.subject],
  )
  const save = () => {
    const view = { ...draft, id: draft.id || crypto.randomUUID() }
    const next = [...views.filter((v) => v.id !== view.id), view]
    localStorage.setItem('analytics-views', JSON.stringify(next))
    setViews(next)
    setParams({})
  }
  const remove = (id: string) => {
    const next = views.filter((v) => v.id !== id)
    setViews(next)
    localStorage.setItem('analytics-views', JSON.stringify(next))
  }
  return (
    <div className="space-y-5">
      <div className="section-toolbar">
        <div>
          <span className="eyebrow">Analytics · board</span>
          <h1 className="text-xl mt-1">Exclusive choices</h1>
        </div>
        <button
          className="workspace-button flex gap-2 items-center"
          onClick={() => setParams({ builder: builder ? '0' : '1' })}
        >
          {builder ? <X size={14} /> : <Plus size={14} />}{' '}
          {builder ? 'Close builder' : 'Add view'}
        </button>
      </div>
      {builder ? (
        <Card className="p-5 space-y-4">
          <div className="builder-grid">
            <label>
              Name
              <input
                className="workspace-input"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label>
              Records
              <select
                className="workspace-input"
                value={draft.subject}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    subject: e.target.value,
                    row: 'leader',
                    metric: 'turn',
                  })
                }
              >
                {SUBJECTS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label>
              Rows
              <select
                className="workspace-input"
                value={draft.row}
                onChange={(e) => setDraft({ ...draft, row: e.target.value })}
              >
                {Object.keys(fields.data?.rows[0] || { leader: '' })
                  .filter((k) => k !== 'id')
                  .map((k) => (
                    <option key={k} value={k}>
                      {pretty(k)}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Values
              <select
                className="workspace-input"
                value={draft.operation}
                onChange={(e) =>
                  setDraft({ ...draft, operation: e.target.value })
                }
              >
                <option value="count">Observation count & share</option>
                <option value="mean">Mean</option>
                <option value="sum">Sum</option>
              </select>
            </label>
            {draft.operation !== 'count' && (
              <label>
                Quantity
                <select
                  className="workspace-input"
                  value={draft.metric}
                  onChange={(e) =>
                    setDraft({ ...draft, metric: e.target.value })
                  }
                >
                  {Object.keys(fields.data?.rows[0] || { turn: 0 })
                    .filter(
                      (k) => typeof fields.data?.rows[0]?.[k] === 'number',
                    )
                    .map((k) => (
                      <option key={k}>{k}</option>
                    ))}
                </select>
              </label>
            )}
            <label>
              Display
              <select
                className="workspace-input"
                value={draft.chart}
                onChange={(e) => setDraft({ ...draft, chart: e.target.value })}
              >
                <option value="table">Table</option>
                <option value="bars">Bars</option>
              </select>
            </label>
          </div>
          <PivotView view={draft} />
          <button className="workspace-button" onClick={save}>
            Save to board
          </button>
        </Card>
      ) : (
        <>
          <div className="filter-row">
            {(['research', 'skills', 'buildings'] as const).map((f) => (
              <button
                className={family === f ? 'active' : ''}
                key={f}
                onClick={() => setFamily(f)}
              >
                {f}
              </button>
            ))}
          </div>
          <ChoicesView family={family} />
          {views.map((view) => (
            <Card className="p-5" key={view.id}>
              <div className="section-toolbar mb-4">
                <h2 className="text-lg">{view.name}</h2>
                <div className="flex gap-3">
                  <button
                    onClick={() => {
                      setDraft(view)
                      setParams({ builder: '1' })
                    }}
                  >
                    Edit
                  </button>
                  <button
                    aria-label={`Remove ${view.name}`}
                    onClick={() => remove(view.id)}
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
              <PivotView view={view} />
            </Card>
          ))}
        </>
      )}
    </div>
  )
}

type DatabaseRow = {
  origin: string
  name: string
  estimated_rows: number
  bytes: number
  columns: number
  last_write: number | null
  refreshed: number | null
}
export function Database() {
  const { data, error, reload } = useApi<DatabaseRow[]>('/api/game/database')
  const [selected, setSelected] = useState<DatabaseRow | null>(null)
  const detail = useApi<
    {
      name: string
      type: string
      nullable: string
      null_frac: number | null
      n_distinct: number | null
      sample: string | null
    }[]
  >(
    selected ? `/api/game/database/${selected.origin}/${selected.name}` : null,
    [selected?.origin, selected?.name],
  )
  const cols: Col<DatabaseRow>[] = [
    {
      key: 'table',
      label: 'Table',
      value: (r) => r.name,
      render: (r) => <b>{r.name}</b>,
    },
    {
      key: 'origin',
      label: 'Schema',
      value: (r) => r.origin,
      render: (r) => r.origin,
    },
    {
      key: 'rows',
      label: 'Estimated rows',
      value: (r) => r.estimated_rows,
      render: (r) => n(r.estimated_rows),
    },
    {
      key: 'bytes',
      label: 'Size on disk',
      value: (r) => r.bytes,
      render: (r) => `${n(r.bytes / 1024 / 1024, 2)} MB`,
    },
    {
      key: 'columns',
      label: 'Columns',
      value: (r) => r.columns,
      render: (r) => n(r.columns),
    },
    {
      key: 'write',
      label: 'Last write',
      value: (r) => r.last_write ?? undefined,
      render: (r) =>
        r.last_write == null
          ? 'Not recorded'
          : new Date(r.last_write * 1000).toLocaleString(),
    },
  ]
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <Skeleton rows={8} />
  return (
    <div className="space-y-5">
      <div className="section-toolbar">
        <h1 className="text-xl">Database</h1>
        <button className="workspace-button" onClick={reload}>
          Refresh
        </button>
      </div>
      <div className="detail-cards">
        {[
          ['Tables', n(data.length)],
          ['Estimated rows', n(data.reduce((s, r) => s + r.estimated_rows, 0))],
          [
            'Size on disk',
            `${n(data.reduce((s, r) => s + r.bytes, 0) / 1024 ** 3, 2)} GB`,
          ],
        ].map(([label, v]) => (
          <Card key={label} className="p-4">
            <span className="eyebrow">{label}</span>
            <strong className="text-xl block mt-2">{v}</strong>
          </Card>
        ))}
      </div>
      <DataTable
        rows={data}
        cols={cols}
        rowId={(r) => `${r.origin}.${r.name}`}
        onRowClick={setSelected}
        pageSize={25}
      />
      {selected && (
        <Section
          title={`${selected.origin}.${selected.name}`}
          scope={{ text: 'Recorded column definitions' }}
        >
          {detail.error ? (
            <ErrorState error={detail.error} onRetry={detail.reload} />
          ) : !detail.data ? (
            <Skeleton rows={4} />
          ) : (
            <DataTable
              rows={detail.data}
              cols={[
                {
                  key: 'name',
                  label: 'Column',
                  value: (r) => r.name,
                  render: (r) => r.name,
                },
                {
                  key: 'type',
                  label: 'Type',
                  value: (r) => r.type,
                  render: (r) => <span className="num">{r.type}</span>,
                },
                {
                  key: 'null',
                  label: 'Nullable',
                  value: (r) => r.nullable,
                  render: (r) => r.nullable,
                },
                {
                  key: 'missing',
                  label: 'Null fraction',
                  value: (r) => r.null_frac ?? undefined,
                  render: (r) =>
                    r.null_frac == null ? '—' : n(r.null_frac * 100, 1) + '%',
                },
                {
                  key: 'distinct',
                  label: 'Distinct estimate',
                  value: (r) => r.n_distinct ?? undefined,
                  render: (r) => (r.n_distinct == null ? '—' : n(r.n_distinct)),
                },
                {
                  key: 'sample',
                  label: 'Sample',
                  value: (r) => r.sample || '',
                  render: (r) => r.sample || '—',
                },
              ]}
              rowId={(r) => r.name}
            />
          )}
        </Section>
      )}
    </div>
  )
}
export function Options() {
  const [frame, setFrame] = useState(
    () => localStorage.getItem('map-frame') || 'realm',
  )
  const [chars, setChars] = useState(
    () => localStorage.getItem('home-characters') || '4',
  )
  return (
    <div className="space-y-8">
      <h1 className="text-xl">Options</h1>
      <Section
        title="Score definition"
        scope={{ text: 'Weights used to rank campaign rewards' }}
      >
        <RewardWeightsCard />
      </Section>
      <Section title="Home" scope={{ text: 'Saved in this browser' }}>
        <Card className="p-5 max-w-xl space-y-4">
          <label className="flex justify-between items-center gap-3">
            Map frame
            <select
              className="workspace-input"
              value={frame}
              onChange={(e) => {
                setFrame(e.target.value)
                localStorage.setItem('map-frame', e.target.value)
              }}
            >
              <option value="realm">Owned + adjacent</option>
              <option value="world">Whole known world</option>
            </select>
          </label>
          <label className="flex justify-between items-center gap-3">
            Characters shown
            <select
              className="workspace-input"
              value={chars}
              onChange={(e) => {
                setChars(e.target.value)
                localStorage.setItem('home-characters', e.target.value)
              }}
            >
              <option value="4">First four</option>
              <option value="all">All recorded</option>
              <option value="1">Faction leader</option>
            </select>
          </label>
        </Card>
      </Section>
      <Link className="small-link" to="/status">
        Service status →
      </Link>
    </div>
  )
}
