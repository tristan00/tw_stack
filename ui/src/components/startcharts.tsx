import { useEffect, useRef, useState, type ReactNode } from 'react'
import { n as fmtN } from '@/lib/format'


export function useMeasure<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

export function mapColor(raw?: string | null) {
  if (raw === 'wh3_main_combi') return 'var(--map-ie)'
  if (raw === 'wh3_main_chaos') return 'var(--map-roc)'
  return 'var(--dim)'
}

export function mapShort(raw?: string | null, label?: string | null) {
  if (raw === 'wh3_main_combi') return 'IE'
  if (raw === 'wh3_main_chaos') return 'RoC'
  return label ?? '—'
}

const P = { l: 42, r: 14, t: 10, b: 24 }
const RIB = 14

function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo]
  const span = hi - lo
  const raw = span / count
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? mag
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6))
  return out
}

function tickText(v: number) {
  if (Math.abs(v) >= 100) return v.toFixed(0)
  if (Number.isInteger(v)) return v.toFixed(0)
  return v.toFixed(Math.abs(v) < 1 ? 2 : 1)
}

function HoverLine({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  return <div className="text-dim mt-1 min-h-4 px-1 text-2xs">{children ?? fallback}</div>
}

export function Legend({ items }: { items: { label: string; color: string; shape?: 'dot' | 'line' | 'square' | 'ring' }[] }) {
  return (
    <div className="text-dim flex flex-wrap items-center gap-3 px-1 text-2xs">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1">
          {it.shape === 'line' ? (
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: it.color }} />
          ) : it.shape === 'square' ? (
            <span className="inline-block size-2 rounded-sm" style={{ background: it.color }} />
          ) : it.shape === 'ring' ? (
            <span className="inline-block size-2 rounded-full border-2" style={{ borderColor: it.color }} />
          ) : (
            <span className="inline-block size-2 rounded-full" style={{ background: it.color }} />
          )}
          {it.label}
        </span>
      ))}
    </div>
  )
}

function Ribbon({ xs, values, y, width }: { xs: number[]; values: (number | null | undefined)[]; y: number; width: number }) {
  const segs: { x0: number; x1: number; v: number }[] = []
  const step = xs.length > 1 ? xs[1] - xs[0] : 8
  for (let i = 0; i < xs.length; i++) {
    const v = values[i]
    if (v === null || v === undefined) continue
    const last = segs[segs.length - 1]
    if (last && last.v === v) last.x1 = xs[i] + step / 2
    else segs.push({ x0: xs[i] - step / 2, x1: xs[i] + step / 2, v })
  }
  return (
    <g>
      {segs.map((s, i) => (
        <g key={i}>
          <rect
            x={Math.max(P.l, s.x0)}
            y={y}
            width={Math.max(1, Math.min(width - P.r, s.x1) - Math.max(P.l, s.x0))}
            height={RIB - 4}
            rx="2"
            fill="var(--raised)"
            stroke="var(--line)"
          />
          {s.x1 - s.x0 > 28 && (
            <text x={(Math.max(P.l, s.x0) + Math.min(width - P.r, s.x1)) / 2} y={y + RIB - 7} textAnchor="middle" fontSize="9" className="num fill-[var(--dim)]">
              C={s.v}
            </text>
          )}
        </g>
      ))}
    </g>
  )
}


export interface HistSeries {
  key: string
  label: string
  color: string
}

