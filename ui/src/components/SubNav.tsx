import { useSearchParams } from 'react-router-dom'
import { cn } from '@/lib/utils'


export function SubNav({
  views,
  param = 'view',
}: {
  views: { key: string; label: string; asks?: string }[]
  param?: string
}) {
  const [params, setParams] = useSearchParams()
  const active = params.get(param) ?? views[0].key
  return (
    <div className="border-line mb-4 flex flex-wrap gap-1 border-b pb-2">
      {views.map((v) => (
        <button
          key={v.key}
          title={v.asks}
          onClick={() => {
            const next = new URLSearchParams(params)
            next.set(param, v.key)
            setParams(next, { replace: true })
          }}
          className={cn(
            'rounded-md px-2.5 py-1 text-xs',
            active === v.key
              ? 'bg-raised text-fg font-semibold'
              : 'text-dim hover:text-fg hover:bg-raised/60',
          )}
        >
          {v.label}
        </button>
      ))}
    </div>
  )
}

export function useSubView(views: { key: string }[], param = 'view') {
  const [params] = useSearchParams()
  return params.get(param) ?? views[0].key
}
