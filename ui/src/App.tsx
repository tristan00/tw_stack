import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Campaigns } from '@/routes/Campaigns'
import { CampaignDetail } from '@/routes/CampaignDetail'
import { Decisions } from '@/routes/Decisions'
import { DecisionDetail } from '@/routes/DecisionDetail'
import { Infra } from '@/routes/Infra'
import { Models } from '@/routes/Models'
import { Run } from '@/routes/Run'


export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/run" replace />} />
        <Route path="/run" element={<Run />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/campaigns/:campaignKey" element={<CampaignDetail />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/decisions/:decisionId" element={<DecisionDetail />} />
        <Route path="/models" element={<Models />} />
        <Route path="/infra" element={<Infra />} />
        <Route path="*" element={<Navigate to="/run" replace />} />
      </Route>
    </Routes>
  )
}
