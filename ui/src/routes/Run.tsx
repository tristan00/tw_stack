import { Card, Dot, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { CountText } from '@/components/primitives'
import { DataTable, type Col } from '@/components/DataTable'
import { useApi, type RunPage, type Schemas } from '@/lib/api'
import { ms, stateText } from '@/lib/format'

type TimingRow = Schemas['TimingRow']

const timingCols: Col<TimingRow>[] = [
  { key: 'stage', label: 'stage', render: (r) => r.stage },
  {
    key: 'median',
    label: 'median',
    unit: 'ms',
    align: 'right',
    value: (r) => r.median_ms ?? 0,
    render: (r) => <span className={stateText[r.state ?? 'neutral']}>{ms(r.median_ms)}</span>,
  },
  {
    key: 'max',
    label: 'worst',
    unit: 'ms',
    align: 'right',
    value: (r) => r.max_ms ?? 0,


    render: (r) => ms(r.max_ms),
  },
]

export function Run() {
  const { data, error, loading, reload } = useApi<RunPage>('/api/run')

  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />

  return (
    <div className="space-y-7">
      <Section title="right now" scope={data.scope}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {data.throughput.map((m) => (
            <MetricTile key={m.label} metric={m} />
          ))}
          <Card className="px-3.5 py-3">
            <div className="text-dim text-2xs uppercase tracking-wide">services</div>
            <div className="mt-1.5 space-y-1">
              {data.services.map((s) => (
                <div key={s.name} className="flex items-center gap-2 text-xs">
                  <Dot state={s.up ? 'ok' : 'bad'} />
                  <span>{s.name}</span>
                  {s.pid && <span className="num text-dim text-2xs">pid {s.pid}</span>}
                  {s.detail && <span className="text-dim text-2xs">{s.detail}</span>}
                </div>
              ))}
            </div>
          </Card>
        </div>
      </Section>

      <Section
        title="corpus"
        scope={{ text: 'four counts of four different things' }}
      >
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {data.totals.map((c) => (
            <Card key={c.noun} className="px-3.5 py-3">
              <CountText count={c} className="flex-wrap" />
            </Card>
          ))}
        </div>
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section
          title="recorder"
          scope={{ text: 'median ms per stage', detail: 'over the recent window' }}
        >
          <DataTable
            rows={data.collect_timing}
            cols={timingCols}
            rowId={(r) => r.stage}
            dense
            emptyWhat="no timing recorded yet"
            emptyWhy="the recorder writes these once a decision completes"
          />
        </Section>
        <Section
          title="execution"
          scope={{ text: 'median ms per stage', detail: 'over the recent window' }}
        >
          <DataTable
            rows={data.cycle_timing}
            cols={timingCols}
            rowId={(r) => r.stage}
            dense
            emptyWhat="no execution timing yet"
          />
        </Section>
      </div>

    </div>
  )
}
