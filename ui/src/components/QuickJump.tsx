import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useUiMode } from '@/components/Layout'
import { mapShort } from '@/components/startcharts'
import { useApi, type CampaignsPage, type StartsPage } from '@/lib/api'
import { cn } from '@/lib/utils'

const PAGES = [
  { label: 'campaigns', to: '/campaigns' },
  { label: 'starts', to: '/campaigns?view=starts' },
  { label: 'lookup', to: '/lookup' },
  { label: 'items', to: '/items' },
  { label: 'buildings', to: '/buildings' },
  { label: 'research', to: '/research' },
  { label: 'skills', to: '/skills' },
  { label: 'status', to: '/status' },
]

const STACK_PAGES = [
  { label: 'run', to: '/run' },
  { label: 'decisions', to: '/decisions' },
  { label: 'positions', to: '/positions' },
  { label: 'log', to: '/log' },
  { label: 'selector', to: '/selector' },
  { label: 'models', to: '/models' },
  { label: 'infra', to: '/infra' },
]

interface Hit {
  key: string
  to: string
  label: string
  sub: string
  kind: string
  hay: string
}

export function QuickJump() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [at, setAt] = useState(0)
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const mode = useUiMode()
  const starts = useApi<StartsPage>(open ? '/api/campaigns/starts' : null, [open], { live: false })
  const camps = useApi<CampaignsPage>(open ? '/api/campaigns' : null, [open], { live: false })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
        setQ('')
        setAt(0)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  const hits = useMemo(() => {
    const pages = mode === 'dashboard' ? PAGES : [...PAGES, ...STACK_PAGES]
    const all: Hit[] = pages.map((p) => ({
      key: `p:${p.to}`,
      to: p.to,
      label: p.label,
      sub: '',
      kind: 'page',
      hay: p.label,
    }))
    for (const s of starts.data?.rows ?? []) {
      const lord = s.leader ?? s.faction.label
      const m = s.campaign_map?.raw ?? ''
      all.push({
        key: `s:${m}|${s.faction.raw}`,
        to: `/starts/${encodeURIComponent(m)}/${encodeURIComponent(s.faction.raw)}`,
        label: lord,
        sub: `${s.faction.culture ?? ''} · ${mapShort(m, s.campaign_map?.label)} · ${s.n} campaigns`,
        kind: 'start',
        hay: `${lord} ${s.faction.culture ?? ''} ${s.faction.raw} ${m}`,
      })
    }
    for (const c of (camps.data?.rows ?? []).slice(0, 40)) {
      const lord = c.leader ?? c.campaign.label
      all.push({
        key: `c:${c.campaign.raw}`,
        to: `/campaigns/${encodeURIComponent(c.campaign.raw)}`,
        label: lord,
        sub: `${c.campaign.raw.slice(-12)} · ${c.outcome?.label ?? 'running'} · ${c.ended_when ?? ''}`,
        kind: 'campaign',
        hay: `${lord} ${c.campaign.raw} ${c.outcome?.label ?? ''}`,
      })
    }
    const needle = q.trim().toLowerCase()
    if (!needle) return all.slice(0, 12)
    return all.filter((h) => h.hay.toLowerCase().includes(needle)).slice(0, 12)
  }, [q, mode, starts.data, camps.data])

  useEffect(() => {
    setAt((a) => Math.min(a, Math.max(0, hits.length - 1)))
  }, [hits.length])

  if (!open) return null
  const go = (h: Hit) => {
    setOpen(false)
    navigate(h.to)
  }
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-[12vh]"
      onClick={() => setOpen(false)}
    >
      <div
        className="bg-surface border-line w-full max-w-lg rounded-lg border shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <label className="border-line flex items-center gap-2 border-b px-3 py-2.5">
          <Search className="text-dim size-4 shrink-0" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setAt(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setAt((a) => Math.min(a + 1, hits.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setAt((a) => Math.max(a - 1, 0))
              } else if (e.key === 'Enter' && hits[at]) {
                go(hits[at])
              }
            }}
            placeholder="jump to a start, campaign, or page…"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="text-dim text-2xs">esc</kbd>
        </label>
        <div className="max-h-80 overflow-auto p-1.5">
          {hits.map((h, i) => (
            <button
              key={h.key}
              onClick={() => go(h)}
              onPointerEnter={() => setAt(i)}
              className={cn(
                'flex w-full items-baseline justify-between gap-3 rounded-md px-2.5 py-1.5 text-left text-sm',
                i === at ? 'bg-raised' : '',
              )}
            >
              <span className="min-w-0 truncate">
                {h.label}
                {h.sub && <span className="text-dim ml-2 text-2xs">{h.sub}</span>}
              </span>
              <span className="text-dim shrink-0 text-2xs">{h.kind}</span>
            </button>
          ))}
          {!hits.length && <div className="text-dim px-2.5 py-4 text-xs">nothing matches</div>}
        </div>
      </div>
    </div>
  )
}