export function Histogram({
  bins,
  series,
  height = 170,
  xLabel,
}: {
  bins: { x: number; counts: Record<string, number> }[]
  series: HistSeries[]
  height?: number
  xLabel?: string
}) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(320, w)
  const iw = width - P.l - P.r
  const ph = height - P.t - P.b
  const top = Math.max(1, ...bins.flatMap((b) => series.map((s) => b.counts[s.key] ?? 0)))
  const total = bins.reduce((a, b) => a + series.reduce((c, s) => c + (b.counts[s.key] ?? 0), 0), 0)
  const gw = iw / Math.max(1, bins.length)
  const bw = Math.max(1, (gw - 4 - 2 * (series.length - 1)) / series.length)
  const sy = (v: number) => P.t + ph * (1 - v / top)
  const yt = niceTicks(0, top, 3)
  return (
    <div ref={ref} className="min-w-0">
      <svg width={width} height={height} className="block">
        {yt.map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={sy(v)} y2={sy(v)} stroke="var(--line)" shapeRendering="crispEdges" />
            <text x={P.l - 5} y={sy(v) + 3.5} textAnchor="end" fontSize="10" className="num fill-[var(--dim)]">{tickText(v)}</text>
          </g>
        ))}
        {bins.map((b, i) => {
          const x0 = P.l + i * gw + 2
          return (
            <g key={b.x} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={P.l + i * gw} y={P.t} width={gw} height={ph} fill="transparent" />
              {series.map((s, k) => {
                const v = b.counts[s.key] ?? 0
                const h = v ? Math.max(1, ph * (v / top)) : 0
                return <rect key={s.key} x={x0 + k * (bw + 2)} y={P.t + ph - h} width={bw} height={h} rx="1.5" fill={s.color} opacity={hover === null || hover === i ? 1 : 0.55} />
              })}
              {(bins.length <= 20 || i % Math.ceil(bins.length / 20) === 0) && (
                <text x={P.l + i * gw + gw / 2} y={height - 8} textAnchor="middle" fontSize="10" className="num fill-[var(--dim)]">{b.x}</text>
              )}
            </g>
          )
        })}
        <line x1={P.l} x2={width - P.r} y1={P.t + ph} y2={P.t + ph} stroke="var(--line)" shapeRendering="crispEdges" />
        {xLabel && <text x={width - P.r} y={P.t + 10} textAnchor="end" fontSize="10" className="fill-[var(--dim)]">{xLabel}</text>}
      </svg>
      {series.length > 1 && <Legend items={series.map((s) => ({ label: s.label, color: s.color, shape: 'square' }))} />}
      <HoverLine fallback={`${fmtN(total)} campaigns`}>
        {hover !== null && bins[hover] ? (
          <>
            <b className="num text-fg">{bins[hover].x}</b> ·{' '}
            {series.map((s) => (
              <span key={s.key} className="mr-2">
                {s.label} <b className="num text-fg">{fmtN(bins[hover].counts[s.key] ?? 0)}</b>
                {total ? ` (${((100 * (bins[hover].counts[s.key] ?? 0)) / total).toFixed(0)}%)` : ''}
              </span>
            ))}
          </>
        ) : null}
      </HoverLine>
    </div>
  )
}


export function ShareHistogram({ a, b, aLabel, bLabel, height = 170 }: { a: number[]; b: number[]; aLabel: string; bLabel: string; height?: number }) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(320, w)
  const iw = width - P.l - P.r
  const ph = height - P.t - P.b
  const len = Math.max(a.length, b.length)
  const ta = a.reduce((x, y) => x + y, 0) || 1
  const tb = b.reduce((x, y) => x + y, 0) || 1
  const sa = Array.from({ length: len }, (_, i) => (a[i] ?? 0) / ta)
  const sb = Array.from({ length: len }, (_, i) => (b[i] ?? 0) / tb)
  const top = Math.max(0.05, ...sa, ...sb)
  const gw = iw / Math.max(1, len)
  const sy = (v: number) => P.t + ph * (1 - v / top)
  return (
    <div ref={ref} className="min-w-0">
      <svg width={width} height={height} className="block">
        {niceTicks(0, top, 3).map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={sy(v)} y2={sy(v)} stroke="var(--line)" shapeRendering="crispEdges" />
            <text x={P.l - 5} y={sy(v) + 3.5} textAnchor="end" fontSize="10" className="num fill-[var(--dim)]">{(100 * v).toFixed(0)}%</text>
          </g>
        ))}
        {sa.map((v, i) => {
          const x0 = P.l + i * gw
          const ha = v ? Math.max(1, ph * (v / top)) : 0
          const hb = sb[i] ? Math.max(1, ph * (sb[i] / top)) : 0
          return (
            <g key={i} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={x0} y={P.t} width={gw} height={ph} fill="transparent" />
              <rect x={x0 + 3} y={P.t + ph - hb} width={Math.max(1, gw - 6)} height={hb} fill="none" stroke="var(--dim)" strokeDasharray="3 2" />
              <rect x={x0 + 6} y={P.t + ph - ha} width={Math.max(1, gw - 12)} height={ha} rx="1.5" fill="var(--accent)" opacity={hover === null || hover === i ? 1 : 0.6} />
              <text x={x0 + gw / 2} y={height - 8} textAnchor="middle" fontSize="10" className="num fill-[var(--dim)]">{i}</text>
            </g>
          )
        })}
        <line x1={P.l} x2={width - P.r} y1={P.t + ph} y2={P.t + ph} stroke="var(--line)" shapeRendering="crispEdges" />
      </svg>
      <Legend items={[{ label: aLabel, color: 'var(--accent)', shape: 'square' }, { label: bLabel, color: 'var(--dim)', shape: 'ring' }]} />
      <HoverLine fallback={`${fmtN(ta)} vs ${fmtN(tb)} campaigns`}>
        {hover !== null ? (
          <>
            reward <b className="num text-fg">{hover}</b> · {aLabel} <b className="num text-fg">{(100 * sa[hover]).toFixed(0)}%</b> ({fmtN(a[hover] ?? 0)}) · {bLabel}{' '}
            <b className="num text-fg">{(100 * sb[hover]).toFixed(0)}%</b> ({fmtN(b[hover] ?? 0)})
          </>
        ) : null}
      </HoverLine>
    </div>
  )
}


