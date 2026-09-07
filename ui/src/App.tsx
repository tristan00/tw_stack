import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Campaigns } from '@/routes/Campaigns'
import { CampaignWorkspace } from '@/routes/CampaignWorkspace'
import { Catalog } from '@/routes/Catalog'
import { ThingPage } from '@/routes/ThingPage'
import { Decisions } from '@/routes/Decisions'
import { DecisionDetail } from '@/routes/DecisionDetail'
import { Infra } from '@/routes/Infra'
import { Items } from '@/routes/Items'
import { Log } from '@/routes/Log'
import { Lookup } from '@/routes/Lookup'
import { Models } from '@/routes/Models'
import { Positions } from '@/routes/Positions'
import { Experiments, ModelRun } from '@/routes/ModelWorkspace'
import { Selector } from '@/routes/Selector'
import { StartDetail } from '@/routes/StartDetail'
import { Status } from '@/routes/Status'
import { Analytics, Database, Explore, Options } from '@/routes/Workspaces'


export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<CampaignWorkspace />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/explore" element={<Explore />} />
        <Route path="/database" element={<Database />} />
        <Route path="/options" element={<Options />} />
        <Route path="/run" element={<ModelRun />} />
        <Route path="/experiments" element={<Experiments />} />
        <Route path="/lookup" element={<Lookup />} />
        <Route path="/status" element={<Status />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/campaigns/:campaignKey" element={<CampaignWorkspace />} />
        <Route path="/starts/:campaignMap/:faction" element={<StartDetail />} />
        <Route path="/selector" element={<Selector />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/items" element={<Items />} />
        <Route path="/items/:itemKey" element={<ThingPage family="items" />} />
        <Route path="/buildings" element={<Catalog family="buildings" />} />
        <Route path="/buildings/:key" element={<ThingPage family="buildings" />} />
        <Route path="/research" element={<Catalog family="research" />} />
        <Route path="/research/:key" element={<ThingPage family="research" />} />
        <Route path="/skills" element={<Catalog family="skills" />} />
        <Route path="/skills/:key" element={<ThingPage family="skills" />} />
        <Route path="/traits" element={<Catalog family="traits" />} />
        <Route path="/traits/:key" element={<ThingPage family="traits" />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/decisions/:decisionId" element={<DecisionDetail />} />
        <Route path="/models" element={<Models />} />
        <Route path="/log" element={<Log />} />
        <Route path="/infra" element={<Infra />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
