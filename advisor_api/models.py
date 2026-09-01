
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

State = Literal["ok", "warn", "bad", "neutral"]


class Ident(BaseModel):
    raw: str
    label: str
    culture: str | None = None
    tag: str | None = Field(default=None, description="short run-unique campaign tag")


class Count(BaseModel):
    value: int
    noun: str = Field(min_length=1, description="what is being counted, e.g. 'campaigns'")
    population: str = Field(
        min_length=1,
        description="which population, e.g. 'with a recorded decision'. Required: a count "
                    "without its population is the defect this field exists to prevent.")


class Rate(BaseModel):
    n: int
    of: int
    noun: str = Field(min_length=1)
    population: str = Field(min_length=1)

    @property
    def pct(self) -> float | None:
        return (100.0 * self.n / self.of) if self.of else None


class Scope(BaseModel):
    text: str = Field(min_length=1)
    detail: str | None = None


class SeriesPoint(BaseModel):
    x: float
    y: float | None = None


class Service(BaseModel):
    name: str
    up: bool
    pid: int | None = None
    started: str | None = None
    detail: str | None = None


class Metric(BaseModel):
    label: str
    value: float | int | str | None = None
    unit: str | None = None
    sub: str | None = None
    state: State = "neutral"
    spark: list[float] = Field(default_factory=list)


class Current(BaseModel):
    campaign: Ident | None = None
    leader: str | None = None
    faction_key: str | None = None
    campaign_map: Ident | None = None
    turn: int | None = None
    settlements: float | None = None
    power_rank: float | None = None
    lord_level: float | None = None
    stored_campaigns: int | None = None
    age_seconds: float | None = None
    decisions: int | None = None
    started_ts: float | None = None
    pick_id: int | None = None


class TimingRow(BaseModel):
    stage: str
    median_ms: float | None = None
    max_ms: float | None = None
    state: State = "neutral"


class RunPage(BaseModel):
    scope: Scope
    services: list[Service]
    current: Current
    throughput: list[Metric]
    totals: list[Count]
    collect_timing: list[TimingRow]
    cycle_timing: list[TimingRow]


class LordState(BaseModel):
    rank: int | None = None
    hp: float | None = None
    wounded: bool = False
    region: str | None = None
    skill_points: int | None = None


class CampaignStatePage(BaseModel):
    scope: Scope
    research: Ident | None = None
    researched_n: int = 0
    built_n: int = 0
    ranked_n: int = 0
    lord: LordState | None = None
    equipped: list[Ident] = []
    pool: list[Ident] = []


class LogPage(BaseModel):
    scope: Scope
    file: str | None = None
    files: list[str] = []
    size: int = 0
    lines: list[str] = []
    cursor: int | None = None
    scanned: int = 0


class CampaignRow(BaseModel):
    campaign_id: int
    campaign: Ident
    faction_key: str | None = None
    leader: str | None = None
    campaign_map: Ident | None = None
    presave_radius: float | None = None
    turns: int | None = None
    decisions: int = 0
    no_action: int = 0
    attempted: int = 0
    confirmed: int = 0
    confirm_rate: Rate | None = None
    span_min: float | None = None
    peak_settlements: float | None = None
    peak_power_rank: float | None = None
    peak_lord_level: float | None = None
    final_settlements: float | None = None
    final_power_rank: float | None = None
    final_income: float | None = None

    turn_rows: int = 0
    first_turn: int | None = None
    last_measured_turn: int | None = None
    growth_span_turns: int | None = None
    first_settlements: float | None = None
    first_lord_level: float | None = None
    final_lord_level: float | None = None
    settlements_growth: float | None = None
    lord_growth: float | None = None
    settlements_per_turn: float | None = None
    lord_per_turn: float | None = None
    growth_state: Literal["measured", "single_turn", "no_turn_rows"] = "no_turn_rows"

    outcome: Ident | None = None
    outcome_state: State = "neutral"
    ended_because: str | None = None
    suspicious: bool = False
    ended_when: str | None = None
    reward: float | None = None
    settlements_gained: float | None = None
    levels_gained: float | None = None
    pick_id: int | None = None


class OutcomeTally(BaseModel):
    outcome: Ident
    count: int
    state: State = "neutral"


class CampaignsPage(BaseModel):
    scope: Scope
    headline: list[OutcomeTally]
    suspicious: Count
    unjoined: Count
    growth_coverage: Rate
    maps: list[Ident] = Field(default_factory=list)
    races: list[str] = Field(default_factory=list)
    total: int = 0
    page: int = 0
    page_size: int = 25
    rows: list[CampaignRow]


class ProducedCampaign(BaseModel):
    campaign: Ident
    reward: float | None = None
    turns: int | None = None
    outcome: Ident | None = None
    outcome_state: State = "neutral"


class UcbPick(BaseModel):
    pick_id: int
    ts: float | None = None
    c: float | None = None
    total_plays: int = 0
    leader: str | None = None
    faction: Ident
    campaign_map: Ident | None = None
    n: int = 0
    mean: float | None = None
    blend: float | None = None
    entropy: float | None = None
    std: float | None = None
    explore: float | None = None
    score: float | None = None
    adjust: float | None = None
    margin: float | None = None
    tied: int = 0
    starts: int = 0
    repeat: bool = False
    produced: ProducedCampaign | None = None
    distinct_50: int = 0
    repeat_50: float | None = None
    cum_distinct: int = 0
    gini: float | None = None
    under_min: int = 0