export interface LineSeries {
  key: string
  label: string
  color: string
  values: (number | null | undefined)[]
  dashed?: boolean
  pct?: boolean
}

export function Lines({
  series,
  xs,
  ribbon,
  refLines = [],
  yDomain,
  height = 170,
  marks,
  onSelect,
  selectedIndex,
}: {
  series: LineSeries[]
  xs: (string | number)[]
  ribbon?: (number | null | undefined)[]
  refLines?: { y: number; label: string }[]
  yDomain?: [number, number]
  height?: number
  marks?: boolean[]
  onSelect?: (index: number) => void
  selectedIndex?: number | null
}) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(320, w)
  const iw = width - P.l - P.r
  const top0 = P.t + (ribbon ? RIB : 0)
  const ph = height - top0 - P.b
  const len = xs.length
  const vals = series.flatMap((s) => s.values.filter((v): v is number => v !== null && v !== undefined))
  const lo = yDomain ? yDomain[0] : Math.min(0, ...vals, ...refLines.map((r) => r.y))
  const hi = yDomain ? yDomain[1] : Math.max(1e-6, ...vals, ...refLines.map((r) => r.y))
  const sx = (i: number) => P.l + (iw * (i + 0.5)) / Math.max(1, len)
  const sy = (v: number) => top0 + ph * (1 - (v - lo) / (hi - lo || 1))
  const xsPos = xs.map((_, i) => sx(i))
  const path = (vs: (number | null | undefined)[]) => {
    let d = ''
    let pen = false
    vs.forEach((v, i) => {
      if (v === null || v === undefined) {
        pen = false
        return
      }
      d += `${pen ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`
      pen = true
    })
    return d
  }
  const at = hover
  const xt = len > 1 ? [0, Math.floor(len / 4), Math.floor(len / 2), Math.floor((3 * len) / 4), len - 1] : [0]
  const pctAny = series.some((s) => s.pct)
  return (
    <div ref={ref} className="min-w-0">
      <svg
        width={width}
        height={height}
        className="block"
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const x = e.clientX - r.left
          const i = Math.round(((x - P.l) / iw) * len - 0.5)
          setHover(i >= 0 && i < len ? i : null)
        }}
        onPointerLeave={() => setHover(null)}
        onClick={() => hover !== null && onSelect?.(hover)}
        style={{ cursor: onSelect ? 'pointer' : undefined }}
      >
        {ribbon && <Ribbon xs={xsPos} values={ribbon} y={P.t} width={width} />}
        {niceTicks(lo, hi, 3).map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={sy(v)} y2={sy(v)} stroke="var(--line)" shapeRendering="crispEdges" />
            <text x={P.l - 5} y={sy(v) + 3.5} textAnchor="end" fontSize="10" className="num fill-[var(--dim)]">{pctAny ? `${(100 * v).toFixed(0)}%` : tickText(v)}</text>
          </g>
        ))}
        {refLines.map((r) => (
          <g key={r.label}>
            <line x1={P.l} x2={width - P.r} y1={sy(r.y)} y2={sy(r.y)} stroke="var(--fg)" strokeDasharray="2 3" opacity="0.6" />
            <text x={P.l + 4} y={sy(r.y) + 10} fontSize="9" className="num fill-[var(--dim)]">{r.label}</text>
          </g>
        ))}
        {marks && marks.map((m, i) => (m ? <line key={i} x1={sx(i)} x2={sx(i)} y1={top0 + ph} y2={top0 + ph + 4} stroke="var(--warn)" strokeWidth="1.5" /> : null))}
        {series.map((s) => (
          <path key={s.key} d={path(s.values)} fill="none" stroke={s.color} strokeWidth="1.75" strokeDasharray={s.dashed ? '4 3' : undefined} strokeLinejoin="round" />
        ))}
        {xt.map((i) => (
          <text key={i} x={sx(i)} y={height - 8} textAnchor={i === 0 ? 'start' : i === len - 1 ? 'end' : 'middle'} fontSize="10" className="num fill-[var(--dim)]">{xs[i]}</text>
        ))}
        {selectedIndex !== null && selectedIndex !== undefined && selectedIndex >= 0 && selectedIndex < len && (
          <line x1={sx(selectedIndex)} x2={sx(selectedIndex)} y1={top0} y2={top0 + ph} stroke="var(--accent)" strokeWidth="1.5" />
        )}
        {at !== null && (
          <g>
            <line x1={sx(at)} x2={sx(at)} y1={top0} y2={top0 + ph} stroke="var(--dim)" opacity="0.6" />
            {series.map((s) => {
              const v = s.values[at]
              return v === null || v === undefined ? null : <circle key={s.key} cx={sx(at)} cy={sy(v)} r="3.5" fill={s.color} stroke="var(--surface)" strokeWidth="1.5" />
            })}
          </g>
        )}
        <line x1={P.l} x2={width - P.r} y1={top0 + ph} y2={top0 + ph} stroke="var(--line)" shapeRendering="crispEdges" />
      </svg>
      <Legend items={series.map((s) => ({ label: s.label, color: s.color, shape: 'line' }))} />
      <HoverLine fallback={`${len} picks`}>
        {at !== null ? (
          <>
            pick <b className="num text-fg">{xs[at]}</b> ·{' '}
            {series.map((s) => {
              const v = s.values[at]
              return (
                <span key={s.key} className="mr-2">
                  {s.label} <b className="num text-fg">{v === null || v === undefined ? '—' : s.pct ? `${(100 * v).toFixed(0)}%` : fmtN(v, Number.isInteger(v) ? 0 : 3)}</b>
                </span>
              )
            })}
          </>
        ) : null}
      </HoverLine>
    </div>
  )
}


