
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
    turn: int | None = None
    settlements: float | None = None
    power_rank: float | None = None
    lord_level: float | None = None
    age_seconds: float | None = None


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
    log_tail: list[str]
    log_name: str | None = None


class CampaignRow(BaseModel):
    campaign_id: int
    campaign: Ident
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
    rows: list[CampaignRow]


class StartRow(BaseModel):
    faction: Ident
    n: int
    single_sample: bool = False
    avg_turns: float | None = None
    best_turns: int | None = None
    best_settlements: float | None = None
    best_power_rank: float | None = None
    best_lord_level: float | None = None
    ever_allied: int = 0
    ever_vassal: int = 0
    confirm_rate: Rate | None = None


class StartsPage(BaseModel):
    scope: Scope
    low_sample: Count
    rows: list[StartRow]


class MatrixCell(BaseModel):
    action_type: Ident
    rate: Rate
    total_ms: float | None = None
    per_try_ms: float | None = None
    state: State = "neutral"


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


class CampaignDetail(BaseModel):
    scope: Scope
    row: CampaignRow
    reward: list[RewardPoint]
    constant_columns: list[str] = Field(
        default_factory=list,
        description="reward columns holding one distinct value across this campaign; the "
                    "client hides them by default rather than shipping a dead column")
    diplomacy: list[DiploEvent]
    decisions: list["DecisionRow"]


class DecisionRow(BaseModel):
    decision_id: int
    ts: float | None = None
    campaign: Ident | None = None
    turn: int | None = None
    offers: int | None = None
    entity: str | None = None
    action_type: Ident | None = None
    action_key: str | None = None
    result: Ident | None = None
    result_state: State = "neutral"
    refusal: Ident | None = None
    policy: Ident | None = None
    exploit: float | None = None
    pct_global: float | None = None
    pct_local: float | None = None
    cat_rank: int | None = None
    gnn_impact: float | None = None
    gnn_rank: int | None = None
    delta_pct: float | None = None
    rho: float | None = None
    rho_n: int | None = None
    latency_ms: float | None = None


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
    pct_local: float | None = None
    gnn_impact: float | None = None
    gnn_rank: int | None = None
    taken: bool = False


class EntityState(BaseModel):
    context_kind: str
    context_id: str
    features: dict


class DecisionDetail(BaseModel):
    scope: Scope
    row: DecisionRow
    agreement: "DecisionAgreement | None" = None
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
    tree_scored: int
    graph_scored: int
    both: int
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


class ForcingPage(BaseModel):
    scope: Scope
    decisions: Count
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
    cat_rank: float | None = None
    cat_pct: float | None = None
    gnn_rank: float | None = None
    gnn_pct: float | None = None
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
    n: Count
    status: str
    rho: float | None = None
    tau_b: float | None = None
    rbo: float | None = None
    top1_same: bool | None = None
    top3_overlap: float | None = None
    cat_top_in_gnn: int | None = None
    gnn_top_in_cat: int | None = None
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


class AgreementPage(BaseModel):
    scope: Scope
    freshness: AnalyticsFreshness
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
    turns: int
    share: Rate | None = None
    per_campaign: float | None = None
    settlements_r: float | None = None
    settlements_gate: str | None = None
    lord_r: float | None = None
    lord_gate: str | None = None


class CorrelationTile(BaseModel):
    label: Literal["action ranker", "interrupt model"]
    rows: list[CorrelationRow]


class CorrelationsPage(BaseModel):
    scope: Scope
    tiles: list[CorrelationTile]


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
    backend: str | None = None
    cfg: str | None = None
    mix: dict = Field(default_factory=dict)
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


class TrainingPage(BaseModel):
    scope: Scope
    trials: list[TrialRow]
    history: list[TrainingEvent]
    group_order: list[str]


class ActivityRow(BaseModel):
    stream: str
    last_write: str | None = None
    age_seconds: float | None = None
    state: State = "neutral"


class LaunchDefaults(BaseModel):
    campaigns: int = 100
    turns_min: int = 2
    turns_max: int = 20
    retrain_first: bool = True
    retrain_every: int = 0
    model: str = "catboost"
    cfg: str = ""
    strategies: str = ""
    ruleset: str = ""
    dev: bool = False


class InfraPage(BaseModel):
    scope: Scope
    services: list[Service]
    activity: list[ActivityRow]
    policy_note: str
    models: list[str]
    defaults: LaunchDefaults
    cold_defaults: LaunchDefaults
    log_tail: list[str]


class ControlResult(BaseModel):
    ok: bool
    steps: list[str]


CampaignDetail.model_rebuild()
DecisionDetail.model_rebuild()
