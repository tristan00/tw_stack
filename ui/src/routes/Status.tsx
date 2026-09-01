import { DataTable, type Col } from '@/components/DataTable'
import { Card, Chip, Dot, ErrorState, Section, Skeleton } from '@/components/primitives'
import { RewardWeightsCard } from '@/components/reward'
import { useApi, type InfraPage, type Schemas } from '@/lib/api'

type ActivityRow = Schemas['ActivityRow']

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

export function ServicesGrid({ services }: { services: InfraPage['services'] }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {services.map((s) => (
        <Card key={s.name} className="px-3.5 py-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm">{s.name}</span>
            <Chip state={s.up ? 'ok' : 'bad'}>{s.up ? 'up' : 'down'}</Chip>
          </div>
          {s.pid && <div className="num text-dim mt-1 text-2xs">pid {s.pid}</div>}
          {s.started && <div className="text-dim text-2xs">{s.started}</div>}
          {s.detail && <div className="text-warn mt-1 text-2xs">{s.detail}</div>}
        </Card>
      ))}
    </div>
  )
}

export function ActivityTable({ rows }: { rows: ActivityRow[] }) {
  return (
    <DataTable rows={rows} cols={activityCols} rowId={(r) => r.stream} dense emptyWhat="no stream found" />
  )
}

export function Status() {
  const { data, error, loading, reload } = useApi<InfraPage>('/api/infra')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  return (
    <div className="space-y-7">
      <Section title="services" scope={data.scope}>
        <ServicesGrid services={data.services} />
      </Section>
      <Section title="activity" scope={{ text: 'when each stream last wrote' }}>
        <ActivityTable rows={data.activity} />
      </Section>
      <Section title="view settings" scope={{ text: 'a save re-ranks every reward on this dashboard' }}>
        <RewardWeightsCard />
      </Section>
    </div>
  )
}
