
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse

import arms
import common
from advisor_api import db, ident, proc, queries as q
from advisor_api.models import (
    ActionsPage, AgreementBreakdownPage, AgreementPage, AgreementSeriesPage, AnalyticsPage,
    CampaignDecisions, CampaignDetail, CampaignsPage, ControlResult, Count,
    DecisionDetail, DecisionsPage, DiplomacyPage, ForcingPage, InfraPage, LaunchDefaults,
    LogPage,
    MatrixCell,
    MatrixPage, MatrixRow, MatrixTotal, MenusPage, ModelsPage, Rate, RunPage, Scope,
    StartsPage, StartActions, StartCampaignsPage, StartDetail, StartOpenings,
    StartPerformance, TimelinePage, TrainingPage, CorrelationsPage, UcbPickPage,
    UcbPicksPage,
    CampaignItemsPage, CampaignResearchPage, CampaignSkillsPage, ItemPage, ItemsPage,
    StartItems, StartResearch, StartSkills,
    CampaignBuildingsPage, CatalogIndexPage, CatalogKeyPage, PositionsPage,
    RewardWeightsPage, StartBuildings,
)

UI_DIST = os.path.join(common.ROOT, "ui", "dist")

MODE = ("dashboard"
        if os.environ.get("TW_DASHBOARD_ONLY", "").strip().lower()
        in ("1", "true", "yes") or "--dashboard" in sys.argv[1:] else "full")


_warm_stop = threading.Event()


def _warm_loop():
    q._WARM_ALIVE.set()
    while not _warm_stop.is_set():
        t0 = time.perf_counter()
        steps = q.warm_caches(db.connect())
        print("API warm %.0fms %s" % ((time.perf_counter() - t0) * 1000,
                                      " ".join(steps)))
        _warm_stop.wait(q.WARM_EVERY_S)
    q._WARM_ALIVE.clear()


@contextlib.asynccontextmanager
async def _lifespan(_app):
    proc.start()
    _warm_stop.clear()
    threading.Thread(target=_warm_loop, name="cache-warm", daemon=True).start()
    try:
        yield
    finally:
        _warm_stop.set()
        proc.stop()


app = FastAPI(title="advisor", version="8", lifespan=_lifespan,
              description="Telemetry for the Total War campaign-advisor harness.")
app.add_middleware(GZipMiddleware, minimum_size=2048)

UNTIMED = ("/api/events", "/api/health")