class UcbRow(BaseModel):
    rank: int
    leader: str | None = None
    faction: Ident
    campaign_map: Ident | None = None
    n: int = 0
    mean: float | None = None
    entropy: float | None = None
    std: float | None = None
    blend: float | None = None
    explore: float | None = None
    score: float | None = None
    adjust: float | None = None
    delta: float | None = None
    chosen: bool = False


class WindowEdgeRow(BaseModel):
    campaign: Ident
    leader: str | None = None
    faction: Ident
    campaign_map: Ident | None = None
    played_ts: float | None = None
    turns: int | None = None
    reward: float | None = None
    start_n: int = Field(
        0, description="how many plays this start currently holds in the window")
    campaigns_away: int = Field(
        0, description="completed campaigns since it left the window, or until it will")


class UcbPicksPage(BaseModel):
    scope: Scope
    window: int = 0
    min_plays: int = 0
    pool: int = 0
    tiles: list[Metric] = Field(default_factory=list)
    picks: list[UcbPick] = []
    dropped_out: list[WindowEdgeRow] = Field(default_factory=list)
    next_out: list[WindowEdgeRow] = Field(default_factory=list)
    cursor: int | None = None


class UcbPickPage(BaseModel):
    scope: Scope
    pick: UcbPick | None = None
    under_min: int = 0
    rows: list[UcbRow] = []


class HistBin(BaseModel):
    x: int
    counts: dict[str, int] = Field(default_factory=dict)


class StartRow(BaseModel):
    faction: Ident
    leader: str | None = None
    campaign_map: Ident | None = None
    in_pool: bool = True
    n: int
    n_window: int = 0
    mean: float | None = None
    std: float | None = None
    entropy: float | None = None
    blend: float | None = None
    explore: float | None = None
    score: float | None = None
    adjust: float | None = None
    rank: int | None = None
    picks: int = 0
    picks_ago: int | None = None
    plays_ago: int | None = None
    best: float | None = None
    zero_rate: Rate | None = None
    reward_bins: list[int] = Field(default_factory=list)
    settlements_avg: float | None = None
    levels_avg: float | None = None
    avg_turns: float | None = None
    sec_per_turn: float | None = None
    settlements_gained_best: float | None = None
    settlements_gained_avg: float | None = None
    levels_gained_best: float | None = None
    levels_gained_avg: float | None = None
    allies_gained_best: float | None = None
    allies_gained_avg: float | None = None
    vassals_gained_best: float | None = None
    vassals_gained_avg: float | None = None
    total_gained_best: float | None = None
    total_gained_avg: float | None = None
    ever_allied: int = 0
    ever_vassal: int = 0
    confirm_rate: Rate | None = None


class StartsPage(BaseModel):
    scope: Scope
    window: int = 0
    min_plays: int = 0
    c: float | None = None
    total_plays: int = 0
    tiles: list[Metric] = Field(default_factory=list)
    maps: list[Ident] = Field(default_factory=list)
    reward_bins: list[HistBin] = Field(default_factory=list)
    turns_bins: list[HistBin] = Field(default_factory=list)
    rows: list[StartRow]


class MatrixCell(BaseModel):
    action_type: Ident
    rate: Rate
    total_ms: float | None = None
    per_try_ms: float | None = None
    state: State = "neutral"
    counted: Rate | None = None


class MatrixTotal(BaseModel):
    action_type: Ident
    rate: Rate
    total_ms: float | None = None
    per_try_ms: float | None = None
    state: State = "neutral"


class MatrixRow(BaseModel):
    faction: Ident
    cells: list[MatrixCell]


class MatrixPage(BaseModel):
    scope: Scope
    kind: Literal["action", "interrupt"]
    totals: list[MatrixTotal]
    columns: list[Ident]
    rows: list[MatrixRow]


class StartCampaign(BaseModel):
    campaign: Ident
    ts: float | None = None
    turns: int | None = None
    reward: float | None = None
    settlements_gained: float | None = None
    levels_gained: float | None = None
    outcome: Ident | None = None
    outcome_state: State = "neutral"
    ended_because: str | None = None
    decisions: int = 0
    confirm_rate: Rate | None = None
    first_research: Ident | None = None
    first_skill: Ident | None = None
    first_building: Ident | None = None


class StartDetail(BaseModel):
    scope: Scope
    start: StartRow
    window: int
    last_played: StartCampaign | None = None


class StartCampaignsPage(BaseModel):
    scope: Scope
    rows: list[StartCampaign] = Field(default_factory=list)


class PerfBar(BaseModel):
    id: str
    label: str
    ts: float | None = None
    settlements: float = 0.0
    levels: float = 0.0
    total_max: float | None = None
    n: int = 1
    trail: float | None = None


class LengthBand(BaseModel):
    label: str
    n: int = 0
    avg_reward: float | None = None
    reward_per_turn: float | None = None


class OutcomeCount(BaseModel):
    outcome: Ident
    state: State = "neutral"
    n: int = 0
    avg_turns: float | None = None


