import { useState } from 'react'
import { DataTable, type Col } from '@/components/DataTable'
import { Card, Chip, Dot, ErrorState, Section, Skeleton } from '@/components/primitives'
import { post, useApi, type InfraPage, type Schemas } from '@/lib/api'
import { cn } from '@/lib/utils'

type ActivityRow = Schemas['ActivityRow']
type LaunchDefaults = Schemas['LaunchDefaults']

const activityCols: Col<ActivityRow>[] = [
  { key: 'stream', label: 'stream', value: (r) => r.stream, render: (r) => r.stream },
  {
    key: 'age',
    label: 'last write',
    align: 'right',
    value: (r) => r.age_seconds ?? 1e9,
    render: (r) => (
      <span className="flex items-center justify-end gap-2">
        <Dot state={r.state ?? 'neutral'} />
        <span>{r.last_write ?? 'never'}</span>
      </span>
    ),
  },
]

function Field({
  label,
  children,
  help,
}: {
  label: string
  children: React.ReactNode
  help?: string
}) {
  return (
    <label className="block">
      <span className="text-dim text-2xs uppercase tracking-wide">{label}</span>
      {children}
      {}
      {help && <span className="text-dim mt-0.5 block text-2xs leading-snug">{help}</span>}
    </label>
  )
}

function LaunchForm({
  defaults,
  kind,
  arms,
  interruptArms,
  trainable,
}: {
  defaults: LaunchDefaults
  kind: 'run' | 'cold'
  arms: string[]
  interruptArms: string[]
  trainable: string[]
}) {
  const storeKey = `launch.${kind}`
  const [form, setForm] = useState<LaunchDefaults>(() => {
    try {
      const saved = localStorage.getItem(storeKey)
      if (saved) return { ...defaults, ...JSON.parse(saved) }
    } catch {

    }
    return defaults
  })
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<string[]>([])

  const set = <K extends keyof LaunchDefaults>(k: K, v: LaunchDefaults[K]) => {
    const next = { ...form, [k]: v }
    setForm(next)
    localStorage.setItem(storeKey, JSON.stringify(next))
  }

  const go = async () => {
    const label = kind === 'cold' ? 'cold start' : 'run'
    if (!confirm(`Start a ${label}? This kills the running session and the game first.`)) return
    setBusy(true)
    try {
      const r = await post<{ ok: boolean; steps: string[] }>(
        kind === 'cold' ? '/api/infra/coldstart' : '/api/infra/launch',
        form,
      )
      setSteps(r.steps)
    } catch (e) {
      setSteps([`failed: ${(e as Error).message}`])
    } finally {
      setBusy(false)
    }
  }

  const input = 'border-line bg-surface mt-0.5 w-full rounded-md border px-2 py-1 text-xs num'

  return (
    <Card className="p-3.5">
      <div className="mb-3 text-sm font-semibold">
        {kind === 'cold' ? 'cold start' : 'launch a run'}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="campaigns">
          <input
            type="number"
            className={input}
            value={form.campaigns ?? 0}
            onChange={(e) => set('campaigns', Number(e.target.value))}
          />
        </Field>
        <Field label="turns min">
          <input
            type="number"
            className={input}
            value={form.turns_min ?? 0}
            onChange={(e) => set('turns_min', Number(e.target.value))}
          />
        </Field>
        <Field label="turns max">
          <input
            type="number"
            className={input}
            value={form.turns_max ?? 0}
            onChange={(e) => set('turns_max', Number(e.target.value))}
          />
        </Field>
        <Field label="factions" help="all, or a comma-separated list of faction keys baked at this radius">
          <input
            className={input}
            value={form.factions ?? 'all'}
            onChange={(e) => set('factions', e.target.value)}
          />
        </Field>
        <Field label="presave radius" help="starts are sampled from the baked saves at exactly this radius">
          <input
            type="number"
            className={input}
            value={form.presave_radius ?? 0}
            onChange={(e) => set('presave_radius', Number(e.target.value))}
          />
        </Field>
        <Field label="ucb" help="blank = uniform over the starts; a number is the UCB1 exploration constant">
          <input
            className={input}
            placeholder="uniform"
            value={form.ucb === null || form.ucb === undefined ? '' : String(form.ucb)}
            onChange={(e) => set('ucb', e.target.value.trim() === '' ? null : Number(e.target.value))}
          />
        </Field>
        {kind === 'run' && (
          <>
            <Field
              label="retrain every"
              help="0 = never. N = retrain before every Nth campaign, but NOT before campaign 1 — tick 'retrain first' for that."
            >
              <input
                type="number"
                className={input}
                value={form.retrain_every ?? 0}
                onChange={(e) => set('retrain_every', Number(e.target.value))}
              />
            </Field>
            <Field
              label="action strategies"
              help={`the per-decision mix over ${arms.join(', ')}; ${trainable.join(', ')} need their trained models`}
            >
              <input
                className={input}
                placeholder={trainable.map((a) => `${a}=0.2`).join(',') + ',random=0.4'}
                value={form.strategies ?? ''}
                onChange={(e) => set('strategies', e.target.value)}
              />
            </Field>
            <Field
              label="interrupt strategies"
              help={`the mix on blocking screens, over ${interruptArms.join(', ')} only — the graph arms have no interrupt model`}
            >
              <input
                className={input}
                placeholder="greedy_catboost=0.5,random=0.5"
                value={form.interrupt_strategies ?? ''}
                onChange={(e) => set('interrupt_strategies', e.target.value)}
              />
            </Field>
            <Field label="ruleset" help="required when 'ruleset' is in either mix">
              <input
                className={input}
                placeholder="probe_gaps"
                value={form.ruleset ?? ''}
                onChange={(e) => set('ruleset', e.target.value)}
              />
            </Field>
          </>
        )}
        <div className="flex items-end gap-4">
          {kind === 'run' && (
            <label className="flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                checked={!!form.retrain_first}
                onChange={(e) => set('retrain_first', e.target.checked)}
              />
              retrain first
            </label>
          )}
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              checked={!!form.dev}
              onChange={(e) => set('dev', e.target.checked)}
            />
            dev logging
          </label>
        </div>
      </div>
      <button
        onClick={go}
        disabled={busy}
        className={cn(
          'border-line mt-3 rounded-md border px-3 py-1.5 text-xs font-medium',
          'hover:border-accent hover:text-accent disabled:opacity-50',
        )}
      >
        {busy ? 'starting…' : kind === 'cold' ? 'launch cold start' : 'launch run'}
      </button>
      {steps.length > 0 && (
        <pre className="num text-dim mt-2 max-h-40 overflow-auto text-2xs">{steps.join('\n')}</pre>
      )}
    </Card>
  )
}