@app.middleware("http")
async def _timed(request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in UNTIMED:
        return await call_next(request)
    token = db.trace_begin()
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        items = db.trace_end(token)
    ms = (time.perf_counter() - t0) * 1000
    work = sorted(items, key=lambda i: -i[1])
    url = path + ("?" + request.url.query if request.url.query else "")
    print("API %s %s %d %.0fms computed=%d %s"
          % (request.method, url, response.status_code, ms, len(work),
             " ".join("%s=%.0fms" % (n, m) for n, m, _k in work[:8])))
    response.headers["Server-Timing"] = ", ".join(
        ["total;dur=%.0f" % ms]
        + ["%s;dur=%.0f" % (n.strip("_"), m) for n, m, _k in work[:8]])
    return response


def _con():
    return db.connect()


def _scope(text, detail=None) -> Scope:
    return Scope(text=text, detail=detail)


@app.get("/api/run", response_model=RunPage, tags=["run"])
def get_run() -> RunPage:
    con = _con()
    return RunPage(
        scope=_scope("this run dir", common.RUN_DIR),
        services=proc.services(),
        current=q.current(con),
        throughput=q.throughput(con),
        totals=q.totals(con),
        collect_timing=q.collect_timing(con),
        cycle_timing=q.cycle_timing(con))


@app.get("/api/log", response_model=LogPage, tags=["log"])
def get_log(file: str | None = None, q_text: str | None = None, t0: str | None = None,
            t1: str | None = None, limit: int = 500, cursor: int | None = None) -> LogPage:
    try:
        r = q.read_session_log(file=file, q=q_text, t0=t0, t1=t1,
                               limit=limit, cursor=cursor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return LogPage(
        scope=_scope("the session log, oldest first within the window",
                     "chunked reads from disk; nothing loads whole"),
        **r)


@app.get("/api/campaigns/starts", response_model=StartsPage, tags=["campaigns"])
def get_starts() -> StartsPage:
    con = _con()
    gains = q.gains_all(con)
    cx = q.ucb_context(con, gains)
    extras = q.starts_page_extras(con, cx, gains)
    return StartsPage(
        scope=_scope("one row per playable start, best total gained first",
                     "gained columns are per-campaign first-to-peak deltas: best is the "
                     "single strongest campaign, avg is across all of that start's campaigns"),
        window=q.UCB.WINDOW, min_plays=q.UCB.MIN_PLAYS, c=cx["c"], total_plays=cx["total"],
        tiles=extras["tiles"], maps=extras["maps"], reward_bins=extras["reward_bins"],
        turns_bins=extras["turns_bins"], rows=q.starts_rows(con, None, cx, gains))


@app.get("/api/campaigns/starts/{campaign_map}/{faction}", response_model=StartDetail,
         tags=["campaigns"])
def get_start(campaign_map: str, faction: str) -> StartDetail:
    con = _con()
    row, _gains, _cx = q.start_head(con, campaign_map, faction)
    if row is None:
        raise HTTPException(404, "no start %s on %s" % (faction, campaign_map))
    return StartDetail(
        scope=_scope("one start", "%s on %s" % (row.leader or row.faction.label,
                                                 row.campaign_map.label
                                                 if row.campaign_map else campaign_map)),
        start=row, window=q.UCB.WINDOW,
        last_played=q.start_last_played(con, campaign_map, faction))


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/performance",
         response_model=StartPerformance, tags=["campaigns"])
def get_start_performance(campaign_map: str, faction: str) -> StartPerformance:
    con = _con()
    got = q.start_performance(con, campaign_map, faction)
    return StartPerformance(
        scope=_scope("how this start earns when played",
                     "reward history in play order, distribution vs the pool, and how "
                     "its campaigns end"),
        window=q.UCB.WINDOW, **got)


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/openings",
         response_model=StartOpenings, tags=["campaigns"])
def get_start_openings(campaign_map: str, faction: str,
                       band: str = Query("all", pattern=r"^(all|1-3|4-6|7\+)$")
                       ) -> StartOpenings:
    con = _con()
    got = q.start_openings(con, campaign_map, faction, band=band)
    return StartOpenings(
        scope=_scope("the first choice its campaigns made in each exclusive family, "
                     "and how each opening scored",
                     "campaigns are short truncated rollouts; these are openings, not "
                     "builds -- compare within a length band"),
        **got)


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/campaigns",
         response_model=StartCampaignsPage, tags=["campaigns"])
def get_start_campaigns(campaign_map: str, faction: str) -> StartCampaignsPage:
    con = _con()
    return StartCampaignsPage(
        scope=_scope("every campaign of this start, newest first"),
        rows=q.start_campaigns_slice(con, campaign_map, faction))


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/actions",
         response_model=StartActions, tags=["campaigns"])
def get_start_actions(campaign_map: str, faction: str) -> StartActions:
    con = _con()
    return StartActions(
        scope=_scope("every action attempt by this faction, worst confirmed first",
                     "a family whose attempts cannot count is excluded from confirm "
                     "aggregates and marked"),
        cells=q.start_actions(con, campaign_map, faction))


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/research",
         response_model=StartResearch, tags=["campaigns"])
def get_start_research(campaign_map: str, faction: str) -> StartResearch:
    con = _con()
    got = q.start_research(con, campaign_map, faction)
    return StartResearch(
        scope=_scope("every tech in this start's tree, one row each",
                     "avg reward is over the campaigns that started it"), **got)


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/skills",
         response_model=StartSkills, tags=["campaigns"])
