import * as Popover from '@radix-ui/react-popover'
import { HelpCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import type { Count, Ident, Metric, Rate, Scope, State } from '@/lib/api'
import { n, pct, pctValue, stateBg, stateFill, stateText } from '@/lib/format'
import { cn } from '@/lib/utils'


export function CountText({ count, className }: { count: Count; className?: string }) {
  return (
    <span className={cn('inline-flex items-baseline gap-1.5', className)}>
      <b className="num text-fg font-semibold">{n(count.value)}</b>
      <span>{count.noun}</span>
      <span className="text-dim text-2xs">{count.population}</span>
    </span>
  )
}


export function RateText({ rate, digits = 0 }: { rate: Rate | null; digits?: number }) {
  if (!rate) return <span className="text-dim">—</span>
  const p = pct(rate, digits)
  if (p === null) {

    return (
      <span className="text-dim" title={`nothing ${rate.population} yet`}>
        —
      </span>
    )
  }
  return (
    <span className="num whitespace-nowrap">
      {p} <span className="text-dim text-2xs">{n(rate.n)} of {n(rate.of)}</span>
    </span>
  )
}

export function Chip({
  children,
  state = 'neutral',
  title,
  className,
}: {
  children: ReactNode
  state?: State
  title?: string
  className?: string
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-medium whitespace-nowrap',
        stateBg[state],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Dot({ state = 'neutral' }: { state?: State }) {
  return <span className={cn('inline-block size-2 rounded-full align-middle', stateFill[state])} />
}


export function IdentLabel({
  ident,
  className,
  showCulture = true,
}: {
  ident: Ident | null | undefined
  className?: string
  showCulture?: boolean
}) {
  if (!ident) return <span className="text-dim">—</span>
  return (
    <button
      type="button"
      title={`${ident.raw}\n(click to copy)`}
      onClick={(e) => {
        e.stopPropagation()
        void navigator.clipboard?.writeText(ident.raw)
      }}
      className={cn('text-left hover:underline decoration-dotted underline-offset-2', className)}
    >
      <span>{ident.label}</span>
      {showCulture && ident.culture && (
        <span className="text-dim text-2xs ml-1.5">{ident.culture}</span>
      )}
      {ident.tag && <span className="text-dim text-2xs num ml-1.5">#{ident.tag}</span>}
    </button>
  )
}


export function Help({ children }: { children: ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label="what this means"
          className="text-dim hover:text-fg align-middle ml-1 inline-flex"
        >
          <HelpCircle className="size-3.5" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          className="bg-surface border-line text-fg z-50 max-w-80 rounded-lg border p-3 text-xs shadow-lg"
        >
          {children}
          <Popover.Arrow className="fill-[var(--line)]" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}


export function ScopeLine({ scope }: { scope: Scope | undefined }) {
  if (!scope) return null
  return (
    <p className="text-dim text-2xs">
      {scope.text}
      {scope.detail && <span className="ml-1.5 opacity-80">· {scope.detail}</span>}
    </p>
  )
}

export function Section({
  title,
  scope,
  right,
  children,
  className,
}: {
  title: string
  scope?: Scope
  right?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('min-w-0', className)}>
      <header className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          <ScopeLine scope={scope} />
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </header>
      {children}
    </section>
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('bg-surface border-line rounded-lg border', className)}>{children}</div>
  )
}

export function MetricTile({ metric }: { metric: Metric }) {
  return (
    <Card className="px-3.5 py-3">
      <div className="text-dim text-2xs uppercase tracking-wide">{metric.label}</div>
      <div className={cn('num mt-0.5 text-xl leading-tight', stateText[metric.state ?? 'neutral'])}>
        {metric.value === null || metric.value === undefined ? '—' : String(metric.value)}
        {metric.unit && <span className="text-dim ml-0.5 text-sm">{metric.unit}</span>}
      </div>
      {metric.spark && metric.spark.length > 1 && <Sparkline values={metric.spark} />}
      {metric.sub && <div className="text-dim mt-1 text-2xs">{metric.sub}</div>}
    </Card>
  )
}

export function Sparkline({
  values,
  width = 96,
  height = 20,
}: {
  values: number[]
  width?: number
  height?: number
}) {
  if (values.length < 2) return null
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = height - ((v - lo) / span) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return (
    <svg width={width} height={height} className="mt-1 block" aria-hidden>
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
    </svg>
  )
}


export function Bar({ rate, width = 92 }: { rate: Rate | null; width?: number }) {
  const p = pctValue(rate)
  if (p === null) {
    return (
      <span className="text-dim" title={rate ? `nothing ${rate.population} yet` : undefined}>
        —
      </span>
    )
  }


  return (
    <span className="flex items-center gap-2">
      <span className="bg-raised relative inline-block h-2 rounded" style={{ width }}>
        <span
          className="bg-fg/45 absolute inset-y-0 left-0 rounded"
          style={{ width: `${Math.max(2, p)}%` }}
        />
      </span>
      <span className="num text-2xs whitespace-nowrap">
        {p.toFixed(0)}% <span className="text-dim">{n(rate!.n)}/{n(rate!.of)}</span>
      </span>
    </span>
  )
}


export function EmptyState({ what, why }: { what: string; why?: string | null }) {
  return (
    <Card className="text-dim px-4 py-8 text-center">
      <div className="text-sm">{what}</div>
      {why && <div className="text-2xs mt-1 opacity-80">{why}</div>}
    </Card>
  )
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <Card className="border-bad px-4 py-6">
      <div className="text-bad text-sm font-medium">this view could not load</div>
      <div className="text-dim mt-1 text-2xs">{error}</div>
      {onRetry && (
        <button onClick={onRetry} className="border-line mt-3 rounded border px-2 py-1 text-2xs">
          try again
        </button>
      )}
    </Card>
  )
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <Card className="divide-line divide-y">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="px-3 py-2.5">
          <div className="bg-raised h-3 animate-pulse rounded" style={{ width: `${90 - i * 7}%` }} />
        </div>
      ))}
    </Card>
  )
}


