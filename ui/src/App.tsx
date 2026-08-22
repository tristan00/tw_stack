import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Campaigns } from '@/routes/Campaigns'
import { CampaignDetail } from '@/routes/CampaignDetail'
import { Decisions } from '@/routes/Decisions'
import { DecisionDetail } from '@/routes/DecisionDetail'
import { Infra } from '@/routes/Infra'
import { Log } from '@/routes/Log'
import { Models } from '@/routes/Models'
import { Run } from '@/routes/Run'
import { StartDetail } from '@/routes/StartDetail'


export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/run" replace />} />
        <Route path="/run" element={<Run />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/campaigns/:campaignKey" element={<CampaignDetail />} />
        <Route path="/starts/:campaignMap/:faction" element={<StartDetail />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/decisions/:decisionId" element={<DecisionDetail />} />
        <Route path="/models" element={<Models />} />
        <Route path="/log" element={<Log />} />
        <Route path="/infra" element={<Infra />} />
        <Route path="*" element={<Navigate to="/run" replace />} />
      </Route>
    </Routes>
  )
}