def get_start_skills(campaign_map: str, faction: str,
                     subtype: str | None = None) -> StartSkills:
    con = _con()
    got = q.start_skills(con, campaign_map, faction, subtype)
    return StartSkills(
        scope=_scope("every node in this character's skill tree, one row each",
                     "ranks read from live snapshots; avg reward is over the campaigns "
                     "that ranked it"), **got)


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/items",
         response_model=StartItems, tags=["campaigns"])
def get_start_items(campaign_map: str, faction: str) -> StartItems:
    con = _con()
    got = q.start_items(con, campaign_map, faction)
    return StartItems(
        scope=_scope("every item this faction ever held, one row each",
                     "equipped vs benched compares campaigns that held the same item: "
                     "benched = in the pool, never worn"), **got)


@app.get("/api/campaigns/starts/{campaign_map}/{faction}/buildings",
         response_model=StartBuildings, tags=["campaigns"])
def get_start_buildings(campaign_map: str, faction: str) -> StartBuildings:
    con = _con()
    got = q.start_buildings(con, campaign_map, faction)
    return StartBuildings(
        scope=_scope("every building its campaigns were ever offered, one row each",
                     "avg reward is over the campaigns that constructed it"), **got)


@app.get("/api/items", response_model=ItemsPage, tags=["catalog"])
def get_items() -> ItemsPage:
    con = _con()
    got = q.items_page(con)
    return ItemsPage(
        scope=_scope("every item this run dir ever held, one row each",
                     "equipped vs benched compares campaigns that held the same item"),
        **got)


@app.get("/api/items/{item_key}", response_model=ItemPage, tags=["catalog"])
def get_item(item_key: str) -> ItemPage:
    con = _con()
    got = q.item_page(con, item_key)
    if got is None:
        raise HTTPException(404, "no item %r in this run dir" % item_key)
    return ItemPage(scope=_scope("one item across every start that held it"), **got)


_CATALOG_SCOPES = {
    "building": ("every building this run dir was ever offered, one row each",
                 "took = campaigns that constructed it at least once"),
    "research": ("every tech this run dir was ever offered, one row each",
                 "took = campaigns that started it"),
    "skills": ("every skill a campaign ever put a point in, one row each",
               "took = campaigns that ranked it at least once"),
}


def _catalog_index(family: str):
    con = _con()
    got = q.catalog_index(con, family)
    text, detail = _CATALOG_SCOPES[family]
    return CatalogIndexPage(scope=_scope(text, detail), **got)


def _catalog_key(family: str, key: str):
    con = _con()
    got = q.catalog_key_page(con, family, key)
    if got is None:
        raise HTTPException(404, "no %s %r in this run dir" % (family, key))
    return CatalogKeyPage(
        scope=_scope("one %s across every start" % family), **got)


@app.get("/api/buildings", response_model=CatalogIndexPage,
         response_model_exclude_none=True, tags=["catalog"])
def get_buildings() -> CatalogIndexPage:
    return _catalog_index("building")


@app.get("/api/buildings/{key}", response_model=CatalogKeyPage,
         response_model_exclude_none=True, tags=["catalog"])
def get_building(key: str) -> CatalogKeyPage:
    return _catalog_key("building", key)


@app.get("/api/research", response_model=CatalogIndexPage,
         response_model_exclude_none=True, tags=["catalog"])
def get_research() -> CatalogIndexPage:
    return _catalog_index("research")


@app.get("/api/research/{key}", response_model=CatalogKeyPage,
         response_model_exclude_none=True, tags=["catalog"])
def get_tech(key: str) -> CatalogKeyPage:
    return _catalog_key("research", key)


@app.get("/api/skills", response_model=CatalogIndexPage,
         response_model_exclude_none=True, tags=["catalog"])
def get_skills() -> CatalogIndexPage:
    return _catalog_index("skills")


@app.get("/api/skills/{key}", response_model=CatalogKeyPage,
         response_model_exclude_none=True, tags=["catalog"])
def get_skill(key: str) -> CatalogKeyPage:
    return _catalog_key("skills", key)


