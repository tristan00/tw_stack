import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { post, useApi, type RewardWeightsPage } from '@/lib/api'

const SHORT: Record<string, string> = {
  settlements: 'setts',
  lord_levels: 'lords',
  allies: 'allies',
  vassals: 'vassals',
}

export function RewardControl() {
  const { data, reload } = useApi<RewardWeightsPage>('/api/reward-weights', [], { live: false })
  const [draft, setDraft] = useState<Record<string, string> | null>(null)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  if (!data) return null
  const weights = data.weights ?? {}
  const parts = (data.components ?? [])
    .map((c) => ({ key: c.key, w: weights[c.key] ?? c.default }))
    .filter((p) => p.w !== 0)
    .map((p) => `${SHORT[p.key] ?? p.key} ${n2(p.w)}`)
  const current = draft ?? Object.fromEntries(
    (data.components ?? []).map((c) => [c.key, String(weights[c.key] ?? c.default)]),
  )
  const dirty = (data.components ?? []).some(
    (c) => Number(current[c.key]) !== (weights[c.key] ?? c.default),
  )
  const save = async () => {
    setSaving(true)
    setErr('')
    try {
      const body = Object.fromEntries(
        Object.entries(current).map(([k, v]) => [k, Number(v) || 0]),
      )
      await post('/api/reward-weights', body)
      window.location.reload()
    } catch (e) {
      setErr((e as Error).message)
      setSaving(false)
    }
  }
  return (
    <Popover.Root
      onOpenChange={(open) => {
        if (!open) {
          setDraft(null)
          setErr('')
          reload()
        }
      }}
    >
      <Popover.Trigger asChild>
        <button
          className="border-line text-dim hover:text-fg rounded-md border px-2 py-1.5 text-2xs whitespace-nowrap"
          title="reward — every reward on this dashboard"
          aria-label="edit the reward definition"
        >
          reward <span className="num text-fg">{parts.join(' · ') || '0'}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          className="bg-surface border-line z-50 w-64 rounded-lg border p-3 shadow-lg"
        >
          <table className="w-full text-xs">
            <thead>
              <tr className="border-line text-dim border-b text-left text-2xs">
                <th className="py-1 font-normal">per gained</th>
                <th className="py-1 text-right font-normal">worth</th>
              </tr>
            </thead>
            <tbody>
              {(data.components ?? []).map((c) => (
                <tr key={c.key} className="border-line/60 border-b last:border-0">
                  <td className="py-1.5">{c.label}</td>
                  <td className="py-1.5 text-right">
                    <input
                      value={current[c.key]}
                      onChange={(e) =>
                        setDraft({ ...current, [c.key]: e.target.value.replace(/[^\d.]/g, '') })
                      }
                      className="border-line bg-surface num w-16 rounded-md border px-1.5 py-0.5 text-right"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={save}
              disabled={!dirty || saving}
              className="border-line bg-raised hover:text-fg rounded-md border px-2.5 py-1 text-xs font-semibold disabled:opacity-40"
            >
              {saving ? 'saving…' : 'save'}
            </button>
            {!data.is_default && (
              <button
                onClick={() =>
                  setDraft(Object.fromEntries(
                    (data.components ?? []).map((c) => [c.key, String(c.default)]),
                  ))
                }
                className="text-dim hover:text-fg text-2xs"
              >
                reset
              </button>
            )}
            {err && <span className="text-bad text-2xs">{err}</span>}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function n2(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}