class StartPerformance(BaseModel):
    scope: Scope
    window: int
    bucket: int = 1
    bars: list[PerfBar] = Field(default_factory=list)
    reward_bins: list[int] = Field(default_factory=list)
    population_bins: list[int] = Field(default_factory=list)
    pool_mean: float | None = None
    turns_hist: list[int] = Field(default_factory=list)
    outcomes: list[OutcomeCount] = Field(default_factory=list)
    bands: list[LengthBand] = Field(default_factory=list)
    reward_turns_r: float | None = None


class OpeningBranch(BaseModel):
    key: str
    label: str | None = None
    n: int = 0
    share: float | None = None
    avg_reward: float | None = None
    delta_mean: float | None = None
    avg_turns: float | None = None
    reward_per_turn: float | None = None
    offered: int = 0
    taken: int = 0


class OpeningOffer(BaseModel):
    key: str
    label: str | None = None
    offered: int = 0
    taken: int = 0
    avg_reward_taken: float | None = None


class OpeningFamily(BaseModel):
    family: str
    label: str
    coverage: Rate
    avg_offers: float | None = None
    spread: float | None = None
    branches: list[OpeningBranch] = Field(default_factory=list)
    pooled: OpeningBranch | None = None
    offers: list[OpeningOffer] = Field(default_factory=list)


class RibbonBucket(BaseModel):
    label: str
    shares: list[float] = Field(default_factory=list)


class ConquestStep(BaseModel):
    step: int
    key: str
    label: str | None = None
    reached: int = 0
    of: int = 0
    median_turn: float | None = None


class StartOpenings(BaseModel):
    scope: Scope
    band: str
    campaigns: int = 0
    mean_reward: float | None = None
    sd_reward: float | None = None
    families: list[OpeningFamily] = Field(default_factory=list)
    ribbon_family: str = "building"
    ribbon_keys: list[str] = Field(default_factory=list)
    ribbon_labels: list[str] = Field(default_factory=list)
    ribbon: list[RibbonBucket] = Field(default_factory=list)
    conquest: list[ConquestStep] = Field(default_factory=list)
    no_settlement: int = 0


class StartActions(BaseModel):
    scope: Scope
    cells: list[MatrixCell] = Field(default_factory=list)


class TechRow(BaseModel):
    key: str
    label: str | None = None
    parent: Ident | None = None
    line: str | None = None
    tier: int | None = None
    points: int | None = None
    took: Rate | None = None
    avg_turn: float | None = None
    avg_reward: float | None = None
    delta_mean: float | None = None


class StartResearch(BaseModel):
    scope: Scope
    mean_reward: float | None = None
    started_ever: int = 0
    universe: int = 0
    has_parents: bool = False
    rows: list[TechRow] = Field(default_factory=list)


class SkillRow(BaseModel):
    key: str
    label: str | None = None
    parents: list[Ident] = Field(default_factory=list)
    line: str | None = None
    tier: int | None = None
    max_ranks: int | None = None
    took: Rate | None = None
    avg_ranks: float | None = None
    avg_turn: float | None = None
    avg_reward: float | None = None
    delta_mean: float | None = None


class StartCharacterRow(BaseModel):
    subtype: str
    label: str | None = None
    kind: str = "lord"
    campaigns: int = 0
    avg_rank: float | None = None
    avg_unspent: float | None = None
    avg_ranked: float | None = None
    top: list[Ident] = Field(default_factory=list)


class StartSkills(BaseModel):
    scope: Scope
    mean_reward: float | None = None
    characters: list[StartCharacterRow] = Field(default_factory=list)
    subtype: str | None = None
    avg_rank: float | None = None
    avg_unspent: float | None = None
    taken_ever: int = 0
    rows: list[SkillRow] = Field(default_factory=list)


class ItemRow(BaseModel):
    key: str
    label: str | None = None
    category: str | None = None
    resources: dict[str, float] = Field(default_factory=dict)
    held_in: int = 0
    equipped_in: int = 0
    benched_in: int = 0
    avg_reward_equipped: float | None = None
    avg_reward_benched: float | None = None
    delta: float | None = None


class BehaviourRow(BaseModel):
    label: str
    campaigns: int = 0
    avg_reward: float | None = None
    avg_equips: float | None = None
    avg_unequips: float | None = None


class StartItems(BaseModel):
    scope: Scope
    resources: list[str] = Field(default_factory=list)
    rows: list[ItemRow] = Field(default_factory=list)
    behaviour: list[BehaviourRow] = Field(default_factory=list)


class ItemSwapRow(BaseModel):
    removed: Ident
    equipped: Ident
    category: str | None = None
    campaigns: int = 0
    events: int = 0
    avg_turn: float | None = None
    avg_gap: float | None = None
    kept_rate: Rate | None = None
    avg_kept_turns: float | None = None
    avg_reward: float | None = None
    delta_mean: float | None = None


class ItemsPage(BaseModel):
    scope: Scope
    total: int = 0
    categories: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    rows: list[ItemRow] = Field(default_factory=list)


class SwapsPage(BaseModel):
    scope: Scope
    events: int = 0
    rows: list[ItemSwapRow] = Field(default_factory=list)


class ForkArmRow(BaseModel):
    key: str | None = None
    label: str
    n: int = 0
    avg_reached_turn: float | None = None
    avg_picked_turn: float | None = None
    avg_reward: float | None = None
    avg_future: float | None = None
    delta_future: float | None = None