@app.get("/api/positions", response_model=PositionsPage, tags=["positions"])
def get_positions(faction: str | None = None, culture: str | None = None,
                  map: str | None = None,
                  c: list[str] = Query(default_factory=list)) -> PositionsPage:
    con = _con()
    got = q.positions_page(con, {"faction": faction, "culture": culture,
                                 "map": map}, c)
    return PositionsPage(
        scope=_scope("what gets taken in situations like this, by action type",
                     "conditions AND together over the decision's recorded state "
                     "and its history; a has/has-not condition means the campaign "
                     "had done that thing at or before the decision; rewards use "
                     "the analytics weights from the reward weights tab; future "
                     "reward = what the campaign still gained after the decision"),
        **got)


def _weights_page() -> RewardWeightsPage:
    from advisor_api.models import RewardComponent
    w = q.reward_weights()
    defaults = {k: d for k, _l, d in q.REWARD_COMPONENTS}
    return RewardWeightsPage(
        scope=_scope("what one unit of each gain is worth in the analytics reward",
                     "applies to the catalog, items, positions and the start "
                     "buildings/research/skills/items tabs; the campaigns page's "
                     "recorded reward and the UCB selector keep the official "
                     "1/1 reward and are untouched"),
        components=[RewardComponent(key=k, label=l, default=d)
                    for k, l, d in q.REWARD_COMPONENTS],
        weights=w,
        is_default=all(w[k] == defaults[k] for k in w))


@app.get("/api/reward-weights", response_model=RewardWeightsPage,
         tags=["positions"])
def get_reward_weights() -> RewardWeightsPage:
    return _weights_page()


@app.post("/api/reward-weights", response_model=RewardWeightsPage,
          tags=["positions"])
def post_reward_weights(weights: dict[str, float]) -> RewardWeightsPage:
    try:
        q.set_reward_weights(weights)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _weights_page()


@app.get("/api/campaigns/picks", response_model=UcbPicksPage, tags=["campaigns"])
def get_picks(limit: int = 2000, before: int | None = None) -> UcbPicksPage:
    con = _con()
    lim = max(1, min(int(limit), 5000))
    gains = q.gains_all(con)
    cx = q.ucb_context(con, gains)
    series = q.ucb_pick_series(con, gains)
    picks = q.ucb_picks(series, lim, before)
    dropped, nxt = q.ucb_window_edges(con, gains)
    return UcbPicksPage(
        scope=_scope("one row per UCB start pick, newest first",
                     "score = blend + explore + adj, blend = (mean + H + std) / 3, "
                     "adj = the manual per-faction delta in rules/ucb_adjust.json"),
        window=q.UCB.WINDOW, min_plays=q.UCB.MIN_PLAYS, pool=len(cx["pool"]),
        tiles=q.ucb_tiles(series, cx), picks=picks,
        dropped_out=dropped, next_out=nxt,
        cursor=(picks[-1].pick_id if len(picks) >= lim else None))


@app.get("/api/campaigns/picks/{pick_id}", response_model=UcbPickPage, tags=["campaigns"])
def get_pick(pick_id: int) -> UcbPickPage:
    con = _con()
    pick, rows, under = q.ucb_pick_rows(con, pick_id)
    if pick is None:
        raise HTTPException(status_code=404, detail="no UCB pick %d" % pick_id)
    return UcbPickPage(
        scope=_scope("every start the selector ranked at this pick, best score first",
                     "score = blend + C*sqrt(ln(plays)/n) + adj; under %d plays the score "
                     "is infinite and shown without one" % q.UCB.MIN_PLAYS),
        pick=pick, under_min=under, rows=rows)


