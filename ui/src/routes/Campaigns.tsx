import { useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import {
  Bar,
  Card,
  Chip,
  ErrorState,
  IdentLabel,
  MetricTile,
  RangeMeter,
  Section,
  Skeleton,
} from '@/components/primitives'
import { SubNav, useSubView } from '@/components/SubNav'
import { ChartFrame } from '@/components/charts'
import {
  DotPlot,
  Histogram,
  Lines,
  MiniHist,
  PickLanes,
  RankingBars,
  Scatter,
  ScoreBars,
  SortedBars,
  mapColor,
  mapShort,
} from '@/components/startcharts'
import {
  useApi,
  type CampaignRow,
  type CampaignsPage,
  type StartRow,
  type StartsPage,
  type UcbPick,
  type UcbPickPage,
  type UcbPicksPage,
  type UcbRow,
} from '@/lib/api'
import { clock, n } from '@/lib/format'
import { cn } from '@/lib/utils'

const VIEWS = [
  { key: 'starts', label: 'starts', asks: 'what the pool of starts looks like' },
  { key: 'selector', label: 'selector', asks: 'why the selector played this start' },
  { key: 'campaigns', label: 'campaigns', asks: 'which campaign ended how' },
]
const LEGACY: Record<string, string> = { all: 'campaigns', picks: 'selector', matrix: 'starts' }

const inf = (v: number | null | undefined, digits = 3) => (v == null ? '∞' : n(v, digits))
const dash = (v: number | null | undefined, digits = 0) => (v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>)
const signed = (v: number | null | undefined, digits = 3) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{v >= 0 ? '+' : ''}{n(v, digits)}</span>

function useFilters() {
  const [params, setParams] = useSearchParams()
  const get = (k: string) => params.get(k) ?? ''
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }
  return { map: get('map'), race: get('race'), plays: get('plays'), pick: get('pick'), outcome: get('outcome'), range: get('range'), set }
}

function Select({ value, onChange, children }: { value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="border-line bg-surface rounded-md border px-2 py-1 text-xs">
      {children}
    </select>
  )
}

const startId = (r: { campaign_map?: { raw: string } | null; faction: { raw: string } }) => `${r.campaign_map?.raw ?? ''}|${r.faction.raw}`
const startUrl = (id: string) => {
  const [m, f] = id.split('|')
  return `/starts/${encodeURIComponent(m)}/${encodeURIComponent(f)}`
}
const lordOf = (r: { leader?: string | null; faction: { label: string } }) => r.leader ?? r.faction.label
const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)

function MapCell({ ident }: { ident?: { raw: string; label: string } | null }) {
  if (!ident) return <span className="text-dim">—</span>
  return (
    <span className="inline-flex items-center gap-1.5" title={ident.label}>
      <span className="inline-block size-2 rounded-full" style={{ background: mapColor(ident.raw) }} />
      <span className="text-dim">{mapShort(ident.raw, ident.label)}</span>
    </span>
  )
}


const startCols: Col<StartRow>[] = [
  {
    key: 'lord',
    label: 'lord',
    value: (r) => lordOf(r),
    render: (r) => <IdentLabel ident={{ ...r.faction, label: lordOf(r), culture: null }} />,
  },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'n_window', label: 'n', group: 'window', align: 'right', value: (r) => r.n_window, render: (r) => <span className="num">{r.n_window}</span> },
  { key: 'mean', label: 'mean', group: 'window', align: 'right', value: (r) => r.mean ?? undefined, sortUndefined: 'last', render: (r) => dash(r.mean, 2) },
  { key: 'entropy', label: 'H', unit: 'bits', group: 'window', align: 'right', value: (r) => r.entropy ?? undefined, sortUndefined: 'last', render: (r) => dash(r.entropy, 2) },
  { key: 'std', label: 'std', group: 'window', align: 'right', value: (r) => r.std ?? undefined, sortUndefined: 'last', render: (r) => dash(r.std, 2) },
  {
    key: 'hist',
    label: 'rewards',
    group: 'window',
    value: (r) => r.best ?? 0,
    render: (r) => (r.n_window ? <MiniHist bins={r.reward_bins ?? []} color={mapColor(r.campaign_map?.raw)} /> : <span className="text-dim">—</span>),
  },
  {
    key: 'zero',
    label: 'zero',
    unit: '%',
    group: 'window',
    align: 'right',
    optional: true,
    value: (r) => (r.zero_rate?.of ? r.zero_rate.n / r.zero_rate.of : -1),
    render: (r) => (r.zero_rate?.of ? <span className="num">{((100 * r.zero_rate.n) / r.zero_rate.of).toFixed(0)}</span> : <span className="text-dim">—</span>),
  },
  {
    key: 'blend',
    label: 'blend',
    group: 'score',
    value: (r) => r.blend ?? undefined,
    sortUndefined: 'last',
    render: (r) => (r.blend == null ? <span className="text-dim">—</span> : <RangeMeter value={r.blend} lo={-2} hi={2} width={96} digits={2} />),
  },
  { key: 'explore', label: 'explore', group: 'score', align: 'right', value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{r.n_window ? inf(r.explore) : '∞'}</span> },
  { key: 'score', label: 'score', group: 'score', align: 'right', value: (r) => r.score ?? Number.MAX_SAFE_INTEGER, render: (r) => <strong className="num">{r.in_pool ? inf(r.score) : '—'}</strong> },
  { key: 'rank', label: 'rank', group: 'score', align: 'right', direction: 'down', value: (r) => r.rank ?? undefined, sortUndefined: 'last', render: (r) => (r.rank == null ? <span className="text-dim">—</span> : <span className="num">#{r.rank}</span>) },
  { key: 'picks', label: 'picks', group: 'selector', align: 'right', value: (r) => r.picks, render: (r) => <span className="num">{r.picks}</span> },
  { key: 'picks_ago', label: 'picks ago', group: 'selector', align: 'right', value: (r) => r.picks_ago ?? undefined, sortUndefined: 'last', render: (r) => dash(r.picks_ago) },
  { key: 'plays_ago', label: 'plays ago', group: 'selector', align: 'right', optional: true, value: (r) => r.plays_ago ?? undefined, sortUndefined: 'last', render: (r) => dash(r.plays_ago) },
  { key: 'n', label: 'n', group: 'all time', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'best', label: 'best', group: 'all time', align: 'right', value: (r) => r.best ?? undefined, sortUndefined: 'last', render: (r) => dash(r.best) },
  { key: 'sett_avg', label: 'settlements', unit: 'avg', group: 'all time', align: 'right', optional: true, value: (r) => r.settlements_avg ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_avg, 2) },
  { key: 'lvl_avg', label: 'levels', unit: 'avg', group: 'all time', align: 'right', optional: true, value: (r) => r.levels_avg ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_avg, 2) },
  { key: 'avg_turns', label: 'avg turns', group: 'all time', align: 'right', optional: true, value: (r) => r.avg_turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.avg_turns, 1) },
  { key: 'spt', label: 's/turn', group: 'all time', align: 'right', optional: true, value: (r) => r.sec_per_turn ?? undefined, sortUndefined: 'last', render: (r) => dash(r.sec_per_turn, 1) },
  { key: 'confirm', label: 'confirmed', group: 'all time', optional: true, value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1), render: (r) => <Bar rate={r.confirm_rate ?? null} /> },
]

