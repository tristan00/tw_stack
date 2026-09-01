import { useNavigate } from 'react-router-dom'
import { DataTable, type Col } from '@/components/DataTable'
import { EntityLink } from '@/components/primitives'
import { mapShort } from '@/components/startcharts'
import type { CatalogCampaignRow, CatalogStartRow, Rate } from '@/lib/api'
import { clock, n } from '@/lib/format'
import { cn } from '@/lib/utils'

export const CATALOG = [
  { to: '/items', label: 'items', asks: 'does wearing an item pay' },
  { to: '/buildings', label: 'buildings', asks: 'what gets built, where offered' },
  { to: '/research', label: 'research', asks: 'which techs get started' },
  { to: '/skills', label: 'skills', asks: 'which skills get points, and by whom' },
  { to: '/traits', label: 'traits', asks: 'which traits characters develop, and how far' },
]

export function CatalogNav({ active }: { active: string }) {
  const navigate = useNavigate()
  return (
    <div className="border-line mb-4 flex flex-wrap gap-1 border-b pb-2">
      {CATALOG.map((v) => (
        <button
          key={v.to}
          title={v.asks}
          onClick={() => navigate(v.to)}
          className={cn(
            'rounded-md px-2.5 py-1 text-xs',
            active === v.to
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

export function TookCell({ rate }: { rate: Rate | null | undefined }) {
  if (!rate || !rate.of) return <span className="text-dim">—</span>
  const p = (100 * rate.n) / rate.of
  return (
    <span className={cn('num whitespace-nowrap', !rate.n && 'text-dim')} title={rate.population}>
      {p >= 10 || p === 0 ? p.toFixed(0) : p.toFixed(1)}%{' '}
      <span className="text-dim text-2xs">
        {n(rate.n)}/{n(rate.of)}
      </span>
    </span>
  )
}

export const dashNum = (v: number | null | undefined, digits = 0) =>
  v == null ? <span className="text-dim">—</span> : <span className="num">{n(v, digits)}</span>

export const signedNum = (v: number | null | undefined, digits = 2) =>
  v == null ? (
    <span className="text-dim">—</span>
  ) : (
    <span className={cn('num', v > 0 ? 'text-ok' : v < 0 ? 'text-bad' : 'text-dim')}>
      {v > 0 ? '+' : ''}
      {n(v, digits)}
    </span>
  )

const startUrl = (r: CatalogStartRow) =>
  `/starts/${encodeURIComponent(r.campaign_map?.raw ?? '')}/${encodeURIComponent(r.faction.raw)}`

export function ByStartTable({ rows, verb }: { rows: CatalogStartRow[]; verb: string }) {
  const navigate = useNavigate()
  const cols: Col<CatalogStartRow>[] = [
    {
      key: 'start',
      label: 'start',
      value: (r) => r.leader ?? r.faction.label,
      render: (r) => (
        <EntityLink to={startUrl(r)} title={r.faction.raw}>
          {r.leader ?? r.faction.label}
        </EntityLink>
      ),
    },
    {
      key: 'map',
      label: 'map',
      optional: true,
      value: (r) => r.campaign_map?.label ?? '',
      render: (r) => <span className="text-dim">{mapShort(r.campaign_map?.raw, r.campaign_map?.label)}</span>,
    },
    { key: 'took', label: verb, align: 'right', value: (r) => r.took?.n ?? 0, render: (r) => <TookCell rate={r.took} /> },
    { key: 'turn', label: 'avg turn', align: 'right', value: (r) => r.avg_turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_turn, 1) },
    { key: 'reward', label: 'avg reward', align: 'right', value: (r) => r.avg_reward ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.avg_reward, 2) },
    { key: 'delta', label: 'Δ start mean', align: 'right', value: (r) => r.delta_mean ?? undefined, sortUndefined: 'last', render: (r) => signedNum(r.delta_mean) },
  ]
  return (
    <DataTable
      rows={rows}
      cols={cols}
      rowId={(r) => `${r.campaign_map?.raw ?? ''}|${r.faction.raw}`}
      onRowClick={(r) => navigate(startUrl(r))}
      pageSize={10}
      emptyWhat="no start was ever offered this"
    />
  )
}

export function RecentTable({ rows, verb }: { rows: CatalogCampaignRow[]; verb: string }) {
  const navigate = useNavigate()
  const cols: Col<CatalogCampaignRow>[] = [
    { key: 'when', label: 'when', value: (r) => r.ts ?? 0, render: (r) => <span className="num">{clock(r.ts)}</span> },
    {
      key: 'campaign',
      label: 'campaign',
      value: (r) => r.campaign.tag ?? '',
      render: (r) => (
        <EntityLink to={`/campaigns/${encodeURIComponent(r.campaign.raw)}`} title={r.campaign.raw} className="num text-2xs">
          {r.campaign.tag ?? r.campaign.raw.slice(-6)}
        </EntityLink>
      ),
    },
    { key: 'start', label: 'start', value: (r) => r.leader ?? '', render: (r) => <span className="text-dim">{r.leader ?? '—'}</span> },
    { key: 'turn', label: `${verb} turn`, align: 'right', value: (r) => r.turn ?? undefined, sortUndefined: 'last', render: (r) => dashNum(r.turn) },
    { key: 'reward', label: 'reward', align: 'right', value: (r) => r.reward ?? undefined, sortUndefined: 'last', render: (r) => <strong className="num">{n(r.reward)}</strong> },
  ]
  return (
    <DataTable
      rows={rows}
      cols={cols}
      rowId={(r) => r.campaign.raw}
      onRowClick={(r) => navigate(`/campaigns/${encodeURIComponent(r.campaign.raw)}`)}
      dense
      emptyWhat={`no campaign ever ${verb} it`}
    />
  )
}