@app.get("/api/campaigns/matrix", response_model=MatrixPage, tags=["campaigns"])
def get_matrix(kind: str = Query("action", pattern="^(action|interrupt)$")) -> MatrixPage:
    con = _con()
    grid, totals_ = q.matrix(con, kind)
    noun = "screens" if kind == "interrupt" else "actions"
    tot_rows = []
    for atype, (tried, ok, ms) in totals_.items():
        rate = Rate(n=ok, of=tried, noun=noun,
                    population="attempted of this type across every faction")
        tot_rows.append(MatrixTotal(
            action_type=q._phrase(atype), rate=rate,
            total_ms=round(ms, 0) or None,
            per_try_ms=round(ms / tried, 0) if tried else None))
    tot_rows.sort(key=lambda t: (t.rate.pct if t.rate.pct is not None else 999, -t.rate.of))
    columns = [q._phrase(a) for a, _ in sorted(totals_.items())]
    rows = []
    for faction, cells in sorted(grid.items()):
        out_cells = []
        for atype, _ in sorted(totals_.items()):
            got = cells.get(atype)
            if not got:
                continue
            tried, ok, ms = got
            rate = Rate(n=ok, of=tried, noun=noun,
                        population="attempted of this type by this faction")
            out_cells.append(MatrixCell(
                action_type=q._phrase(atype), rate=rate, total_ms=round(ms, 0) or None,
                per_try_ms=round(ms / tried, 0) if tried else None))
        rows.append(MatrixRow(faction=q._fac(faction), cells=out_cells))
    return MatrixPage(
        scope=_scope("every %s attempt in this run dir, by faction and type" % noun),
        kind=kind, totals=tot_rows, columns=columns, rows=rows)


@app.get("/api/campaigns", response_model=CampaignsPage, tags=["campaigns"])
def get_campaigns() -> CampaignsPage:
    con = _con()
    outcomes, unjoined = q.outcome_join(con)
    rows = q.campaign_rows(con, outcomes=outcomes)
    return CampaignsPage(
        scope=_scope("every campaign in this run dir, newest first",
                     "outcome is joined from the postmortem log"),
        headline=q.outcome_headline(rows),
        suspicious=Count(value=sum(1 for r in rows if r.suspicious), noun="campaigns",
                         population="whose ending looks like a harness fault, not a defeat"),
        unjoined=Count(value=unjoined, noun="endings",
                       population="recorded in the log but belonging to earlier run dirs"),
        growth_coverage=Rate(
            n=sum(1 for r in rows if r.growth_state == "measured"), of=len(rows),
            noun="campaigns",
            population="on this page with two or more recorded turns, so a first -> last "
                       "growth span exists"),
        rows=rows)


@app.get("/api/campaigns/{campaign_key}", response_model=CampaignDetail, tags=["campaigns"])
def get_campaign(campaign_key: str) -> CampaignDetail:
    con = _con()
    row = q.campaign_row(con, campaign_key)
    if row is None:
        raise HTTPException(404, "no campaign %r in this run dir" % campaign_key)
    reward, constant = q.reward_series(con, campaign_key)
    return CampaignDetail(
        scope=_scope("one campaign", ident.campaign(campaign_key)["label"]),
        row=row, reward=reward, constant_columns=constant,
        diplomacy=q.diplomacy_tail(con, campaign_key),
        verdict=q.campaign_verdict(row.ended_because),
        turns=q.campaign_turn_rollup(con, campaign_key))


@app.get("/api/campaigns/{campaign_key}/decisions", response_model=CampaignDecisions,
         tags=["campaigns"])
def get_campaign_decisions(campaign_key: str) -> CampaignDecisions:
    con = _con()
    rows, _total = q.decisions_page(con, 0, 500, campaign=campaign_key)
    return CampaignDecisions(
        scope=_scope("every action taken inside this campaign, newest first"),
        rows=rows)


@app.get("/api/campaigns/{campaign_key}/buildings",
         response_model=CampaignBuildingsPage, tags=["campaigns"])
def get_campaign_buildings(campaign_key: str) -> CampaignBuildingsPage:
    con = _con()
    got = q.campaign_buildings(con, campaign_key)
    if got is None:
        raise HTTPException(404, "no campaign %r in this run dir" % campaign_key)
    return CampaignBuildingsPage(
        scope=_scope("every construction, upgrade, repair and dismantle, in order",
                     "a dismantle's cost is its refund, negative"), **got)


@app.get("/api/campaigns/{campaign_key}/research", response_model=CampaignResearchPage,
         tags=["campaigns"])
