"""Every query the dashboard can ask, with its cost bounded and its population named."""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms
import campaign_growth as CG
import common
import metrics_db
from advisor_api import analytics_db as adb
from advisor_api import db, ident
from advisor_api.models import (
    ActionTypeRow, ActivityRow, AgreementPage, AgreementRankRow, AgreementSummary,
    ArmCoverage, CampaignRow, Count, CorrelationRow, CorrelationTile, Current,
    DecisionRow, DiploEvent, EntityState, ForcingBar, ForcingTile, Ident, InterruptOption,
    InterruptRow, Metric, ModelCard, OfferRow, OutcomeTally, PhaseSpan, PolicyRow, Rate,
    RewardPoint, Scope, Service, StartRow, TimelineAction, TimelineLane,
    TimingRow, TrainingEvent, TrialRow,
)

DECISIONS_PAGE = 50
TIMELINE_DECISIONS = 200
MENUS_ROWS = 60
DIPLO_TAIL = 600
REWARD_CAMPAIGNS = 10

# Outcomes are not all failures, and colouring them as if they were misreports the run.
# A campaign the harness abandoned on the growth bar is a tuning signal; a campaign that
# lost a war is the game working. Only harness faults are red.
_OUTCOME_STATE = {
    "error": "bad",
    "stuck": "bad",
    "unhandled_screen": "bad",
    "stagnant": "warn",
    "defeated": "neutral",
    "no_ending_recorded": "warn",
}

# A campaign with no postmortem is only RUNNING if it is still deciding. `campaigns.outcome`
# is written when a session records an ending, so a session killed mid-campaign -- runctl
# down, a babysitter relaunch, a crash -- leaves that campaign with no outcome forever and
# nothing reconciles it. Measured: 29 campaigns with no outcome, 28 of them with no decision
# for over ten minutes and the oldest 12.2 hours, all rendering as "running".
#
# Silence is not an outcome, so this does not invent one. It reports that no ending was
# recorded, which is the true statement, and leaves the genuinely live campaign alone.
LIVE_WINDOW_S = 600.0

# A trial's row is rewritten once per campaign, and a campaign runs a few minutes, so this
# is deliberately looser than the per-campaign window above.
TRIAL_LIVE_WINDOW_S = 1200.0


def _ended_because(pm: dict) -> str | None:
    """Why the RUN stopped this campaign. NOT a growth measurement."""
    g = pm.get("growth") or {}
    outcome = str(pm.get("outcome") or "")
    if g.get("reason") == "legendary_lord_wounded":
        return ("growth gate: legendary lord wounded at turn %s -- an automatic stop that "
                "measures no growth at all" % g.get("turn"))
    mets = g.get("metrics") or {}
    if mets:
        parts = "; ".join(
            "%s %g -> %g over %s turns"
            % (m.get("label"), _f(m.get("then"), 0.0), _f(m.get("now"), 0.0),
               m.get("window"))
            for m in mets.values() if m.get("then") is not None)
        if parts:
            return ("growth gate at turn %s: %s -- needed +%s on either"
                    % (g.get("turn"), parts, g.get("min_gain")))
    if outcome == "defeated":
        return "the faction was destroyed"
    if outcome in ("stuck", "error", "unhandled_screen"):
        return str(pm.get("error") or outcome)[:200]
    return None


def _i(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _f(v, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) or math.isinf(f) else f


def _jload(v):
    """Parse a stored JSON column into a container, never into None."""
    if not v:
        return {}
    if isinstance(v, (dict, list)):
        return v
    try:
        parsed = json.loads(v)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, (dict, list)) else {}


def _text(v):
    """Whatever a stream wrote, as a string a cell can hold."""
    if v is None or v == "":
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) or None
    if isinstance(v, dict):
        return v.get("name") or v.get("key") or json.dumps(v, sort_keys=True)
    return str(v)


def _id(d: dict) -> Ident:
    return Ident(**d)


def _phrase(v) -> Ident | None:
    return _id(ident.phrase(v)) if v not in (None, "") else None


def _camp(v) -> Ident:
    return _id(ident.campaign(v))


def _fac(v) -> Ident:
    return _id(ident.faction(v))


def _by_arm(rows) -> list:
    """Fold a (policy, count) tally onto strategies. [(arm, picks, fell_back)], biggest first."""
    agg: dict = {}
    for r in rows:
        raw = r["p"]
        arm = arms.arm_of(raw) or arms.UNRECORDED
        n = _i(r["n"], 0) or 0
        slot = agg.setdefault(arm, [0, 0])
        slot[0] += n
        if arms.fell_back(raw):
            slot[1] += n
    return sorted(((a, v[0], v[1]) for a, v in agg.items()), key=lambda t: -t[1])


def _clock(ts):
    """An epoch stamp as a local wall-clock string, or None."""
    t = _f(ts)
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else None