class ForkRow(BaseModel):
    fork: str
    label: str
    cohort: int = 0
    race: str | None = None
    starts: str | None = None
    n_starts: int = 0
    arms: list[ForkArmRow] = Field(default_factory=list)


class ChoicesPage(BaseModel):
    scope: Scope
    forks: list[ForkRow] = Field(default_factory=list)


class ItemStartRow(BaseModel):
    campaign_map: Ident | None = None
    faction: Ident
    leader: str | None = None
    held_in: int = 0
    equipped_in: int = 0
    avg_reward_equipped: float | None = None
    avg_reward_benched: float | None = None
    delta: float | None = None


class ItemCampaignRow(BaseModel):
    campaign: Ident
    ts: float | None = None
    leader: str | None = None
    equip_turn: int | None = None
    turns_worn: int | None = None
    reward: float | None = None


class ItemEffect(BaseModel):
    name: str
    value: str | None = None
    state: State = "neutral"
    scope: str = "self"


class ItemPage(BaseModel):
    scope: Scope
    key: str
    label: str | None = None
    category: str | None = None
    effects: list[ItemEffect] = Field(default_factory=list)
    description: str | None = None
    acquisition: str | None = None
    lord_share: float | None = None
    held_in: int = 0
    starts: int = 0
    equip_rate: Rate | None = None
    delta: float | None = None
    avg_equip_turn: float | None = None
    churned_in: int = 0
    by_start: list[ItemStartRow] = Field(default_factory=list)
    recent: list[ItemCampaignRow] = Field(default_factory=list)


class CampaignTechRow(BaseModel):
    turn: int | None = None
    key: str
    label: str | None = None
    parent: Ident | None = None
    tier: int | None = None
    points: int | None = None
    completed_turn: int | None = None
    in_progress: bool = False


class CampaignResearchPage(BaseModel):
    scope: Scope
    rows: list[CampaignTechRow] = Field(default_factory=list)
    completed: int = 0
    universe: int = 0


class CampaignSkillRow(BaseModel):
    turn: int | None = None
    character: str | None = None
    key: str
    label: str | None = None
    rank: int | None = None
    max_ranks: int | None = None


class CampaignCharacter(BaseModel):
    cqi: str
    kind: str = "lord"
    label: str | None = None
    rank: int | None = None
    points_unspent: int | None = None
    slots: int = 0
    wearing: list[Ident] = Field(default_factory=list)


class CampaignSkillsPage(BaseModel):
    scope: Scope
    characters: list[CampaignCharacter] = Field(default_factory=list)
    rows: list[CampaignSkillRow] = Field(default_factory=list)


class CampaignItemEvent(BaseModel):
    turn: int | None = None
    character: str | None = None
    action: str = "equip"
    key: str
    label: str | None = None
    category: str | None = None


class CampaignItemsPage(BaseModel):
    scope: Scope
    events: list[CampaignItemEvent] = Field(default_factory=list)
    characters: list[CampaignCharacter] = Field(default_factory=list)
    pool: list[Ident] = Field(default_factory=list)


class BuildingRow(BaseModel):
    key: str
    label: str | None = None
    category: str | None = None
    level: int | None = None
    cost: int | None = None
    offered_in: int = 0
    took: Rate | None = None
    avg_turn: float | None = None
    avg_reward: float | None = None
    delta_mean: float | None = None


class StartBuildings(BaseModel):
    scope: Scope
    mean_reward: float | None = None
    constructed_ever: int = 0
    universe: int = 0
    rows: list[BuildingRow] = Field(default_factory=list)


class CampaignBuildingRow(BaseModel):
    turn: int | None = None
    kind: str = "construct"
    key: str
    label: str | None = None
    category: str | None = None
    level: int | None = None
    region: str | None = None
    cost: float | None = None


class CampaignBuildingsPage(BaseModel):
    scope: Scope
    constructed: int = 0
    total_cost: float | None = None
    rows: list[CampaignBuildingRow] = Field(default_factory=list)


class CatalogIndexRow(BaseModel):
    key: str
    label: str | None = None
    race: str | None = None
    category: str | None = None
    level: int | None = None
    cost: int | None = None
    line: str | None = None
    tier: int | None = None
    points: int | None = None
    unlock_rank: int | None = None
    levels: int | None = None
    threshold: int | None = None
    avg_ranks: float | None = None
    took: Rate | None = None
    starts: int = 0
    avg_turn: float | None = None
    avg_reward_took: float | None = None
    avg_reward_passed: float | None = None
    delta: float | None = None


class CatalogIndexPage(BaseModel):
    scope: Scope
    family: str
    total: int = 0
    campaigns: int = 0
    categories: list[str] = Field(default_factory=list)
    rows: list[CatalogIndexRow] = Field(default_factory=list)


class CatalogStartRow(BaseModel):
    campaign_map: Ident | None = None
    faction: Ident
    leader: str | None = None
    took: Rate | None = None
    offered_in: int = 0
    avg_turn: float | None = None
    avg_reward: float | None = None
    delta_mean: float | None = None


class CatalogCampaignRow(BaseModel):
    campaign: Ident
    ts: float | None = None
    leader: str | None = None
    turn: int | None = None
    reward: float | None = None