def get_campaign_research(campaign_key: str) -> CampaignResearchPage:
    con = _con()
    got = q.campaign_research(con, campaign_key)
    if got is None:
        raise HTTPException(404, "no campaign %r in this run dir" % campaign_key)
    return CampaignResearchPage(
        scope=_scope("every research started, in order",
                     "completion is inferred from the next turn's offer set"), **got)


@app.get("/api/campaigns/{campaign_key}/skills", response_model=CampaignSkillsPage,
         tags=["campaigns"])
def get_campaign_skills(campaign_key: str) -> CampaignSkillsPage:
    con = _con()
    got = q.campaign_skills(con, campaign_key)
    if got is None:
        raise HTTPException(404, "no campaign %r in this run dir" % campaign_key)
    return CampaignSkillsPage(
        scope=_scope("every skill point spent, in order"), **got)


@app.get("/api/campaigns/{campaign_key}/items", response_model=CampaignItemsPage,
         tags=["campaigns"])
def get_campaign_items(campaign_key: str) -> CampaignItemsPage:
    con = _con()
    got = q.campaign_items(con, campaign_key)
    if got is None:
        raise HTTPException(404, "no campaign %r in this run dir" % campaign_key)
    return CampaignItemsPage(
        scope=_scope("every equip and unequip, in order",
                     "the pool is a lower bound read from the last item offers"), **got)


@app.get("/api/decisions/actions", response_model=ActionsPage, tags=["decisions"])
def get_actions() -> ActionsPage:
    con = _con()
    tiles, by_type, policies, denominators = q.actions_summary(con)
    return ActionsPage(
        scope=_scope("every action attempted in this run dir",
                     "confirm rate is the game agreeing the action happened"),
        tiles=tiles, by_type=by_type, policies=policies, denominators=denominators)


@app.get("/api/decisions/diplomacy", response_model=DiplomacyPage, tags=["decisions"])
def get_diplomacy(version: str | None = None) -> DiplomacyPage:
    con = _con()
    versions = q.model_versions()
    version = version if version and any(v.version == version for v in versions) else None
    sources, total, rows = q.diplomacy_mix(con, version)
    return DiplomacyPage(
        scope=_scope("every diplomatic action the run attempted, by what was proposed",
                     "attempted excludes actions still awaiting execution; confirmed is the "
                     "game agreeing it happened; the source columns split each row by the "
                     "arm that chose it; pick a model version to keep only the attempts "
                     "made while it was in force"),
        version=version, versions=versions, sources=sources,
        attempts=Count(value=total, noun="diplomatic actions",
                       population="attempted in this run dir"),
        rows=rows)


@app.get("/api/decisions/menus", response_model=MenusPage, tags=["decisions"])
def get_menus() -> MenusPage:
    con = _con()
    total, by_screen, policies, coverage, rows = q.menus(con)
    return MenusPage(
        scope=_scope("blocking screens the run had to answer",
                     "per-option model scores are columns, not hover text"),
        total=total, by_screen=by_screen, policies=policies, coverage=coverage, rows=rows)


@app.get("/api/decisions/timeline", response_model=TimelinePage, tags=["decisions"])
def get_timeline() -> TimelinePage:
    con = _con()
    return TimelinePage(
        scope=_scope("the last %d actions, by campaign and turn" % q.TIMELINE_DECISIONS,
                     "one lane per campaign-turn; turn numbers restart every campaign"),
        phase_legend=["collect - the recorder reading the game",
                      "queue - the request round trip",
                      "score - featurize and rank",
                      "verify - execute and confirm"],
        lanes=q.timeline(con))


@app.get("/api/decisions/{decision_id}", response_model=DecisionDetail, tags=["decisions"])
def get_decision(decision_id: int) -> DecisionDetail:
    con = _con()
    got = q.decision_detail(con, decision_id)
    if not got:
        raise HTTPException(404, "no decision %d in this run dir" % decision_id)
    head, offers, ents, phases = got
    return DecisionDetail(
        scope=_scope("one decision and the whole ranking it produced",
                     "the row the game accepted is marked"),
        row=head, agreement=q.decision_agreement(decision_id),
        offers=offers, entities=ents, phases=phases)


