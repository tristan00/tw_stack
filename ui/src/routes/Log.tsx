import { useCallback, useEffect, useRef, useState } from 'react'
import { Card, ErrorState, Section } from '@/components/primitives'
import { type Schemas } from '@/lib/api'

type LogPage = Schemas['LogPage']

const LIMIT = 500

function buildUrl(params: {
  file: string
  q: string
  t0: string
  t1: string
  cursor?: number | null
}) {
  const u = new URLSearchParams()
  u.set('limit', String(LIMIT))
  if (params.file) u.set('file', params.file)
  if (params.q.trim()) u.set('q_text', params.q.trim())
  if (params.t0) u.set('t0', params.t0)
  if (params.t1) u.set('t1', params.t1)
  if (params.cursor != null) u.set('cursor', String(params.cursor))
  return `/api/log?${u.toString()}`
}

export function Log() {
  const [file, setFile] = useState('')
  const [q, setQ] = useState('')
  const [t0, setT0] = useState('')
  const [t1, setT1] = useState('')
  const [page, setPage] = useState<LogPage | null>(null)
  const [lines, setLines] = useState<string[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [follow, setFollow] = useState(true)
  const abortRef = useRef<AbortController | null>(null)
  const preRef = useRef<HTMLPreElement | null>(null)

  const fetchLog = useCallback(
    async (opts: { older?: boolean } = {}) => {
      abortRef.current?.abort()
      const ctl = new AbortController()
      abortRef.current = ctl
      setLoading(true)
      setError(null)
      try {
        const url = buildUrl({ file, q, t0, t1, cursor: opts.older ? cursor : null })
        const r = await fetch(url, { signal: ctl.signal, headers: { accept: 'application/json' } })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const d = (await r.json()) as LogPage
        setPage(d)
        setCursor(d.cursor ?? null)
        setLines((prev) => (opts.older ? [...(d.lines ?? []), ...prev] : (d.lines ?? [])))
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setError(String((e as Error).message ?? e))
      } finally {
        setLoading(false)
      }
    },
    [file, q, t0, t1, cursor],
  )

  useEffect(() => {
    fetchLog()

  }, [file])

  useEffect(() => {
    if (!follow) return
    const t = setInterval(() => fetchLog(), 3000)
    return () => clearInterval(t)
  }, [follow, fetchLog])

  useEffect(() => {
    const el = preRef.current
    if (follow && el) el.scrollTop = el.scrollHeight
  }, [lines, follow])

  const inputCls =
    'border-line bg-surface text-fg rounded-md border px-2 py-1 text-sm min-w-0'
  return (
    <Section
      title="log"
      scope={{
        text: page?.file ?? 'session log',
        detail: page ? `${(page.size / 1e6).toFixed(1)} MB on disk` : undefined,
      }}
    >
      <Card className="mb-3 flex flex-wrap items-center gap-2 px-3 py-2">
        <select className={inputCls} value={file} onChange={(e) => setFile(e.target.value)}>
          <option value="">current session</option>
          {(page?.files ?? []).map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <input
          className={`${inputCls} flex-1`}
          placeholder="search text…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && fetchLog()}
        />
        <input
          className={inputCls}
          type="datetime-local"
          value={t0}
          onChange={(e) => setT0(e.target.value)}
          title="from"
        />
        <input
          className={inputCls}
          type="datetime-local"
          value={t1}
          onChange={(e) => setT1(e.target.value)}
          title="to"
        />
        <button
          className="border-line hover:bg-raised rounded-md border px-3 py-1 text-sm"
          onClick={() => fetchLog()}
          disabled={loading}
        >
          {loading ? 'loading…' : 'apply'}
        </button>
        <button
          className={`rounded-md border px-3 py-1 text-sm ${
            follow ? 'border-line bg-raised font-semibold' : 'border-line hover:bg-raised'
          }`}
          onClick={() => setFollow(!follow)}
          title="refresh every 3s and stay pinned to the newest lines"
        >
          {follow ? 'following' : 'follow'}
        </button>
        <span className="text-dim text-xs">
          {lines.length} line{lines.length === 1 ? '' : 's'}
        </span>
      </Card>
      {error && <ErrorState error={error} onRetry={() => fetchLog()} />}
      <Card className="overflow-hidden">
        {cursor != null && (
          <button
            className="text-dim hover:text-fg border-line block w-full border-b px-3 py-1.5 text-xs"
            onClick={() => {
              setFollow(false)
              fetchLog({ older: true })
            }}
            disabled={loading}
          >
            load older lines
          </button>
        )}
        <pre
          ref={preRef}
          className="num max-h-[70vh] overflow-auto px-3 py-2 text-2xs leading-relaxed"
        >
          {lines.length ? lines.join('\n') : loading ? 'loading…' : 'nothing in this window'}
        </pre>
      </Card>
    </Section>
  )
}
