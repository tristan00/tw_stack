import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { AgreementSeriesPoint, GenerationRow, RhoBin } from '@/lib/api'
import { n } from '@/lib/format'
import { Card } from '@/components/primitives'
import { cn } from '@/lib/utils'

/**
 * The charts.
 *
 * Hand-rolled SVG, for the reason the rest of this client is assembled from a dozen small
 * primitives: a chart library brings its own colour scale, its own type ramp and its own
 * tooltip DOM, and each one would have to be overridden token by token to stop these pages
 * speaking two visual languages. What is actually needed is a polyline, a set of columns
 * and a scatter -- against roughly 450 KB of library on a bundle the build caps at 900 KB
 * and a client that ships from disk with no network.
 *
 * Three rules hold across all of them.
 *
 *   The y domain of a rho chart is [-1, +1], ALWAYS. Auto-scaling it to the values present
 *   makes a wobble of 0.03 fill the card and read as a trend. rho has a natural range; the
 *   chart shows it.
 *
 *   A bucket the server gated is NOT DRAWN. The line breaks and the bucket appears in the
 *   table view with the reason -- the same refusal the correlation tables make when they
 *   decline an r over eight points rather than print noise wearing a number.
 *
 *   ONE plotted series per chart, in --accent. The two models are never two lines: they
 *   measure about 6 apart in OKLab under protanopia, inside the band where colour alone
 *   cannot separate two marks. Their identity is carried by ModelKey, beside a word.
 */

function useMeasure<T extends HTMLElement>() {
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

/**
 * The card, the chart/table toggle, and a note that belongs to the drawing.
 *
 * Every chart has a table twin. A value reachable only by hovering a shape is invisible to
 * anyone reading with a keyboard, and invisible to anyone who wants to copy it.
 */
export function ChartFrame({
  children,
  table,
  note,
}: {
  children: ReactNode
  table: ReactNode
  note?: ReactNode
}) {
  const [mode, setMode] = useState<'chart' | 'table'>('chart')
  return (
    <Card className="overflow-hidden">
      <div className="border-line flex items-center justify-end gap-1 border-b px-2 py-1">
        {(['chart', 'table'] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={cn(
              'text-2xs rounded px-1.5 py-0.5',
              mode === m ? 'bg-raised text-fg font-semibold' : 'text-dim hover:text-fg',
            )}
          >
            {m}
          </button>
        ))}
      </div>
      {mode === 'chart' ? children : <div className="p-2">{table}</div>}
      {note && <div className="text-dim border-line border-t px-3 py-1.5 text-2xs">{note}</div>}
    </Card>
  )
}

const PAD = { l: 38, r: 54, t: 8, b: 22 }
const RAIL = 18
const PLOT_H = 180
const HEIGHT = PAD.t + RAIL + PLOT_H + PAD.b

