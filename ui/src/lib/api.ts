import { useCallback, useEffect, useRef, useState } from 'react'
import type { components, paths } from '@/api/schema'


export type Schemas = components['schemas']

export type RunPage = Schemas['RunPage']
export type Current = Schemas['Current']
export type CampaignStatePage = Schemas['CampaignStatePage']
export type CampaignsPage = Schemas['CampaignsPage']
export type CampaignDetail = Schemas['CampaignDetail']
export type CampaignRow = Schemas['CampaignRow']
export type StartsPage = Schemas['StartsPage']
export type StartRow = Schemas['StartRow']
export type StartDetail = Schemas['StartDetail']
export type StartCampaign = Schemas['StartCampaign']
export type StartCampaignsPage = Schemas['StartCampaignsPage']
export type StartPerformance = Schemas['StartPerformance']
export type StartOpenings = Schemas['StartOpenings']
export type StartActions = Schemas['StartActions']
export type OpeningFamily = Schemas['OpeningFamily']
export type OpeningBranch = Schemas['OpeningBranch']
export type OpeningOffer = Schemas['OpeningOffer']
export type ConquestStep = Schemas['ConquestStep']
export type CampaignDecisions = Schemas['CampaignDecisions']
export type Verdict = Schemas['Verdict']
export type TurnRollup = Schemas['TurnRollup']
export type TechRow = Schemas['TechRow']
export type StartResearch = Schemas['StartResearch']
export type SkillRow = Schemas['SkillRow']
export type StartSkills = Schemas['StartSkills']
export type ItemRow = Schemas['ItemRow']
export type StartItems = Schemas['StartItems']
export type ItemsPage = Schemas['ItemsPage']
export type ItemPage = Schemas['ItemPage']
export type ItemStartRow = Schemas['ItemStartRow']
export type ItemCampaignRow = Schemas['ItemCampaignRow']
export type CampaignResearchPage = Schemas['CampaignResearchPage']
export type CampaignSkillsPage = Schemas['CampaignSkillsPage']
export type CampaignItemsPage = Schemas['CampaignItemsPage']
export type CampaignCharacter = Schemas['CampaignCharacter']
export type ItemEffect = Schemas['ItemEffect']
export type StartBuildings = Schemas['StartBuildings']
export type BuildingRow = Schemas['BuildingRow']
export type StartCharacterRow = Schemas['StartCharacterRow']
export type CampaignBuildingsPage = Schemas['CampaignBuildingsPage']
export type CampaignBuildingRow = Schemas['CampaignBuildingRow']
export type CatalogIndexPage = Schemas['CatalogIndexPage']
export type CatalogIndexRow = Schemas['CatalogIndexRow']
export type CatalogKeyPage = Schemas['CatalogKeyPage']
export type CatalogVersionRow = Schemas['CatalogVersionRow']
export type TraitLevelRow = Schemas['TraitLevelRow']
export type CatalogStartRow = Schemas['CatalogStartRow']
export type CatalogCampaignRow = Schemas['CatalogCampaignRow']
export type ChainLevel = Schemas['ChainLevel']
export type RelatedKey = Schemas['RelatedKey']
export type ItemSwapRow = Schemas['ItemSwapRow']
export type SwapsPage = Schemas['SwapsPage']
export type ChoicesPage = Schemas['ChoicesPage']
export type ForkRow = Schemas['ForkRow']
export type ForkArmRow = Schemas['ForkArmRow']
export type RewardWeightsPage = Schemas['RewardWeightsPage']
export type CampaignLookupPage = Schemas['CampaignLookupPage']
export type LookupFacets = Schemas['LookupFacets']
export type LookupCampaignRow = Schemas['LookupCampaignRow']
export type SkillCharacterRow = Schemas['SkillCharacterRow']
export type PositionsPage = Schemas['PositionsPage']
export type PositionTypeRow = Schemas['PositionTypeRow']
export type PositionKeyRow = Schemas['PositionKeyRow']
export type PositionFacetOption = Schemas['PositionFacetOption']
export type HistBin = Schemas['HistBin']
export type UcbPick = Schemas['UcbPick']
export type UcbRow = Schemas['UcbRow']
export type UcbPicksPage = Schemas['UcbPicksPage']
export type UcbPickPage = Schemas['UcbPickPage']
export type WindowEdgeRow = Schemas['WindowEdgeRow']
export type MatrixCell = Schemas['MatrixCell']
export type MatrixPage = Schemas['MatrixPage']
export type DecisionsPage = Schemas['DecisionsPage']
export type DecisionDetail = Schemas['DecisionDetail']
export type DecisionRow = Schemas['DecisionRow']
export type ActionsPage = Schemas['ActionsPage']
export type MenusPage = Schemas['MenusPage']
export type TimelinePage = Schemas['TimelinePage']
export type ModelsPage = Schemas['ModelsPage']
export type ForcingPage = Schemas['ForcingPage']
export type AgreementPage = Schemas['AgreementPage']
export type CorrelationsPage = Schemas['CorrelationsPage']
export type AgreementSeriesPage = Schemas['AgreementSeriesPage']
export type AgreementSeriesPoint = Schemas['AgreementSeriesPoint']
export type AgreementBreakdownPage = Schemas['AgreementBreakdownPage']
export type GenerationRow = Schemas['GenerationRow']
export type RhoBin = Schemas['RhoBin']
export type AnalyticsFreshness = Schemas['AnalyticsFreshness']
export type AnalyticsPage = Schemas['AnalyticsPage']
export type TrainingPage = Schemas['TrainingPage']
export type CampaignReward = Schemas['CampaignReward']
export type InfraPage = Schemas['InfraPage']
export type Ident = Schemas['Ident']
export type Count = Schemas['Count']
export type Rate = Schemas['Rate']
export type Scope = Schemas['Scope']
export type Metric = Schemas['Metric']