interface RaceRow {
  race: string
  starts: number
  plays: number
  mean: number | null
  zero: number | null
  best: string
  bestMean: number | null
  picks: number
}

const raceCols: Col<RaceRow>[] = [
  { key: 'race', label: 'race', value: (r) => r.race, render: (r) => r.race },
  { key: 'starts', label: 'starts', align: 'right', value: (r) => r.starts, render: (r) => <span className="num">{r.starts}</span> },
  { key: 'plays', label: 'plays', unit: 'window', align: 'right', value: (r) => r.plays, render: (r) => <span className="num">{r.plays}</span> },
  { key: 'mean', label: 'mean reward', align: 'right', value: (r) => r.mean ?? undefined, sortUndefined: 'last', render: (r) => dash(r.mean, 2) },
  { key: 'zero', label: 'zero', unit: '%', align: 'right', value: (r) => r.zero ?? undefined, sortUndefined: 'last', render: (r) => (r.zero == null ? <span className="text-dim">—</span> : <span className="num">{(100 * r.zero).toFixed(0)}</span>) },
  { key: 'best', label: 'best start', value: (r) => r.bestMean ?? undefined, sortUndefined: 'last', render: (r) => (r.best ? <span>{r.best} <span className="num text-dim">{n(r.bestMean, 2)}</span></span> : <span className="text-dim">—</span>) },
  { key: 'picks', label: 'picks', align: 'right', value: (r) => r.picks, render: (r) => <span className="num">{r.picks}</span> },
]