function fmt(v: number | null | undefined, d = 3) {
  if (v === null || v === undefined) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(d)}`
}

/** Correlation over time, with the retrain boundaries drawn on a rail above the plot. */
export function RhoTrend({
  points,
  marks,
}: {
  points: AgreementSeriesPoint[]
  marks: GenerationRow[]
}) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [cursor, setCursor] = useState<number | null>(null)
  const width = Math.max(360, w)
  const iw = width - PAD.l - PAD.r
  const xs = points.map((p) => p.from_ts ?? 0)
  const x0 = xs.length ? xs[0] : 0
  const x1 = xs.length ? xs[xs.length - 1] : x0 + 1
  const sx = (x: number) => PAD.l + (iw * (x - x0)) / (x1 - x0 || 1)
  const sy = (v: number) => PAD.t + RAIL + PLOT_H * (1 - (v + 1) / 2)

  // A gated bucket breaks the line rather than being interpolated across. Joining two
  // measured points through a gap draws a value nobody computed.
  const segs: AgreementSeriesPoint[][] = []
  let cur: AgreementSeriesPoint[] = []
  for (const p of points) {
    if (p.rho_median === null || p.rho_median === undefined) {
      if (cur.length) segs.push(cur)
      cur = []
    } else cur.push(p)
  }
  if (cur.length) segs.push(cur)

  const line = (s: AgreementSeriesPoint[]) =>
    'M' + s.map((p) => `${sx(p.from_ts ?? 0).toFixed(1)},${sy(p.rho_median!).toFixed(1)}`).join('L')

  const band = (s: AgreementSeriesPoint[]) => {
    const up = s.map(
      (p) => `${sx(p.from_ts ?? 0).toFixed(1)},${sy(p.rho_q3 ?? p.rho_median!).toFixed(1)}`,
    )
    const dn = [...s]
      .reverse()
      .map((p) => `${sx(p.from_ts ?? 0).toFixed(1)},${sy(p.rho_q1 ?? p.rho_median!).toFixed(1)}`)
    return `M${up.join('L')}L${dn.join('L')}Z`
  }

  const at = points[cursor ?? points.length - 1]
  const last = segs.at(-1)?.at(-1)
  const retrains = marks.filter((m) => m.retrained && m.from_ts)
  let lastLabelX = -99

  return (
    <div
      ref={ref}
      tabIndex={0}
      role="img"
      aria-label="median rank correlation between the two models over the run; arrow keys step through buckets"
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') setCursor((c) => Math.max(0, (c ?? points.length - 1) - 1))
        if (e.key === 'ArrowRight')
          setCursor((c) => Math.min(points.length - 1, (c ?? points.length - 1) + 1))
      }}
      className="focus:outline-accent min-w-0 focus:outline-1"
    >
      <svg width={width} height={HEIGHT} className="block">
        {/* Solid hairlines, one shade off the surface, never dashed. Zero is the reference
            the reader actually looks for, so it is one step stronger. */}
        {[-1, -0.5, 0, 0.5, 1].map((v) => (
          <g key={v}>
            <line
              x1={PAD.l}
              x2={width - PAD.r}
              y1={sy(v)}
              y2={sy(v)}
              stroke={v === 0 ? 'var(--dim)' : 'var(--line)'}
              strokeWidth="1"
              opacity={v === 0 ? 0.45 : 1}
              shapeRendering="crispEdges"
            />
            <text
              x={PAD.l - 6}
              y={sy(v) + 3.5}
              textAnchor="end"
              fontSize="11"
              className="num fill-[var(--dim)]"
            >
              {v > 0 ? `+${v}` : v}
            </text>
          </g>
        ))}

        {/* The retrain rail sits ABOVE the plot so its labels can never collide with data. */}
        {retrains.map((m, i) => {
          const x = sx(m.from_ts!)
          const label = x - lastLabelX > 20
          if (label) lastLabelX = x
          return (
            <g key={`${m.trial.raw}-${i}`}>
              <line
                x1={x}
                x2={x}
                y1={PAD.t + RAIL}
                y2={PAD.t + RAIL + PLOT_H}
                stroke="var(--line)"
                strokeWidth="1"
                shapeRendering="crispEdges"
              />
              <path
                d={`M${x - 4},${PAD.t + RAIL - 4}L${x + 4},${PAD.t + RAIL - 4}L${x},${PAD.t + RAIL}Z`}
                fill="var(--dim)"
              />
              {label && (
                <text
                  x={x}
                  y={PAD.t + RAIL - 7}
                  textAnchor="middle"
                  fontSize="11"
                  className="num fill-[var(--dim)]"
                >
                  {i + 1}
                </text>
              )}
            </g>
          )
        })}

        {segs.map((s, i) => (
          <path key={`b${i}`} d={band(s)} fill="var(--accent)" opacity="0.10" />
        ))}
        {segs.map((s, i) => (
          <path
            key={`l${i}`}
            d={line(s)}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {/* One direct label, at the end, on the one series. Never a number per point. */}
        {last && (
          <>
            <circle
              cx={sx(last.from_ts ?? 0)}
              cy={sy(last.rho_median!)}
              r="4"
              fill="var(--accent)"
              stroke="var(--surface)"
              strokeWidth="2"
            />
            <text
              x={sx(last.from_ts ?? 0) + 8}
              y={sy(last.rho_median!) + 4}
              fontSize="11"
              className="num fill-[var(--fg)]"
            >
              {fmt(last.rho_median, 2)}
            </text>
          </>
        )}

        {cursor !== null && points[cursor] && (
          <line
            x1={sx(points[cursor].from_ts ?? 0)}
            x2={sx(points[cursor].from_ts ?? 0)}
            y1={PAD.t + RAIL}
            y2={PAD.t + RAIL + PLOT_H}
            stroke="var(--dim)"
            strokeWidth="1"
            opacity="0.5"
            shapeRendering="crispEdges"
          />
        )}

        <line
          x1={PAD.l}
          x2={width - PAD.r}
          y1={PAD.t + RAIL + PLOT_H}
          y2={PAD.t + RAIL + PLOT_H}
          stroke="var(--line)"
          strokeWidth="1"
          shapeRendering="crispEdges"
        />
        {points
          .filter((_p, i) => points.length < 6 || i % Math.ceil(points.length / 5) === 0)
          .map((p) => (
            <text
              key={p.seq}
              x={sx(p.from_ts ?? 0)}
              y={HEIGHT - 6}
              textAnchor="middle"
              fontSize="11"
              className="num fill-[var(--dim)]"
            >
              #{p.from_decision}
            </text>
          ))}

        {/* The crosshair finds the X: the reader aims at a time, never at a 2px line. */}
        <rect
          x={PAD.l}
          y={PAD.t}
          width={iw}
          height={RAIL + PLOT_H}
          fill="transparent"
          onPointerMove={(e) => {
            const b = e.currentTarget.getBoundingClientRect()
            const t = (e.clientX - b.left) / (b.width || 1)
            const want = x0 + t * (x1 - x0)
            let best = 0
            points.forEach((p, i) => {
              if (Math.abs((p.from_ts ?? 0) - want) < Math.abs((points[best].from_ts ?? 0) - want))
                best = i
            })
            setCursor(best)
          }}
          onPointerLeave={() => setCursor(null)}
        />
      </svg>

      {/* A pinned readout, not a floating tooltip: keyboard-reachable, unclippable by the
          card, and it needs no portal. Values lead; labels follow. */}
      <div
        aria-live="polite"
        className="border-line flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t px-3 py-1.5 text-2xs"
      >
        {at ? (
          <>
            <span className="num text-dim">
              #{at.from_decision}–{at.to_decision}
            </span>
            <span>
              rho <b className="num">{fmt(at.rho_median)}</b>
            </span>
            <span className="text-dim">
              middle half{' '}
              <span className="num">
                {fmt(at.rho_q1, 2)} … {fmt(at.rho_q3, 2)}
              </span>
            </span>
            <span className="text-dim">
              <span className="num">{n(at.decisions.value)}</span> decisions in this bucket
            </span>
            {at.gate && <span className="text-warn">{at.gate}</span>}
          </>
        ) : (
          <span className="text-dim">no bucket under the pointer</span>
        )}
      </div>
    </div>
  )
}

/** 4px rounded data-end, square at the baseline. Never `rx` on all four corners. */
function column(x: number, y: number, w: number, h: number, r = 4) {
  const rr = Math.max(0, Math.min(r, w / 2, h))
  return (
    `M${x},${y + h}V${y + rr}A${rr},${rr} 0 0 1 ${x + rr},${y}` +
    `H${x + w - rr}A${rr},${rr} 0 0 1 ${x + w},${y + rr}V${y + h}Z`
  )
}

const GAP = 2
const CAP = 26

/**
 * The distribution of per-decision rho.
 *
 * One hue, and position carries the sign. Deliberately not diverging and deliberately not
 * status-coloured: a negative rho is not "bad" -- near +1 means the second model is
 * redundant, near 0 means unrelated criteria, and neither is a fault -- and status tokens
 * are reserved for severity the server decided. Colouring bins by their own value would
 * also spend the identity channel re-encoding what bar height already shows.
 */
export function RhoHistogram({ bins, median }: { bins: RhoBin[]; median: number | null }) {
  const [ref, w] = useMeasure<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const width = Math.max(320, w)
  const P = { l: 34, r: 12, t: 10, b: 34 }
  const H = 140
  const iw = width - P.l - P.r
  const slot = iw / (bins.length || 1)
  const bw = Math.min(CAP, slot - GAP)
  const hi = Math.max(1, ...bins.map((b) => b.decisions))
  const sx = (v: number) => P.l + (iw * (v + 1)) / 2
  const at = hover === null ? null : bins[hover]

  return (
    <div ref={ref}>
      <svg width={width} height={P.t + H + P.b} className="block">
        <line
          x1={P.l}
          x2={width - P.r}
          y1={P.t + H}
          y2={P.t + H}
          stroke="var(--line)"
          strokeWidth="1"
          shapeRendering="crispEdges"
        />
        <line
          x1={sx(0)}
          x2={sx(0)}
          y1={P.t}
          y2={P.t + H}
          stroke="var(--dim)"
          strokeWidth="1"
          opacity="0.45"
          shapeRendering="crispEdges"
        />
        {bins.map((b, i) => {
          const h = (b.decisions / hi) * H
          const x = P.l + i * slot + (slot - bw) / 2
          return (
            <g key={b.lo}>
              <path
                d={column(x, P.t + H - h, bw, h)}
                fill="var(--accent)"
                opacity={hover === null || hover === i ? 1 : 0.55}
              />
              {/* The hit target is the whole column slot, never the painted pixels. */}
              <rect
                x={P.l + i * slot}
                y={P.t}
                width={slot}
                height={H}
                fill="transparent"
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
              />
            </g>
          )
        })}
        {median !== null && median !== undefined && (
          <>
            <path
              d={`M${sx(median) - 4},${P.t + H + 3}L${sx(median) + 4},${P.t + H + 3}L${sx(median)},${P.t + H - 1}Z`}
              fill="var(--fg)"
            />
            <text
              x={sx(median)}
              y={P.t + H + 16}
              textAnchor="middle"
              fontSize="11"
              className="num fill-[var(--fg)]"
            >
              median {fmt(median, 2)}
            </text>
          </>
        )}
        {[-1, -0.5, 0, 0.5, 1].map((v) => (
          <text
            key={v}
            x={sx(v)}
            y={P.t + H + 30}
            textAnchor="middle"
            fontSize="11"
            className="num fill-[var(--dim)]"
          >
            {v > 0 ? `+${v}` : v}
          </text>
        ))}
        {[0, hi].map((v, i) => (
          <text
            key={v}
            x={P.l - 6}
            y={i ? P.t + 4 : P.t + H}
            textAnchor="end"
            fontSize="11"
            className="num fill-[var(--dim)]"
          >
            {n(v)}
          </text>
        ))}
      </svg>
      <div aria-live="polite" className="border-line border-t px-3 py-1.5 text-2xs">
        {at ? (
          <span>
            <b className="num">{n(at.decisions)}</b> decisions with rho between{' '}
            <span className="num">{at.lo.toFixed(1)}</span> and{' '}
            <span className="num">{at.hi.toFixed(1)}</span>
          </span>
        ) : (
          <span className="text-dim">hover a bar for its count</span>
        )}
      </div>
    </div>
  )
}

export interface RankPair {
  cat: number
  gnn: number
  taken: boolean
  label: string
}

/**
 * One decision's two rankings, as a scatter.
 *
 * This is the honest visual definition of rho: a cloud along the diagonal IS rho near +1.
 * y grows downward so rank 1 is top-left on both axes and perfect agreement is the
 * top-left to bottom-right diagonal.
 */
export function RankScatter({ pairs, size = 236 }: { pairs: RankPair[]; size?: number }) {
  const [hover, setHover] = useState<number | null>(null)
  if (!pairs.length) return null
  const N = Math.max(2, ...pairs.map((p) => Math.max(p.cat, p.gnn)))
  const P = 22
  const s = (r: number) => P + ((size - 2 * P) * (r - 1)) / (N - 1)
  return (
    <div>
      <svg width={size} height={size} className="block">
        <rect
          x={P}
          y={P}
          width={size - 2 * P}
          height={size - 2 * P}
          fill="none"
          stroke="var(--line)"
          strokeWidth="1"
          shapeRendering="crispEdges"
        />
        {/* Perfect agreement. A reference, not data -- hairline, solid, recessive. */}
        <line x1={P} y1={P} x2={size - P} y2={size - P} stroke="var(--line)" strokeWidth="1" />
        {pairs.map((p, i) => (
          <g key={p.label}>
            <circle
              cx={s(p.cat)}
              cy={s(p.gnn)}
              r={p.taken ? 5 : 3.5}
              fill={p.taken ? 'var(--accent)' : 'var(--dim)'}
              opacity={p.taken ? 1 : hover === i ? 0.9 : 0.5}
              stroke="var(--surface)"
              strokeWidth="2"
            />
            <circle
              cx={s(p.cat)}
              cy={s(p.gnn)}
              r="12"
              fill="transparent"
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
            />
          </g>
        ))}
      </svg>
      <div className="text-dim mt-1 text-2xs">
        {hover !== null ? (
          <>
            {pairs[hover].label} · tree <b className="num">{pairs[hover].cat}</b> · graph{' '}
            <b className="num">{pairs[hover].gnn}</b>
          </>
        ) : (
          'the taken offer is filled; every dot is a row in the ranking below'
        )}
      </div>
    </div>
  )
}