@app.get("/api/decisions", response_model=DecisionsPage, tags=["decisions"])
def get_decisions(offset: int = 0, limit: int = Query(q.DECISIONS_PAGE, ge=1, le=200),
                  action_type: str | None = None, policy: str | None = None,
                  result: str | None = None, campaign: str | None = None,
                  search: str | None = None) -> DecisionsPage:
    con = _con()
    rows, total = q.decisions_page(con, offset, limit, action_type=action_type,
                                   policy=policy, result=result, campaign=campaign,
                                   q=search)
    facets = q.decision_facets(con)
    return DecisionsPage(
        scope=_scope("every action the run took, newest first",
                     "filters narrow the population; the count follows the filter. Each "
                     "ranking arm's rank of the taken action is a column; how the arms "
                     "agree is on the models view and on each decision"),
        total=Count(value=total, noun="actions",
                    population="matching the current filter in this run dir"),
        offset=offset, limit=limit, rows=rows,
        action_types=facets["action_types"], policies=facets["policies"],
        results=facets["results"])


@app.get("/api/models/forcing", response_model=ForcingPage, tags=["models"])
def get_forcing(version: str | None = None) -> ForcingPage:
    con = _con()
    versions = q.model_versions()
    version = version if version and any(v.version == version for v in versions) else None
    tiles, n = q.forcing(con, version)
    return ForcingPage(
        scope=_scope("the action-type mix each model arm actually picked",
                     "bars carry a 95% interval, so a small sample looks small; pick a "
                     "model version to see only the decisions taken while it was in force"),
        decisions=n, version=version, versions=versions, tiles=tiles,
        empty_reason=(None if tiles else
                      "no decision in this run dir was drawn by a model arm yet -- the "
                      "strategy mix has only produced random and ruleset picks so far"))


@app.get("/api/models/agreement", response_model=AgreementPage, tags=["models"])
def get_agreement(pair: str | None = None) -> AgreementPage:
    return q.agreement_page(pair)


@app.get("/api/models/agreement/series", response_model=AgreementSeriesPage,
         tags=["models"])
def get_agreement_series(axis: str = "window", pair: str | None = None) -> AgreementSeriesPage:
    return q.agreement_series(axis, pair)


@app.get("/api/models/agreement/breakdown", response_model=AgreementBreakdownPage,
         tags=["models"])
def get_agreement_breakdown(dim: str = "action_type",
                            pair: str | None = None) -> AgreementBreakdownPage:
    return q.agreement_breakdown(dim, pair)


@app.get("/api/analytics", response_model=AnalyticsPage, tags=["infra"])
def get_analytics() -> AnalyticsPage:
    return q.analytics_status()


@app.post("/api/analytics/rebuild", response_model=ControlResult, tags=["infra"])
def post_analytics_rebuild() -> ControlResult:
    return ControlResult(ok=True, steps=proc.rebuild_analytics())


@app.get("/api/models/correlations", response_model=CorrelationsPage, tags=["models"])
def get_correlations(version: str | None = None) -> CorrelationsPage:
    con = _con()
    versions = q.model_versions()
    version = version if version and any(v.version == version for v in versions) else None
    return CorrelationsPage(
        scope=_scope("does an arm's share of a campaign track how the campaign went",
                     "one row per campaign: the arm's share of that campaign's decisions "
                     "(zero when it drew none) against the campaign's reward, settlements "
                     "gained + lord levels gained; every arm is measured over the same "
                     "campaigns; pick a model version to keep only the campaigns that "
                     "started while it was in force"),
        version=version, versions=versions, tiles=q.correlations(con, version))


@app.get("/api/models/training", response_model=TrainingPage, tags=["models"])
def get_training() -> TrainingPage:
    history = q.training_history()
    seen: list = []
    for ev in history:
        for name in ev.groups:
            if name not in seen:
                seen.append(name)
    return TrainingPage(
        scope=_scope("every trial and every retrain, newest first",
                     "trials come from the experiment ledger; retrains from the session "
                     "reports"),
        trials=q.trials(_con()), history=history, group_order=seen,
        reward=q.campaign_reward_series(_con()))