export interface StackBucket {
  label: string
  shares: number[]
}

const STACK_COLORS = ['var(--accent)', 'var(--ggnn)', 'var(--gnn)']

export function StackShares({ buckets, keys, height = 110 }: { buckets: StackBucket[]; keys: string[]; height?: number }) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(320, w)
  const iw = width - P.l - P.r
  const ph = height - P.t - P.b
  const gw = iw / Math.max(1, buckets.length)
  return (
    <div ref={ref} className="min-w-0">
      <svg width={width} height={height} className="block">
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={P.t + ph * (1 - v)} y2={P.t + ph * (1 - v)} stroke="var(--line)" shapeRendering="crispEdges" />
            <text x={P.l - 5} y={P.t + ph * (1 - v) + 3.5} textAnchor="end" fontSize="10" className="num fill-[var(--dim)]">{(100 * v).toFixed(0)}%</text>
          </g>
        ))}
        {buckets.map((b, i) => {
          const x0 = P.l + i * gw + 1
          const bw = Math.max(1, gw - 2)
          let acc = 0
          return (
            <g key={b.label} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={P.l + i * gw} y={P.t} width={gw} height={ph} fill="transparent" />
              {b.shares.map((s, k) => {
                const h = (ph * s) / 100
                acc += h
                return <rect key={k} x={x0} y={P.t + ph - acc} width={bw} height={Math.max(0, h - 1)} fill={STACK_COLORS[k % STACK_COLORS.length]} opacity={hover === null || hover === i ? 0.9 : 0.5} />
              })}
            </g>
          )
        })}
        <line x1={P.l} x2={width - P.r} y1={P.t + ph} y2={P.t + ph} stroke="var(--line)" shapeRendering="crispEdges" />
        {buckets.length > 0 && (
          <>
            <text x={P.l} y={height - 8} fontSize="10" className="num fill-[var(--dim)]">{buckets[0].label}</text>
            <text x={width - P.r} y={height - 8} textAnchor="end" fontSize="10" className="num fill-[var(--dim)]">{buckets[buckets.length - 1].label}</text>
          </>
        )}
      </svg>
      <Legend items={keys.map((k, i) => ({ label: k, color: STACK_COLORS[i % STACK_COLORS.length], shape: 'square' as const })).concat([{ label: 'other', color: 'var(--raised)', shape: 'square' as const }])} />
      <HoverLine fallback={`${buckets.length} buckets in play order`}>
        {hover !== null && buckets[hover] ? (
          <>
            <b className="num text-fg">{buckets[hover].label}</b>
            {keys.map((k, k2) => (
              <span key={k} className="ml-2">
                {k} <b className="num text-fg">{buckets[hover].shares[k2]?.toFixed(0) ?? 0}%</b>
              </span>
            ))}
          </>
        ) : null}
      </HoverLine>
    </div>
  )
}