function Starts() {
  const { data, error, loading, reload } = useApi<StartsPage>('/api/campaigns/starts')
  const f = useFilters()
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const minPlays = Number(f.plays || 0)
  const rows = data.rows.filter(
    (r) => (!f.map || r.campaign_map?.raw === f.map) && (!f.race || (r.faction.culture ?? '') === f.race) && r.n_window >= minPlays,
  )
  const races = [...new Set(data.rows.map((r) => r.faction.culture ?? ''))].filter(Boolean).sort()
  const go = (id: string) => navigate(startUrl(id))
  const sub = (r: StartRow) => `${r.faction.culture ?? ''} · ${mapShort(r.campaign_map?.raw, r.campaign_map?.label)}`

  const bars = [...rows]
    .sort((a, b) => b.n_window - a.n_window)
    .map((r) => ({ id: startId(r), label: lordOf(r), value: r.n_window, color: mapColor(r.campaign_map?.raw), sub: sub(r) }))
  const played = rows.filter((r) => r.n_window >= data.min_plays && r.mean != null && r.entropy != null)
  const plane = played.map((r) => ({
    id: startId(r),
    label: lordOf(r),
    x: r.mean as number,
    y: r.entropy as number,
    r: 2.5 + Math.sqrt(r.n_window),
    color: mapColor(r.campaign_map?.raw),
    sub: `${sub(r)} · n ${r.n_window} · std ${n(r.std, 2)} · blend ${r.blend == null ? '—' : r.blend.toFixed(2)}`,
  }))
  const pvm = rows
    .filter((r) => r.n_window > 0 && r.mean != null)
    .map((r) => ({ id: startId(r), label: lordOf(r), x: r.n_window, y: r.mean as number, r: 4, color: mapColor(r.campaign_map?.raw), sub: `${sub(r)} · rank ${r.rank ?? '—'}` }))
  const maps = (data.maps ?? []).filter((m) => !f.map || m.raw === f.map)
  const series = maps.map((m) => ({ key: m.raw, label: m.label, color: mapColor(m.raw) }))
  const width = Math.max(1, ...rows.map((r) => (r.reward_bins ?? []).length))
  const rewardBins = Array.from({ length: width }, (_, x) => ({ x, counts: {} as Record<string, number> }))
  for (const r of rows) {
    const m = r.campaign_map?.raw ?? ''
    ;(r.reward_bins ?? []).forEach((c, x) => {
      if (c) rewardBins[x].counts[m] = (rewardBins[x].counts[m] ?? 0) + c
    })
  }
  const turnsBins = (data.turns_bins ?? []).map((b) => ({ x: b.x, counts: Object.fromEntries(Object.entries(b.counts ?? {}).filter(([k]) => !f.map || k === f.map)) }))

  const byRace = new Map<string, StartRow[]>()
  for (const r of rows) byRace.set(r.faction.culture ?? '—', [...(byRace.get(r.faction.culture ?? '—') ?? []), r])
  const raceRows: RaceRow[] = [...byRace.entries()].map(([race, rs]) => {
    const plays = rs.reduce((a, r) => a + r.n_window, 0)
    const mean = plays ? rs.reduce((a, r) => a + (r.mean ?? 0) * r.n_window, 0) / plays : null
    const zeros = rs.reduce((a, r) => a + (r.zero_rate?.n ?? 0), 0)
    const best = [...rs].filter((r) => r.mean != null).sort((a, b) => (b.mean ?? 0) - (a.mean ?? 0))[0]
    return { race, starts: rs.length, plays, mean, zero: plays ? zeros / plays : null, best: best ? lordOf(best) : '', bestMean: best?.mean ?? null, picks: rs.reduce((a, r) => a + r.picks, 0) }
  })
  const dots = raceRows
    .filter((r) => r.mean != null)
    .sort((a, b) => (b.mean ?? 0) - (a.mean ?? 0))
    .map((r) => ({ id: r.race, label: r.race, value: r.mean as number, n: r.plays, sub: `${r.starts} starts · best ${r.best}` }))

  const meanMu = avg(played.map((r) => r.mean as number))
  const hMu = avg(played.map((r) => r.entropy as number))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={f.map} onChange={(v) => f.set('map', v)}>
          <option value="">every map</option>
          {(data.maps ?? []).map((m) => (
            <option key={m.raw} value={m.raw}>{m.label}</option>
          ))}
        </Select>
        <Select value={f.race} onChange={(v) => f.set('race', v)}>
          <option value="">every race</option>
          {races.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </Select>
        <Select value={f.plays} onChange={(v) => f.set('plays', v)}>
          <option value="">any plays</option>
          <option value="5">5+ plays</option>
          <option value="10">10+ plays</option>
          <option value="25">25+ plays</option>
        </Select>
        <span className="text-dim text-2xs">
          <b className="num text-fg">{rows.length}</b> starts · window <b className="num text-fg">{data.window}</b> · C <b className="num text-fg">{n(data.c, 2)}</b>
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(data.tiles ?? []).map((m) => (
          <MetricTile key={m.label} metric={m} />
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="plays per start" scope={{ text: 'inside the selector window, most played first', detail: 'rail: cumulative share of plays' }}>
          <ChartFrame
            table={
              <DataTable
                rows={bars.map((b, i) => ({ ...b, i }))}
                cols={[
                  { key: 'i', label: '#', align: 'right', value: (r) => r.i + 1, render: (r) => r.i + 1 },
                  { key: 'label', label: 'lord', value: (r) => r.label, render: (r) => r.label },
                  { key: 'sub', label: 'race · map', value: (r) => r.sub ?? '', render: (r) => <span className="text-dim">{r.sub}</span> },
                  { key: 'value', label: 'plays', align: 'right', value: (r) => r.value, render: (r) => <span className="num">{r.value}</span> },
                ]}
                rowId={(r) => r.id}
                onRowClick={(r) => go(r.id)}
                dense
                pageSize={10}
                emptyWhat="no start has a play in the window"
              />
            }
          >
            <div className="p-2">
              <SortedBars items={bars} onSelect={go} />
            </div>
          </ChartFrame>
        </Section>

        <Section title="mean vs entropy" scope={{ text: 'every start with enough plays; dot area ∝ n', detail: 'hairlines at the population means the z-scores are taken from' }}>
          <ChartFrame
            table={
              <DataTable
                rows={played}
                cols={[
                  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => lordOf(r) },
                  { key: 'n', label: 'n', align: 'right', value: (r) => r.n_window, render: (r) => r.n_window },
                  { key: 'mean', label: 'mean', align: 'right', value: (r) => r.mean ?? 0, render: (r) => n(r.mean, 2) },
                  { key: 'H', label: 'H', align: 'right', value: (r) => r.entropy ?? 0, render: (r) => n(r.entropy, 2) },
                  { key: 'std', label: 'std', align: 'right', value: (r) => r.std ?? 0, render: (r) => n(r.std, 2) },
                  { key: 'blend', label: 'blend', align: 'right', value: (r) => r.blend ?? 0, render: (r) => signed(r.blend) },
                ]}
                rowId={(r) => startId(r)}
                onRowClick={(r) => go(startId(r))}
                dense
                pageSize={10}
                emptyWhat="no start has enough plays"
              />
            }
          >
            <div className="p-2">
              <Scatter points={plane} xLabel="mean reward" yLabel="H (bits)" hairX={meanMu} hairY={hMu} onSelect={go} fallback={`${plane.length} starts with ${data.min_plays}+ plays`} />
            </div>
          </ChartFrame>
        </Section>

        <Section title="plays vs mean reward" scope={{ text: 'one dot per start, log plays', detail: 'the explore/exploit picture' }}>
          <ChartFrame
            table={
              <DataTable
                rows={rows.filter((r) => r.n_window > 0)}
                cols={[
                  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => lordOf(r) },
                  { key: 'n', label: 'n', align: 'right', value: (r) => r.n_window, render: (r) => r.n_window },
                  { key: 'mean', label: 'mean', align: 'right', value: (r) => r.mean ?? 0, render: (r) => n(r.mean, 2) },
                  { key: 'rank', label: 'rank', align: 'right', value: (r) => r.rank ?? 999, render: (r) => (r.rank == null ? '—' : `#${r.rank}`) },
                ]}
                rowId={(r) => startId(r)}
                onRowClick={(r) => go(startId(r))}
                dense
                pageSize={10}
                emptyWhat="no start has a play in the window"
              />
            }
          >
            <div className="p-2">
              <Scatter points={pvm} xLabel="plays" yLabel="mean reward" xLog onSelect={go} />
            </div>
          </ChartFrame>
        </Section>

        <Section title="reward by race" scope={{ text: 'play-weighted mean reward per race, window', detail: 'dot area ∝ plays' }}>
          <ChartFrame
            table={<DataTable rows={raceRows} cols={raceCols} rowId={(r) => r.race} onRowClick={(r) => f.set('race', r.race)} initialSort={{ key: 'mean', desc: true }} dense pageSize={10} emptyWhat="no race has a play in the window" />}
          >
            <div className="p-2">
              <DotPlot rows={dots} xLabel="mean reward" onSelect={(id) => f.set('race', id)} />
            </div>
          </ChartFrame>
        </Section>

        <Section title="reward per campaign" scope={{ text: 'campaigns in the window by integer reward', detail: 'settlements gained + lord levels gained' }}>
          <ChartFrame
            table={
              <DataTable
                rows={rewardBins}
                cols={[
                  { key: 'x', label: 'reward', align: 'right', value: (r) => r.x, render: (r) => r.x },
                  ...series.map((s) => ({ key: s.key, label: s.label, align: 'right' as const, value: (r: (typeof rewardBins)[number]) => r.counts[s.key] ?? 0, render: (r: (typeof rewardBins)[number]) => n(r.counts[s.key] ?? 0) })),
                ]}
                rowId={(r) => String(r.x)}
                dense
                pageSize={10}
                emptyWhat="no campaign in the window"
              />
            }
          >
            <div className="p-2">
              <Histogram bins={rewardBins} series={series} xLabel="reward" />
            </div>
          </ChartFrame>
        </Section>

        <Section title="turns reached" scope={{ text: 'campaigns in the window by last turn' }}>
          <ChartFrame
            table={
              <DataTable
                rows={turnsBins}
                cols={[
                  { key: 'x', label: 'turns', align: 'right', value: (r) => r.x, render: (r) => r.x },
                  ...series.map((s) => ({ key: s.key, label: s.label, align: 'right' as const, value: (r: (typeof turnsBins)[number]) => r.counts[s.key] ?? 0, render: (r: (typeof turnsBins)[number]) => n(r.counts[s.key] ?? 0) })),
                ]}
                rowId={(r) => String(r.x)}
                dense
                pageSize={10}
                emptyWhat="no campaign in the window"
              />
            }
          >
            <div className="p-2">
              <Histogram bins={turnsBins} series={series} xLabel="turns" />
            </div>
          </ChartFrame>
        </Section>
      </div>

      <Section title="starts" scope={data.scope}>
        <DataTable
          rows={rows}
          cols={startCols}
          rowId={(r) => startId(r)}
          onRowClick={(r) => go(startId(r))}
          initialSort={{ key: 'rank', desc: false }}
          searchPlaceholder="search lord, race, map…"
          pageSize={10}
          emptyWhat="no start matches"
        />
      </Section>

      <Section title="by race" scope={{ text: 'the same starts rolled up by race; click a row to filter' }}>
        <DataTable rows={raceRows} cols={raceCols} rowId={(r) => r.race} onRowClick={(r) => f.set('race', f.race === r.race ? '' : r.race)} initialSort={{ key: 'plays', desc: true }} pageSize={10} emptyWhat="no race matches" />
      </Section>
    </div>
  )
}