export type State = NonNullable<Metric['state']>

export type ApiPath = keyof paths

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal, headers: { accept: 'application/json' } })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const body = await r.json()
      if (body?.detail) detail = String(body.detail)
    } catch {

    }
    throw new Error(detail)
  }
  return (await r.json()) as T
}

export interface Loaded<T> {
  data: T | null
  error: string | null

  loading: boolean
  reload: () => void
}


export function useApi<T>(
  url: string | null,
  deps: unknown[] = [],
  opts: { live?: boolean } = {},
): Loaded<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const stampLive = useCorpusStamp()
  const stamp = opts.live === false ? '' : stampLive
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (!url) return
    const ac = new AbortController()
    getJSON<T>(url, ac.signal)
      .then((d) => {
        if (!alive.current) return
        setData(d)
        setError(null)
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive.current || (e as Error)?.name === 'AbortError') return
        setError((e as Error)?.message ?? 'request failed')
        setLoading(false)
      })
    return () => ac.abort()

  }, [url, nonce, stamp, ...deps])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, loading: loading && data === null, reload }
}


let sharedSource: EventSource | null = null
const listeners = new Set<(s: string) => void>()
const STAMP_THROTTLE_MS = 10000
let lastEmit = 0
let pendingStamp: string | null = null
let pendingTimer: ReturnType<typeof setTimeout> | null = null

function emitStamp(s: string) {
  lastEmit = Date.now()
  listeners.forEach((fn) => fn(s))
}

function onStamp(s: string) {
  const wait = lastEmit + STAMP_THROTTLE_MS - Date.now()
  if (wait <= 0) {
    emitStamp(s)
    return
  }
  pendingStamp = s
  if (!pendingTimer) {
    pendingTimer = setTimeout(() => {
      pendingTimer = null
      if (pendingStamp !== null) {
        const p = pendingStamp
        pendingStamp = null
        emitStamp(p)
      }
    }, wait)
  }
}

function ensureSource() {
  if (sharedSource || typeof window === 'undefined') return
  sharedSource = new EventSource('/api/events')
  sharedSource.addEventListener('corpus', (ev) => {
    onStamp((ev as MessageEvent).data ?? '')
  })
}

export function useCorpusStamp(): string {
  const [stamp, setStamp] = useState('')
  useEffect(() => {
    ensureSource()
    const fn = (s: string) => setStamp(s)
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])
  return stamp
}

export async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return (await r.json()) as T
}