export function Infra() {
  const { data, error, loading, reload } = useApi<InfraPage>('/api/infra')
  const [killing, setKilling] = useState(false)

  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />

  const kill = async () => {
    if (!confirm('Kill the running session and the game?')) return
    setKilling(true)
    try {
      await post('/api/infra/kill', {})
    } finally {
      setKilling(false)
      reload()
    }
  }

  return (
    <div className="space-y-7">
      <Section title="services" scope={data.scope}>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {data.services.map((s) => (
            <Card key={s.name} className="px-3.5 py-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm">{s.name}</span>
                <Chip state={s.up ? 'ok' : 'bad'}>{s.up ? 'up' : 'down'}</Chip>
              </div>
              {s.pid && <div className="num text-dim mt-1 text-2xs">pid {s.pid}</div>}
              {s.started && <div className="text-dim text-2xs">{s.started}</div>}
              {}
              {s.detail && <div className="text-warn mt-1 text-2xs">{s.detail}</div>}
            </Card>
          ))}
        </div>
      </Section>

      <Section title="activity" scope={{ text: 'when each stream last wrote' }}>
        <DataTable
          rows={data.activity}
          cols={activityCols}
          rowId={(r) => r.stream}
          dense
          emptyWhat="no stream found"
        />
      </Section>

      <Section
        title="controls"
        scope={{ text: 'the only place this dashboard changes anything', detail: data.policy_note }}
      >
        <div className="space-y-3">
          <Card className="flex flex-wrap items-center gap-3 p-3.5">
            <button
              onClick={kill}
              disabled={killing}
              className="border-bad text-bad rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
            >
              {killing ? 'killing…' : 'kill session + game'}
            </button>
            <span className="text-dim text-2xs">
              stops the advisor session and Warhammer3. The recorder is left alone.
            </span>
          </Card>
          <LaunchForm
            defaults={data.defaults}
            kind="run"
            arms={data.arms}
            interruptArms={data.interrupt_arms}
            trainable={data.trainable}
          />
          <LaunchForm
            defaults={data.cold_defaults}
            kind="cold"
            arms={data.arms}
            interruptArms={data.interrupt_arms}
            trainable={data.trainable}
          />
        </div>
      </Section>

      <Section title="session log" scope={{ text: 'last lines' }}>
        <Card className="overflow-hidden">
          <pre className="num max-h-64 overflow-auto px-3 py-2 text-2xs">
            {data.log_tail.length ? data.log_tail.join('\n') : 'nothing written yet'}
          </pre>
        </Card>
      </Section>
    </div>
  )
}
