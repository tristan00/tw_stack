import { Card, EntityLink, ErrorState, Skeleton } from '@/components/primitives'
import { useApi, type RunPage } from '@/lib/api'
import { CampaignView } from '@/routes/CampaignDetail'

export function Home() {
  const { data, error, loading, reload } = useApi<RunPage>('/api/run')
  if (error) return <ErrorState error={error} onRetry={reload} />
  if (loading || !data) return <Skeleton rows={8} />
  const cur = data.current
  if (!cur?.campaign) {
    return (
      <Card className="text-dim px-4 py-6 text-center text-sm">
        no campaign playing right now · <EntityLink to="/campaigns?view=campaigns">campaigns</EntityLink>
      </Card>
    )
  }
  return <CampaignView campaignKey={cur.campaign.raw} playingNow={cur} />
}
