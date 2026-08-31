import { Card, Dot, EntityLink, ErrorState, MetricTile, Section, Skeleton } from '@/components/primitives'
import { CountText } from '@/components/primitives'
import { DataTable, type Col } from '@/components/DataTable'
import { useApi, type RunPage, type Schemas } from '@/lib/api'
import { ago, clock, ms, n, stateText } from '@/lib/format'

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

  const cur = data.current
  const live = cur?.campaign && (cur.age_seconds ?? 1e9) < 600
  return (
    <div className="space-y-7">
      {cur?.campaign && (
        <Card className="px-3.5 py-2.5">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
            <span className="text-dim text-2xs uppercase tracking-wide">
              {live ? 'now playing' : 'last played'}
            </span>
            <EntityLink
              to={`/campaigns/${encodeURIComponent(cur.campaign.raw)}`}
              className="font-semibold"
            >
              {cur.leader ?? cur.campaign.label}
            </EntityLink>
            {cur.faction_key && (
              <EntityLink
                to={`/starts/${encodeURIComponent(cur.campaign_map?.raw ?? '')}/${encodeURIComponent(cur.faction_key)}`}
                className="text-dim text-xs"
              >
                {cur.campaign.culture ?? 'its start'} on {cur.campaign_map?.label ?? '—'}
              </EntityLink>
            )}
            {cur.turn !== null && cur.turn !== undefined && (
              <span className="text-dim num text-xs">turn {cur.turn}</span>
            )}
            {cur.pick_id !== null && cur.pick_id !== undefined && (
              <EntityLink
                to={`/campaigns?view=selector&pick=${cur.pick_id}`}
                className="text-dim num text-xs"
              >
                pick #{cur.pick_id}
              </EntityLink>
            )}
            {cur.decisions !== null && cur.decisions !== undefined && (
              <EntityLink
                to={`/campaigns/${encodeURIComponent(cur.campaign.raw)}?tab=decisions`}
                className="text-dim num text-xs"
              >
                decisions {n(cur.decisions)}
              </EntityLink>
            )}
            <span className="text-dim num ml-auto text-2xs">
              {live
                ? `started ${clock(cur.started_ts)}`
                : `state ${ago(cur.age_seconds ?? 0)}`}
            </span>
          </div>
        </Card>
      )}
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