export function Steps({ label, delta, values, turns, height = 96 }: { label: string; delta: string; values: (number | null | undefined)[]; turns: number[]; height?: number }) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(200, w)
  const pts = values.map((v, i) => ({ v, t: turns[i] })).filter((p): p is { v: number; t: number } => p.v !== null && p.v !== undefined)
  const lo = Math.min(...pts.map((p) => p.v))
  const hi = Math.max(...pts.map((p) => p.v))
  const span = hi - lo || 1
  const sx = (i: number) => 8 + (i * (width - 20)) / Math.max(1, pts.length - 1)
  const sy = (v: number) => 12 + (height - 34) * (1 - (v - lo) / span)
  let d = ''
  pts.forEach((p, i) => {
    if (i === 0) d += `M${sx(i).toFixed(1)},${sy(p.v).toFixed(1)}`
    else d += `L${sx(i).toFixed(1)},${sy(pts[i - 1].v).toFixed(1)}L${sx(i).toFixed(1)},${sy(p.v).toFixed(1)}`
  })
  return (
    <div ref={ref} className="bg-surface border-line min-w-0 flex-1 rounded-lg border px-3 py-2.5">
      <div className="flex items-baseline justify-between text-2xs">
        <span className="text-dim">{label}</span>
        <span className="num">{delta}</span>
      </div>
      <svg
        width={width}
        height={height - 30}
        className="mt-1 block"
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const i = Math.round(((e.clientX - r.left - 8) / (width - 20)) * (pts.length - 1))
          setHover(i >= 0 && i < pts.length ? i : null)
        }}
        onPointerLeave={() => setHover(null)}
      >
        <path d={d} fill="none" stroke="var(--accent)" strokeWidth="1.6" />
        {pts.length <= 20 && pts.map((p, i) => <circle key={i} cx={sx(i)} cy={sy(p.v)} r="2.4" fill="var(--accent)" />)}
        {hover !== null && pts[hover] && (
          <circle cx={sx(hover)} cy={sy(pts[hover].v)} r="3.5" fill="var(--fg)" stroke="var(--surface)" strokeWidth="1.5" />
        )}
      </svg>
      <div className="text-dim min-h-4 text-2xs">
        {hover !== null && pts[hover] ? (
          <>
            turn <b className="num text-fg">{pts[hover].t}</b> · <b className="num text-fg">{fmtN(pts[hover].v)}</b>
          </>
        ) : (
          `turns ${pts[0]?.t ?? '—'}–${pts[pts.length - 1]?.t ?? '—'}`
        )}
      </div>
    </div>
  )
}


export interface RewardBar {
  id: string
  label: string
  settlements: number
  levels: number
  picked: boolean
  sub?: string
}

