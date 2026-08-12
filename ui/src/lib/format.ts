import type { Rate, State } from '@/lib/api'

/** Thousands separators, always. A six-digit corpus count is unreadable without them. */
export function n(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/**
 * A rate as a percentage, or an em dash when nothing was attempted.
 *
 * `0%` and `nothing was attempted` are different facts. Rendering both as 0% reads as a
 * total failure where the truth is that the case never arose, so a zero denominator
 * returns null and the caller shows a dash.
 */
export function pct(r: Rate | null | undefined, digits = 0): string | null {
  if (!r || !r.of) return null
  return `${((100 * r.n) / r.of).toFixed(digits)}%`
}

export function pctValue(r: Rate | null | undefined): number | null {
  if (!r || !r.of) return null
  return (100 * r.n) / r.of
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}s`
  return `${n(Math.round(v))}ms`
}

export function ago(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.max(0, Math.round(seconds))
  if (s < 90) return `${s}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function clock(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export const stateText: Record<State, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  bad: 'text-bad',
  neutral: 'text-dim',
}

export const stateBg: Record<State, string> = {
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  bad: 'bg-bad-soft text-bad',
  neutral: 'bg-raised text-dim',
}

export const stateFill: Record<State, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  bad: 'bg-bad',
  neutral: 'bg-dim',
}

/** An arrow in the header is where "lower is better" belongs -- not in a paragraph. */
export function directionMark(dir?: 'up' | 'down'): string {
  return dir === 'down' ? ' ↓' : dir === 'up' ? ' ↑' : ''
}
