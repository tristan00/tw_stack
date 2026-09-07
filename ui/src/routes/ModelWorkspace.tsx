import { Link, useSearchParams } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { Card, ErrorState, Skeleton } from '@/components/primitives'
import { useApi } from '@/lib/api'
import { n } from '@/lib/format'
import { Run } from '@/routes/Run'
import { Selector } from '@/routes/Selector'
import { Log } from '@/routes/Log'

type Trial = {
  trial: string
  ts: number
  campaigns: number | null
  turns_total: number | null
  running: boolean
  status: string | null
  train_window: number | null
  retrain_every: number | null
  turn_budget: number | null
  start_pool: string | null
  sett_mean: number | null
  ll_mean: number | null
  code_version: string | null
}
type Session = {
  session_id: number
  trial: string | null
  segment_id: number | null
  started_ts: number
  ended_ts: number | null
  status: string
  campaigns: number | null
  turns: number | null
  turns_per_hour: number | null
  last_turn_seconds: number | null
  stalls: number | null
}
type ExperimentsPage = {
  trials: Trial[]
  sessions: Session[]
  policies: { trial: string; scope: string; policy: string; weight: number }[]
}
const number = (v: number | null) => (v == null ? '—' : n(v))
export function Experiments() {
  const [params, setParams] = useSearchParams()
  const { data, error, reload } = useApi<ExperimentsPage>(
    '/api/game/experiments',
  )
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <Skeleton rows={8} />
  const trial =
    data.trials.find((t) => t.trial === params.get('trial')) || data.trials[0]
  const cols: Col<Trial>[] = [
    {
      key: 'name',
      label: 'Experiment',
      value: (r) => r.trial,
      render: (r) => <b>{r.trial}</b>,
    },
    {
      key: 'when',
      label: 'Recorded',
      value: (r) => r.ts,
      render: (r) => new Date(r.ts * 1000).toLocaleString(),
    },
    {
      key: 'campaigns',
      label: 'Campaigns',
      value: (r) => r.campaigns ?? undefined,
      render: (r) => number(r.campaigns),
    },
    {
      key: 'status',
      label: 'Status',
      value: (r) => r.status || '',
      render: (r) => r.status || (r.running ? 'Running' : 'Stopped'),
    },
    {
      key: 'sett',
      label: 'Mean settlements gained',
      value: (r) => r.sett_mean ?? undefined,
      render: (r) => number(r.sett_mean),
    },
  ]
  const sessions = data.sessions.filter((s) => s.trial === trial?.trial)
  const sessionCols: Col<Session>[] = [
    {
      key: 'segment',
      label: 'Segment',
      value: (r) => r.segment_id ?? undefined,
      render: (r) => number(r.segment_id),
    },
    {
      key: 'started',
      label: 'Started',
      value: (r) => r.started_ts,
      render: (r) => new Date(r.started_ts * 1000).toLocaleString(),
    },
    {
      key: 'campaigns',
      label: 'Campaigns',
      value: (r) => r.campaigns ?? undefined,
      render: (r) => number(r.campaigns),
    },
    {
      key: 'status',
      label: 'Status',
      value: (r) => r.status,
      render: (r) => r.status,
    },
    {
      key: 'rate',
      label: 'Turns / hr',
      value: (r) => r.turns_per_hour ?? undefined,
      render: (r) => number(r.turns_per_hour),
    },
  ]
  return (
    <div className="space-y-5">
      <div className="section-toolbar">
        <h1 className="text-xl">Experiments</h1>
        <div className="flex gap-4">
          <Link className="workspace-button" to="/models">
            Model diagnostics
          </Link>
          <Link className="workspace-button" to="/infra">
            Launch controls
          </Link>
        </div>
      </div>
      <DataTable
        rows={data.trials}
        cols={cols}
        rowId={(r) => r.trial}
        onRowClick={(r) => {
          const p = new URLSearchParams(params)
          p.set('trial', r.trial)
          setParams(p)
        }}
        pageSize={10}
      />
      {trial && (
        <>
          <h2 className="text-lg mt-5">{trial.trial}</h2>
          <div className="detail-cards">
            <Card className="p-4">
              <span className="eyebrow">Configuration</span>
              {[
                ['Train window', number(trial.train_window)],
                ['Retrain every', number(trial.retrain_every)],
                ['Turn budget', number(trial.turn_budget)],
                ['Start pool', trial.start_pool || 'Not recorded'],
              ].map(([k, v]) => (
                <div key={k} className="finance-row">
                  <span>{k}</span>
                  <b>{v}</b>
                </div>
              ))}
            </Card>
            <Card className="p-4">
              <span className="eyebrow">Strategy mix</span>
              {data.policies
                .filter((p) => p.trial === trial.trial)
                .map((p, i) => (
                  <div className="finance-row" key={i}>
                    <span>
                      {p.policy} <span className="text-dim">· {p.scope}</span>
                    </span>
                    <b>{n(p.weight * 100, 1)}%</b>
                  </div>
                ))}
            </Card>
            <Card className="p-4">
              <span className="eyebrow">Recorded outcome</span>
              <div className="finance-row">
                <span>Turns</span>
                <b>{number(trial.turns_total)}</b>
              </div>
              <div className="finance-row">
                <span>Mean lord levels gained</span>
                <b>{number(trial.ll_mean)}</b>
              </div>
              <p className="text-dim mt-3">{trial.code_version}</p>
            </Card>
          </div>
          <h3>Segments · recorded sessions</h3>
          <DataTable
            rows={sessions}
            cols={sessionCols}
            rowId={(r) => String(r.session_id)}
            emptyWhat="No session segments recorded for this experiment"
          />
        </>
      )}
    </div>
  )
}
export function ModelRun() {
  return (
    <div className="space-y-8">
      <div className="section-toolbar">
        <h1 className="text-xl">Run</h1>
        <Link className="workspace-button" to="/infra">
          Session controls
        </Link>
      </div>
      <Run />
      <details className="border-line border rounded-md p-4">
        <summary>Session log</summary>
        <div className="mt-4">
          <Log />
        </div>
      </details>
      <section>
        <h2 className="text-lg mb-4">Start selector</h2>
        <Selector />
      </section>
    </div>
  )
}