export function RewardBars({ items, onSelect, height = 180, trailing = 5 }: { items: RewardBar[]; onSelect?: (id: string) => void; height?: number; trailing?: number }) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(320, w)
  const iw = width - P.l - P.r
  const ph = height - P.t - P.b
  const len = items.length
  const totals = items.map((i) => i.settlements + i.levels)
  const trend = totals.map((_, i) => {
    const win = totals.slice(Math.max(0, i - trailing + 1), i + 1)
    return win.reduce((a, b) => a + b, 0) / win.length
  })
  const top = Math.max(1, ...totals)
  const gw = iw / Math.max(1, len)
  const bw = Math.max(1, gw - 2)
  const sy = (v: number) => P.t + ph * (1 - v / top)
  const sx = (i: number) => P.l + i * gw
  const xt = len > 1 ? [0, Math.floor(len / 2), len - 1] : [0]
  return (
    <div ref={ref} className="min-w-0">
      <svg width={width} height={height} className="block">
        {niceTicks(0, top, 3).map((v) => (
          <g key={v}>
            <line x1={P.l} x2={width - P.r} y1={sy(v)} y2={sy(v)} stroke="var(--line)" shapeRendering="crispEdges" />
            <text x={P.l - 5} y={sy(v) + 3.5} textAnchor="end" fontSize="10" className="num fill-[var(--dim)]">{tickText(v)}</text>
          </g>
        ))}
        {items.map((it, i) => {
          const dim = hover !== null && hover !== i
          const hs = it.settlements ? Math.max(1, ph * (it.settlements / top)) : 0
          const hl = it.levels ? Math.max(1, ph * (it.levels / top)) : 0
          return (
            <g key={it.id} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} onClick={() => onSelect?.(it.id)} className={onSelect ? 'cursor-pointer' : ''}>
              <rect x={sx(i)} y={P.t} width={gw} height={ph + 10} fill="transparent" />
              <rect x={sx(i) + 1} y={P.t + ph - hs} width={bw} height={hs} fill="var(--accent)" opacity={dim ? 0.4 : 0.95} />
              <rect x={sx(i) + 1} y={P.t + ph - hs - hl - (hs && hl ? 2 : 0)} width={bw} height={hl} fill="var(--ggnn)" opacity={dim ? 0.4 : 0.95} />
              {it.picked && <circle cx={sx(i) + gw / 2} cy={P.t + ph + 5} r="2" fill="none" stroke="var(--fg)" strokeWidth="1.25" />}
            </g>
          )
        })}
        {len > 1 && <path d={'M' + trend.map((v, i) => `${(sx(i) + gw / 2).toFixed(1)},${sy(v).toFixed(1)}`).join('L')} fill="none" stroke="var(--fg)" strokeWidth="1.5" opacity="0.8" />}
        <line x1={P.l} x2={width - P.r} y1={P.t + ph} y2={P.t + ph} stroke="var(--line)" shapeRendering="crispEdges" />
        {xt.map((i) => (
          <text key={i} x={sx(i) + gw / 2} y={height - 6} textAnchor={i === 0 ? 'start' : i === len - 1 ? 'end' : 'middle'} fontSize="10" className="num fill-[var(--dim)]">{i + 1}</text>
        ))}
      </svg>
      <Legend
        items={[
          { label: 'settlements gained', color: 'var(--accent)', shape: 'square' },
          { label: 'lord levels gained', color: 'var(--ggnn)', shape: 'square' },
          { label: `trailing ${trailing} mean`, color: 'var(--fg)', shape: 'line' },
          ...(items.some((i) => i.picked) ? [{ label: 'UCB pick', color: 'var(--fg)', shape: 'ring' as const }] : []),
        ]}
      />
      <HoverLine fallback={`${len} campaigns`}>
        {hover !== null && items[hover] ? (
          <>
            <b className="text-fg">{items[hover].label}</b>
            {items[hover].sub ? <span> · {items[hover].sub}</span> : null} · settlements <b className="num text-fg">{fmtN(items[hover].settlements)}</b> · levels <b className="num text-fg">{fmtN(items[hover].levels)}</b> · reward{' '}
            <b className="num text-fg">{fmtN(totals[hover])}</b> · trailing mean <b className="num text-fg">{trend[hover].toFixed(2)}</b>
          </>
        ) : null}
      </HoverLine>
    </div>
  )
}