class ChainLevel(BaseModel):
    key: str
    label: str | None = None
    level: int | None = None
    cost: int | None = None
    constructed_in: int = 0
    took: Rate | None = None
    avg_reward_took: float | None = None
    avg_reward_passed: float | None = None
    delta: float | None = None
    this: bool = False


class RelatedKey(BaseModel):
    key: str
    label: str | None = None
    kind: str = "unlocks"
    tier: int | None = None
    points: int | None = None
    unlock_rank: int | None = None
    took_in: int = 0
    took: Rate | None = None
    avg_reward_took: float | None = None
    avg_reward_passed: float | None = None
    delta: float | None = None


class SkillCharacterRow(BaseModel):
    subtype: str
    label: str | None = None
    kind: str = "lord"
    campaigns: int = 0
    ranked: Rate | None = None
    avg_ranks: float | None = None
    avg_turn: float | None = None


class TraitLevelRow(BaseModel):
    level: int
    name: str | None = None
    threshold: int | None = None
    effects: list[ItemEffect] = Field(default_factory=list)


class CatalogVersionRow(BaseModel):
    version: str
    stamp: str
    took: Rate | None = None
    avg_reward_took: float | None = None
    avg_reward_passed: float | None = None


class CatalogKeyPage(BaseModel):
    scope: Scope
    family: str
    key: str
    label: str | None = None
    description: str | None = None
    category: str | None = None
    level: int | None = None
    cost: int | None = None
    upkeep: int | None = None
    turns_to_build: int | None = None
    parent: Ident | None = None
    line: str | None = None
    tier: int | None = None
    points: int | None = None
    unlock_rank: int | None = None
    took_in: int = 0
    took: Rate | None = None
    starts: int = 0
    avg_turn: float | None = None
    avg_reward_took: float | None = None
    avg_reward_passed: float | None = None
    delta: float | None = None
    chain: list[ChainLevel] = Field(default_factory=list)
    related: list[RelatedKey] = Field(default_factory=list)
    by_character: list[SkillCharacterRow] = Field(default_factory=list)
    trait_levels: list[TraitLevelRow] = Field(default_factory=list)
    by_start: list[CatalogStartRow] = Field(default_factory=list)
    by_version: list[CatalogVersionRow] = Field(default_factory=list)
    recent: list[CatalogCampaignRow] = Field(default_factory=list)


class PositionKeyRow(BaseModel):
    key: str
    label: str | None = None
    n: int = 0
    avg_reward: float | None = None
    avg_future: float | None = None
    delta_future: float | None = None


class PositionTypeRow(BaseModel):
    action_type: Ident
    n: int = 0
    share: float | None = None
    avg_reward: float | None = None
    avg_future: float | None = None
    delta_future: float | None = None
    keys: list[PositionKeyRow] = Field(default_factory=list)


class PositionFacetOption(BaseModel):
    key: str
    label: str
    culture: str | None = None
    campaigns: int = 0


class LookupFacets(BaseModel):
    scope: Scope
    factions: list[PositionFacetOption] = Field(default_factory=list)
    cultures: list[str] = Field(default_factory=list)
    maps: list[Ident] = Field(default_factory=list)
    settlements: list[PositionFacetOption] = Field(default_factory=list)
    resources: list[PositionFacetOption] = Field(default_factory=list)
    hero_types: list[PositionFacetOption] = Field(default_factory=list)


class PositionsPage(BaseModel):
    scope: Scope
    decisions: int = 0
    campaigns: int = 0
    takes: int = 0
    mean_reward: float | None = None
    mean_future: float | None = None
    rows: list[PositionTypeRow] = Field(default_factory=list)


class LookupCampaignRow(BaseModel):
    campaign: Ident
    ts: float | None = None
    leader: str | None = None
    campaign_map: Ident | None = None
    faction: Ident
    first_turn: int | None = None
    matched: int = 0
    turns: int | None = None
    reward: float | None = None
    settlements_gained: float | None = None
    levels_gained: float | None = None
    outcome: Ident | None = None
    outcome_state: State = "neutral"


class CampaignLookupPage(BaseModel):
    scope: Scope
    campaigns: int = 0
    decisions: int = 0
    mean_reward: float | None = None
    mean_turns: float | None = None
    total: int = 0
    page: int = 0
    page_size: int = 25
    rows: list[LookupCampaignRow] = Field(default_factory=list)


class RewardComponent(BaseModel):
    key: str
    label: str
    default: float = 1.0


class RewardWeightsPage(BaseModel):
    scope: Scope
    components: list[RewardComponent] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    is_default: bool = True


class RewardPoint(BaseModel):
    turn: int
    income: float | None = None
    settlements: float | None = None
    allies: float | None = None
    vassals: float | None = None
    power_rank: float | None = None


class DiploEvent(BaseModel):
    turn: int | None = None
    channel: Ident | None = None
    faction: Ident | None = None
    outcome: Ident | None = None
    deal_score: float | None = None
    standing: float | None = None
    terms: str | None = None
    state: State = "neutral"


class Verdict(BaseModel):
    kind: str | None = None
    text: str
    detail: str | None = None
    pct: float | None = None
    roots: list[str] = Field(default_factory=list)


class TurnRollup(BaseModel):
    turn: int
    decisions: int = 0
    confirmed: int = 0
    refused: int = 0


