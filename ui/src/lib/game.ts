import { createContext, useContext } from 'react'
import type { Loaded } from '@/lib/api'

export const SUBJECTS = [
  'Home',
  'Characters',
  'Research',
  'Realm',
  'Diplomacy',
  'Forces',
  'Items',
  'Events',
  'Starts',
]
export interface CampaignChoice {
  key: string
  leader: string | null
  faction: string
  map: string | null
  turn: number
  ts: number
}
export interface Point {
  score: number | null
  turn: number
  snapshot_id: number
  ts: number
  settlements: number | null
  treasury: number | null
  income: number | null
  net_income: number | null
  armies: number | null
  allies: number | null
  vassals: number | null
  power_rank: number | null
  lord_level: number | null
  background_income: number | null
}
export interface Effect {
  name: string
  value: string | null
  scope: string | null
}
export interface Item {
  key: string
  label: string
  effects?: Effect[]
}
export interface Skill {
  key: string
  label: string
  line: string
  effect: string | null
  level: number
  total_levels: number
  tier: number
  status: string
  unlock_rank: number | null
  parents: string[]
  effects: Effect[]
}
export interface Character {
  character_id: number
  cqi: number
  label: string | null
  subtype: string
  is_leader: boolean
  is_hero: boolean
  rank: number
  skill_points: number
  region: string | null
  stance: string
  units: number | null
  hp: number | null
  wounded: boolean
  bonuses: (Effect & { sources: string[] })[]
  traits: { key: string; label: string; level: number; effects: Effect[] }[]
  trait_progress: {
    key: string
    label: string
    points: number
    threshold: number | null
  }[]
  items: Item[]
  skills: Skill[]
  units_list: {
    key: string
    label: string
    strength_pct: number
    xp: number
  }[]
}
export interface Region {
  region_id: number
  key: string
  label: string
  x: number | null
  y: number | null
  cx: number | null
  cy: number | null
  outline: number[][]
  adjacent: number[]
  owned: boolean
  province: string
  owner: string | null
  capital: boolean
}
export interface Tech {
  effects: Effect[]
  key: string
  label: string
  researched: boolean
  researching: boolean
  can_research: boolean
  cost: number
  branch: string
  parent: string[] | null
  effect: string | null
}
export interface GameEvent {
  event_id: number
  turn: number
  kind: string
  choice: string | null
  label: string
  character_cqi: number | null
  item: string | null
  region: string | null
}
export interface GamePage {
  meta: {
    key: string
    leader: string | null
    faction: string
    map: string | null
  }
  selected: Point
  series: Point[]
  characters: Character[]
  research: Tech[]
  regions: Region[]
  provinces: {
    region_id: number
    label: string
    settlement_level: number | null
    public_order: number | null
    growth_per_turn: number | null
    income: number | null
    free_slots: number | null
    income_breakdown: { label: string; amount: number | null }[]
    slots: { key: string; label: string; slot_index: number }[]
  }[]
  diplomacy: {
    key: string
    label: string
    standing: number | null
    at_war: boolean
    allied: boolean
    trade: boolean
    nap: boolean
    mil_access: boolean
    since: number | null
    since_kind: string | null
  }[]
  armies: {
    cqi: number
    label: string
    units: number | null
    hp: number | null
    region: string | null
    stance: string
    x: number | null
    y: number | null
  }[]
  hostiles: {
    cqi: number | null
    faction: string
    units: number | null
    hp: number | null
    x: number
    y: number
  }[]
  events: GameEvent[]
  finance: {
    component_id: string
    label: string | null
    value: string | null
    kind: string
    turn: number
  }[]
  pool: Item[]
}
export const GameContext = createContext<
  Loaded<GamePage> & { campaignKey: string }
>({
  data: null,
  error: null,
  loading: true,
  reload: () => {},
  campaignKey: '',
})
export const useGame = () => useContext(GameContext)
export function mapName(map: string | null) {
  return map === 'wh3_main_combi'
    ? 'Immortal Empires'
    : map === 'wh3_main_chaos'
      ? 'Realm of Chaos'
      : (map?.replaceAll('_', ' ') ?? 'Unknown map')
}