export function RangeMeter({
  value,
  lo = -1,
  hi = 1,
  band = null,
  zero = 0,
  digits = 3,
  width = 132,
}: {
  value: number | null | undefined
  lo?: number
  hi?: number

  band?: [number | null | undefined, number | null | undefined] | null

  zero?: number | null
  digits?: number
  width?: number
}) {
  if (value === null || value === undefined) return <span className="text-dim">—</span>
  const at = (v: number) => (100 * (Math.min(hi, Math.max(lo, v)) - lo)) / (hi - lo)
  const b0 = band?.[0]
  const b1 = band?.[1]
  const hasBand = b0 !== null && b0 !== undefined && b1 !== null && b1 !== undefined
  return (
    <span className="flex items-center gap-2">
      <span className="bg-raised relative inline-block h-2.5 rounded" style={{ width }}>
        {hasBand && (
          <span
            className="bg-accent/20 absolute inset-y-0 rounded-sm"
            style={{ left: `${at(b0!)}%`, width: `${Math.max(1, at(b1!) - at(b0!))}%` }}
          />
        )}
        {zero !== null && zero !== undefined && (
          <span className="bg-dim/60 absolute inset-y-0 w-px" style={{ left: `${at(zero)}%` }} />
        )}
        <span
          className="bg-accent absolute inset-y-0 w-0.5 rounded-full"
          style={{ left: `calc(${at(value)}% - 1px)` }}
        />
      </span>
      <span className="num text-2xs whitespace-nowrap">
        {value >= 0 ? '+' : ''}
        {value.toFixed(digits)}
      </span>
    </span>
  )
}


export function ModelKey({ model, children }: { model: 'cat' | 'gnn'; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span
        className={cn('inline-block h-0.5 w-3 rounded-full', model === 'cat' ? 'bg-cat' : 'bg-gnn')}
      />
      {children}
    </span>
  )
}