const pickCols: Col<UcbPick>[] = [
  { key: 'pick', label: 'pick', align: 'right', value: (r) => r.pick_id, render: (r) => <span className="num">{r.pick_id}</span> },
  { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => <IdentLabel ident={{ ...r.faction, label: lordOf(r), culture: null }} /> },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'c', label: 'C', align: 'right', value: (r) => r.c ?? 0, render: (r) => n(r.c, 2) },
  { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'blend', label: 'blend', group: 'winning score', align: 'right', value: (r) => r.blend ?? undefined, sortUndefined: 'last', render: (r) => signed(r.blend) },
  { key: 'explore', label: 'explore', group: 'winning score', align: 'right', value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{inf(r.explore)}</span> },
  { key: 'score', label: 'score', group: 'winning score', align: 'right', value: (r) => r.score ?? Number.MAX_SAFE_INTEGER, render: (r) => <strong className="num">{inf(r.score)}</strong> },
  { key: 'margin', label: 'margin', unit: 'to #2', group: 'winning score', align: 'right', value: (r) => r.margin ?? undefined, sortUndefined: 'last', render: (r) => dash(r.margin, 3) },
  { key: 'tied', label: 'tied', align: 'right', value: (r) => r.tied, render: (r) => (r.tied > 1 ? <Chip state="warn">{n(r.tied)}</Chip> : <span className="num">{r.tied}</span>) },
  { key: 'repeat', label: 'repeat', optional: true, value: (r) => (r.repeat ? 1 : 0), render: (r) => (r.repeat ? <Chip state="neutral">same as previous</Chip> : <span className="text-dim">—</span>) },
  { key: 'reward', label: 'reward', group: 'produced', align: 'right', value: (r) => r.produced?.reward ?? undefined, sortUndefined: 'last', render: (r) => dash(r.produced?.reward) },
  { key: 'turns', label: 'turns', group: 'produced', align: 'right', value: (r) => r.produced?.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.produced?.turns) },
  {
    key: 'outcome',
    label: 'outcome',
    group: 'produced',
    value: (r) => r.produced?.outcome?.label ?? '',
    render: (r) => (r.produced?.outcome ? <Chip state={r.produced.outcome_state ?? 'neutral'}>{r.produced.outcome.label}</Chip> : <span className="text-dim">{r.produced ? 'running' : '—'}</span>),
  },
  { key: 'mean', label: 'mean', optional: true, align: 'right', value: (r) => r.mean ?? 0, render: (r) => dash(r.mean, 2) },
  { key: 'entropy', label: 'H', optional: true, align: 'right', value: (r) => r.entropy ?? undefined, sortUndefined: 'last', render: (r) => dash(r.entropy, 2) },
  { key: 'std', label: 'std', optional: true, align: 'right', value: (r) => r.std ?? undefined, sortUndefined: 'last', render: (r) => dash(r.std, 2) },
  { key: 'plays', label: 'plays', unit: 'window', optional: true, align: 'right', value: (r) => r.total_plays, render: (r) => n(r.total_plays) },
  { key: 'ranked', label: 'ranked', optional: true, align: 'right', value: (r) => r.starts, render: (r) => n(r.starts) },
  { key: 'distinct', label: 'distinct', unit: 'last 50', optional: true, align: 'right', value: (r) => r.distinct_50, render: (r) => n(r.distinct_50) },
  { key: 'gini', label: 'gini of n', optional: true, align: 'right', value: (r) => r.gini ?? undefined, sortUndefined: 'last', render: (r) => dash(r.gini, 3) },
]