def _age_words(seconds):
    if seconds is None:
        return None
    s = int(seconds)
    if s < 90:
        return "%ds ago" % s
    if s < 5400:
        return "%dm ago" % (s // 60)
    if s < 172800:
        return "%dh ago" % (s // 3600)
    return "%dd ago" % (s // 86400)


# ----------------------------------------------------------------------------------------
# file-backed sources
# ----------------------------------------------------------------------------------------

@db.cached
def postmortems(con) -> list:
    """Every recorded campaign ending, newest last."""
    out = []
    for payload, key in con.execute(
            "SELECT payload,campaign_key FROM postmortems ORDER BY postmortem_id"):
        d = _jload(payload) or {}
        if key and not d.get("campaign_key"):
            d["campaign_key"] = key
        out.append(d)
    return out


@db.cached
def join_outcomes(con) -> dict:
    """campaign_key -> the postmortem that ended it."""
    claimed = {}
    for pm in postmortems(con):
        key = pm.get("campaign_key")
        if key:
            claimed[key] = pm
    return claimed


@db.cached
def unjoined_endings(con) -> int:
    """Endings naming no campaign this corpus holds."""
    keys = {r[0] for r in con.execute("SELECT campaign_key FROM campaigns")}
    return sum(1 for pm in postmortems(con)
               if not pm.get("campaign_key") or pm["campaign_key"] not in keys)


# ----------------------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------------------

@db.cached
def current(con) -> Current:
    """The campaign being played right now, resolved once."""
    row = con.execute(
        "SELECT campaign_id, turn, settlements, power_rank, lord_level, ts"
        " FROM target_rows ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return Current()
    return Current(campaign=_camp(row["campaign_id"]), turn=_i(row["turn"]),
                   settlements=_f(row["settlements"]), power_rank=_f(row["power_rank"]),
                   lord_level=_f(row["lord_level"]),
                   age_seconds=max(0.0, time.time() - (_f(row["ts"]) or 0.0)))


@db.cached
def totals(con) -> list:
    """The corpus totals, each naming the population it counted."""
    q = lambda s: con.execute(s).fetchone()[0] or 0
    return [
        Count(value=q("SELECT COUNT(DISTINCT campaign_id) FROM decision_points"),
              noun="campaigns", population="with a recorded decision in this run dir"),
        Count(value=q("SELECT COUNT(*) FROM decision_points"),
              noun="decisions", population="recorded in this run dir"),
        Count(value=q("SELECT COUNT(*) FROM action_offers"),
              noun="offers", population="scored across those decisions"),
        Count(value=q("SELECT COUNT(*) FROM action_taken WHERE counted=1"),
              noun="actions", population="confirmed by the game"),
    ]


@db.cached
def throughput(con) -> list:
    """Campaign and turn rates over the recent window, with the window stated."""
    rows = con.execute(
        "SELECT ts, campaign_id, turn FROM decision_points"
        " ORDER BY decision_id DESC LIMIT 4000").fetchall()
    out = []
    if not rows:
        return out
    span_h = max(1e-6, ((_f(rows[0]["ts"]) or 0) - (_f(rows[-1]["ts"]) or 0)) / 3600.0)
    camps = len({r["campaign_id"] for r in rows})
    turns = len({(r["campaign_id"], r["turn"]) for r in rows})
    taken = con.execute(
        "SELECT COUNT(*) a,"
        " SUM(CASE WHEN counted=1 THEN 1 ELSE 0 END) c"
        " FROM action_taken WHERE refusal IS NOT 'awaiting_execution'").fetchone()
    attempted, confirmed = _i(taken["a"], 0) or 0, _i(taken["c"], 0) or 0
    pct = (100.0 * confirmed / attempted) if attempted else None
    camp_spark, turn_spark = _rate_sparks(rows)
    out.append(Metric(label="campaigns/hr", value=round(camps / span_h, 1),
                      sub="over the last %d decisions" % len(rows), spark=camp_spark))
    out.append(Metric(label="turns/hr", value=round(turns / span_h, 1),
                      sub="over the last %d decisions" % len(rows), spark=turn_spark))
    out.append(Metric(label="confirm rate", value=(round(pct, 1) if pct is not None else None),
                      unit="%", sub="%d of %d attempted actions" % (confirmed, attempted),
                      spark=_confirm_spark(con)))
    return out


SPARK_BUCKETS = 16


def _rate_sparks(rows, buckets=SPARK_BUCKETS):
    """Distinct campaigns and campaign-turns per equal-width time bucket, oldest first."""
    stamps = [(_f(r["ts"]) or 0.0, r["campaign_id"], r["turn"]) for r in rows]
    stamps = [s for s in stamps if s[0]]
    if len(stamps) < buckets:
        return [], []
    lo, hi = min(s[0] for s in stamps), max(s[0] for s in stamps)
    width = (hi - lo) / buckets or 1.0
    camp_sets = [set() for _ in range(buckets)]
    turn_sets = [set() for _ in range(buckets)]
    for ts, cid, turn in stamps:
        i = min(buckets - 1, int((ts - lo) / width))
        camp_sets[i].add(cid)
        turn_sets[i].add((cid, turn))
    per_hour = 3600.0 / width
    return ([round(len(s) * per_hour, 2) for s in camp_sets],
            [round(len(s) * per_hour, 2) for s in turn_sets])


@db.cached
def _confirm_spark(con, buckets=SPARK_BUCKETS):
    """Confirm rate per equal-count bucket over the recent window, oldest first."""
    rows = con.execute(
        "SELECT counted, refusal FROM action_taken"
        " ORDER BY decision_id DESC LIMIT 2000").fetchall()
    rows = [r for r in rows if r["refusal"] != "awaiting_execution"]
    if len(rows) < buckets:
        return []
    rows.reverse()
    size = len(rows) // buckets or 1
    out = []
    for i in range(buckets):
        chunk = rows[i * size:(i + 1) * size]
        if not chunk:
            continue
        ok = sum(1 for r in chunk if _i(r["counted"], 0))
        out.append(round(100.0 * ok / len(chunk), 1))
    return out


@db.cached
def collect_timing(con) -> list:
    """Median ms per recorder stage over the recent window."""
    rows = con.execute("SELECT timings FROM decision_points"
                       " ORDER BY decision_id DESC LIMIT 400").fetchall()
    buckets = {}
    for r in rows:
        t = _jload(r["timings"])
        for k in ("collect_ms", "roundtrip_ms", "score_ms", "store_ms", "trace_ms",
                  "housekeep_ms", "pickup_lag_ms"):
            v = _f(t.get(k))
            if v is not None:
                buckets.setdefault(k, []).append(v)
    label = {"collect_ms": "recorder collect", "roundtrip_ms": "request round trip",
             "score_ms": "featurize + rank", "store_ms": "sqlite store",
             "trace_ms": "trace write", "housekeep_ms": "housekeeping",
             "pickup_lag_ms": "recorder pickup"}
    out = []
    for k, name in label.items():
        vals = buckets.get(k) or []
        if not vals:
            continue
        med, mx = statistics.median(vals), max(vals)
        out.append(TimingRow(stage=name, median_ms=round(med, 1), max_ms=round(mx, 1),
                             state="bad" if med > 3000 else ("warn" if med > 800 else "ok")))
    out.sort(key=lambda r: -(r.median_ms or 0))
    return out


@db.cached
def cycle_timing(con) -> list:
    """Median ms per execution stage over the recent window."""
    rows = con.execute("SELECT timing FROM action_taken"
                       " ORDER BY decision_id DESC LIMIT 400").fetchall()
    buckets = {}
    for r in rows:
        t = _jload(r["timing"])
        for k in ("snapshot_ms", "execute_ms", "confirm_ms", "gates_ms", "total_ms"):
            v = _f(t.get(k))
            if v is not None:
                buckets.setdefault(k, []).append(v)
    label = {"snapshot_ms": "snapshot", "execute_ms": "execute", "confirm_ms": "confirm",
             "gates_ms": "gates", "total_ms": "total"}
    out = []
    for k, name in label.items():
        vals = buckets.get(k) or []
        if not vals:
            continue
        med, mx = statistics.median(vals), max(vals)
        out.append(TimingRow(stage=name, median_ms=round(med, 1), max_ms=round(mx, 1),
                             state="bad" if med > 3000 else ("warn" if med > 800 else "ok")))
    out.sort(key=lambda r: -(r.median_ms or 0))
    return out


# ----------------------------------------------------------------------------------------
# campaigns
# ----------------------------------------------------------------------------------------

@db.cached
def campaign_rows(con) -> list:
    """One row per campaign: its decisions, its trajectory, and its outcome."""
    decs = {r["ckey"]: r for r in con.execute(
        "SELECT campaign_id ckey, COUNT(*) n, MIN(ts) t0, MAX(ts) t1, MAX(turn) last_turn"
        " FROM decision_points GROUP BY campaign_id")}
    acts = {r["ckey"]: r for r in con.execute(
        "SELECT dp.campaign_id ckey,"
        "       COUNT(*) rows_,"
        "       SUM(CASE WHEN at.refusal IS 'awaiting_execution' THEN 0 ELSE 1 END) attempted,"
        "       SUM(CASE WHEN at.counted=1 THEN 1 ELSE 0 END) confirmed"
        " FROM action_taken at JOIN decision_points dp ON dp.decision_id = at.decision_id"
        " GROUP BY dp.campaign_id")}
    growth = {g["campaign_key"]: CG.enrich(g)
              for g in adb.rows("SELECT * FROM campaign_growth")}
    meta = {r["campaign_key"]: r for r in con.execute(
        "SELECT campaign_id, campaign_key, faction, turns, campaign_map FROM campaigns")}

    outcomes = join_outcomes(con)
    out = []
    for ckey, d in decs.items():
        m = meta.get(ckey)
        a = acts.get(ckey)
        g = growth.get(ckey) or {}
        attempted = _i(a["attempted"], 0) if a else 0
        confirmed = _i(a["confirmed"], 0) if a else 0
        rows_ = _i(a["rows_"], 0) if a else 0
        n_dec = _i(d["n"], 0) or 0
        row = CampaignRow(
            campaign_id=_i(m["campaign_id"], 0) if m else 0,
            campaign=_camp(ckey),
            campaign_map=_id(ident.campaign_map(m["campaign_map"] if m else None)),
            turns=_i(d["last_turn"], _i(m["turns"]) if m else None),
            decisions=n_dec,
            # A decision point that produced no action row at all is not a failed action;
            # it is a decision where nothing was offered or nothing was chosen.
            no_action=max(0, n_dec - rows_),
            attempted=attempted,
            confirmed=confirmed,
            confirm_rate=Rate(n=confirmed, of=attempted, noun="actions",
                              population="attempted in this campaign"),
            span_min=round(((_f(d["t1"]) or 0) - (_f(d["t0"]) or 0)) / 60.0, 1),
            peak_settlements=_f(g.get("peak_settlements")),
            peak_power_rank=_f(g.get("peak_power_rank")),
            peak_lord_level=_f(g.get("peak_lord_level")),
            final_settlements=_f(g.get("final_settlements")),
            final_power_rank=_f(g.get("final_power_rank")),
            final_income=_f(g.get("final_income")),
            turn_rows=_i(g.get("turn_rows"), 0) or 0,
            first_turn=_i(g.get("first_turn")),
            last_measured_turn=_i(g.get("last_measured_turn")),
            growth_span_turns=_i(g.get("growth_span_turns")),
            first_settlements=_f(g.get("first_settlements")),
            first_lord_level=_f(g.get("first_lord_level")),
            final_lord_level=_f(g.get("final_lord_level")),
            settlements_growth=_f(g.get("settlements_growth")),
            lord_growth=_f(g.get("lord_growth")),
            settlements_per_turn=_f(g.get("settlements_per_turn")),
            lord_per_turn=_f(g.get("lord_per_turn")),
            growth_state=g.get("growth_state") or CG.NO_TURN_ROWS,
        )
        pm = outcomes.get(ckey)
        if pm:
            outcome = str(pm.get("outcome") or "")
            row.outcome = _phrase(outcome)
            row.outcome_state = _OUTCOME_STATE.get(outcome, "neutral")
            row.ended_when = pm.get("when")
            row.ended_because = _ended_because(pm)
            verdict = str((pm.get("plausibility") or {}).get("verdict") or "")
            row.suspicious = ("harness_failure_likely" in verdict) or ("ambiguous" in verdict)
            if row.suspicious:
                row.outcome_state = "bad"
        elif time.time() - (_f(d["t1"]) or 0.0) > LIVE_WINDOW_S:
            row.outcome = _phrase("no_ending_recorded")
            row.outcome_state = _OUTCOME_STATE["no_ending_recorded"]
            row.ended_because = ("the session stopped before this campaign ended, so no "
                                 "outcome was recorded for it")
    # newest first
        out.append(row)
    out.sort(key=lambda r: -(decs[r.campaign.raw]["t1"] or 0))
    return out


@db.cached
def outcome_headline(con) -> list:
    """The outcome spread across joined campaigns, worst first."""
    tally = {}
    for row in campaign_rows(con):
        if not row.outcome:
            continue
        tally.setdefault(row.outcome.raw, [0, row.outcome_state])
        tally[row.outcome.raw][0] += 1
    order = {"bad": 0, "warn": 1, "neutral": 2, "ok": 3}
    out = [OutcomeTally(outcome=_phrase(k), count=v[0], state=v[1]) for k, v in tally.items()]
    out.sort(key=lambda t: (order.get(t.state, 9), -t.count))
    return out


@db.cached
def starts_rows(con) -> list:
    """Per-faction start quality. One grouped query per fact, never one per campaign."""
    per = {}
    for row in campaign_rows(con):
        fkey, _ = ident.split_campaign_key(row.campaign.raw)
        b = per.setdefault(fkey, {"n": 0, "turns": [], "sett": [], "rank": [], "lord": [],
                                  "att": 0, "conf": 0})
        b["n"] += 1
        if row.turns is not None:
            b["turns"].append(row.turns)
        for k, v in (("sett", row.peak_settlements), ("rank", row.peak_power_rank),
                     ("lord", row.peak_lord_level)):
            if v is not None:
                b[k].append(v)
        b["att"] += row.attempted
        b["conf"] += row.confirmed

    allied = {r["ckey"]: r["n"] for r in con.execute(
        "SELECT campaign_id ckey, SUM(CASE WHEN allies>0 THEN 1 ELSE 0 END) n"
        " FROM target_rows GROUP BY campaign_id")}
    vassal = {r["ckey"]: r["n"] for r in con.execute(
        "SELECT campaign_id ckey, SUM(CASE WHEN vassals>0 THEN 1 ELSE 0 END) n"
        " FROM target_rows GROUP BY campaign_id")}
    ever_a, ever_v = {}, {}
    for row in campaign_rows(con):
        fkey, _ = ident.split_campaign_key(row.campaign.raw)
        ever_a[fkey] = ever_a.get(fkey, 0) + (1 if (allied.get(row.campaign.raw) or 0) else 0)
        ever_v[fkey] = ever_v.get(fkey, 0) + (1 if (vassal.get(row.campaign.raw) or 0) else 0)

    out = []
    for fkey, b in per.items():
        out.append(StartRow(
            faction=_fac(fkey), n=b["n"], single_sample=b["n"] <= 2,
            avg_turns=round(sum(b["turns"]) / len(b["turns"]), 1) if b["turns"] else None,
            best_turns=max(b["turns"]) if b["turns"] else None,
            best_settlements=max(b["sett"]) if b["sett"] else None,
            # power rank counts downwards, so the best value is the smallest one.
            best_power_rank=min(b["rank"]) if b["rank"] else None,
            best_lord_level=max(b["lord"]) if b["lord"] else None,
            ever_allied=ever_a.get(fkey, 0), ever_vassal=ever_v.get(fkey, 0),
            confirm_rate=Rate(n=b["conf"], of=b["att"], noun="actions",
                              population="attempted across this start's campaigns")))
    out.sort(key=lambda r: (-r.n, r.faction.label))
    return out


@db.cached
def matrix(con, kind: str = "action"):
    """faction x action-type crosstab, and the totals row that makes it readable."""
    if kind == "interrupt":
        sql = ("SELECT c.faction faction, i.kind atype,"
               "       COUNT(*) tried,"
               "       SUM(CASE WHEN i.counted=1 THEN 1 ELSE 0 END) ok,"
               "       SUM(COALESCE(i.latency_ms,0)) ms"
               " FROM interrupt_decisions i"
               " JOIN campaigns c ON c.campaign_key = i.campaign_id"
               " GROUP BY c.faction, i.kind")
    else:
        sql = ("SELECT c.faction faction, at.action_type atype,"
               "       SUM(CASE WHEN at.refusal IS 'awaiting_execution' THEN 0 ELSE 1 END) tried,"
               "       SUM(CASE WHEN at.counted=1 THEN 1 ELSE 0 END) ok,"
               "       SUM(COALESCE(at.latency_ms,0)) ms"
               " FROM action_taken at"
               " JOIN decision_points dp ON dp.decision_id = at.decision_id"
               " JOIN campaigns c ON c.campaign_key = dp.campaign_id"
               " GROUP BY c.faction, at.action_type")
    grid, totals_ = {}, {}
    for r in con.execute(sql):
        f, a = r["faction"], r["atype"]
        tried, ok, ms = _i(r["tried"], 0) or 0, _i(r["ok"], 0) or 0, _f(r["ms"], 0.0) or 0.0
        if not a:
            continue
        grid.setdefault(f, {})[a] = (tried, ok, ms)
        t = totals_.setdefault(a, [0, 0, 0.0])
        t[0] += tried
        t[1] += ok
        t[2] += ms
    return grid, totals_


# ----------------------------------------------------------------------------------------
# decisions
# ----------------------------------------------------------------------------------------

def _options_of(options_json) -> list:
    """The options on a blocking screen, as (label, payload) pairs."""
    opts = _jload(options_json)
    if isinstance(opts, dict):
        return [(k, v) for k, v in opts.items() if isinstance(v, dict)]
    if isinstance(opts, list):
        out = []
        for o in opts:
            if isinstance(o, dict):
                out.append((str(o.get("label") or o.get("key") or o.get("option") or ""), o))
        return out
    return []


def _result_of(row) -> tuple:
    refusal = row["refusal"] if "refusal" in row.keys() else None
    if refusal == "awaiting_execution":
        return "awaiting execution", "neutral"
    if _i(row["counted"], 0):
        return "confirmed", "ok"
    if refusal:
        return "refused", "bad"
    if _i(row["executed"], 0):
        return "executed, unconfirmed", "warn"
    return "no action", "neutral"


def decisions_page(con, offset=0, limit=DECISIONS_PAGE, action_type=None, policy=None,
                   result=None, campaign=None, q=None):
    """The decision log, one page at a time."""
    where, args = [], []
    if action_type:
        where.append("at.action_type = ?")
        args.append(action_type)
    if policy:
        raw = [r[0] for r in con.execute(
            "SELECT DISTINCT policy FROM action_taken WHERE policy IS NOT NULL")
            if arms.arm_of(r[0]) == policy]
        if not raw:
            raw = [policy]
        where.append("COALESCE(at.policy, dp.policy) IN (%s)" % ",".join("?" * len(raw)))
        args += raw
    if campaign:
        where.append("dp.campaign_id = ?")
        args.append(campaign)
    if q:
        where.append("(at.action_key LIKE ? OR dp.campaign_id LIKE ?)")
        args += ["%%%s%%" % q, "%%%s%%" % q]
    if result == "confirmed":
        where.append("at.counted = 1")
    elif result == "refused":
        where.append("at.refusal IS NOT NULL AND at.refusal IS NOT 'awaiting_execution'")
    elif result == "awaiting":
        where.append("at.refusal = 'awaiting_execution'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = _i(con.execute(
        "SELECT COUNT(*) FROM action_taken at"
        " JOIN decision_points dp ON dp.decision_id = at.decision_id" + clause,
        args).fetchone()[0], 0) or 0

    rows = con.execute(
        "SELECT at.decision_id, at.ts, at.context_kind, at.context_id, at.action_type,"
        "       at.action_key, at.executed, at.confirmed, at.counted, at.refusal,"
        "       at.latency_ms, at.offer_id, COALESCE(at.policy, dp.policy) policy,"
        "       dp.campaign_id, dp.turn, dp.n_offers"
        " FROM action_taken at"
        " JOIN decision_points dp ON dp.decision_id = at.decision_id" + clause +
        " ORDER BY at.decision_id DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()

    offer_by_id = {}
    ids = [r["offer_id"] for r in rows if r["offer_id"] is not None]
    if ids:
        marks = ",".join("?" * len(ids))
        for o in con.execute(
                "SELECT offer_id, exploit, pct_global, pct_local, rank, gnn_impact, gnn_rank"
                " FROM action_offers WHERE offer_id IN (%s)" % marks, ids):
            offer_by_id[o["offer_id"]] = o

    rho_by_id = rho_for([r["decision_id"] for r in rows])

    out = []
    for r in rows:
        res, state = _result_of(r)
        o = offer_by_id.get(r["offer_id"])
        rho, rho_n = rho_by_id.get(_i(r["decision_id"]), (None, None))
        gnn_rank = _i(o["gnn_rank"]) if o else None
        cat_rank = _i(o["rank"]) if o else None
        n_off = _i(r["n_offers"])
        delta = None
        if gnn_rank and cat_rank and n_off and n_off > 1:
            # Percentile, so decisions with different offer counts are comparable.
            # gnn minus catboost: positive means the graph model rated the taken action
            # higher than the tree did.
            delta = round(100.0 * (cat_rank - gnn_rank) / (n_off - 1), 1)
        out.append(DecisionRow(
            decision_id=_i(r["decision_id"], 0) or 0, ts=_f(r["ts"]),
            campaign=_camp(r["campaign_id"]), turn=_i(r["turn"]), offers=n_off,
            entity="%s %s" % (r["context_kind"] or "", r["context_id"] or ""),
            action_type=_phrase(r["action_type"]), action_key=r["action_key"],
            result=_phrase(res), result_state=state, refusal=_phrase(r["refusal"]),
            policy=_phrase(r["policy"]),
            exploit=_f(o["exploit"]) if o else None,
            pct_global=_f(o["pct_global"]) if o else None,
            pct_local=_f(o["pct_local"]) if o else None,
            cat_rank=cat_rank, gnn_impact=_f(o["gnn_impact"]) if o else None,
            gnn_rank=gnn_rank, delta_pct=delta, rho=rho, rho_n=rho_n,
            latency_ms=_f(r["latency_ms"])))
    return out, total


@db.cached
def decision_facets(con) -> dict:
    """The distinct values worth filtering by, so the client never invents a filter."""
    at = [r[0] for r in con.execute(
        "SELECT DISTINCT action_type FROM action_taken WHERE action_type IS NOT NULL"
        " ORDER BY action_type")]
    po = sorted({arms.arm_of(r[0]) for r in con.execute(
        "SELECT DISTINCT policy FROM action_taken WHERE policy IS NOT NULL")} - {None})
    return {"action_types": [_phrase(a) for a in at],
            "policies": [_phrase(p) for p in po],
            "results": [_phrase(x) for x in ("confirmed", "refused", "awaiting")]}


def decision_detail(con, decision_id: int):
    """One decision, in full. Every query is keyed on the id, so cost is flat."""
    row = con.execute(
        "SELECT at.decision_id, at.ts, at.context_kind, at.context_id, at.action_type,"
        "       at.action_key, at.executed, at.confirmed, at.counted, at.refusal,"
        "       at.latency_ms, at.offer_id, at.timing, COALESCE(at.policy, dp.policy) policy,"
        "       dp.campaign_id, dp.turn, dp.n_offers, dp.timings"
        " FROM action_taken at JOIN decision_points dp ON dp.decision_id = at.decision_id"
        " WHERE at.decision_id = ?", (decision_id,)).fetchone()
    if not row:
        row = con.execute(
            "SELECT decision_id, ts, NULL context_kind, NULL context_id, NULL action_type,"
            "       NULL action_key, 0 executed, 0 confirmed, 0 counted, NULL refusal,"
            "       NULL latency_ms, NULL offer_id, NULL timing, policy,"
            "       campaign_id, turn, n_offers, timings"
            " FROM decision_points WHERE decision_id = ?", (decision_id,)).fetchone()
    if not row:
        return None
    res, state = _result_of(row)
    head = DecisionRow(
        decision_id=_i(row["decision_id"], 0) or 0, ts=_f(row["ts"]),
        campaign=_camp(row["campaign_id"]), turn=_i(row["turn"]),
        offers=_i(row["n_offers"]),
        entity="%s %s" % (row["context_kind"] or "", row["context_id"] or ""),
        action_type=_phrase(row["action_type"]), action_key=row["action_key"],
        result=_phrase(res), result_state=state, refusal=_phrase(row["refusal"]),
        policy=_phrase(row["policy"]), latency_ms=_f(row["latency_ms"]))

    taken_offer = row["offer_id"]
    offers = []
    for o in con.execute(
            "SELECT offer_id, context_kind, context_id, action_type, action_key, exploit,"
            "       pct_global, pct_local, rank, gnn_impact, gnn_rank"
            " FROM action_offers WHERE decision_id = ?"
            " ORDER BY COALESCE(rank, 9999), offer_id", (decision_id,)):
        offers.append(OfferRow(
            rank=_i(o["rank"]), entity="%s %s" % (o["context_kind"] or "", o["context_id"] or ""),
            action_type=_phrase(o["action_type"]), action_key=o["action_key"],
            exploit=_f(o["exploit"]), pct_global=_f(o["pct_global"]),
            pct_local=_f(o["pct_local"]), gnn_impact=_f(o["gnn_impact"]),
            gnn_rank=_i(o["gnn_rank"]), taken=(o["offer_id"] == taken_offer)))

    ents = []
    for e in con.execute(
            "SELECT context_kind, context_id, features FROM entity_snapshots"
            " WHERE decision_id = ? LIMIT 40", (decision_id,)):
        ents.append(EntityState(context_kind=e["context_kind"] or "",
                                context_id=str(e["context_id"] or ""),
                                features=_jload(e["features"])))
    return head, offers, ents, _phases(row)


def _phases(row) -> list:
    """The four phases of one action, in ms."""
    t = _jload(row["timings"] if "timings" in row.keys() else None)
    a = _jload(row["timing"] if "timing" in row.keys() else None)
    collect = _f(t.get("collect_ms"), 0.0) or 0.0
    score = _f(t.get("score_ms"), 0.0) or 0.0
    round_trip = _f(t.get("roundtrip_ms"), 0.0) or 0.0
    verify = _f(a.get("total_ms"), 0.0) or 0.0
    queue = max(0.0, round_trip - collect - score)
    out = []
    for name, ms in (("collect", collect), ("queue", queue), ("score", score),
                     ("verify", verify)):
        if ms:
            out.append(PhaseSpan(phase=name, ms=round(ms, 1)))
    return out


@db.cached
def actions_summary(con):
    """Confirm rate per action type, plus the policy tally, plus every denominator named."""
    rows = []
    for r in con.execute(
            "SELECT action_type,"
            "       SUM(CASE WHEN refusal IS 'awaiting_execution' THEN 0 ELSE 1 END) tried,"
            "       SUM(CASE WHEN counted=1 THEN 1 ELSE 0 END) ok"
            " FROM action_taken WHERE action_type IS NOT NULL"
            " GROUP BY action_type"):
        tried, ok = _i(r["tried"], 0) or 0, _i(r["ok"], 0) or 0
        rate = Rate(n=ok, of=tried, noun="actions",
                    population="attempted of type %s" % r["action_type"])
        rows.append(ActionTypeRow(action_type=_phrase(r["action_type"]), rate=rate))
    rows.sort(key=lambda r: (r.rate.pct if r.rate.pct is not None else 999, -r.rate.of))

    refus = {}
    for r in con.execute(
            "SELECT action_type, refusal, COUNT(*) n FROM action_taken"
            " WHERE refusal IS NOT NULL AND refusal IS NOT 'awaiting_execution'"
            " GROUP BY action_type, refusal"):
        refus.setdefault(r["action_type"], []).append((_i(r["n"], 0) or 0, r["refusal"]))
    for row in rows:
        got = sorted(refus.get(row.action_type.raw, []), reverse=True)[:3]
        row.refusals = [_phrase(x[1]) for x in got]

    pol_rows = _by_arm(con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, COUNT(*) n FROM action_taken"
        " GROUP BY p ORDER BY n DESC").fetchall())
    drawn = sum(n for p, n, _fb in pol_rows
                if p not in ("forced_end_turn", "(unrecorded)"))
    policies = []
    for p, n, fell in pol_rows:
        note = None
        if p == "forced_end_turn":
            # Not a strategy draw: the loop ends the turn when nothing is eligible, so
            # counting it inside the mix would understate every real arm's share.
            note = "not a strategy draw -- the loop ended the turn"
        elif fell:
            note = ("%d of these were drawn but could not score, so random picked instead"
                    % fell)
        policies.append(PolicyRow(
            policy=_phrase(p), picks=n,
            share=Rate(n=n, of=drawn or 1, noun="picks",
                       population="drawn from the strategy mix"),
            note=note))

    all_rows = _i(con.execute("SELECT COUNT(*) FROM action_taken").fetchone()[0], 0) or 0
    attempted = _i(con.execute("SELECT COUNT(*) FROM action_taken"
                               " WHERE refusal IS NOT 'awaiting_execution'").fetchone()[0], 0) or 0
    confirmed = _i(con.execute("SELECT COUNT(*) FROM action_taken"
                               " WHERE counted=1").fetchone()[0], 0) or 0
    denominators = [
        Count(value=all_rows, noun="actions", population="recorded in this run dir"),
        Count(value=attempted, noun="actions",
              population="attempted (excludes awaiting execution)"),
        Count(value=drawn, noun="actions",
              population="drawn from the strategy mix (excludes the forced end turn)"),
    ]
    tiles = [
        Metric(label="confirmed", value=confirmed, sub="of %d attempted" % attempted),
        Metric(label="attempted", value=attempted, sub="of %d recorded" % all_rows),
        Metric(label="action types", value=len(rows), sub="seen in this run dir"),
    ]
    return tiles, rows, policies, denominators


# ----------------------------------------------------------------------------------------
# blocking screens
# ----------------------------------------------------------------------------------------

@db.cached
def menus(con):
    """Blocking-screen decisions, with every per-option model score as data."""
    total = _i(con.execute("SELECT COUNT(*) FROM interrupt_decisions").fetchone()[0], 0) or 0
    by_screen = [Count(value=_i(r["n"], 0) or 0, noun=str(r["kind"] or "screens"),
                       population="blocking-menu decisions of this kind")
                 for r in con.execute(
                     "SELECT kind, COUNT(*) n FROM interrupt_decisions"
                     " GROUP BY kind ORDER BY n DESC")]

    pol_rows = _by_arm(con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, COUNT(*) n FROM interrupt_decisions"
        " GROUP BY p ORDER BY n DESC").fetchall())
    tot_pol = sum(n for _p, n, _fb in pol_rows) or 1
    policies = [PolicyRow(policy=_phrase(p), picks=n,
                          share=Rate(n=n, of=tot_pol, noun="picks",
                                     population="on blocking-menu decisions"),
                          note=("%d of these were drawn but could not score, so random "
                                "picked instead" % fell) if fell else None)
                for p, n, fell in pol_rows]

    rows = []
    cover = {}
    for r in con.execute(
            "SELECT interrupt_id, ts, kind, root, campaign_id, turn, n_options, chosen,"
            "       executed, confirmed, counted, refusal, latency_ms, policy, options_json"
            " FROM interrupt_decisions ORDER BY interrupt_id DESC LIMIT ?", (MENUS_ROWS,)):
        res, state = _result_of(r)
        chosen = r["chosen"]
        options = [InterruptOption(label=_phrase(label), exploit=_f(o.get("exploit")),
                                   gnn=_f(o.get("gnn")), chosen=(str(label) == str(chosen)))
                   for label, o in _options_of(r["options_json"])[:24]]
        rows.append(InterruptRow(
            interrupt_id=_i(r["interrupt_id"], 0) or 0, ts=_f(r["ts"]),
            kind=_phrase(r["kind"]), root=r["root"], campaign=_camp(r["campaign_id"]),
            turn=_i(r["turn"]), result=_phrase(res), result_state=state,
            chosen=_phrase(chosen), n_options=_i(r["n_options"]),
            policy=_phrase(r["policy"]), latency_ms=_f(r["latency_ms"]), options=options))

    # Arm coverage: how often each screen kind was scored by each arm, from the stored
    # options rather than from a model reload -- the panel must not import a ranker.
    for r in con.execute("SELECT kind, options_json FROM interrupt_decisions"):
        b = cover.setdefault(r["kind"], {"rows": 0, "tree": 0, "graph": 0, "both": 0,
                                         "agree": 0, "cmp": 0})
        b["rows"] += 1
        opts = _options_of(r["options_json"])
        tree = [(k, o) for k, o in opts if o.get("exploit") is not None]
        graph = [(k, o) for k, o in opts if o.get("gnn") is not None]
        b["tree"] += 1 if tree else 0
        b["graph"] += 1 if graph else 0
        if tree and graph:
            b["both"] += 1
            b["cmp"] += 1
            best_t = max(tree, key=lambda kv: _f(kv[1].get("exploit"), -1e9))
            best_g = max(graph, key=lambda kv: _f(kv[1].get("gnn"), -1e9))
            if best_t[0] == best_g[0]:
                b["agree"] += 1
    coverage = [ArmCoverage(
        screen=_phrase(k), rows=v["rows"], tree_scored=v["tree"], graph_scored=v["graph"],
        both=v["both"],
        agree=Rate(n=v["agree"], of=v["cmp"], noun="screens",
                   population="scored by both arms")) for k, v in sorted(cover.items())]
    coverage.sort(key=lambda c: -c.rows)

    return (Count(value=total, noun="decisions", population="on blocking menus in this run dir"),
            by_screen, policies, coverage, rows)


# ----------------------------------------------------------------------------------------
# timeline
# ----------------------------------------------------------------------------------------

@db.cached
def timeline(con) -> list:
    """The most recent actions, grouped into (campaign, turn) lanes."""
    rows = con.execute(
        "SELECT at.decision_id, at.ts, at.action_type, at.action_key, at.executed,"
        "       at.confirmed, at.counted, at.refusal, at.timing,"
        "       dp.campaign_id, dp.turn, dp.timings"
        " FROM action_taken at JOIN decision_points dp ON dp.decision_id = at.decision_id"
        " ORDER BY at.decision_id DESC LIMIT ?", (TIMELINE_DECISIONS,)).fetchall()
    lanes: dict = {}
    prev_ts: dict = {}
    for r in reversed(rows):                       # oldest first, so gaps make sense
        key = (r["campaign_id"], _i(r["turn"], 0) or 0)
        lane = lanes.setdefault(key, {"actions": [], "ok": 0, "n": 0, "t0": None, "t1": None})
        res, state = _result_of(r)
        phases = _phases(r)
        total = sum(p.ms for p in phases)
        ts = _f(r["ts"]) or 0.0
        gap = None
        last = prev_ts.get(r["campaign_id"])
        if last is not None:
            # Blank across a campaign boundary: the elapsed time there is game teardown
            # and reload, not anything this action did.
            gap = round(max(0.0, (ts - last) * 1000.0), 1)
        prev_ts[r["campaign_id"]] = ts
        lane["actions"].append(TimelineAction(
            decision_id=_i(r["decision_id"], 0) or 0, action_type=_phrase(r["action_type"]),
            action_key=r["action_key"], result=_phrase(res), result_state=state,
            phases=phases, total_ms=round(total, 1), gap_ms=gap,
            unaccounted_ms=(round(gap - total, 1) if gap is not None else None)))
        lane["n"] += 1
        lane["ok"] += 1 if _i(r["counted"], 0) else 0
        lane["t0"] = ts if lane["t0"] is None else min(lane["t0"], ts)
        lane["t1"] = ts if lane["t1"] is None else max(lane["t1"], ts)

    out = []
    for (ckey, turn), lane in lanes.items():
        out.append(TimelineLane(
            campaign=_camp(ckey), turn=turn,
            confirmed=Rate(n=lane["ok"], of=lane["n"], noun="actions",
                           population="taken in this turn"),
            in_turn_s=round((lane["t1"] or 0) - (lane["t0"] or 0), 1),
            actions=lane["actions"]))
    out.reverse()
    return out


# ----------------------------------------------------------------------------------------
# models
# ----------------------------------------------------------------------------------------

_MODEL_DIRS = (
    ("greedy_catboost global", common.MODEL_GLOBAL, "catboost",
     "The advantage model the greedy arm ranks on: e1 predicts the return with the action, "
     "e2 the same state without it, and e1 - e2 is the advantage. Ranks every offered "
     "action across the whole faction."),
    ("greedy_catboost local", common.MODEL_LOCAL, "catboost",
     "The same advantage within one entity's own option set, so a lord's choices compete "
     "against each other rather than against the whole map. Blended into the global rank."),
    ("greedy_catboost interrupt", common.MODEL_INTERRUPT, "catboost",
     "The advantage model for blocking screens -- battles, dilemmas, occupation choices."),
    ("marwil_gnn", common.MODEL_MAPGRAPH, "mapgraph",
     "MARWIL/AWR over the graph encoder: the map and its entities as a graph, the action a "
     "node in it, trained by exponentially advantage-weighted imitation of the logged "
     "action rather than by maximising the advantage."),
    ("marwil_gnn interrupt", common.MODEL_MAPGRAPH_INTERRUPT, "mapgraph",
     "The same algorithm and encoder on blocking screens -- a different decision family, "
     "its own weights."),
)


def model_cards() -> list:
    return _model_cards()


@db.cached_files(*[os.path.join(common.native(p), "meta.json") for _n, p, _b, _r in _MODEL_DIRS])
def _model_cards() -> list:
    out = []
    for name, path, family, role in _MODEL_DIRS:
        d = common.native(path)
        meta_path = os.path.join(d, "meta.json")
        rows, note, status, trained = [], None, "missing", None
        meta = {}
        if os.path.isdir(d):
            try:
                meta = json.load(open(meta_path, encoding="utf-8"))
                status = "ready"
            except (OSError, ValueError):
                status = "incomplete"
                note = "meta.json is missing or unreadable"
        if status == "ready":
            want = ("encoder.pt", "head.pt") if family == "mapgraph" else ("e1.cbm",)
            missing = [f for f in want if not os.path.exists(os.path.join(d, f))]
            if missing:
                status, note = "incomplete", "missing on disk: %s" % ", ".join(missing)
            try:
                trained = time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(os.path.getmtime(meta_path)))
            except OSError:
                trained = None
            rows.append(("rows", "{:,}".format(_i(meta.get("rows"), 0) or 0)))
            camps = meta.get("campaigns")
            if isinstance(camps, list):
                rows.append(("campaigns", "{:,}".format(len(camps))))
            elif camps is not None:
                rows.append(("campaigns", str(camps)))
            if family == "mapgraph":
                loss = meta.get("loss") or {}
                if isinstance(loss, dict):
                    for k in ("val", "held_out", "nll"):
                        if k in loss:
                            rows.append(("held-out listwise NLL", "%.4f" % (_f(loss[k]) or 0)))
                            break
                rows.append(("graph schema", "v%s %s" % (meta.get("schema_version"),
                                                         str(meta.get("schema_hash"))[:8])))
                screens = meta.get("screens")
                if screens:
                    rows.append(("screens", str(len(screens) if isinstance(screens, list)
                                                else screens)))
                fit = meta.get("fit") or {}
                if isinstance(fit, dict) and fit:
                    rows.append(("fit", ", ".join("%s %s" % (k, v)
                                                  for k, v in list(fit.items())[:3])))
            else:
                for k, label in (("sd_global", "target spread (sd)"),
                                 ("sd_local", "target spread (sd)"),
                                 ("epsilon", "epsilon"), ("beta", "beta")):
                    if k in meta:
                        rows.append((label, "%.4g" % (_f(meta[k]) or 0)))
                screens = meta.get("screens")
                if isinstance(screens, (list, dict)):
                    rows.append(("screens covered", str(len(screens))))
        out.append(ModelCard(
            name=name, role=role, status=status,
            state={"ready": "ok", "missing": "bad", "incomplete": "bad",
                   "stale schema": "warn"}.get(status, "neutral"),
            rows=rows, note=note, trained_at=trained))
    return out


def fit_config() -> list:
    return _fit_config()


@db.cached_files(os.path.join(common.native(common.MODEL_MAPGRAPH), "meta.json"),
                 os.path.join(common.native(common.MODEL_GLOBAL), "meta.json"))
def _fit_config() -> list:
    from advisor_api.models import FitConfigRow
    out = []
    try:
        g = json.load(open(os.path.join(common.native(common.MODEL_MAPGRAPH), "meta.json"),
                           encoding="utf-8"))
    except (OSError, ValueError):
        g = {}
    cfg = g.get("cfg") or {}
    out.append(FitConfigRow(
        family="mapgraph", role="graph ranker over the map",
        hyperparameters={k: cfg[k] for k in sorted(cfg)},
        compute={"device": (g.get("fit") or {}).get("device", "auto"),
                 "rows": g.get("rows"), "schema": g.get("schema_version")}))
    try:
        c = json.load(open(os.path.join(common.native(common.MODEL_GLOBAL), "meta.json"),
                           encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    out.append(FitConfigRow(
        family="catboost", role="action ranker",
        hyperparameters={k: c[k] for k in ("short_horizon", "short_weight", "w_local",
                                           "exp_lo", "exp_hi", "target") if k in c},
        compute={"rows": c.get("rows"),
                 "features": len(c.get("num") or []) + len(c.get("cat") or [])}))
    return out


@db.cached
def forcing(con):
    """What each model wants to do: the action-type mix each arm actually picked."""
    rows = con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, action_type, COUNT(*) n"
        " FROM action_taken WHERE action_type IS NOT NULL GROUP BY p, action_type").fetchall()
    by_arm: dict = {}
    for r in rows:
        arm = arms.arm_of(r["p"]) or arms.UNRECORDED
        mix = by_arm.setdefault(arm, {})
        mix[r["action_type"]] = mix.get(r["action_type"], 0) + (_i(r["n"], 0) or 0)
    tiles = []
    for arm in ("greedy_catboost", "marwil_gnn"):
        mix = by_arm.get(arm) or {}
        tot = sum(mix.values())
        if not tot:
            continue
        bars = []
        for atype, n in sorted(mix.items(), key=lambda kv: -kv[1])[:8]:
            lo, hi = _wilson(n, tot)
            bars.append(ForcingBar(
                action_type=_phrase(atype),
                share=Rate(n=n, of=tot, noun="picks",
                           population="this arm made in this run dir"),
                ci_lo=round(100 * lo, 1), ci_hi=round(100 * hi, 1)))
        tiles.append(ForcingTile(model=arm,
                                 favours=bars[0].action_type if bars else None, bars=bars))
    n_dec = sum(sum((by_arm.get(a) or {}).values()) for a in ("greedy_catboost", "marwil_gnn"))
    return tiles, Count(value=n_dec, noun="decisions",
                        population="drawn by a model arm in this run dir")


def _wilson(k, n, z=1.96):
    """Wilson score interval. A bar with no interval invites reading noise as signal."""
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return max(0.0, (c - s) / d), min(1.0, (c + s) / d)


# ----------------------------------------------------------------------------------------
# model comparison -- served from the precomputed analytics tables, never computed here
# ----------------------------------------------------------------------------------------

def _freshness(tenant: str):
    """How current the precomputed numbers are, as a required field on every page."""
    from advisor_api.models import AnalyticsFreshness
    st = adb.tenant_state(tenant)
    rows = _i(st.get("rows"), 0) or 0
    watermark = _i(st.get("watermark"), 0) or 0
    behind = 0
    if tenant == "model_agreement":
        try:
            corpus_hi = _i(db.connect().execute(
                "SELECT MAX(decision_id) FROM decisions").fetchone()[0], 0) or 0
        except Exception:
            corpus_hi = 0
        behind = max(0, corpus_hi - 1 - watermark)
    age = (time.time() - _f(st.get("last_run_ts"), 0.0)) if st.get("last_run_ts") else None
    state, detail = "ok", None
    if not st:
        state = "bad"
        detail = ("nothing has been built for this run dir yet -- start the analytics "
                  "service with `python -m analytics.runner`")
    elif st.get("last_error"):
        state, detail = "bad", "the last pass failed: %s" % st["last_error"]
    elif age is not None and age > 120:
        state = "bad"
        detail = ("the analytics service has not run for %s -- it may have stopped"
                  % _age_words(age))
    elif behind:
        state = "warn"
        detail = ("%d decisions are not folded in yet; the service picks them up within a "
                  "few seconds" % behind)
    return AnalyticsFreshness(
        tenant=tenant,
        behind=Count(value=behind, noun="decisions",
                     population="recorded in this run dir but not yet folded into the "
                                "precomputed table"),
        rows=Count(value=rows, noun="rows",
                   population="in the precomputed table for this run dir"),
        computed_through=watermark or None, age_seconds=(round(age, 1) if age else None),
        formula_version=_i(st.get("formula_version"), 0) or 0,
        state=state, detail=detail)


def _excluded_counts(s: dict) -> list:
    return [
        Count(value=_i(s.get("no_gnn"), 0) or 0, noun="decisions",
              population="recorded before the graph model had weights, so only one model "
                         "ranked them"),
        Count(value=_i(s.get("too_few"), 0) or 0, noun="decisions",
              population="where both models ranked fewer than three of the same offers"),
        Count(value=_i(s.get("no_scores"), 0) or 0, noun="decisions",
              population="carrying no stored scores at all"),
    ]


_SECONDARY_NOTE = "a supplement to the rank correlation above, not a substitute for it"


def _secondary(s: dict) -> list:
    """RBO and top-k overlap. Reported, and reported as secondary."""
    from advisor_api.models import SecondaryMeasure
    comparable = _i(s.get("comparable"), 0) or 0
    out = []
    if s.get("rbo_mean") is not None:
        out.append(SecondaryMeasure(
            measure="rank-biased overlap (p=0.9)",
            value="%.3f" % _f(s["rbo_mean"], 0.0)))
    for k, key in ((3, "top3_mean"), (5, "top5_mean"), (10, "top10_mean")):
        if s.get(key) is None:
            continue
        out.append(SecondaryMeasure(
            measure="top-%d overlap" % k,
            value="%.1f%%" % (100.0 * _f(s[key], 0.0)),
            rate=Rate(n=int(round(_f(s[key], 0.0) * comparable)), of=comparable,
                      noun="of each model's best %d" % k,
                      population="comparable decisions, averaged")))
    return out


@adb.cached
def agreement_page():
    """Everything the agreement view shows. Keyed reads of a flat table, no aggregation."""
    from advisor_api.models import (AgreementPage, AgreementRankRow, AgreementSummary,
                                    CorrelationSummary, RhoBin)
    fresh = _freshness("model_agreement")
    scope = Scope(text="rank correlation between the two models, over the offers both "
                       "ranked",
                  detail="every decision in this run dir, precomputed")
    s = adb.one("SELECT * FROM agreement_summary WHERE scope='all'")
    if not s:
        return AgreementPage(
            scope=scope, freshness=fresh, summary=[], rows=[],
            empty_reason=("nothing has been folded in for this run dir yet -- the analytics "
                          "service builds it within a few seconds of starting"))
    comparable = _i(s.get("comparable"), 0) or 0
    decisions = _i(s.get("decisions"), 0) or 0
    if not comparable:
        return AgreementPage(
            scope=scope, freshness=fresh, summary=[], rows=[],
            empty_reason=("no decision in this run dir carries both a tree rank and a graph "
                          "rank, so there is nothing to correlate"))
    same = _i(s.get("top1_same"), 0) or 0
    corr = CorrelationSummary(
        compared=Count(value=comparable, noun="decisions",
                       population="where both models ranked at least three of the same "
                                  "offers"),
        coverage=Rate(n=comparable, of=decisions, noun="decisions",
                      population="recorded in this run dir"),
        rho_median=_f(s.get("rho_median")), rho_mean=_f(s.get("rho_mean")),
        rho_q1=_f(s.get("rho_q1")), rho_q3=_f(s.get("rho_q3")),
        tau_median=_f(s.get("tau_median")), tau_mean=_f(s.get("tau_mean")),
        same_best=Rate(n=same, of=comparable, noun="decisions", population="comparable"),
        overlap_median=_f(s.get("overlap_median")),
        from_decision=_i(s.get("from_decision")), to_decision=_i(s.get("to_decision")),
        excluded=_excluded_counts(s))
    summary = [
        AgreementSummary(measure="decisions compared", value="{:,}".format(comparable),
                         help=None),
        AgreementSummary(measure="offers both models ranked (median)",
                         value="{:,.0f}".format(_f(s.get("overlap_median"), 0.0))),
        AgreementSummary(measure="Spearman rho, median",
                         value="%+0.3f" % _f(s.get("rho_median"), 0.0),
                         help=None),
        AgreementSummary(measure="Spearman rho, mean",
                         value="%+0.3f" % _f(s.get("rho_mean"), 0.0),
                         help=None),
        AgreementSummary(measure="Kendall tau-b, median",
                         value="%+0.3f" % _f(s.get("tau_median"), 0.0),
                         help=None),
        AgreementSummary(measure="both picked the same best action",
                         value="%.0f%% (%d of %d)"
                               % (100.0 * same / comparable, same, comparable)),
    ]
    rows = [AgreementRankRow(
        picked_by=_phrase(r["key"]), decisions=_i(r["decisions"], 0) or 0,
        cat_rank=_f(r["cat_rank"]), cat_pct=_f(r["cat_pct"]),
        gnn_rank=_f(r["gnn_rank"]), gnn_pct=_f(r["gnn_pct"]),
        delta_pct=_f(r["delta_pct"]), rho_median=_f(r["rho_median"]),
        fell_back=_i(r["fell_back"], 0) or 0)
        for r in adb.rows("SELECT * FROM agreement_breakdown WHERE dim='arm'"
                          " ORDER BY decisions DESC")]
    bins = [RhoBin(lo=_f(b["lo"], 0.0), hi=_f(b["hi"], 0.0),
                   decisions=_i(b["decisions"], 0) or 0)
            for b in adb.rows("SELECT * FROM agreement_hist ORDER BY bucket")]
    return AgreementPage(scope=scope, freshness=fresh, correlation=corr, rho_bins=bins,
                         summary=summary, rows=rows, secondary=_secondary(s))


ALIGNMENT_CAVEAT = None


@adb.cached
def agreement_series(axis: str = "window"):
    """Agreement over time, or by model generation."""
    from advisor_api.models import (AgreementSeriesPage, AgreementSeriesPoint,
                                    GenerationRow)
    axis = "generation" if axis == "generation" else "window"
    fresh = _freshness("model_agreement")
    pts = adb.rows("SELECT * FROM agreement_series WHERE axis=? ORDER BY seq", (axis,))
    s = adb.one("SELECT * FROM agreement_summary WHERE scope='all'") or {}
    scope = Scope(
        text=("median rank correlation per model generation" if axis == "generation"
              else "median rank correlation over the run, newest last"),
        detail=("windows come from the training ledger's own start and flush times"
                if axis == "generation"
                else "equal-count buckets of decision id -- wall-clock buckets would put "
                     "three decisions beside three hundred, because a retrain takes the "
                     "game down for tens of minutes"))

    def point(r):
        n = _i(r["decisions"], 0) or 0
        return AgreementSeriesPoint(
            label=(r["label"] or ("#%s" % r["from_decision"])), seq=_i(r["seq"], 0) or 0,
            decisions=Count(value=n, noun="decisions",
                            population="comparable, in this bucket"),
            from_decision=_i(r["from_decision"]), to_decision=_i(r["to_decision"]),
            from_ts=_f(r["from_ts"]), to_ts=_f(r["to_ts"]),
            rho_median=_f(r["rho_median"]), rho_mean=_f(r["rho_mean"]),
            rho_q1=_f(r["rho_q1"]), rho_q3=_f(r["rho_q3"]),
            tau_mean=_f(r["tau_mean"]), rbo_mean=_f(r["rbo_mean"]),
            same_top=Rate(n=_i(r["same_top"], 0) or 0, of=n, noun="decisions",
                          population="comparable, in this bucket"),
            gate=r["gate"])

    # Always populated, on BOTH axes. On the generation axis these are the rows of the
    # table; on the run axis they are the retrain boundaries drawn over the trend, which is
    # the comparison the view exists to make -- did agreement move when the weights did.
    gens = []
    for r in adb.rows("SELECT * FROM agreement_series WHERE axis='generation' ORDER BY seq"):
        n = _i(r["decisions"], 0) or 0
        gens.append(GenerationRow(
            trial=_phrase(r["trial"] or "unstamped"), generation=_i(r["generation"]),
            retrained=bool(r["retrained"]), from_ts=_f(r["from_ts"]),
            to_ts=_f(r["to_ts"]), overlapped_by=r["overlapped_by"],
            decisions=Count(value=n, noun="decisions",
                            population="comparable, inside this generation's window"),
            rho_median=_f(r["rho_median"]), rho_mean=_f(r["rho_mean"]),
            tau_mean=_f(r["tau_mean"]), rbo_mean=_f(r["rbo_mean"]),
            same_top=Rate(n=_i(r["same_top"], 0) or 0, of=n, noun="decisions",
                          population="comparable, inside this generation's window")))
    drawable = [p for p in pts if p["rho_median"] is not None]
    return AgreementSeriesPage(
        scope=scope, freshness=fresh, axis=axis, is_alignment=(axis == "generation"),
        caveat=(None),
        bucket_decisions=(_i(pts[0]["bucket_decisions"]) if pts else None),
        ambiguous=Count(value=_i(s.get("ambiguous"), 0) or 0, noun="decisions",
                        population="whose timestamp falls inside more than one training "
                                   "window, so which generation ranked them is ambiguous"),
        points=[point(r) for r in pts], generations=gens,
        empty_reason=(None if drawable else
                      ("no bucket has enough comparable decisions for a median to mean "
                       "anything yet")))


@adb.cached
def agreement_breakdown(dim: str = "action_type"):
    from advisor_api.models import AgreementBreakdownPage, AgreementBreakdownRow
    if dim not in ("arm", "action_type", "context_kind"):
        dim = "action_type"
    rows = adb.rows("SELECT * FROM agreement_breakdown WHERE dim=? ORDER BY decisions DESC",
                    (dim,))
    out = []
    for r in rows:
        n = _i(r["decisions"], 0) or 0
        out.append(AgreementBreakdownRow(
            key=_phrase(r["key"]),
            decisions=Count(value=n, noun="decisions",
                            population="comparable, in this group"),
            rho_median=_f(r["rho_median"]), rho_mean=_f(r["rho_mean"]),
            tau_mean=_f(r["tau_mean"]), rbo_mean=_f(r["rbo_mean"]),
            same_top=Rate(n=_i(r["same_top"], 0) or 0, of=n, noun="decisions",
                          population="comparable, in this group")))
    return AgreementBreakdownPage(
        scope=Scope(text="rank correlation grouped by %s" % dim.replace("_", " "),
                    detail="every comparable decision in this run dir"),
        freshness=_freshness("model_agreement"), dim=dim, rows=out,
        empty_reason=(None if out else "nothing comparable has been folded in yet"))


@adb.cached
def analytics_status():
    from advisor_api.models import AnalyticsPage, TenantStatus
    out = []
    for st in adb.all_state():
        f = _freshness(st["tenant"])
        out.append(TenantStatus(
            tenant=st["tenant"], formula_version=_i(st.get("formula_version"), 0) or 0,
            rows=f.rows, behind=f.behind, watermark=_i(st.get("watermark")),
            built=_clock(st.get("built_ts")), last_run=_clock(st.get("last_run_ts")),
            last_run_seconds=_f(st.get("last_run_seconds")),
            last_error=st.get("last_error"), state=f.state))
    return AnalyticsPage(
        scope=Scope(text="what the analytics service has precomputed for this run dir",
                    detail="every model-comparison and growth number is read from these "
                           "tables rather than computed per request"),
        tenants=out, db_path=adb.path(), runner_hint="python -m analytics.runner")


def decision_agreement(decision_id: int):
    """One decision's agreement -- a primary-key read, not a recompute."""
    from advisor_api.models import DecisionAgreement
    r = adb.one("SELECT * FROM model_agreement WHERE decision_id=?", (int(decision_id),))
    if not r:
        return None
    status = r["status"] or ""
    note = {
        "no_gnn": "the graph model had no weights when this decision was recorded, so only "
                  "the tree model ranked it",
        "too_few": "both models ranked fewer than three of the same offers -- over two, a "
                   "rank correlation can only be +1 or -1",
        "no_scores": "no scores were stored for this decision",
    }.get(status)
    return DecisionAgreement(
        n=Count(value=_i(r["n"], 0) or 0, noun="offers",
                population="on this decision that both models ranked"),
        status=status, rho=_f(r["rho"]), tau_b=_f(r["tau_b"]), rbo=_f(r["rbo"]),
        top1_same=(bool(r["top1_same"]) if r["top1_same"] is not None else None),
        top3_overlap=_f(r["top3_overlap"]),
        cat_top_in_gnn=_i(r["cat_top_in_gnn"]), gnn_top_in_cat=_i(r["gnn_top_in_cat"]),
        note=note)


def rho_for(decision_ids) -> dict:
    """{decision_id: (rho, n)} for the ids on one page of the log."""
    ids = [int(i) for i in decision_ids if i is not None]
    out = {}
    for i in range(0, len(ids), 400):                      # under SQLite's variable limit
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in adb.rows("SELECT decision_id, rho, n FROM model_agreement"
                          " WHERE decision_id IN (%s)" % marks, chunk):
            out[_i(r["decision_id"])] = (_f(r["rho"]), _i(r["n"]))
    return out


@db.cached
def correlations(con) -> list:
    """Does an arm's share of a campaign track how that campaign went?"""
    tiles = []
    for label, table, idcol in (("action ranker", "action_taken", "decision_id"),
                                ("interrupt model", "interrupt_decisions", "interrupt_id")):
        if table == "action_taken":
            sql = ("SELECT COALESCE(at.policy,'(unrecorded)') arm, dp.campaign_id ckey,"
                   "       dp.turn turn, COUNT(*) n"
                   " FROM action_taken at JOIN decision_points dp"
                   "      ON dp.decision_id = at.decision_id"
                   " GROUP BY arm, ckey, turn")
        else:
            sql = ("SELECT COALESCE(policy,'(unrecorded)') arm, campaign_id ckey,"
                   "       turn turn, COUNT(*) n FROM interrupt_decisions"
                   " GROUP BY arm, ckey, turn")
        per: dict = {}
        turn_totals: dict = {}
        for r in con.execute(sql):
            k = (r["ckey"], _i(r["turn"], 0) or 0)
            # Folded onto the strategy, like every other arm tally. Grouping on the raw
            # policy correlated each ruleset RULE against campaign outcome separately, so
            # the ruleset arm never had enough turns in one row to clear the gate.
            arm = arms.arm_of(r["arm"]) or arms.UNRECORDED
            n = _i(r["n"], 0) or 0
            cells = per.setdefault(arm, {})
            cells[k] = cells.get(k, 0) + n
            turn_totals[k] = turn_totals.get(k, 0) + n

        target = {(r["campaign_id"], _i(r["turn"], 0) or 0):
                  (_f(r["settlements"]), _f(r["lord_level"]))
                  for r in con.execute("SELECT campaign_id, turn, settlements, lord_level"
                                       " FROM target_rows")}
        rows = []
        for arm, cells in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
            shares, setts, lords = [], [], []
            camps = set()
            for k, n in cells.items():
                tot = turn_totals.get(k) or 0
                t = target.get(k)
                if not tot or not t:
                    continue
                camps.add(k[0])
                shares.append(n / tot)
                setts.append(t[0])
                lords.append(t[1])
            r_s, g_s = _pearson_gated(shares, setts)
            r_l, g_l = _pearson_gated(shares, lords)
            picks = sum(cells.values())
            rows.append(CorrelationRow(
                arm=_phrase(arm), campaigns=len(camps), turns=len(shares),
                share=Rate(n=picks, of=sum(turn_totals.values()) or 1, noun="picks",
                           population="on %s decisions in this run dir" % label),
                per_campaign=round(picks / len(camps), 1) if camps else None,
                settlements_r=r_s, settlements_gate=g_s, lord_r=r_l, lord_gate=g_l))
        tiles.append(CorrelationTile(label=label, rows=rows))
    return tiles


def _pearson_gated(xs, ys, min_n=12):
    """Pearson r, or None with the reason it was not computed."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < min_n:
        return None, "n=%d, below %d" % (len(pairs), min_n)
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    if len(set(xs2)) < 2 or len(set(ys2)) < 2:
        return None, "constant on one axis"
    try:
        return round(statistics.correlation(xs2, ys2), 3), None
    except statistics.StatisticsError:
        return None, "undefined"


SESSION_REPORTS = 12


def training_history() -> list:
    """One row per retrain, newest first: what the corpus was and what the fit produced."""
    return _training_history()


@db.cached_files(common.native(common.RUNS_ROOT))
def _training_history() -> list:
    import glob

    root = common.native(common.RUNS_ROOT)
    reports = sorted(glob.glob(os.path.join(root, "session_*.json")))[-SESSION_REPORTS:]
    out = []
    for path in reports:
        try:
            with open(path, encoding="utf-8") as fh:
                rep = json.load(fh)
        except (OSError, ValueError):
            continue
        stamp = os.path.basename(path).replace("session_", "").replace(".json", "")
        gen = 0
        for camp in rep.get("campaigns") or []:
            rt = camp.get("retrain")
            if not rt:
                continue
            gen += 1
            local = rt.get("local") or {}
            irt = camp.get("retrain_interrupt") or {}
            gnn = camp.get("retrain_gnn") or {}
            fit = rt.get("fit") or {}
            e1, e2 = (fit.get("e1") or {}), (fit.get("e2") or {})
            r1, r2 = _f(e1.get("val_rmse")), _f(e2.get("val_rmse"))
            groups = {
                "corpus": _clean({
                    "rows": _i(rt.get("rows")),
                    "campaigns": _i(rt.get("campaigns")),
                    "decisions": _i(rt.get("n_decisions")),
                    "seconds": _f(rt.get("seconds")),
                }),
                "catboost global": _clean({
                    "e1 rmse": r1,
                    "e2 rmse": r2,
                    # e2 is the baseline e1 is measured against; the lift is the number
                    # that says whether this retrain was worth anything.
                    "lift": (round(r2 - r1, 4) if (r1 is not None and r2 is not None)
                             else None),
                    "val rows": _i(e1.get("val_rows")),
                    "best iter": _i(e1.get("best_iteration")),
                    "in-sample MAE": _f(rt.get("mae_in_sample")),
                }),
                "local": _clean({
                    "rows": _i(local.get("rows")),
                    "e1 rmse": _f(((local.get("fit") or {}).get("local_e1") or {})
                                  .get("val_rmse")),
                }),
                "interrupt": _clean({
                    "rows": _i(irt.get("rows")),
                    "screens": (len(irt.get("screens")) if isinstance(irt.get("screens"), (list, dict))
                                else _i(irt.get("screens"))),
                }),
                "gnn": _clean({
                    "rows": _i(gnn.get("rows")),
                    "listwise NLL": _f((gnn.get("fit") or {}).get("val_listwise_nll")),
                    "epochs": _i((gnn.get("fit") or {}).get("epochs_run")),
                    "device": (gnn.get("fit") or {}).get("device"),
                    "stopped by": (gnn.get("fit") or {}).get("stopped_by"),
                }),
            }
            out.append(TrainingEvent(
                when=time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(_f(camp.get("started"), 0.0) or 0.0)),
                trial="%s-g%d" % (stamp, gen),
                corpus_rows=_i(rt.get("rows")),
                corpus_campaigns=_i(rt.get("campaigns")),
                groups={k: v for k, v in groups.items() if v}))
    out.reverse()
    return out


def _clean(d: dict) -> dict:
    """Drop keys with no value, so an empty group is visibly empty rather than a row of dashes."""
    return {k: v for k, v in d.items() if v is not None}


def trials():
    return _trials()


@db.cached_files(metrics_db.DB_PATH, metrics_db.DB_PATH + "-wal")
def _trials() -> list:
    out = []
    # `running` is written by _checkpoint_trial and never cleared, so every session that
    # was killed rather than finished leaves its last row claiming to be live forever.
    # Measured: 3 rows marked running, of which 1 was the live session.
    #
    # A running trial rewrites its row after every campaign, so a live one always has a
    # fresh ts -- and only the newest such row can be live, because a session superseded
    # by a later one has certainly stopped.
    rows = list(metrics_db.trials())
    newest_live = max((float(d.get("ts") or 0) for d in rows if d.get("running")),
                      default=None)
    for d in rows:
        corpus = d.get("corpus_at_train") or {}
        setts = d.get("settlements") or {}
        lord = d.get("lord_level") or {}
        timing = d.get("timing") or {}
        row = TrialRow(
            trial=str(d.get("trial") or ""),
            backend=d.get("backend"),
            cfg=(json.dumps(d["backend_cfg"], sort_keys=True)
                 if d.get("backend_cfg") else None),
            mix={arms.canonical(k): v for k, v in (d.get("strategies") or {}).items()},
            # ruleset is {name, sha256}; the name is what identifies the run.
            ruleset=_text(d.get("ruleset")),
            campaigns=_i(d.get("campaigns")),
            corpus=_i(corpus.get("rows")),
            settlements_per_campaign=_f(setts.get("mean")),
            settlements_total=_f(setts.get("total")),
            grew=(Rate(n=_i(setts.get("campaigns_that_gained"), 0) or 0,
                       of=_i(setts.get("campaigns_measured"), 0) or 0,
                       noun="campaigns",
                       population="in this trial with a measurable growth span")
                  if setts.get("campaigns_measured") is not None else None),
            shrank=(Rate(n=_i(setts.get("campaigns_that_lost"), 0) or 0,
                         of=_i(setts.get("campaigns_measured"), 0) or 0,
                         noun="campaigns",
                         population="in this trial with a measurable growth span")
                    if setts.get("campaigns_measured") is not None else None),
            growth_baseline=_text(d.get("baseline")),
            lord_per_campaign=_f(lord.get("mean")),
            turns_per_campaign=_f(d.get("turns_per_campaign")),
            seconds_per_campaign=_f(timing.get("s_per_campaign")),
            seconds_per_turn=_f(timing.get("s_per_turn")),
            live=(bool(d.get("running"))
                  and float(d.get("ts") or 0) == newest_live
                  and time.time() - float(d.get("ts") or 0) <= TRIAL_LIVE_WINDOW_S),
            notes=(", ".join("%s %s" % (k, v)
                             for k, v in (d.get("outcomes") or {}).items()) or None))
        row.snapshots = _i(d.get("_snapshots"), 1)
        out.append(row)
    out.reverse()                                          # newest first
    return out[:200]


# ----------------------------------------------------------------------------------------
# campaign detail
# ----------------------------------------------------------------------------------------

def reward_series(con, campaign_key: str):
    """The turn series for one campaign, plus the columns that carry no signal."""
    rows = con.execute(
        "SELECT turn, income, settlements, allies, vassals, power_rank"
        " FROM target_rows WHERE campaign_id = ? ORDER BY turn", (campaign_key,)).fetchall()
    pts = [RewardPoint(turn=_i(r["turn"], 0) or 0, income=_f(r["income"]),
                       settlements=_f(r["settlements"]), allies=_f(r["allies"]),
                       vassals=_f(r["vassals"]), power_rank=_f(r["power_rank"]))
           for r in rows]
    constant = []
    for field in ("income", "settlements", "allies", "vassals", "power_rank"):
        vals = {getattr(p, field) for p in pts}
        if len(pts) >= 3 and len(vals) <= 1:
            constant.append(field)
    return pts, constant


@db.cached
def diplomacy_tail(con, campaign_key: str | None = None) -> list:
    """Recent deal events, newest first."""
    if campaign_key:
        rows = con.execute(
            "SELECT ts,campaign_key,turn,kind,payload FROM diplomacy_events"
            " WHERE campaign_key=? ORDER BY event_id DESC LIMIT ?",
            (campaign_key, DIPLO_TAIL)).fetchall()
    else:
        rows = con.execute(
            "SELECT ts,campaign_key,turn,kind,payload FROM diplomacy_events"
            " ORDER BY event_id DESC LIMIT ?", (DIPLO_TAIL,)).fetchall()
    out = []
    for r in rows:
        d = _jload(r["payload"]) or {}
        d.setdefault("kind", r["kind"])
        d.setdefault("turn", r["turn"])
        score = _f(d.get("deal_score"), _f(d.get("success_chance")))
        outcome = d.get("outcome") or d.get("result")
        state = "neutral"
        if outcome in ("accepted",):
            state = "ok"
        elif outcome in ("declined", "ai_would_refuse"):
            state = "warn"
        elif outcome in ("not_staged", "deal_selection"):
            # our own failure to build the deal, not a diplomatic answer
            state = "bad"
        out.append(DiploEvent(
            turn=_i(d.get("turn")), channel=_phrase(d.get("channel") or d.get("kind")),
            faction=(_fac(_who) if (_who := (d.get("target") or d.get("faction"))) else None),
            outcome=_phrase(outcome), deal_score=score, standing=_f(d.get("standing")),
            terms=_text(d.get("terms") or d.get("speech")), state=state))
    # Already newest-first: the query orders by event_id DESC. The old version read the
    # file forwards and had to reverse.
    return out[:200]


# ----------------------------------------------------------------------------------------
# infra
# ----------------------------------------------------------------------------------------

# The advisor/recorder channel is not here any more: it is a table, not a file, so there
# is no mtime to watch. Its liveness shows up as decisions arriving, which is what the run
# view already reports.
_ACTIVITY = (
    ("session log", None),
    ("trace.jsonl", "trace.jsonl"),
    ("decisions_stream.jsonl", "decisions_stream.jsonl"),
)


def activity() -> list:
    out = []
    for label, fname in _ACTIVITY:
        path = session_log_path() if fname is None else os.path.join(
            common.native(common.RUN_DIR), fname)
        age = None
        try:
            age = time.time() - os.path.getmtime(path)
        except OSError:
            pass
        state = "neutral"
        if age is not None:
            state = "ok" if age < 120 else ("warn" if age < 900 else "bad")
        out.append(ActivityRow(stream=label, last_write=_age_words(age), age_seconds=age,
                               state=state))
    return out


def session_log_path() -> str | None:
    try:
        with open(common.CURRENT_SESSION_LOG, encoding="utf-8-sig") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def session_log_tail(n=24) -> tuple:
    path = session_log_path()
    if not path:
        return [], None
    lines: list = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - (1 << 18)))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return [], path
    return [l for l in lines if l.strip()][-n:], path