class CampaignDetail(BaseModel):
    scope: Scope
    row: CampaignRow
    reward: list[RewardPoint]
    constant_columns: list[str] = Field(
        default_factory=list,
        description="reward columns holding one distinct value across this campaign; the "
                    "client hides them by default rather than shipping a dead column")
    diplomacy: list[DiploEvent]
    verdict: Verdict | None = None
    turns: list[TurnRollup] = Field(default_factory=list)


class CampaignDecisions(BaseModel):
    scope: Scope
    rows: list["DecisionRow"] = Field(default_factory=list)


class DecisionRow(BaseModel):
    decision_id: int
    ts: float | None = None
    campaign: Ident | None = None
    turn: int | None = None
    offers: int | None = None
    entity: str | None = None
    action_type: Ident | None = None
    action_key: str | None = None
    target: str | None = None
    result: Ident | None = None
    result_state: State = "neutral"
    refusal: Ident | None = None
    policy: Ident | None = None
    exploit: float | None = None
    pct_global: float | None = None
    cat_rank: int | None = None
    gnn_impact: float | None = None
    gnn_rank: int | None = None
    ggnn_score: float | None = None
    ggnn_rank: int | None = None
    latency_ms: float | None = None


class PairOption(BaseModel):
    key: str
    a: str
    b: str
    comparable: Count


class DecisionsPage(BaseModel):
    scope: Scope
    total: Count
    offset: int
    limit: int
    action_types: list[Ident]
    policies: list[Ident]
    results: list[Ident]
    rows: list[DecisionRow]


class OfferRow(BaseModel):
    rank: int | None = None
    entity: str | None = None
    action_type: Ident | None = None
    action_key: str | None = None
    exploit: float | None = None
    pct_global: float | None = None
    gnn_impact: float | None = None
    gnn_rank: int | None = None
    ggnn_score: float | None = None
    ggnn_rank: int | None = None
    taken: bool = False


class EntityState(BaseModel):
    context_kind: str
    context_id: str
    features: dict


class DecisionDetail(BaseModel):
    scope: Scope
    row: DecisionRow
    agreement: list["DecisionAgreement"] = Field(default_factory=list)
    offers: list[OfferRow]
    entities: list[EntityState]
    phases: list["PhaseSpan"]


class ActionTypeRow(BaseModel):
    action_type: Ident
    rate: Rate
    refusals: list[Ident] = Field(default_factory=list)
    state: State = "neutral"


class PolicyRow(BaseModel):
    policy: Ident
    picks: int
    share: Rate
    note: str | None = None


class ActionsPage(BaseModel):
    scope: Scope
    tiles: list[Metric]
    by_type: list[ActionTypeRow]
    policies: list[PolicyRow]
    denominators: list[Count] = Field(
        default_factory=list,
        description="every distinct decision denominator on this page, named. They differ "
                    "legitimately (one excludes awaiting-execution, one excludes the "
                    "forced end turn) and were previously all printed as 'decisions'.")


class InterruptOption(BaseModel):
    label: Ident
    exploit: float | None = None
    gnn: float | None = None
    chosen: bool = False


class InterruptRow(BaseModel):
    interrupt_id: int
    ts: float | None = None
    kind: Ident
    root: str | None = None
    campaign: Ident | None = None
    turn: int | None = None
    result: Ident | None = None
    result_state: State = "neutral"
    chosen: Ident | None = None
    n_options: int | None = None
    policy: Ident | None = None
    latency_ms: float | None = None
    options: list[InterruptOption] = Field(default_factory=list)


class ArmCoverage(BaseModel):
    screen: Ident
    rows: int
    scored: dict[str, int] = Field(
        default_factory=dict,
        description="per arm, how many of this screen's rows carry that arm's scores; an "
                    "arm with no interrupt model never appears")
    compared: int = 0
    agree: Rate | None = None


class MenusPage(BaseModel):
    scope: Scope
    total: Count
    by_screen: list[Count]
    policies: list[PolicyRow]
    coverage: list[ArmCoverage]
    rows: list[InterruptRow]


class PhaseSpan(BaseModel):
    phase: Literal["collect", "queue", "score", "verify"]
    ms: float


class TimelineAction(BaseModel):
    decision_id: int
    action_type: Ident | None = None
    action_key: str | None = None
    result: Ident | None = None
    result_state: State = "neutral"
    phases: list[PhaseSpan]
    total_ms: float | None = None
    gap_ms: float | None = None
    unaccounted_ms: float | None = None


class TimelineLane(BaseModel):
    campaign: Ident
    turn: int
    confirmed: Rate
    in_turn_s: float | None = None
    actions: list[TimelineAction]


class TimelinePage(BaseModel):
    scope: Scope
    phase_legend: list[str]
    lanes: list[TimelineLane]


class ModelCard(BaseModel):
    name: str
    role: str
    status: Literal["ready", "missing", "incomplete", "stale schema"]
    state: State = "neutral"
    rows: list[tuple[str, str]] = Field(default_factory=list)
    note: str | None = None
    trained_at: str | None = None


class FitConfigRow(BaseModel):
    family: str
    role: str
    hyperparameters: dict
    compute: dict


class ModelsPage(BaseModel):
    scope: Scope
    cards: list[ModelCard]
    fit: list[FitConfigRow]


class ForcingBar(BaseModel):
    action_type: Ident
    share: Rate
    ci_lo: float | None = None
    ci_hi: float | None = None