const rankCols: Col<UcbRow>[] = [
  { key: 'rank', label: '#', align: 'right', value: (r) => r.rank, render: (r) => (r.chosen ? <strong className="num">{r.rank}</strong> : <span className="num">{r.rank}</span>) },
  { key: 'lord', label: 'lord', value: (r) => lordOf(r), render: (r) => <IdentLabel ident={{ ...r.faction, label: lordOf(r), culture: null }} /> },
  { key: 'race', label: 'race', value: (r) => r.faction.culture ?? '', render: (r) => <span className="text-dim">{r.faction.culture ?? '—'}</span> },
  { key: 'map', label: 'map', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  { key: 'n', label: 'n', align: 'right', value: (r) => r.n, render: (r) => <span className="num">{r.n}</span> },
  { key: 'mean', label: 'mean', align: 'right', value: (r) => r.mean ?? 0, render: (r) => dash(r.mean, 2) },
  { key: 'entropy', label: 'H', align: 'right', optional: true, value: (r) => r.entropy ?? undefined, sortUndefined: 'last', render: (r) => dash(r.entropy, 2) },
  { key: 'std', label: 'std', align: 'right', optional: true, value: (r) => r.std ?? undefined, sortUndefined: 'last', render: (r) => dash(r.std, 2) },
  { key: 'z_mean', label: 'z(mean)', align: 'right', optional: true, value: (r) => r.z_mean ?? undefined, sortUndefined: 'last', render: (r) => signed(r.z_mean, 2) },
  { key: 'z_entropy', label: 'z(H)', align: 'right', optional: true, value: (r) => r.z_entropy ?? undefined, sortUndefined: 'last', render: (r) => signed(r.z_entropy, 2) },
  { key: 'z_std', label: 'z(std)', align: 'right', optional: true, value: (r) => r.z_std ?? undefined, sortUndefined: 'last', render: (r) => signed(r.z_std, 2) },
  { key: 'blend', label: 'blend', align: 'right', value: (r) => r.blend ?? undefined, sortUndefined: 'last', render: (r) => signed(r.blend) },
  { key: 'explore', label: 'explore', align: 'right', value: (r) => r.explore ?? Number.MAX_SAFE_INTEGER, render: (r) => <span className="num">{inf(r.explore)}</span> },
  { key: 'score', label: 'score', align: 'right', value: (r) => r.score ?? Number.MAX_SAFE_INTEGER, render: (r) => (r.chosen ? <strong className="num">{inf(r.score)}</strong> : <span className="num">{inf(r.score)}</span>) },
  { key: 'delta', label: 'Δ to #1', align: 'right', value: (r) => r.delta ?? undefined, sortUndefined: 'last', render: (r) => signed(r.delta) },
]

const N_BUCKETS: [number, number, string][] = [
  [0, 1, '0–1'],
  [2, 3, '2–3'],
  [4, 7, '4–7'],
  [8, 15, '8–15'],
  [16, Infinity, '16+'],
]

function Selector() {
  const { data, error, loading, reload } = useApi<UcbPicksPage>('/api/campaigns/picks')
  const f = useFilters()
  const navigate = useNavigate()
  const desc = data?.picks ?? []
  const pickId = f.pick ? Number(f.pick) : (desc[0]?.pick_id ?? null)
  const detail = useApi<UcbPickPage>(pickId == null ? null : `/api/campaigns/picks/${pickId}`, [pickId])
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const asc = [...desc].reverse()
  const rangeN = f.range === 'all' ? asc.length : Number(f.range || 200)
  const shown = asc.slice(-rangeN)
  const select = (id: number) => f.set('pick', String(id))
  const sub = (p: UcbPick) => `${p.faction.culture ?? ''} · ${mapShort(p.campaign_map?.raw, p.campaign_map?.label)}`

  const scoreBars = shown.map((p) => ({ id: p.pick_id, label: lordOf(p), blend: p.blend, explore: p.explore, score: p.score, tied: p.tied, c: p.c, sub: `${sub(p)} · n ${p.n}` }))
  const lanes = shown.map((p) => ({ id: p.pick_id, startId: startId(p), startLabel: lordOf(p), mapRaw: p.campaign_map?.raw, n: p.n, tied: p.tied, c: p.c, sub: sub(p) }))
  const xs = shown.map((p) => p.pick_id)
  const ribbon = shown.map((p) => p.c)
  const marks = shown.map((p) => p.tied > 1)
  const selIdx = shown.findIndex((p) => p.pick_id === pickId)
  const joined = asc.filter((p) => p.produced && p.produced.reward != null && p.mean != null)
  const evr = joined.map((p) => ({ id: String(p.pick_id), label: lordOf(p), x: p.mean as number, y: p.produced!.reward as number, r: 2.5 + Math.sqrt(p.n) / 1.5, color: mapColor(p.campaign_map?.raw), sub: `pick ${p.pick_id} · n ${p.n}` }))
  const buckets = N_BUCKETS.map(([lo, hi, label]) => {
    const ps = joined.filter((p) => p.n >= lo && p.n <= hi)
    const exp = avg(ps.map((p) => p.mean as number))
    const real = avg(ps.map((p) => p.produced!.reward as number))
    return { label, picks: ps.length, exp, real, zero: ps.length ? ps.filter((p) => (p.produced!.reward ?? 0) <= 0).length / ps.length : null, diff: exp != null && real != null ? real - exp : null }
  })
  const rows = detail.data?.rows ?? []
  const rankingRows = rows.map((r) => ({ id: startId(r), rank: r.rank, label: lordOf(r), sub: `${r.faction.culture ?? ''} · ${mapShort(r.campaign_map?.raw, r.campaign_map?.label)}`, n: r.n, zMean: r.z_mean, zEntropy: r.z_entropy, zStd: r.z_std, blend: r.blend, explore: r.explore, score: r.score, chosen: r.chosen }))
  const head = detail.data?.pick

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(data.tiles ?? []).map((m) => (
          <MetricTile key={m.label} metric={m} />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-dim text-2xs">picks shown</span>
        {['100', '200', '500', 'all'].map((r) => (
          <button key={r} onClick={() => f.set('range', r)} className={cn('rounded px-2 py-0.5 text-2xs', (f.range || '200') === r ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg')}>
            {r === 'all' ? `all ${asc.length}` : `last ${r}`}
          </button>
        ))}
        {pickId != null && (
          <span className="text-dim ml-auto text-2xs">
            selected pick <b className="num text-fg">{pickId}</b>
          </span>
        )}
      </div>

      <Section title="winning score per pick" scope={{ text: 'blend + explore of the chosen start, in pick order', detail: 'ribbon: C in force; click a bar to open that pick' }}>
        <ChartFrame table={<DataTable rows={desc} cols={pickCols} rowId={(r) => String(r.pick_id)} onRowClick={(r) => select(r.pick_id)} initialSort={{ key: 'pick', desc: true }} dense pageSize={10} emptyWhat="no UCB pick recorded" />}>
          <div className="p-2">
            <ScoreBars picks={scoreBars} selectedId={pickId} onSelect={select} />
          </div>
        </ChartFrame>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="coverage" scope={{ text: 'distinct starts among the trailing 50 picks, and ever', detail: 'dashed: pool size' }}>
          <ChartFrame
            table={
              <DataTable
                rows={shown}
                cols={[
                  { key: 'pick', label: 'pick', align: 'right', value: (r) => r.pick_id, render: (r) => r.pick_id },
                  { key: 'd50', label: 'distinct (50)', align: 'right', value: (r) => r.distinct_50, render: (r) => r.distinct_50 },
                  { key: 'cum', label: 'distinct ever', align: 'right', value: (r) => r.cum_distinct, render: (r) => r.cum_distinct },
                  { key: 'under', label: 'under min plays', align: 'right', value: (r) => r.under_min, render: (r) => r.under_min },
                ]}
                rowId={(r) => String(r.pick_id)}
                onRowClick={(r) => select(r.pick_id)}
                initialSort={{ key: 'pick', desc: true }}
                dense
                pageSize={10}
                emptyWhat="no pick"
              />
            }
          >
            <div className="p-2">
              <Lines
                series={[
                  { key: 'd50', label: 'distinct in last 50', color: 'var(--accent)', values: shown.map((p) => p.distinct_50) },
                  { key: 'cum', label: 'distinct ever', color: 'var(--ggnn)', values: shown.map((p) => p.cum_distinct) },
                ]}
                xs={xs}
                ribbon={ribbon}
                refLines={[{ y: data.pool, label: `pool ${data.pool}` }]}
                marks={marks}
                selectedIndex={selIdx}
                onSelect={(i) => shown[i] && select(shown[i].pick_id)}
              />
            </div>
          </ChartFrame>
        </Section>

        <Section title="concentration" scope={{ text: 'repeat-of-previous share over the trailing 50 picks; gini of plays across the pool at each pick', detail: 'ticks below the axis: tied picks' }}>
          <ChartFrame
            table={
              <DataTable
                rows={shown}
                cols={[
                  { key: 'pick', label: 'pick', align: 'right', value: (r) => r.pick_id, render: (r) => r.pick_id },
                  { key: 'rep', label: 'repeat (50)', align: 'right', value: (r) => r.repeat_50 ?? 0, render: (r) => (r.repeat_50 == null ? '—' : `${(100 * r.repeat_50).toFixed(0)}%`) },
                  { key: 'gini', label: 'gini', align: 'right', value: (r) => r.gini ?? 0, render: (r) => n(r.gini, 3) },
                  { key: 'tied', label: 'tied', align: 'right', value: (r) => r.tied, render: (r) => r.tied },
                ]}
                rowId={(r) => String(r.pick_id)}
                onRowClick={(r) => select(r.pick_id)}
                initialSort={{ key: 'pick', desc: true }}
                dense
                pageSize={10}
                emptyWhat="no pick"
              />
            }
          >
            <div className="p-2">
              <Lines
                series={[
                  { key: 'rep', label: 'repeat of previous (last 50)', color: 'var(--rho-neg)', values: shown.map((p) => p.repeat_50), pct: true },
                  { key: 'gini', label: 'gini of plays', color: 'var(--gnn)', values: shown.map((p) => p.gini), pct: true },
                ]}
                xs={xs}
                ribbon={ribbon}
                yDomain={[0, 1]}
                marks={marks}
                selectedIndex={selIdx}
                onSelect={(i) => shown[i] && select(shown[i].pick_id)}
              />
            </div>
          </ChartFrame>
        </Section>
      </div>

      <Section title="pick lanes" scope={{ text: 'one lane per start chosen in the shown range, most chosen first', detail: 'dot shade: n at the pick · shape: map · ring: tied' }}>
        <ChartFrame
          table={
            <DataTable
              rows={[...lanes.reduce((m, l) => m.set(l.startId, { id: l.startId, label: l.startLabel, sub: l.sub, count: (m.get(l.startId)?.count ?? 0) + 1 }), new Map<string, { id: string; label: string; sub: string; count: number }>()).values()]}
              cols={[
                { key: 'label', label: 'lord', value: (r) => r.label, render: (r) => r.label },
                { key: 'sub', label: 'race · map', value: (r) => r.sub, render: (r) => <span className="text-dim">{r.sub}</span> },
                { key: 'count', label: 'picks', unit: 'in range', align: 'right', value: (r) => r.count, render: (r) => <span className="num">{r.count}</span> },
              ]}
              rowId={(r) => r.id}
              onRowClick={(r) => navigate(startUrl(r.id))}
              initialSort={{ key: 'count', desc: true }}
              dense
              pageSize={10}
              emptyWhat="no pick"
            />
          }
        >
          <div className="p-2">
            <PickLanes picks={lanes} selectedId={pickId} onSelect={select} />
          </div>
        </ChartFrame>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="expected vs realised" scope={{ text: "winner's mean at the pick vs the reward of the campaign it produced", detail: 'dashed: realised = expected · dot area ∝ n' }}>
          <ChartFrame
            table={
              <DataTable
                rows={buckets}
                cols={[
                  { key: 'label', label: 'n at pick', value: (r) => r.label, render: (r) => r.label },
                  { key: 'picks', label: 'picks', align: 'right', value: (r) => r.picks, render: (r) => r.picks },
                  { key: 'exp', label: 'expected', align: 'right', value: (r) => r.exp ?? 0, render: (r) => dash(r.exp, 2) },
                  { key: 'real', label: 'realised', align: 'right', value: (r) => r.real ?? 0, render: (r) => dash(r.real, 2) },
                  { key: 'zero', label: 'zero', unit: '%', align: 'right', value: (r) => r.zero ?? 0, render: (r) => (r.zero == null ? '—' : `${(100 * r.zero).toFixed(0)}`) },
                  { key: 'diff', label: 'realised − expected', align: 'right', value: (r) => r.diff ?? 0, render: (r) => signed(r.diff, 2) },
                ]}
                rowId={(r) => r.label}
                dense
                emptyWhat="no pick has a joined campaign"
              />
            }
          >
            <div className="p-2">
              <Scatter points={evr} xLabel="expected (mean at pick)" yLabel="realised reward" diagonal onSelect={(id) => select(Number(id))} fallback={`${evr.length} picks with a joined campaign`} />
            </div>
          </ChartFrame>
        </Section>

        <Section title={head ? `ranking at pick ${head.pick_id}` : 'ranking at the selected pick'} scope={detail.data?.scope}>
          {detail.error && <ErrorState error={detail.error} onRetry={detail.reload} />}
          {!detail.error && (detail.loading || !detail.data) && <Skeleton rows={8} />}
          {!detail.error && detail.data && (
            <ChartFrame table={<DataTable rows={rows} cols={rankCols} rowId={(r) => String(r.rank)} onRowClick={(r) => navigate(startUrl(startId(r)))} initialSort={{ key: 'rank', desc: false }} dense pageSize={10} emptyWhat="no ranking stored for this pick" />}>
              <div className="p-2">
                {head && (
                  <div className="text-dim mb-1 flex flex-wrap gap-3 px-1 text-2xs">
                    <span><b className="text-fg">{lordOf(head)}</b> · {sub(head)}</span>
                    <span>C <b className="num text-fg">{n(head.c, 2)}</b></span>
                    <span>plays <b className="num text-fg">{n(head.total_plays)}</b></span>
                    <span>tied <b className="num text-fg">{head.tied}</b></span>
                    <span>under min plays <b className="num text-fg">{detail.data.under_min}</b></span>
                    {head.produced && (
                      <span>
                        produced reward <b className="num text-fg">{n(head.produced.reward)}</b>
                        {head.produced.outcome ? <> · {head.produced.outcome.label}</> : null}
                      </span>
                    )}
                  </div>
                )}
                <RankingBars rows={rankingRows} onSelect={(id) => navigate(startUrl(id))} />
              </div>
            </ChartFrame>
          )}
        </Section>
      </div>

      <Section title="pick log" scope={data.scope}>
        <DataTable rows={desc} cols={pickCols} rowId={(r) => String(r.pick_id)} onRowClick={(r) => select(r.pick_id)} initialSort={{ key: 'pick', desc: true }} searchPlaceholder="search lord, race, map, outcome…" pageSize={10} emptyWhat="no UCB pick has been recorded yet" emptyWhy="only runs started with --ucb record them" />
      </Section>
    </div>
  )
}


const campaignCols: Col<CampaignRow>[] = [
  {
    key: 'campaign',
    label: 'lord',
    group: 'campaign',
    value: (r) => r.leader ?? r.campaign.label,
    render: (r) => <IdentLabel ident={{ ...r.campaign, label: r.leader ?? r.campaign.label, culture: null }} />,
  },
  { key: 'race', label: 'race', group: 'campaign', value: (r) => r.campaign.culture ?? '', render: (r) => <span className="text-dim">{r.campaign.culture ?? '—'}</span> },
  { key: 'map', label: 'map', group: 'campaign', value: (r) => r.campaign_map?.label ?? '', render: (r) => <MapCell ident={r.campaign_map} /> },
  {
    key: 'outcome',
    label: 'outcome',
    group: 'campaign',
    value: (r) => r.outcome?.label ?? '',
    render: (r) =>
      r.outcome ? (
        <span className="flex items-center gap-1.5">
          <Chip state={r.outcome_state ?? 'neutral'}>{r.outcome.label}</Chip>
          {r.suspicious && <Chip state="bad">suspicious</Chip>}
        </span>
      ) : (
        <span className="text-dim">running</span>
      ),
  },
  { key: 'turns', label: 'turns', group: 'campaign', align: 'right', value: (r) => r.turns ?? undefined, sortUndefined: 'last', render: (r) => dash(r.turns) },
  { key: 'when', label: 'when', group: 'campaign', value: (r) => r.ended_when ?? '', render: (r) => <span className="text-dim text-2xs">{r.ended_when ?? '—'}</span> },
  { key: 'ended_because', label: 'why it ended', group: 'campaign', optional: true, value: (r) => r.ended_because ?? '', render: (r) => (r.ended_because ? <span className="text-2xs">{r.ended_because}</span> : <span className="text-dim">—</span>) },
  { key: 'reward', label: 'reward', group: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => (r.reward == null ? <span className="text-dim">—</span> : <strong className="num">{n(r.reward)}</strong>) },
  { key: 'sett', label: 'settlements', unit: 'gained', group: 'reward', align: 'right', value: (r) => r.settlements_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.settlements_gained) },
  { key: 'lvl', label: 'lord levels', unit: 'gained', group: 'reward', align: 'right', value: (r) => r.levels_gained ?? undefined, sortUndefined: 'last', render: (r) => dash(r.levels_gained) },
  { key: 'decisions', label: 'decisions', group: 'volume', align: 'right', value: (r) => r.decisions, render: (r) => n(r.decisions) },
  { key: 'confirm', label: 'confirmed', group: 'volume', value: (r) => (r.confirm_rate?.of ? r.confirm_rate.n / r.confirm_rate.of : -1), render: (r) => <Bar rate={r.confirm_rate ?? null} /> },
  { key: 'no_action', label: 'no action', group: 'volume', align: 'right', optional: true, value: (r) => r.no_action, render: (r) => n(r.no_action) },
  { key: 'pick', label: 'UCB pick', group: 'volume', align: 'right', optional: true, value: (r) => r.pick_id ?? undefined, sortUndefined: 'last', render: (r) => dash(r.pick_id) },
  { key: 'settlements_start', label: 'starting', unit: 'settlements', group: 'growth', align: 'right', optional: true, value: (r) => r.first_settlements ?? undefined, sortUndefined: 'last', render: (r) => dash(r.first_settlements) },
  { key: 'settlements_per_turn', label: 'settlements', unit: 'per turn', group: 'growth', align: 'right', optional: true, value: (r) => r.settlements_per_turn ?? undefined, sortUndefined: 'last', render: (r) => signed(r.settlements_per_turn, 2) },
  { key: 'lord_level', label: 'lord level', unit: 'reached', group: 'growth', align: 'right', optional: true, value: (r) => r.peak_lord_level ?? undefined, sortUndefined: 'last', render: (r) => dash(r.peak_lord_level) },
  { key: 'lord_per_turn', label: 'lord level', unit: 'per turn', group: 'growth', align: 'right', optional: true, value: (r) => r.lord_per_turn ?? undefined, sortUndefined: 'last', render: (r) => signed(r.lord_per_turn, 2) },
  { key: 'peak_setts', label: 'peak settlements', group: 'growth', align: 'right', optional: true, value: (r) => r.peak_settlements ?? 0, render: (r) => dash(r.peak_settlements) },
  { key: 'peak_rank', label: 'peak power rank', group: 'growth', align: 'right', direction: 'down', optional: true, value: (r) => r.peak_power_rank ?? 0, render: (r) => dash(r.peak_power_rank) },
  { key: 'final_rank', label: 'final power rank', group: 'growth', align: 'right', direction: 'down', optional: true, value: (r) => r.final_power_rank ?? 0, render: (r) => dash(r.final_power_rank) },
  { key: 'span', label: 'span', unit: 'min', group: 'growth', align: 'right', optional: true, value: (r) => r.span_min ?? 0, render: (r) => dash(r.span_min, 1) },
]

function AllCampaigns() {
  const { data, error, loading, reload } = useApi<CampaignsPage>('/api/campaigns')
  const f = useFilters()
  const navigate = useNavigate()
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={10} />
  const maps = [...new Map(data.rows.filter((r) => r.campaign_map).map((r) => [r.campaign_map!.raw, r.campaign_map!])).values()]
  const races = [...new Set(data.rows.map((r) => r.campaign.culture ?? ''))].filter(Boolean).sort()
  const rows = data.rows.filter(
    (r) => (!f.map || r.campaign_map?.raw === f.map) && (!f.race || (r.campaign.culture ?? '') === f.race) && (!f.outcome || r.outcome?.raw === f.outcome),
  )
  return (
    <Section title="campaigns" scope={data.scope}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select value={f.map} onChange={(v) => f.set('map', v)}>
          <option value="">every map</option>
          {maps.map((m) => (
            <option key={m.raw} value={m.raw}>{m.label}</option>
          ))}
        </Select>
        <Select value={f.race} onChange={(v) => f.set('race', v)}>
          <option value="">every race</option>
          {races.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </Select>
        {data.headline.map((h) => (
          <button key={h.outcome.raw} onClick={() => f.set('outcome', f.outcome === h.outcome.raw ? '' : h.outcome.raw)} className={cn('rounded-full', f.outcome === h.outcome.raw && 'ring-accent ring-2')}>
            <Chip state={h.state ?? 'neutral'}>
              <span className="num mr-1 font-semibold">{h.count}</span>
              {h.outcome.label}
            </Chip>
          </button>
        ))}
        <Card className="text-dim ml-auto px-2 py-1 text-2xs">
          <b className="num text-fg">{rows.length}</b> of {data.rows.length}
        </Card>
      </div>
      <DataTable rows={rows} cols={campaignCols} rowId={(r) => r.campaign.raw} onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)} searchPlaceholder="search lord, race, outcome…" pageSize={10} emptyWhat="no campaign matches" />
    </Section>
  )
}

export function Campaigns() {
  const raw = useSubView(VIEWS)
  const view = LEGACY[raw] ?? raw
  return (
    <div>
      <SubNav views={VIEWS} />
      {view === 'starts' && <Starts />}
      {view === 'selector' && <Selector />}
      {view === 'campaigns' && <AllCampaigns />}
    </div>
  )
}
