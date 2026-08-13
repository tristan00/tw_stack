import { useCallback, useEffect, useRef, useState } from 'react'
import type { components, paths } from '@/api/schema'

/**
 * The API client.
 *
 * Every response type comes from `@/api/schema`, which is generated from the server's
 * OpenAPI document (`npm run gen:api`). Nothing here is hand-written, so a field that
 * the server does not send is a compile error rather than a column that renders blank
 * and is never noticed. That is the property that replaces the old dashboard's habit of
 * scraping its own rendered HTML to find out whether it had lied.
 */

export type Schemas = components['schemas']

export type RunPage = Schemas['RunPage']
export type CampaignsPage = Schemas['CampaignsPage']
export type CampaignDetail = Schemas['CampaignDetail']
export type CampaignRow = Schemas['CampaignRow']
export type StartsPage = Schemas['StartsPage']
export type MatrixPage = Schemas['MatrixPage']
export type DecisionsPage = Schemas['DecisionsPage']
export type DecisionDetail = Schemas['DecisionDetail']
export type DecisionRow = Schemas['DecisionRow']
export type ActionsPage = Schemas['ActionsPage']
export type MenusPage = Schemas['MenusPage']
export type TimelinePage = Schemas['TimelinePage']
export type ModelsPage = Schemas['ModelsPage']
export type ForcingPage = Schemas['ForcingPage']
export type AgreementPage = Schemas['AgreementPage']
export type CorrelationsPage = Schemas['CorrelationsPage']
export type AgreementSeriesPage = Schemas['AgreementSeriesPage']
export type AgreementSeriesPoint = Schemas['AgreementSeriesPoint']
export type AgreementBreakdownPage = Schemas['AgreementBreakdownPage']
export type GenerationRow = Schemas['GenerationRow']
export type RhoBin = Schemas['RhoBin']
export type AnalyticsFreshness = Schemas['AnalyticsFreshness']
export type AnalyticsPage = Schemas['AnalyticsPage']
export type TrainingPage = Schemas['TrainingPage']
export type InfraPage = Schemas['InfraPage']
export type Ident = Schemas['Ident']
export type Count = Schemas['Count']
export type Rate = Schemas['Rate']
export type Scope = Schemas['Scope']
export type Metric = Schemas['Metric']

/**
 * Severity, decided server-side.
 *
 * Written out rather than pulled from the generated schema because it is a bare literal
 * union on the Python side, so the generator inlines it at every use instead of emitting
 * a named component. It is pinned to `Metric['state']` so it cannot drift from the server
 * without failing this file's own typecheck.
 */
export type State = NonNullable<Metric['state']>

export type ApiPath = keyof paths

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal, headers: { accept: 'application/json' } })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const body = await r.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* a non-JSON error body is still an error; the status carries it */
    }
    throw new Error(detail)
  }
  return (await r.json()) as T
}

export interface Loaded<T> {
  data: T | null
  error: string | null
  /** true only for the first load. A refresh must never blank a populated view. */
  loading: boolean
  reload: () => void
}

/**
 * Load a view, and reload it when the corpus changes.
 *
 * `loading` is true only while there is nothing to show. A refresh keeps the previous
 * data on screen and swaps it when the new data arrives, so a live view never flashes
 * empty and never throws away your scroll position -- the two things that made the old
 * timer-driven dashboard feel clunky.
 */
export function useApi<T>(url: string | null, deps: unknown[] = []): Loaded<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const stamp = useCorpusStamp()
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (!url) return
    const ac = new AbortController()
    getJSON<T>(url, ac.signal)
      .then((d) => {
        if (!alive.current) return
        setData(d)
        setError(null)
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive.current || (e as Error)?.name === 'AbortError') return
        setError((e as Error)?.message ?? 'request failed')
        setLoading(false)
      })
    return () => ac.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, nonce, stamp, ...deps])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, loading: loading && data === null, reload }
}

/**
 * The corpus change stream.
 *
 * One EventSource for the whole app, shared by every view. The server emits when the
 * corpus gains rows, so views re-fetch because something happened -- not because a timer
 * fired. A quiet run costs nothing; a busy one updates immediately. EventSource handles
 * reconnection itself, so there is no reconnect logic here to get wrong.
 */
let sharedSource: EventSource | null = null
const listeners = new Set<(s: string) => void>()

function ensureSource() {
  if (sharedSource || typeof window === 'undefined') return
  sharedSource = new EventSource('/api/events')
  sharedSource.addEventListener('corpus', (ev) => {
    const s = (ev as MessageEvent).data ?? ''
    listeners.forEach((fn) => fn(s))
  })
}

export function useCorpusStamp(): string {
  const [stamp, setStamp] = useState('')
  useEffect(() => {
    ensureSource()
    const fn = (s: string) => setStamp(s)
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])
  return stamp
}

export async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as T
}