class ForcingTile(BaseModel):
    model: str
    favours: Ident | None = None
    bars: list[ForcingBar]


class ModelVersion(BaseModel):
    version: str
    label: str
    trained: bool = False
    trained_ts: float | None = None
    corpus_decisions: int | None = None
    trials: int = 0
    campaigns: int = 0
    from_ts: float | None = None
    to_ts: float | None = None
    windows: list[list[float]] = Field(default_factory=list)


class ForcingPage(BaseModel):
    scope: Scope
    decisions: Count
    version: str | None = None
    versions: list[ModelVersion] = Field(default_factory=list)
    tiles: list[ForcingTile]
    empty_reason: str | None = Field(
        default=None,
        description="why there is nothing to draw, when there is nothing to draw. The old "
                    "panel rendered a bare em dash and left the reader to guess.")


class AgreementSummary(BaseModel):
    measure: str
    value: str
    help: str | None = None


class AgreementRankRow(BaseModel):
    picked_by: Ident
    decisions: int
    a_rank: float | None = None
    a_pct: float | None = None
    b_rank: float | None = None
    b_pct: float | None = None
    delta_pct: float | None = None
    rho_median: float | None = None
    fell_back: int = 0


class AnalyticsFreshness(BaseModel):
    tenant: str
    behind: Count
    rows: Count
    computed_through: int | None = None
    age_seconds: float | None = None
    formula_version: int = 0
    state: State = "neutral"
    detail: str | None = None


class RhoBin(BaseModel):
    lo: float
    hi: float
    decisions: int


class SecondaryMeasure(BaseModel):
    measure: str = Field(min_length=1)
    value: str
    rate: Rate | None = None


class CorrelationSummary(BaseModel):
    compared: Count
    coverage: Rate
    rho_median: float | None = None
    rho_mean: float | None = None
    rho_q1: float | None = None
    rho_q3: float | None = None
    tau_median: float | None = None
    tau_mean: float | None = None
    same_best: Rate
    overlap_median: float | None = None
    from_decision: int | None = None
    to_decision: int | None = None
    excluded: list[Count] = Field(default_factory=list)


class DecisionAgreement(BaseModel):
    pair: str
    a: str
    b: str
    n: Count
    status: str
    rho: float | None = None
    tau_b: float | None = None
    rbo: float | None = None
    top1_same: bool | None = None
    top3_overlap: float | None = None
    a_top_in_b: int | None = None
    b_top_in_a: int | None = None
    note: str | None = None


class AgreementSeriesPoint(BaseModel):
    label: str
    seq: int
    decisions: Count
    from_decision: int | None = None
    to_decision: int | None = None
    from_ts: float | None = None
    to_ts: float | None = None
    rho_median: float | None = None
    rho_mean: float | None = None
    rho_q1: float | None = None
    rho_q3: float | None = None
    tau_mean: float | None = None
    rbo_mean: float | None = None
    same_top: Rate
    gate: str | None = None


class GenerationRow(BaseModel):
    trial: Ident
    generation: int | None = None
    retrained: bool = False
    from_ts: float | None = None
    to_ts: float | None = None
    overlapped_by: str | None = None
    decisions: Count
    rho_median: float | None = None
    rho_mean: float | None = None
    tau_mean: float | None = None
    rbo_mean: float | None = None
    same_top: Rate


class AgreementSeriesPage(BaseModel):
    scope: Scope
    freshness: AnalyticsFreshness
    pair: str
    a: str
    b: str
    pairs: list[PairOption] = Field(default_factory=list)
    axis: Literal["window", "generation"]
    is_alignment: bool = False
    caveat: str | None = None
    bucket_decisions: int | None = None
    min_decisions: int | None = None
    ambiguous: Count
    points: list[AgreementSeriesPoint] = Field(default_factory=list)
    generations: list[GenerationRow] = Field(default_factory=list)
    empty_reason: str | None = None


class AgreementBreakdownRow(BaseModel):
    key: Ident
    decisions: Count
    rho_median: float | None = None
    rho_mean: float | None = None
    tau_mean: float | None = None
    rbo_mean: float | None = None
    same_top: Rate


class AgreementBreakdownPage(BaseModel):
    scope: Scope
    freshness: AnalyticsFreshness
    pair: str
    a: str
    b: str
    pairs: list[PairOption] = Field(default_factory=list)
    dim: Literal["arm", "action_type", "context_kind"]
    rows: list[AgreementBreakdownRow] = Field(default_factory=list)
    empty_reason: str | None = None


class TenantStatus(BaseModel):
    tenant: str
    formula_version: int = 0
    rows: Count
    behind: Count
    watermark: int | None = None
    built: str | None = None
    last_run: str | None = None
    last_run_seconds: float | None = None
    last_error: str | None = None
    state: State = "neutral"


class AnalyticsPage(BaseModel):
    scope: Scope
    tenants: list[TenantStatus] = Field(default_factory=list)
    db_path: str
    runner_hint: str


class AgreementMatrixCell(BaseModel):
    a: str
    b: str
    pair: str
    rho_median: float | None = None
    decisions: Count
    note: str | None = None


class AgreementMatrix(BaseModel):
    key: Literal["generation", "all"]
    title: str
    detail: str | None = None
    arms: list[str]
    cells: list[AgreementMatrixCell] = Field(default_factory=list)


