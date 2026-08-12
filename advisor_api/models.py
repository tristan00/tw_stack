"""The API contract.

These models are the single source of truth for what the dashboard can say. TypeScript
types are generated from the OpenAPI schema FastAPI derives from them, so a client column
wired to a field that does not exist is a compile error rather than a blank column nobody
notices.

Two invariants are encoded as types rather than left to discipline:

`Count` -- a number of things cannot be constructed without naming WHICH population it
counted. The dashboard once rendered campaigns as 117, 118, 124 and 126 on tabs one click
apart; each was correct for its own population (campaigns that recorded a decision,
campaigns with a turn series, lines in the postmortem log) and every one was labelled just
"campaigns", so the dashboard read as contradicting itself. `population` is a required
field with a minimum length, so the wrong version does not serialise.

`Rate` -- a proportion cannot be constructed without its denominator. Three different
decision denominators once sat on one screen (3861 acted on, 3834 in the table, 3810 in
the policy tally) and none of them was printed. Carrying `of` makes two rates over
different populations visibly different instead of silently different.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

State = Literal["ok", "warn", "bad", "neutral"]


class Ident(BaseModel):
    """A game identifier in both forms. `raw` is what you grep; `label` is what you read."""
    raw: str
    label: str
    culture: str | None = None
    tag: str | None = Field(default=None, description="short run-unique campaign tag")


class Count(BaseModel):
    """A number of things, plus the population it counted.

    `population` is required and non-empty by construction. Renderers show it beside the
    number; there is no code path that produces a bare count.
    """
    value: int
    noun: str = Field(min_length=1, description="what is being counted, e.g. 'campaigns'")
    population: str = Field(
        min_length=1,
        description="which population, e.g. 'with a recorded decision'. Required: a count "
                    "without its population is the defect this field exists to prevent.")


class Rate(BaseModel):
    """A proportion that carries its own denominator."""
    n: int
    of: int
    noun: str = Field(min_length=1)
    population: str = Field(min_length=1)

    @property
    def pct(self) -> float | None:
        # None, not 0.0. "nothing was attempted" and "attempted and none succeeded" are
        # different facts and must not both render as 0%.
        return (100.0 * self.n / self.of) if self.of else None


class Scope(BaseModel):
    """One line stating exactly what a section covers. Every section has one."""
    text: str = Field(min_length=1)
    detail: str | None = None


class SeriesPoint(BaseModel):
    x: float
    y: float | None = None


# ----------------------------------------------------------------------------------------
# run -- is it healthy right now
# ----------------------------------------------------------------------------------------

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


class Signal(BaseModel):
    """Something worth noticing, with its severity already decided server-side."""
    text: str
    state: State = "neutral"
    detail: str | None = None


class Current(BaseModel):
    """The campaign being played right now.

    Resolved once, here, from the corpus. The old dashboard read this from two places --
    a database summary and a parse of the session log -- and rendered both on one screen,
    where they disagreed about which faction was playing and what turn it was. One
    accessor means header and body cannot contradict each other.
    """
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
    signals: list[Signal]
    collect_timing: list[TimingRow]
    cycle_timing: list[TimingRow]
    log_tail: list[str]
    log_name: str | None = None


# ----------------------------------------------------------------------------------------
# campaigns -- how are campaigns going
# ----------------------------------------------------------------------------------------

class CampaignRow(BaseModel):
    """One campaign, with its outcome joined on.

    Outcome and per-campaign metrics were previously two tables sharing no key, so the
    question the page exists to answer -- which campaign ended how -- could not be
    answered from it. They are joined here, server-side, and the join is tested.
    """
    campaign_id: int
    campaign: Ident
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
    # joined from the postmortem log
    outcome: Ident | None = None
    outcome_state: State = "neutral"
    ended_at_turn: int | None = None
    settlements_from: float | None = None
    settlements_to: float | None = None
    lord_from: float | None = None
    lord_to: float | None = None
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
    rows: list[CampaignRow]


class StartRow(BaseModel):
    """One playable start. `n` leads, because most starts have n=1.

    Of 61 starts, 38 had a single campaign and 53 had two or fewer, while the columns were
    labelled "avg turns" and "best power rank" -- aggregate vocabulary over one sample.
    `n` is a first-class column and `single_sample` marks the rows where the aggregates
    are one observation wearing an average's name.
    """
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
    """The totals row. The reason this page exists.

    Aggregated across every faction, `diplomacy` passes 73 of 425 attempts while every
    other action type sits between 75% and 100%. In a 1342-cell alphabetical grid with no
    totals row that finding is invisible. It leads the page now, worst first.
    """
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


# ----------------------------------------------------------------------------------------
# decisions -- what did it choose and why
# ----------------------------------------------------------------------------------------

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
    """One option on a blocking screen, with its per-model scores as data.

    All 69 per-option scores previously existed only inside title attributes -- the
    heading said so out loud: "hover chosen for all options and scores". Hover text is
    unselectable, un-searchable and absent on touch, so the scores are fields now.
    """
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
    """One phase of one action, in ms.

    The four phases are the decomposition of a decision's wall clock: the recorder reading
    the game, the request round trip, featurize+rank, and execute+confirm.
    """
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


# ----------------------------------------------------------------------------------------
# models -- are the models learning
# ----------------------------------------------------------------------------------------

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


class AgreementPage(BaseModel):
    scope: Scope
    summary: list[AgreementSummary]
    rows: list[AgreementRankRow]
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
    """`label` is the tile's identity: "action ranker" or "interrupt model".

    They share arm names, so a check that searches the whole page for an arm row finds the
    action table every time and never inspects the interrupt one. Splitting by tile is the
    only way to verify each against its own corpus counts.
    """
    label: Literal["action ranker", "interrupt model"]
    rows: list[CorrelationRow]


class CorrelationsPage(BaseModel):
    scope: Scope
    tiles: list[CorrelationTile]


class TrialRow(BaseModel):
    trial: str
    backend: str | None = None
    cfg: str | None = None
    mix: dict = Field(default_factory=dict)
    ruleset: str | None = None
    campaigns: int | None = None
    corpus: int | None = None
    settlements_per_campaign: float | None = None
    settlements_total: float | None = None
    grew: str | None = None
    lord_per_campaign: float | None = None
    turns_per_campaign: float | None = None
    seconds_per_campaign: float | None = None
    seconds_per_turn: float | None = None
    notes: str | None = None
    live: bool = False


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


# ----------------------------------------------------------------------------------------
# infra -- services and controls
# ----------------------------------------------------------------------------------------

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
