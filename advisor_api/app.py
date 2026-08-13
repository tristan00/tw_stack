"""The dashboard's HTTP surface: a typed JSON API, a change stream, and the built client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

import common
from advisor_api import db, ident, proc, queries as q
from advisor_api.models import (
    ActionsPage, AgreementBreakdownPage, AgreementPage, AgreementSeriesPage, AnalyticsPage,
    CampaignDetail, CampaignsPage, ControlResult, Count,
    DecisionDetail, DecisionsPage, ForcingPage, InfraPage, LaunchDefaults, MatrixCell,
    MatrixPage, MatrixRow, MatrixTotal, MenusPage, ModelsPage, Rate, RunPage, Scope,
    StartsPage, TimelinePage, TrainingPage, CorrelationsPage,
)

UI_DIST = os.path.join(common.ROOT, "ui", "dist")


@contextlib.asynccontextmanager
async def _lifespan(_app):
    # The process probe runs for the life of the server, not per request -- see proc.py.
    proc.start()
    try:
        yield
    finally:
        proc.stop()


app = FastAPI(title="advisor", version="8", lifespan=_lifespan,
              description="Telemetry for the Total War campaign-advisor harness.")


def _con():
    return db.connect()


def _scope(text, detail=None) -> Scope:
    return Scope(text=text, detail=detail)


# ----------------------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------------------

@app.get("/api/run", response_model=RunPage, tags=["run"])
def get_run() -> RunPage:
    con = _con()
    tail, log_path = q.session_log_tail()
    return RunPage(
        scope=_scope("this run dir", common.RUN_DIR),
        services=proc.services(),
        current=q.current(con),
        throughput=q.throughput(con),
        totals=q.totals(con),
        collect_timing=q.collect_timing(con),
        cycle_timing=q.cycle_timing(con),
        log_tail=tail,
        log_name=os.path.basename(log_path) if log_path else None)


# ----------------------------------------------------------------------------------------
# campaigns
# ----------------------------------------------------------------------------------------

@app.get("/api/campaigns/starts", response_model=StartsPage, tags=["campaigns"])
def get_starts() -> StartsPage:
    con = _con()
    rows = q.starts_rows(con)
    low = sum(1 for r in rows if r.single_sample)
    return StartsPage(
        scope=_scope("one row per playable start, most-played first",
                     "peaks are the best value that start ever reached across its campaigns"),
        low_sample=Count(value=low, noun="starts",
                         population="with two or fewer campaigns, where an average is one "
                                    "or two observations"),
        rows=rows)


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
    # Worst first. This ordering IS the finding: aggregated across every faction one
    # action type confirms far below the rest, and in an alphabetical grid of a thousand
    # cells that fact has nowhere to appear.
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
    rows = q.campaign_rows(con)
    return CampaignsPage(
        scope=_scope("every campaign in this run dir, newest first",
                     "outcome is joined from the postmortem log"),
        headline=q.outcome_headline(con),
        suspicious=Count(value=sum(1 for r in rows if r.suspicious), noun="campaigns",
                         population="whose ending looks like a harness fault, not a defeat"),
        unjoined=Count(value=q.unjoined_endings(con), noun="endings",
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
    row = next((r for r in q.campaign_rows(con) if r.campaign.raw == campaign_key), None)
    if row is None:
        raise HTTPException(404, "no campaign %r in this run dir" % campaign_key)
    reward, constant = q.reward_series(con, campaign_key)
    decisions, _ = q.decisions_page(con, 0, 100, campaign=campaign_key)
    return CampaignDetail(
        scope=_scope("one campaign", ident.campaign(campaign_key)["label"]),
        row=row, reward=reward, constant_columns=constant,
        diplomacy=q.diplomacy_tail(con, campaign_key), decisions=decisions)


# ----------------------------------------------------------------------------------------
# decisions
# ----------------------------------------------------------------------------------------

@app.get("/api/decisions/actions", response_model=ActionsPage, tags=["decisions"])
def get_actions() -> ActionsPage:
    con = _con()
    tiles, by_type, policies, denominators = q.actions_summary(con)
    return ActionsPage(
        scope=_scope("every action attempted in this run dir",
                     "confirm rate is the game agreeing the action happened"),
        tiles=tiles, by_type=by_type, policies=policies, denominators=denominators)


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
                     "filters narrow the population; the count follows the filter"),
        total=Count(value=total, noun="actions",
                    population="matching the current filter in this run dir"),
        offset=offset, limit=limit, rows=rows,
        action_types=facets["action_types"], policies=facets["policies"],
        results=facets["results"])


# ----------------------------------------------------------------------------------------
# models
# ----------------------------------------------------------------------------------------

@app.get("/api/models/forcing", response_model=ForcingPage, tags=["models"])
def get_forcing() -> ForcingPage:
    con = _con()
    tiles, n = q.forcing(con)
    return ForcingPage(
        scope=_scope("the action-type mix each model arm actually picked",
                     "bars carry a 95% interval, so a small sample looks small"),
        decisions=n, tiles=tiles,
        empty_reason=(None if tiles else
                      "no decision in this run dir was drawn by a model arm yet -- the "
                      "strategy mix has only produced random and ruleset picks so far"))


@app.get("/api/models/agreement", response_model=AgreementPage, tags=["models"])
def get_agreement() -> AgreementPage:
    return q.agreement_page()


@app.get("/api/models/agreement/series", response_model=AgreementSeriesPage,
         tags=["models"])
def get_agreement_series(axis: str = "window") -> AgreementSeriesPage:
    """How agreement has tracked over the run, or by model generation."""
    return q.agreement_series(axis)


@app.get("/api/models/agreement/breakdown", response_model=AgreementBreakdownPage,
         tags=["models"])
def get_agreement_breakdown(dim: str = "action_type") -> AgreementBreakdownPage:
    return q.agreement_breakdown(dim)


@app.get("/api/analytics", response_model=AnalyticsPage, tags=["infra"])
def get_analytics() -> AnalyticsPage:
    """What the analytics service has precomputed, and how far behind it is."""
    return q.analytics_status()


@app.post("/api/analytics/rebuild", response_model=ControlResult, tags=["infra"])
def post_analytics_rebuild() -> ControlResult:
    """Ask the analytics service to rebuild from scratch."""
    return ControlResult(ok=True, steps=proc.rebuild_analytics())


@app.get("/api/models/correlations", response_model=CorrelationsPage, tags=["models"])
def get_correlations() -> CorrelationsPage:
    con = _con()
    return CorrelationsPage(
        scope=_scope("does an arm's share of a turn track how the campaign went",
                     "correlated per turn inside a campaign, not per campaign"),
        tiles=q.correlations(con))


@app.get("/api/models/training", response_model=TrainingPage, tags=["models"])
def get_training() -> TrainingPage:
    history = q.training_history()
    # Group order is decided here, once, so the client renders whatever groups exist
    # without hard-coding a column list that drifts from the data.
    seen: list = []
    for ev in history:
        for name in ev.groups:
            if name not in seen:
                seen.append(name)
    return TrainingPage(
        scope=_scope("every trial and every retrain, newest first",
                     "trials come from the experiment ledger; retrains from the session "
                     "reports"),
        trials=q.trials(_con()), history=history, group_order=seen)


@app.get("/api/models", response_model=ModelsPage, tags=["models"])
def get_models() -> ModelsPage:
    return ModelsPage(scope=_scope("the models on disk right now", common.MODELS),
                      cards=q.model_cards(), fit=q.fit_config())


# ----------------------------------------------------------------------------------------
# infra
# ----------------------------------------------------------------------------------------

@app.get("/api/infra", response_model=InfraPage, tags=["infra"])
def get_infra() -> InfraPage:
    tail, _ = q.session_log_tail(14)
    return InfraPage(
        scope=_scope("services, streams and controls", common.RUN_DIR),
        services=proc.services(), activity=q.activity(),
        policy_note="Picks are drawn from the run's strategy mix; the mix is recorded on "
                    "the trial and shown on the models view.",
        models=["catboost", "gnn"],
        defaults=LaunchDefaults(),
        cold_defaults=LaunchDefaults(campaigns=10, turns_min=2, turns_max=40,
                                     retrain_first=False),
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


# ----------------------------------------------------------------------------------------
# change stream
# ----------------------------------------------------------------------------------------

@app.get("/api/events", tags=["run"])
async def events():
    """Server-sent events: one message whenever the corpus gains rows."""
    async def gen():
        last = None
        beat = 0
        while True:
            try:
                cur = db.stamp()
            except Exception:                                   # noqa: BLE001
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
    return {"ok": True, "run_dir": common.RUN_DIR, "stamp": list(db.stamp())}


# ----------------------------------------------------------------------------------------
# the built client
# ----------------------------------------------------------------------------------------

@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    """Serve the built client."""
    index = os.path.join(UI_DIST, "index.html")
    if not os.path.isfile(index):
        return {"error": "the client is not built",
                "fix": "cd ui && npm install && npm run build",
                "expected": UI_DIST}
    # Only ever serve files from inside the build directory: a path that escapes it after
    # normalisation is a traversal attempt, not a client route.
    candidate = os.path.normpath(os.path.join(UI_DIST, full_path))
    if (full_path and os.path.isfile(candidate)
            and os.path.commonpath([os.path.abspath(candidate),
                                    os.path.abspath(UI_DIST)]) == os.path.abspath(UI_DIST)):
        return FileResponse(candidate)
    return FileResponse(index)


def main():
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