class AgreementPage(BaseModel):
    scope: Scope
    freshness: AnalyticsFreshness
    pair: str
    a: str
    b: str
    pairs: list[PairOption] = Field(default_factory=list)
    matrices: list[AgreementMatrix] = Field(default_factory=list)
    correlation: CorrelationSummary | None = None
    rho_bins: list[RhoBin] = Field(default_factory=list)
    summary: list[AgreementSummary]
    rows: list[AgreementRankRow]
    secondary: list[SecondaryMeasure] = Field(default_factory=list)
    warning: str | None = None
    empty_reason: str | None = None


class CorrelationRow(BaseModel):
    arm: Ident
    campaigns: int
    share: Rate | None = None
    per_campaign: float | None = None
    reward_r: float | None = None
    reward_gate: str | None = None
    settlements_r: float | None = None
    settlements_gate: str | None = None
    lord_r: float | None = None
    lord_gate: str | None = None


class CorrelationTile(BaseModel):
    label: Literal["action ranker", "interrupt model"]
    rows: list[CorrelationRow]


class CorrelationsPage(BaseModel):
    scope: Scope
    version: str | None = None
    versions: list[ModelVersion] = Field(default_factory=list)
    tiles: list[CorrelationTile]


class DiplomacyCell(BaseModel):
    source: Ident
    attempted: int = 0
    confirmed: int = 0
    share: float | None = None


class DiplomacyRow(BaseModel):
    term: Ident
    attempted: int = 0
    confirmed: int = 0
    share: Rate
    by_source: list[DiplomacyCell] = Field(default_factory=list)


class DiplomacyPage(BaseModel):
    scope: Scope
    version: str | None = None
    versions: list[ModelVersion] = Field(default_factory=list)
    sources: list[Ident] = Field(default_factory=list)
    attempts: Count
    rows: list[DiplomacyRow] = Field(default_factory=list)


class TrialCorr(BaseModel):
    r: float | None = None
    gate: str | None = Field(
        default=None,
        description="why r was not computed. Present exactly when r is null: too few "
                    "campaigns, or one axis constant. Never rendered as a zero.")
    over: Count


class TrialRow(BaseModel):
    trial: str
    snapshots: int = Field(
        default=1,
        description="how many ledger lines this trial wrote. The ledger appends a snapshot "
                    "per campaign as a trial progresses, so one trial owns many lines; the "
                    "row shows its newest state and this says how many were folded in.")
    mix: dict = Field(default_factory=dict)
    interrupt_mix: dict = Field(
        default_factory=dict,
        description="the mix drawn on blocking screens. Trials older than the split "
                    "played the action mix there, so they carry it here too.")
    ruleset: str | None = None
    campaigns: int | None = None
    corpus: int | None = None
    settlements_per_campaign: float | None = None
    settlements_total: float | None = None
    grew: Rate | None = None
    shrank: Rate | None = None
    growth_baseline: str | None = Field(
        default=None,
        description="which definition this row was measured on. Rows written before the "
                    "clamp was removed are not comparable with rows written after it.")
    lord_per_campaign: float | None = None
    reward_per_campaign: float | None = Field(
        default=None,
        description="settlements gained plus legendary lord levels gained, per campaign -- "
                    "the same reward the UCB start selector averages")
    turns_per_campaign: float | None = None
    seconds_per_campaign: float | None = None
    seconds_per_turn: float | None = None
    notes: str | None = None
    live: bool = False
    growth_corr: dict[str, TrialCorr] = Field(
        default_factory=dict,
        description="per strategy: the correlation across this trial's campaigns between "
                    "the strategy's share of a campaign's picks and that campaign's "
                    "settlements gained, first snapshot -> peak.")


class TrainingEvent(BaseModel):
    when: str | None = None
    trial: str | None = None
    corpus_rows: int | None = None
    corpus_campaigns: int | None = None
    groups: dict = Field(default_factory=dict)


class CampaignReward(BaseModel):
    seq: int
    campaign_id: int
    faction: str | None = None
    settlements: float
    lord_level: float
    vassals: float
    allies: float
    total: float
    turns: Count


class TrainingPage(BaseModel):
    scope: Scope
    trials: list[TrialRow]
    history: list[TrainingEvent]
    group_order: list[str]
    reward: list[CampaignReward]


class ActivityRow(BaseModel):
    stream: str
    last_write: str | None = None
    age_seconds: float | None = None
    state: State = "neutral"


class LaunchDefaults(BaseModel):
    campaigns: int = 100
    turns_min: int = 2
    turns_max: int = 20
    factions: str = "all"
    retrain_every: int = 0
    retrain_first: bool = False
    strategies: str = ""
    interrupt_strategies: str = ""
    ruleset: str = ""
    presave_radius: float = 150.0
    width: int = 0
    ucb: float | None = None
    dev: bool = True


class InfraPage(BaseModel):
    scope: Scope
    services: list[Service]
    activity: list[ActivityRow]
    policy_note: str
    arms: list[str]
    interrupt_arms: list[str]
    trainable: list[str]
    defaults: LaunchDefaults
    cold_defaults: LaunchDefaults
    log_tail: list[str]


class ControlResult(BaseModel):
    ok: bool
    steps: list[str]


CampaignDetail.model_rebuild()
DecisionDetail.model_rebuild()