@app.get("/api/models", response_model=ModelsPage, tags=["models"])
def get_models() -> ModelsPage:
    return ModelsPage(scope=_scope("the models on disk right now", common.MODELS),
                      cards=q.model_cards(), fit=q.fit_config())


def _launch_defaults() -> LaunchDefaults:
    import run_config
    run = run_config.RUN
    turns = str(run["turns"])
    lo, hi = turns.split("-", 1) if "-" in turns else (turns, turns)
    return LaunchDefaults(
        campaigns=int(run["campaigns"]), turns_min=int(lo), turns_max=int(hi),
        factions=str(run.get("factions") or "all"),
        retrain_every=int(run.get("retrain_every") or 0),
        retrain_first=bool(run.get("retrain_first")),
        strategies=str(run.get("strategies") or ""),
        interrupt_strategies=str(run.get("interrupt_strategies") or ""),
        ruleset=str(run.get("ruleset") or ""),
        presave_radius=float(run["presave_radius"]),
        ucb=(float(run["ucb"]) if run.get("ucb") else None), dev=bool(run.get("dev", True)))


@app.get("/api/infra", response_model=InfraPage, tags=["infra"])
def get_infra() -> InfraPage:
    tail, _ = q.session_log_tail(14)
    defaults = _launch_defaults()
    return InfraPage(
        scope=_scope("services, streams and controls", common.RUN_DIR),
        services=proc.services(), activity=q.activity(),
        policy_note="Picks are drawn from two strategy mixes -- one over actions, one over "
                    "blocking screens. Both are recorded on the trial and shown on the "
                    "models view; the defaults below are run_config.RUN.",
        arms=list(arms.NAMES), interrupt_arms=list(arms.INTERRUPT_NAMES),
        trainable=list(arms.TRAINABLE),
        defaults=defaults,
        cold_defaults=LaunchDefaults(campaigns=10, turns_min=2, turns_max=40,
                                     retrain_every=0, retrain_first=False,
                                     strategies="", interrupt_strategies="", ruleset="",
                                     presave_radius=defaults.presave_radius,
                                     dev=defaults.dev),
        log_tail=tail)


@app.post("/api/infra/kill", response_model=ControlResult, tags=["infra"])
def post_kill() -> ControlResult:
    return ControlResult(ok=True, steps=proc.kill_session_and_game())


@app.post("/api/infra/launch", response_model=ControlResult, tags=["infra"])
def post_launch(params: LaunchDefaults) -> ControlResult:
    return ControlResult(ok=True, steps=proc.launch("run", params.model_dump()))


@app.post("/api/infra/coldstart", response_model=ControlResult, tags=["infra"])
def post_coldstart(params: LaunchDefaults) -> ControlResult:
    return ControlResult(ok=True, steps=proc.launch("cold", params.model_dump()))


@app.get("/api/events", tags=["run"])
async def events():
    async def gen():
        last = None
        beat = 0
        while True:
            try:
                cur = db.stamp()
            except Exception:
                cur = last
            if cur != last:
                last = cur
                yield "event: corpus\ndata: %s\n\n" % json.dumps({"stamp": list(cur or ())})
            beat += 1
            if beat % 10 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/health", tags=["run"])
def health():
    return {"ok": True, "mode": MODE, "run_dir": common.RUN_DIR,
            "stamp": list(db.stamp())}


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    index = os.path.join(UI_DIST, "index.html")
    if not os.path.isfile(index):
        return {"error": "the client is not built",
                "fix": "cd ui && npm install && npm run build",
                "expected": UI_DIST}
    candidate = os.path.normpath(os.path.join(UI_DIST, full_path))
    if (full_path and os.path.isfile(candidate)
            and os.path.commonpath([os.path.abspath(candidate),
                                    os.path.abspath(UI_DIST)]) == os.path.abspath(UI_DIST)):
        return FileResponse(candidate)
    return FileResponse(index)


def main():
    import uvicorn
    common.install_stamped_logs()
    plain = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(plain[0]) if plain else 8777
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
