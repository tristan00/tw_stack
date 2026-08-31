import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Campaigns } from '@/routes/Campaigns'
import { CampaignDetail } from '@/routes/CampaignDetail'
import { Catalog } from '@/routes/Catalog'
import { CatalogDetail } from '@/routes/CatalogDetail'
import { Decisions } from '@/routes/Decisions'
import { DecisionDetail } from '@/routes/DecisionDetail'
import { Infra } from '@/routes/Infra'
import { ItemDetail } from '@/routes/ItemDetail'
import { Items } from '@/routes/Items'
import { Log } from '@/routes/Log'
import { Models } from '@/routes/Models'
import { Positions } from '@/routes/Positions'
import { Run } from '@/routes/Run'
import { Selector } from '@/routes/Selector'
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
        <Route path="/selector" element={<Selector />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/items" element={<Items />} />
        <Route path="/items/:itemKey" element={<ItemDetail />} />
        <Route path="/buildings" element={<Catalog family="buildings" />} />
        <Route path="/buildings/:key" element={<CatalogDetail family="buildings" />} />
        <Route path="/research" element={<Catalog family="research" />} />
        <Route path="/research/:key" element={<CatalogDetail family="research" />} />
        <Route path="/skills" element={<Catalog family="skills" />} />
        <Route path="/skills/:key" element={<CatalogDetail family="skills" />} />
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
