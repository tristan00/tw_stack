
from __future__ import annotations

import ast
import json
import math
import os
import re
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms
import common
import run_config
import ucb_stats as UCB
from advisor_api import db, ident, labels
from decisions import hydrate
from advisor_api.models import (
    ActionTypeRow, ActivityRow,
    ArmCoverage, CampaignRow, ConquestStep, Count, CorrelationRow, CorrelationTile,
    Current, CampaignReward, DecisionRow, DiploEvent, EntityState, ForcingBar,
    ForcingTile, Ident, InterruptOption, InterruptRow, LengthBand, Metric, ModelCard,
    OfferRow, OpeningBranch, OpeningFamily, OpeningOffer, OutcomeCount, OutcomeTally,
    PairOption, PerfBar, PhaseSpan, PolicyRow, Rate, RewardPoint, RibbonBucket, Scope,
    StartRow, TimelineAction, TimelineLane, TimingRow, TrainingEvent, TrialCorr,
    TrialRow, TurnRollup, UcbPick, UcbRow, HistBin, ProducedCampaign,
    StartCampaign, Verdict, WindowEdgeRow,
    BehaviourRow, BuildingRow, CampaignBuildingRow, CampaignCharacter,
    CampaignItemEvent, CampaignSkillRow, CampaignTechRow, CatalogCampaignRow,
    CatalogIndexRow, CatalogStartRow, ChainLevel, ForkArmRow,
    ForkRow, ItemEffect, TraitLevelRow,
    ItemCampaignRow, ItemRow, LordState,
    ItemStartRow, ItemSwapRow, LookupCampaignRow, PositionFacetOption,
    PositionKeyRow, PositionTypeRow, RelatedKey, SkillCharacterRow, SkillRow,
    StartCharacterRow, TechRow,
)
DECISIONS_PAGE = 50
TIMELINE_DECISIONS = 200
MENUS_ROWS = 60
DIPLO_TAIL = 600

MAX_OFFERS_PER_DECISION = 1 << 20
RANKED_ARMS = ("greedy_catboost", "marwil_gnn", "greedy_gnn")
PAIRS = tuple((a, b) for i, a in enumerate(RANKED_ARMS) for b in RANKED_ARMS[i + 1:])


def pair_key(a, b):
    return "%s|%s" % (a, b)

_OUTCOME_STATE = {
    "error": "bad",
    "stuck": "bad",
    "unhandled_screen": "bad",
    "stagnant": "warn",
    "defeated": "neutral",
    "no_ending_recorded": "warn",
}

LIVE_WINDOW_S = 600.0


def _ended_because(pm: dict) -> str | None:
    if pm.get("because"):
        return pm["because"]
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
            mg = _f(g.get("min_gain"))
            return ("growth gate at turn %s: %s -- needed +%s on either"
                    % (g.get("turn"), parts,
                       ("%g" % mg) if mg is not None else g.get("min_gain")))
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


_ENUMS: dict = {}


def _enum(domain: str) -> dict:
    hit = _ENUMS.get(domain)
    if hit is None:
        hit = _ENUMS[domain] = {r["key"]: r["enum_id"] for r in db.connect().execute(
            "SELECT key, enum_id FROM dict.enum WHERE domain = %s", (domain,))}
    return hit


def _skip_refusals() -> list:
    return [_enum("refusal")["awaiting_execution"], _enum("refusal")["campaign_died"]]


@db.timed
def outcome_join(con) -> tuple:
    metrics: dict = {}
    for r in con.execute(
            "SELECT p.campaign_id, m.label, m.then_value, m.now_value, m.window_turns"
            " FROM corpus.postmortem_growth_metric m"
            " JOIN corpus.postmortem p USING (postmortem_id)"
            " WHERE p.postmortem_id = (SELECT MAX(postmortem_id)"
            "  FROM corpus.postmortem q WHERE q.campaign_id = p.campaign_id)"
            " ORDER BY m.label"):
        metrics.setdefault(r["campaign_id"], {})[r["label"]] = {
            "label": r["label"], "then": _f(r["then_value"]),
            "now": _f(r["now_value"]), "window": _i(r["window_turns"])}
    claimed: dict = {}
    for r in con.execute(
            "SELECT e.campaign_id, e.campaign_key, e.ts, f.key AS faction,"
            " o.key AS outcome, e.when_text, e.error, e.verdict,"
            " e.growth_reason, e.growth_turn, e.growth_min_gain"
            " FROM corpus.campaign_ending e"
            " LEFT JOIN dict.faction f ON f.id = e.faction_id"
            " LEFT JOIN dict.enum o ON o.enum_id = e.outcome_id"):
        ck = r["campaign_key"]
        claimed[ck] = {"campaign_key": ck, "outcome": r["outcome"],
                       "when": r["when_text"], "error": r["error"],
                       "plausibility": {"verdict": r["verdict"] or ""},
                       "growth": {"reason": r["growth_reason"],
                                  "turn": _i(r["growth_turn"]),
                                  "min_gain": _f(r["growth_min_gain"]),
                                  "metrics": metrics.get(r["campaign_id"]) or {}},
                       "faction": r["faction"], "ts": _f(r["ts"])}
    unjoined = _i(con.execute(
        "SELECT COUNT(*) FROM corpus.postmortem WHERE campaign_id IS NULL"
    ).fetchone()[0], 0) or 0
    return claimed, unjoined


@db.timed
def current(con) -> Current:
    row = con.execute(
        "SELECT s.turn, s.ts, s.campaign_id, c.campaign_key, c.leader,"
        "       f.key AS faction, m.key AS campaign_map,"
        "       sc.settlements, sc.power_rank, sc.lord_level,"
        "       c.n_decisions, c.first_ts"
        " FROM corpus.snapshot s"
        " JOIN corpus.decision dd ON dd.decision_id = s.snapshot_id"
        " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
        " JOIN dict.faction f ON f.id = c.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
        " JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = s.snapshot_id"
        " ORDER BY s.snapshot_id DESC LIMIT 1").fetchone()
    if not row:
        return Current()
    stored = con.execute(
        "SELECT SUM(n) FROM corpus.start_counts").fetchone()
    pick = con.execute(
        "SELECT p.pick_id FROM corpus.ucb_pick p"
        " JOIN dict.faction f ON f.id = p.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = p.campaign_map_id"
        " WHERE m.key IS NOT DISTINCT FROM %s AND f.key = %s"
        " ORDER BY p.pick_id DESC LIMIT 1",
        (row["campaign_map"], row["faction"])).fetchone()
    span = {"n": row["n_decisions"], "t0": row["first_ts"]}
    return Current(campaign=_camp(row["campaign_key"]), turn=_i(row["turn"]),
                   leader=row["leader"], faction_key=row["faction"],
                   campaign_map=_id(ident.campaign_map(row["campaign_map"]))
                   if row["campaign_map"] else None,
                   settlements=_f(row["settlements"]),
                   power_rank=_f(row["power_rank"]),
                   lord_level=_f(row["lord_level"]),
                   stored_campaigns=_i(stored[0]) if stored else None,
                   age_seconds=max(0.0, time.time() - (_f(row["ts"]) or 0.0)),
                   decisions=_i(span["n"]) if span else None,
                   started_ts=_f(span["t0"]) if span else None,
                   pick_id=_i(pick["pick_id"]) if pick else None)


@db.timed
def campaign_state(con, campaign_key: str) -> dict | None:
    meta = _campaign_id_of(con, campaign_key)
    if not meta:
        return None
    cid = _i(meta["campaign_id"], 0)
    out = {"research": None, "researched_n": 0, "built_n": 0, "ranked_n": 0,
           "lord": None, "equipped": [], "pool": []}
    counts = {r["family"]: _i(r["n"], 0) or 0 for r in con.execute(
        "SELECT family, COUNT(DISTINCT key_id) n FROM acquisition"
        " WHERE campaign_id = %(cid)s AND acquired_snapshot IS NOT NULL"
        " AND acquired_snapshot > first_seen_snapshot"
        " AND family IN ('research', 'building', 'skills') GROUP BY 1",
        {"cid": cid})}
    out["researched_n"] = counts.get("research", 0)
    out["built_n"] = counts.get("building", 0)
    out["ranked_n"] = counts.get("skills", 0)
    row = con.execute(
        "SELECT tn.key AS research, cs.equipped_all_set_id, cs.anc_pool_set_id"
        " FROM corpus.campaign_state cs"
        " JOIN corpus.snapshot s ON s.snapshot_id = cs.snapshot_id"
        " LEFT JOIN dict.tech_node tn ON tn.id = cs.current_research_id"
        " WHERE s.campaign_id = %s ORDER BY cs.snapshot_id DESC LIMIT 1",
        (cid,)).fetchone()
    if row:
        rk = str(row["research"] or "")
        if rk and rk != "None":
            out["research"] = Ident(raw=rk,
                                    label=labels.tech_name(rk) or labels.pretty(rk))
        for field, target in (("equipped_all_set_id", "equipped"),
                              ("anc_pool_set_id", "pool")):
            for m in con.execute(
                    "SELECT a.key FROM corpus.item_slot_set_member i"
                    " JOIN dict.ancillary a ON a.id = i.ancillary_id"
                    " WHERE i.set_id = %s ORDER BY i.ord", (row[field],)):
                out[target].append(Ident(raw=str(m["key"]),
                                         label=_item_ident(str(m["key"]))))
    lead = con.execute(
        "SELECT DISTINCT ON (cs.character_id) cs.rank, cs.hp, cs.wounded,"
        " cs.skill_points, dr.key AS region"
        " FROM corpus.character ch"
        " JOIN corpus.char_state cs USING (character_id)"
        " LEFT JOIN dict.region dr ON dr.id = cs.region_id"
        " WHERE ch.campaign_id = %s AND NOT cs.is_hero AND cs.is_leader"
        " ORDER BY cs.character_id, cs.snapshot_id DESC", (cid,)).fetchone()
    if lead:
        region = str(lead["region"] or "")
        hp = _f(lead["hp"])
        out["lord"] = LordState(
            rank=_i(lead["rank"]),
            hp=round(hp * 100) if hp is not None and 0 <= hp <= 1 else None,
            wounded=bool(lead["wounded"]),
            region=(labels.name_for("garrison", region)
                    or labels.pretty(region)) if region else None,
            skill_points=_i(lead["skill_points"]))
    return out


_totals_memo: tuple = (0.0, None)


@db.timed
def totals(con) -> list:
    global _totals_memo
    ts, got = _totals_memo
    if got is not None and time.time() - ts < 60.0:
        return got
    r = con.execute(
        "SELECT COUNT(*) FILTER (WHERE n_decisions >= 2) camps,"
        " SUM(n_decisions) decs, SUM(n_counted) counted"
        " FROM corpus.campaign").fetchone()
    offers = con.execute(
        "SELECT COALESCE(SUM(n_offers), 0) FROM corpus.decision").fetchone()[0]
    out = [
        Count(value=_i(r["camps"], 0) or 0,
              noun="campaigns", population="with two or more decisions in this run dir"),
        Count(value=_i(r["decs"], 0) or 0,
              noun="decisions", population="recorded in this run dir"),
        Count(value=_i(offers, 0) or 0,
              noun="offers", population="scored across those decisions"),
        Count(value=_i(r["counted"], 0) or 0,
              noun="actions", population="confirmed by the game"),
    ]
    _totals_memo = (time.time(), out)
    return out


@db.timed
def throughput(con) -> list:
    import time
    now = time.time()
    since = now - 3600.0
    rows = con.execute(
        "SELECT s.ts, s.campaign_id, s.turn FROM corpus.snapshot s"
        " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
        " WHERE s.ts >= %s ORDER BY s.snapshot_id DESC", (since,)).fetchall()
    out = []
    if not rows:
        return out
    span_h = max(1e-6, (now - (_f(rows[-1]["ts"]) or now)) / 3600.0)
    camps = len({r["campaign_id"] for r in rows})
    turns = len({(r["campaign_id"], r["turn"]) for r in rows})
    taken = con.execute(
        "SELECT COUNT(*) a, COUNT(*) FILTER (WHERE counted) c"
        " FROM corpus.taken WHERE (refusal_id IS NULL OR"
        " refusal_id <> ALL(%s)) AND ts >= %s",
        (_skip_refusals(), since)).fetchone()
    attempted, confirmed = _i(taken["a"], 0) or 0, _i(taken["c"], 0) or 0
    pct = (100.0 * confirmed / attempted) if attempted else None
    camp_spark, turn_spark = _rate_sparks(rows)
    out.append(Metric(label="campaigns/hr", value=round(camps / span_h, 1),
                      sub="over the last 60 min", spark=camp_spark))
    out.append(Metric(label="turns/hr", value=round(turns / span_h, 1),
                      sub="over the last 60 min", spark=turn_spark))
    out.append(Metric(label="confirm rate", value=(round(pct, 1) if pct is not None else None),
                      unit="%", sub="%d of %d actions, last 60 min" % (confirmed, attempted),
                      spark=_confirm_spark(con)))
    return out


SPARK_BUCKETS = 16


def _rate_sparks(rows, buckets=SPARK_BUCKETS):
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


@db.timed
def _confirm_spark(con, buckets=SPARK_BUCKETS):
    rows = con.execute(
        "SELECT counted, refusal_id FROM corpus.taken"
        " ORDER BY decision_id DESC LIMIT 2000").fetchall()
    skip = set(_skip_refusals())
    rows = [r for r in rows if r["refusal_id"] not in skip]
    if len(rows) < buckets:
        return []
    rows.reverse()
    size = len(rows) // buckets or 1
    out = []
    for i in range(buckets):
        chunk = rows[i * size:(i + 1) * size]
        if not chunk:
            continue
        ok = sum(1 for r in chunk if r["counted"])
        out.append(round(100.0 * ok / len(chunk), 1))
    return out


@db.timed
def collect_timing(con) -> list:
    rows = con.execute(
        "SELECT dt.collect_ms, dt.roundtrip_ms, dt.score_ms, dt.store_ms,"
        " dt.trace_ms, dt.housekeep_ms, dt.pickup_lag_ms FROM corpus.decision d"
        " LEFT JOIN corpus.decision_timing dt USING (decision_id)"
        " ORDER BY d.decision_id DESC LIMIT 400").fetchall()
    buckets = {}
    for r in rows:
        for k in ("collect_ms", "roundtrip_ms", "score_ms", "store_ms", "trace_ms",
                  "housekeep_ms", "pickup_lag_ms"):
            v = _f(r[k])
            if v is not None:
                buckets.setdefault(k, []).append(v)
    label = {"collect_ms": "recorder collect", "roundtrip_ms": "request round trip",
             "score_ms": "featurize + rank", "store_ms": "corpus store",
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


@db.timed
def cycle_timing(con) -> list:
    rows = con.execute(
        "SELECT snapshot_ms, execute_ms, confirm_ms, gates_ms, total_ms"
        " FROM corpus.taken ORDER BY decision_id DESC LIMIT 400").fetchall()
    buckets = {}
    for r in rows:
        for k in ("snapshot_ms", "execute_ms", "confirm_ms", "gates_ms", "total_ms"):
            v = _f(r[k])
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


def _campaign_keys(con) -> dict:
    return {r["campaign_id"]: r["campaign_key"] for r in con.execute(
        "SELECT campaign_id, campaign_key FROM corpus.campaign")}


def _faction_of(con) -> dict:
    return {r["campaign_id"]: r["faction"] for r in con.execute(
        "SELECT c.campaign_id, f.key AS faction FROM corpus.campaign c"
        " JOIN dict.faction f ON f.id = c.faction_id")}


def _decs_all(con) -> dict:
    return {r["ckey"]: dict(r) for r in con.execute(
        "SELECT c.campaign_key ckey, COUNT(*) n, MIN(s.ts) t0, MAX(s.ts) t1,"
        "       MAX(s.turn) last_turn, MIN(s.turn) first_turn,"
        "       MAX(sc.power_rank) peak_power_rank"
        " FROM corpus.snapshot s"
        " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
        " JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = s.snapshot_id"
        " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
        " GROUP BY c.campaign_key")}


def _acts_all(con) -> dict:
    out = {}
    for r in con.execute(
            "SELECT c.campaign_key ckey, COUNT(*) rows_,"
            "       COUNT(*) FILTER (WHERE t.refusal_id IS NULL"
            "        OR t.refusal_id <> ALL(%s)) attempted,"
            "       COUNT(*) FILTER (WHERE t.counted) confirmed"
            " FROM corpus.taken t JOIN corpus.campaign c USING (campaign_id)"
            " GROUP BY c.campaign_key", (_skip_refusals(),)):
        out[r["ckey"]] = dict(r)
    return out


def _growth_state(turn_rows):
    if not turn_rows:
        return NO_TURN_ROWS
    return MEASURED if int(turn_rows) >= 2 else SINGLE_TURN


MEASURED = "measured"
SINGLE_TURN = "single_turn"
NO_TURN_ROWS = "no_turn_rows"


def _growth_all(con, decs) -> dict:
    out = {}
    for r in con.execute(
            "SELECT c.campaign_key ckey, c.n_decisions turn_rows,"
            " fs.settlements first_settlements, fs.lord_level first_lord_level,"
            " c.peak_settlements, c.peak_lord_level,"
            " ls.settlements final_settlements, ls.lord_level final_lord_level,"
            " ls.power_rank final_power_rank, ls.income final_income"
            " FROM corpus.campaign c"
            " LEFT JOIN corpus.snapshot_campaign fs"
            "  ON fs.snapshot_id = c.first_snapshot_id"
            " LEFT JOIN corpus.snapshot_campaign ls"
            "  ON ls.snapshot_id = c.last_snapshot_id"
            " WHERE c.first_snapshot_id IS NOT NULL"):
        g = dict(r)
        d = decs.get(r["ckey"]) or {}
        g["first_turn"] = _i(d.get("first_turn"))
        g["last_measured_turn"] = _i(d.get("last_turn"))
        g["peak_power_rank"] = _f(d.get("peak_power_rank"))
        tr = g["turn_rows"]
        g["growth_state"] = _growth_state(tr)
        measured = g["growth_state"] == MEASURED
        span = None
        if measured and g["first_turn"] is not None \
                and g["last_measured_turn"] is not None:
            n = g["last_measured_turn"] - g["first_turn"]
            span = n if n > 0 else None
        g["growth_span_turns"] = span

        def _delta(first, last):
            if not measured or first is None or last is None:
                return None
            return float(last) - float(first)

        s = _delta(g["first_settlements"], g["peak_settlements"])
        l = _delta(g["first_lord_level"], g["peak_lord_level"])
        g["settlements_growth"] = s
        g["lord_growth"] = l
        g["settlements_per_turn"] = (float(s) / span
                                     if s is not None and span else None)
        g["lord_per_turn"] = float(l) / span if l is not None and span else None
        out[r["ckey"]] = g
    return out


@db.timed
def campaign_rows(con) -> list:
    decs = _decs_all(con)
    acts = _acts_all(con)
    growth = _growth_all(con, decs)
    meta = {r["campaign_key"]: r for r in con.execute(
        "SELECT c.campaign_id, c.campaign_key, f.key AS faction, c.turns,"
        " m.key AS campaign_map, c.presave_radius, c.leader, c.ucb_pick_id"
        " FROM corpus.campaign c"
        " JOIN dict.faction f ON f.id = c.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id")}

    outcomes = outcome_join(con)[0]
    gmap = {g["campaign_key"]: g for g in gains_all(con)}
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
            faction_key=(m["faction"] if m else ident.split_campaign_key(ckey)[0]),
            leader=m["leader"] if m else None,
            campaign_map=_id(ident.campaign_map(m["campaign_map"] if m else None)),
            presave_radius=(m["presave_radius"] if m else None),
            turns=_i(d["last_turn"], _i(m["turns"]) if m else None),
            decisions=n_dec,
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
            growth_state=g.get("growth_state") or NO_TURN_ROWS,
        )
        sg, lg = _f(g.get("settlements_growth")), _f(g.get("lord_growth"))
        if sg is not None and lg is not None:
            row.settlements_gained, row.levels_gained = sg, lg
            ga = gmap.get(ckey)
            row.reward = (_f(ga["reward"]) if ga
                          else _weighted_reward(sg, lg, None, None))
        row.pick_id = _i(m["ucb_pick_id"]) if m else None
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
        out.append(row)
    out.sort(key=lambda r: -(decs[r.campaign.raw]["t1"] or 0))
    return out


def campaign_rows_cached(con, force=False) -> list:
    return _stamped_slow("campaign_rows",
                         lambda: campaign_rows(con), force=force)


_CAMPAIGN_SORTS = {
    "campaign": lambda r: (r.leader or r.campaign.label or "").lower(),
    "race": lambda r: (r.campaign.culture or "").lower(),
    "map": lambda r: (r.campaign_map.label if r.campaign_map else "").lower(),
    "outcome": lambda r: (r.outcome.label if r.outcome else "").lower(),
    "turns": lambda r: r.turns,
    "when": lambda r: r.ended_when or "",
    "reward": lambda r: r.reward,
    "sett": lambda r: r.settlements_gained,
    "lvl": lambda r: r.levels_gained,
    "decisions": lambda r: r.decisions,
}


def _slice_rows(rows, sorts, sort, desc, search, texts, page, page_size):
    if search:
        needle = search.lower()
        rows = [r for r in rows
                if any(needle in t.lower() for t in texts(r) if t)]
    key = sorts.get(sort or "")
    if key is not None:
        missing = [r for r in rows if key(r) is None]
        present = [r for r in rows if key(r) is not None]
        present.sort(key=key, reverse=bool(desc))
        rows = present + missing
    total = len(rows)
    size = max(1, min(int(page_size or 25), 200))
    pages = max(1, -(-total // size))
    at = min(max(0, int(page or 0)), pages - 1)
    return rows[at * size:(at + 1) * size], total, at, size


@db.timed
def campaigns_slice(con, sort=None, desc=True, map_key=None, culture=None,
                    outcome=None, search=None, page=0, page_size=25) -> dict:
    rows = campaign_rows_cached(con)
    if map_key:
        rows = [r for r in rows
                if r.campaign_map and r.campaign_map.raw == map_key]
    if culture:
        rows = [r for r in rows if (r.campaign.culture or "") == culture]
    if outcome:
        rows = [r for r in rows if r.outcome and r.outcome.raw == outcome]

    def texts(r):
        return (r.leader, r.campaign.label, r.campaign.raw,
                r.campaign.culture,
                r.outcome.label if r.outcome else None)

    got, total, at, size = _slice_rows(rows, _CAMPAIGN_SORTS, sort, desc,
                                       search, texts, page, page_size)
    return {"rows": got, "total": total, "page": at, "page_size": size}


@db.timed
def outcome_headline(rows) -> list:
    tally = {}
    for row in rows:
        if not row.outcome:
            continue
        tally.setdefault(row.outcome.raw, [0, row.outcome_state])
        tally[row.outcome.raw][0] += 1
    order = {"bad": 0, "warn": 1, "neutral": 2, "ok": 3}
    out = [OutcomeTally(outcome=_phrase(k), count=v[0], state=v[1]) for k, v in tally.items()]
    out.sort(key=lambda t: (order.get(t.state, 9), -t.count))
    return out


_GAINS_SQL = (
    "SELECT m.key AS campaign_map, f.key AS faction, c.campaign_id,"
    " c.campaign_key, c.first_ts, c.turns AS turns_reached,"
    " COALESCE(c.peak_settlements, 0) - COALESCE(fs.settlements, 0)"
    "  AS settlements_gained,"
    " COALESCE(c.peak_lord_level, 0) - COALESCE(fs.lord_level, 0)"
    "  AS levels_gained,"
    " COALESCE(c.allies_max, 0) - COALESCE(fs.allies, 0) AS allies_gained,"
    " COALESCE(c.vassals_max, 0) - COALESCE(fs.vassals, 0) AS vassals_gained"
    " FROM corpus.campaign c"
    " JOIN dict.faction f ON f.id = c.faction_id"
    " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
    " LEFT JOIN corpus.snapshot_campaign fs"
    "  ON fs.snapshot_id = c.first_snapshot_id"
    " WHERE c.n_decisions >= 2")


def gains_all(con) -> list:
    rows = []
    for r in con.execute(_GAINS_SQL):
        g = dict(r)
        g["reward"] = _weighted_reward(g["settlements_gained"], g["levels_gained"],
                                       g["allies_gained"], g["vassals_gained"])
        rows.append(g)
    rows.sort(key=lambda g: -(_f(g["first_ts"], 0.0) or 0.0))
    return rows


def _pool() -> dict:
    try:
        import presaves as PS
        radius = run_config.RUN.get("presave_radius")
        return {(p["campaign_map"], p["faction"]): p
                for p in PS.list_presaves(radius=radius)}
    except Exception:
        return {}


@db.timed
def pick_campaigns(con) -> dict:
    return {_i(r["ucb_pick_id"], 0): r["campaign_key"] for r in con.execute(
        "SELECT ucb_pick_id, campaign_key FROM corpus.campaign"
        " WHERE ucb_pick_id IS NOT NULL")}


@db.timed
def ucb_context(con, gains=None) -> dict:
    if gains is None:
        gains = gains_all(con)
    rewards: dict = {}
    for g in gains[:UCB.WINDOW]:
        rewards.setdefault((g["campaign_map"], g["faction"]), []).append(
            (_f(g["settlements_gained"], 0.0) or 0.0)
            + (_f(g["levels_gained"], 0.0) or 0.0))
    stats = UCB.start_stats(rewards)
    scale = UCB.window_blend(rewards)
    total = max(1, sum(d["n"] for d in stats.values()))
    k = _f(run_config.RUN.get("ucb"))
    c = None if k is None else k * scale
    adjust, adjust_path = UCB.load_adjustments()
    pool = _pool()
    scored = {}
    for key in set(pool) | set(stats):
        d = stats.get(key) or dict(UCB.EMPTY)
        played = d["n"] >= UCB.MIN_PLAYS
        a = adjust.get(key[1], 0.0)
        if c is None:
            b, e, s = (UCB.blend(d) if played else None), None, None
        else:
            b, e, s = UCB.score(d, c, total, a)
        scored[key] = {"d": d, "blend": b if played else None, "explore": e, "score": s,
                       "adjust": a}
    order = sorted(pool, key=lambda k: (
        -(scored[k]["score"] if scored[k]["score"] is not None else float("-inf")),
        pool[k].get("file") or ""))
    rank = {k: i + 1 for i, k in enumerate(order)}
    picks = [((r["campaign_map"], r["faction"])) for r in con.execute(
        "SELECT m.key AS campaign_map, f.key AS faction FROM corpus.ucb_pick p"
        " JOIN dict.faction f ON f.id = p.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = p.campaign_map_id"
        " ORDER BY p.pick_id")]
    pick_count, last_pick = {}, {}
    for i, key in enumerate(picks):
        pick_count[key] = pick_count.get(key, 0) + 1
        last_pick[key] = i
    plays_ago = {}
    for i, g in enumerate(gains):
        plays_ago.setdefault((g["campaign_map"], g["faction"]), i)
    top = max([int(round(max(v))) for v in rewards.values()] + [0])
    return {"rewards": rewards, "stats": stats, "total": total, "c": c, "k": k,
            "scale": scale, "adjust": adjust, "adjust_path": adjust_path,
            "pool": pool, "scored": scored, "rank": rank, "n_picks": len(picks),
            "pick_count": pick_count, "last_pick": last_pick, "plays_ago": plays_ago,
            "top_reward": top}


def _bins(vals, top) -> list:
    out = [0] * (top + 1)
    for v in vals:
        k = min(top, max(0, int(round(v))))
        out[k] += 1
    return out


@db.timed
def starts_rows(con, rows=None, cx=None, gains=None) -> list:
    if rows is None:
        rows = campaign_rows(con)
    if gains is None:
        gains = gains_all(con)
    per = {}
    for row in rows:
        fkey, _ = ident.split_campaign_key(row.campaign.raw)
        mkey = row.campaign_map.raw if row.campaign_map else ""
        b = per.setdefault((mkey, fkey), {"n": 0, "turns": [], "span_min": 0.0,
                                          "att": 0, "conf": 0})
        b["n"] += 1
        if row.turns is not None:
            b["turns"].append(row.turns)
        b["span_min"] += row.span_min or 0.0
        b["att"] += row.attempted
        b["conf"] += row.confirmed
    counts = {((r["campaign_map"] or ""), r["faction"]): r["n"] for r in con.execute(
        "SELECT m.key AS campaign_map, f.key AS faction, sc.n"
        " FROM corpus.start_counts sc"
        " JOIN dict.faction f ON f.id = sc.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = sc.campaign_map_id")}
    leaders = _start_leaders(con)
    if cx is None:
        cx = ucb_context(con, gains)
    window = gains[:UCB.WINDOW]
    win_rows: dict = {}
    for g in window:
        win_rows.setdefault((g["campaign_map"], g["faction"]), []).append(g)
    best = {}
    for g in gains:
        k = (g["campaign_map"], g["faction"])
        best[k] = max(best.get(k, 0.0), _f(g["reward"], 0.0) or 0.0)
    acc: dict = {}
    for g in gains:
        vals = acc.setdefault(((g["campaign_map"] or ""), g["faction"]),
                              {"s": [], "l": [], "a": [], "v": [], "t": []})
        s, l = _f(g["settlements_gained"], 0.0) or 0.0, _f(g["levels_gained"], 0.0) or 0.0
        vals["s"].append(s)
        vals["l"].append(l)
        vals["a"].append(_f(g["allies_gained"], 0.0) or 0.0)
        vals["v"].append(_f(g["vassals_gained"], 0.0) or 0.0)
        vals["t"].append(s + l)
    gains = {k: {"sb": max(v["s"]), "sa": sum(v["s"]) / len(v["s"]),
                 "lb": max(v["l"]), "la": sum(v["l"]) / len(v["l"]),
                 "ab": max(v["a"]), "aa": sum(v["a"]) / len(v["a"]),
                 "vb": max(v["v"]), "va": sum(v["v"]) / len(v["v"]),
                 "tb": max(v["t"]), "ta": sum(v["t"]) / len(v["t"])}
             for k, v in acc.items()}
    allied, vassal = {}, {}
    for r in con.execute(
            "SELECT c.campaign_key ckey,"
            " COUNT(*) FILTER (WHERE o.allies > 0) a,"
            " COUNT(*) FILTER (WHERE o.vassals > 0) v"
            " FROM corpus.turn_open o"
            " JOIN corpus.campaign c USING (campaign_id)"
            " GROUP BY c.campaign_key"):
        allied[r["ckey"]] = r["a"]
        vassal[r["ckey"]] = r["v"]
    ever_a, ever_v = {}, {}
    for row in rows:
        fkey, _ = ident.split_campaign_key(row.campaign.raw)
        k = (row.campaign_map.raw if row.campaign_map else "", fkey)
        ever_a[k] = ever_a.get(k, 0) + (1 if (allied.get(row.campaign.raw) or 0) else 0)
        ever_v[k] = ever_v.get(k, 0) + (1 if (vassal.get(row.campaign.raw) or 0) else 0)
    out = []
    for key in set(per) | set(cx["pool"]) | set(cx["stats"]):
        mkey, fkey = key
        b = per.get(key) or {"n": 0, "turns": [], "span_min": 0.0, "att": 0, "conf": 0}
        sc = cx["scored"].get(key) or {"d": dict(UCB.EMPTY), "blend": None,
                                       "explore": None, "score": None, "adjust": 0.0}
        d = sc["d"]
        g = gains.get(key)
        rewards = cx["rewards"].get(key) or []
        wr = win_rows.get(key) or []
        fin = lambda v: None if v is None or v == float("inf") else round(float(v), 4)
        out.append(StartRow(
            faction=_fac(fkey),
            leader=leaders.get((mkey or "", fkey)),
            campaign_map=_id(ident.campaign_map(mkey)) if mkey else None,
            in_pool=key in cx["pool"],
            n=counts.get(key, b["n"]),
            n_window=d["n"],
            mean=round(d["mean"], 4) if d["n"] else None,
            std=round(d["std"], 4) if d["n"] else None,
            entropy=round(d["entropy"], 4) if d["n"] else None,
            blend=fin(sc["blend"]), explore=fin(sc["explore"]), score=fin(sc["score"]),
            adjust=(sc.get("adjust") or None),
            rank=cx["rank"].get(key),
            picks=cx["pick_count"].get(key, 0),
            picks_ago=(cx["n_picks"] - 1 - cx["last_pick"][key]
                       if key in cx["last_pick"] else None),
            plays_ago=cx["plays_ago"].get(key),
            best=best.get(key),
            zero_rate=Rate(n=sum(1 for r in rewards if r <= 0), of=len(rewards),
                           noun="campaigns",
                           population="of this start inside the selector's window that "
                                      "gained nothing"),
            reward_bins=_bins(rewards, cx["top_reward"]),
            settlements_avg=(round(sum(_f(g["settlements_gained"], 0.0) or 0.0
                                       for g in wr) / len(wr), 3) if wr else None),
            levels_avg=(round(sum(_f(g["levels_gained"], 0.0) or 0.0
                                  for g in wr) / len(wr), 3) if wr else None),
            avg_turns=round(sum(b["turns"]) / len(b["turns"]), 1) if b["turns"] else None,
            sec_per_turn=(round(b["span_min"] * 60.0 / sum(b["turns"]), 1)
                          if sum(b["turns"]) else None),
            settlements_gained_best=_f(g["sb"]) if g else None,
            settlements_gained_avg=round(_f(g["sa"]) or 0, 2) if g else None,
            levels_gained_best=_f(g["lb"]) if g else None,
            levels_gained_avg=round(_f(g["la"]) or 0, 2) if g else None,
            allies_gained_best=_f(g["ab"]) if g else None,
            allies_gained_avg=round(_f(g["aa"]) or 0, 2) if g else None,
            vassals_gained_best=_f(g["vb"]) if g else None,
            vassals_gained_avg=round(_f(g["va"]) or 0, 2) if g else None,
            total_gained_best=_f(g["tb"]) if g else None,
            total_gained_avg=round(_f(g["ta"]) or 0, 2) if g else None,
            ever_allied=ever_a.get(key, 0), ever_vassal=ever_v.get(key, 0),
            confirm_rate=Rate(n=b["conf"], of=b["att"], noun="actions",
                              population="attempted across this start's campaigns")))
    out.sort(key=lambda r: (-(r.total_gained_avg or 0.0), -r.n, r.faction.label))
    return out


def _hist(rows, field, top=None) -> list:
    by_x: dict = {}
    for g in rows:
        v = _f(g[field], 0.0) or 0.0
        x = int(round(v))
        if top is not None:
            x = min(top, max(0, x))
        by_x.setdefault(x, {})
        m = g["campaign_map"] or ""
        by_x[x][m] = by_x[x].get(m, 0) + 1
    if not by_x:
        return []
    lo, hi = min(by_x), max(by_x)
    return [HistBin(x=x, counts=by_x.get(x, {})) for x in range(lo, hi + 1)]


@db.timed
def starts_page_extras(con, cx=None, gains=None) -> dict:
    if gains is None:
        gains = gains_all(con)
    if cx is None:
        cx = ucb_context(con, gains)
    window = gains[:UCB.WINDOW]
    maps = sorted({g["campaign_map"] or "" for g in window} | {k[0] for k in cx["pool"]})
    ns = [cx["stats"][k]["n"] for k in cx["pool"] if k in cx["stats"]]
    ns_all = [cx["stats"].get(k, UCB.EMPTY)["n"] for k in cx["pool"]]
    rewards = [_f(g["reward"], 0.0) or 0.0 for g in window]
    zeros = sum(1 for r in rewards if r <= 0)
    ns_sorted = sorted(ns_all, reverse=True)
    top10 = (sum(ns_sorted[:10]) / sum(ns_sorted)) if sum(ns_sorted) else 0.0
    blocks = []
    asc = list(reversed(window))
    for i in range(0, len(asc), 50):
        chunk = asc[i:i + 50]
        if len(chunk) >= 10:
            blocks.append(round(sum(_f(g["reward"], 0.0) or 0.0 for g in chunk)
                                / len(chunk), 3))
    under = sum(1 for n_ in ns_all if n_ < UCB.MIN_PLAYS)
    tiles = [
        Metric(label="starts in pool", value=len(cx["pool"]),
               sub="%d played inside the window" % len(ns)),
        Metric(label="plays in window", value=cx["total"],
               sub="of %d campaigns all time" % len(gains)),
        Metric(label="plays per start", value=(statistics.median(ns) if ns else None),
               sub=("min %d · max %d" % (min(ns), max(ns)) if ns else None)),
        Metric(label="reward per campaign",
               value=(round(sum(rewards) / len(rewards), 2) if rewards else None),
               spark=blocks, sub="trailing 50-campaign means"),
        Metric(label="zero reward", value=(round(100.0 * zeros / len(rewards)) if rewards
                                           else None), unit="%",
               sub="%d of %d campaigns in window" % (zeros, len(rewards))),
        Metric(label="gini of plays", value=round(UCB.gini(ns_all), 3) if ns_all else None,
               sub="top 10 starts hold %d%%" % round(100 * top10)),
        Metric(label="under %d plays" % UCB.MIN_PLAYS, value=under,
               sub="scored infinite, played first",
               state="warn" if under else "neutral"),
        Metric(label="C", value=cx["c"], sub="explore = C·sqrt(ln plays / n)"),
        Metric(label="manual adjust",
               value=sum(1 for key in cx["pool"] if cx["adjust"].get(key[1])),
               sub=("/".join((cx["adjust_path"] or "").replace("\\", "/").split("/")[-2:])
                    if cx["adjust_path"] else "no %s" % UCB.ADJUST_FILE),
               state="neutral"),
    ]
    return {"tiles": tiles, "maps": [_id(ident.campaign_map(m)) for m in maps if m],
            "reward_bins": _hist(window, "reward", cx["top_reward"]),
            "turns_bins": _hist(window, "turns_reached")}


OPEN_FAMILIES = ("building", "skills", "research", "items")
OPEN_BANDS = {"1-3": (1, 3), "4-6": (4, 6), "7+": (7, 10 ** 6)}
FAMILY_LABEL = {"building": "first building", "skills": "first skill",
                "research": "first research", "items": "first item"}
PERF_BARS = 240
RIBBON_BUCKET_MIN = 15
RIBBON_BUCKETS_MAX = 24


@db.timed
def start_head(con, mkey: str, fkey: str, gains=None, cx=None):
    gains = gains_all(con) if gains is None else gains
    cx = ucb_context(con, gains) if cx is None else cx
    key = (mkey, fkey)
    mine = [g for g in gains if (g["campaign_map"] or "") == mkey
            and g["faction"] == fkey]
    if not mine and key not in cx["pool"]:
        return None, gains, cx
    ids = [_i(r["campaign_id"], 0) for r in con.execute(
        "SELECT c.campaign_id FROM corpus.campaign c"
        " JOIN dict.faction f ON f.id = c.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
        " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND f.key = %s", key)]
    turns, span_min = [], 0.0
    if ids:
        for r in con.execute(
                "SELECT MAX(s.turn) t, MIN(s.ts) t0, MAX(s.ts) t1"
                " FROM corpus.snapshot s"
                " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
                " WHERE s.campaign_id = ANY(%s) GROUP BY s.campaign_id", (ids,)):
            if r["t"] is not None:
                turns.append(_i(r["t"], 0) or 0)
            span_min += ((_f(r["t1"]) or 0.0) - (_f(r["t0"]) or 0.0)) / 60.0
    att = conf = 0
    if ids:
        c_ = con.execute(
            "SELECT COUNT(*) FILTER (WHERE refusal_id IS NULL"
            "  OR refusal_id <> ALL(%s)) att,"
            " COUNT(*) FILTER (WHERE counted) conf"
            " FROM corpus.taken WHERE campaign_id = ANY(%s)",
            (_skip_refusals(), ids)).fetchone()
        att, conf = _i(c_["att"], 0) or 0, _i(c_["conf"], 0) or 0
    d = (cx["stats"].get(key) or dict(UCB.EMPTY))
    mine_rewards = [_f(g["reward"], 0.0) or 0.0 for g in mine]
    counts = con.execute(
        "SELECT sc.n FROM corpus.start_counts sc"
        " JOIN dict.faction f ON f.id = sc.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = sc.campaign_map_id"
        " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND f.key = %s",
        key).fetchone()
    row = StartRow(
        faction=_fac(fkey),
        leader=_start_leaders(con).get((mkey or "", fkey)),
        campaign_map=_id(ident.campaign_map(mkey)) if mkey else None,
        in_pool=key in cx["pool"],
        n=_i(counts["n"], len(mine)) if counts else len(mine),
        n_window=d["n"],
        mean=round(statistics.mean(mine_rewards), 4) if mine_rewards else None,
        std=(round(statistics.pstdev(mine_rewards), 4)
             if len(mine_rewards) > 1 else None),
        best=max(mine_rewards) if mine_rewards else None,
        zero_rate=Rate(n=sum(1 for r in mine_rewards if r <= 0),
                       of=len(mine_rewards), noun="campaigns",
                       population="of this start that gained nothing"),
        avg_turns=round(sum(turns) / len(turns), 1) if turns else None,
        sec_per_turn=round(span_min * 60.0 / sum(turns), 1) if sum(turns) else None,
        confirm_rate=Rate(n=conf, of=att, noun="actions",
                          population="attempted across this start's campaigns"))
    return row, gains, cx


@db.timed
def start_last_played(con, mkey: str, fkey: str) -> StartCampaign | None:
    mine = _start_gains(con, mkey, fkey)
    if not mine:
        return None
    g = mine[-1]
    outcome = g["outcome"]
    return StartCampaign(
        campaign=_camp(g["campaign_key"]), ts=_f(g["first_ts"]),
        turns=_i(g["turns_reached"]), reward=_f(g["reward"]),
        settlements_gained=_f(g["settlements_gained"]),
        levels_gained=_f(g["levels_gained"]),
        outcome=_phrase(outcome) if outcome else None,
        outcome_state=_OUTCOME_STATE.get(outcome or "", "neutral"))


@db.timed
def start_firsts(con, mkey: str, fkey: str) -> dict:
    out: dict = {}
    for r in con.execute(
            "SELECT DISTINCT ON (t.campaign_id, at.key)"
            " t.campaign_id, at.key AS action_type, a.action_key, t.decision_id"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
            " JOIN dict.faction f ON f.id = c.faction_id"
            " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
            " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND f.key = %s"
            "   AND t.counted AND at.key = ANY(%s)"
            " ORDER BY t.campaign_id, at.key, t.decision_id",
            (mkey, fkey, list(OPEN_FAMILIES))):
        out.setdefault(_i(r["campaign_id"], 0), {})[r["action_type"]] = (
            r["action_key"], _i(r["decision_id"], 0))
    return out


def _open_ident(fam, key):
    if not key:
        return None
    return Ident(raw=key, label=labels.name_for(fam, key) or labels.pretty(key))


_STAMPED: dict = {}


def _stamped(name, build, force=False):
    key = db.stamp()
    hit = _STAMPED.get(name)
    if not force and hit and hit[0] == key:
        return hit[1]
    v = build()
    _STAMPED[name] = (key, v)
    return v


WARM_EVERY_S = 120.0
_WARM_ALIVE = threading.Event()

REWARD_COMPONENTS = (
    ("settlements", "settlements gained", 1.0),
    ("lord_levels", "legendary lord levels gained", 1.0),
    ("allies", "allies gained", 0.0),
    ("vassals", "vassals gained", 0.0),
)
REWARD_WEIGHTS_PATH = os.path.join(common.TWDATA, "rules", "reward_weights.json")
_weights_cache: dict = {"mtime": "?", "weights": None}


def reward_weights() -> dict:
    try:
        mt = os.path.getmtime(REWARD_WEIGHTS_PATH)
    except OSError:
        mt = None
    if _weights_cache["weights"] is not None and _weights_cache["mtime"] == mt:
        return _weights_cache["weights"]
    w = {k: dflt for k, _l, dflt in REWARD_COMPONENTS}
    if mt is not None:
        try:
            with open(REWARD_WEIGHTS_PATH, encoding="utf-8") as f:
                got = json.load(f)
            for k, _l, _d in REWARD_COMPONENTS:
                v = _f((got.get("weights") or {}).get(k))
                if v is not None:
                    w[k] = v
        except (OSError, ValueError):
            pass
    _weights_cache.update(mtime=mt, weights=w)
    return w


def set_reward_weights(weights: dict) -> dict:
    allowed = {k for k, _l, _d in REWARD_COMPONENTS}
    clean = {}
    for k, v in (weights or {}).items():
        if k not in allowed:
            raise ValueError("unknown reward component %r" % k)
        fv = _f(v)
        if fv is None or fv < 0 or fv > 1000:
            raise ValueError("the %r weight must be a number in [0, 1000]" % k)
        clean[k] = round(fv, 3)
    w = {k: dflt for k, _l, dflt in REWARD_COMPONENTS}
    w.update(clean)
    os.makedirs(os.path.dirname(REWARD_WEIGHTS_PATH), exist_ok=True)
    with open(REWARD_WEIGHTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"weights": w}, f, indent=1)
    _weights_cache.update(mtime="?", weights=None)
    _STAMPED.clear()
    return reward_weights()


def _weighted_reward(settlements_gained, levels_gained, allies_gained,
                     vassals_gained) -> float:
    w = reward_weights()
    return round(w["settlements"] * (_f(settlements_gained, 0.0) or 0.0)
                 + w["lord_levels"] * (_f(levels_gained, 0.0) or 0.0)
                 + w["allies"] * (_f(allies_gained, 0.0) or 0.0)
                 + w["vassals"] * (_f(vassals_gained, 0.0) or 0.0), 3)


_FACT_CAMPS = (
    "camps AS (SELECT c.campaign_id, c.campaign_key, f.key AS faction,"
    " COALESCE(m.key, '') campaign_map, c.leader,"
    " c.first_ts, c.turns AS turns_reached,"
    " ((COALESCE(c.peak_settlements, 0) - COALESCE(fs.settlements, 0)) * %(w_s)s"
    "  + (COALESCE(c.peak_lord_level, 0) - COALESCE(fs.lord_level, 0)) * %(w_l)s"
    "  + (COALESCE(c.allies_max, 0) - COALESCE(fs.allies, 0)) * %(w_a)s"
    "  + (COALESCE(c.vassals_max, 0) - COALESCE(fs.vassals, 0)) * %(w_v)s) reward"
    " FROM corpus.campaign c"
    " JOIN dict.faction f ON f.id = c.faction_id"
    " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
    " LEFT JOIN corpus.snapshot_campaign fs"
    "  ON fs.snapshot_id = c.first_snapshot_id"
    " WHERE c.n_decisions >= 2)")

_ACQ_DICT = {"research": "tech_node", "items": "ancillary", "skills": "skill",
             "traits": "trait", "building": "building", "settlement": "region"}


def _acq_join(family, alias="a"):
    return " JOIN dict.%s dk ON dk.id = %s.key_id" % (_ACQ_DICT[family], alias)


def _rows(sql, params=None) -> list:
    return db.connect().execute(sql, params).fetchall()


def _one(sql, params=None):
    return db.connect().execute(sql, params).fetchone()


def _fact_params(**extra) -> dict:
    w = reward_weights()
    out = {"w_s": w["settlements"], "w_l": w["lord_levels"],
           "w_a": w["allies"], "w_v": w["vassals"]}
    out.update(extra)
    return out


def _fact_campaign_count() -> int:
    got = _one("SELECT COUNT(*) n FROM corpus.campaign WHERE n_decisions >= 2")
    return _i((got or {}).get("n"), 0) or 0


def _stamped_slow(name, build, force=False):
    return _stamped(name, build, force=force)


def warm_caches(con) -> list:
    out = []
    for label, fn in (
            ("camp_meta", lambda: _camp_meta(con, force=True)),
            ("campaign_rows", lambda: campaign_rows_cached(con, force=True)),
            ("positions", lambda: _positions_data(con, force=True)),
            ("history", lambda: [_hist_map(con, fam, force=True)
                                 for fam in POSITION_HISTORY]),
    ):
        t0 = time.perf_counter()
        try:
            fn()
            out.append("%s=%.0fms" % (label, (time.perf_counter() - t0) * 1000))
        except Exception as e:
            out.append("%s=FAILED:%s" % (label, str(e)[:120]))
    return out


def campaign_row(con, campaign_key: str):
    rows = _stamped("campaign_rows", lambda: campaign_rows(con))
    return next((r for r in rows if r.campaign.raw == campaign_key), None)


def start_campaigns_slice(con, mkey: str, fkey: str) -> list:
    rows = _stamped("campaign_rows", lambda: campaign_rows(con))
    by_camp = {r.campaign.raw: r for r in rows}
    ids = {r["campaign_key"]: _i(r["campaign_id"], 0) for r in con.execute(
        "SELECT c.campaign_id, c.campaign_key FROM corpus.campaign c"
        " JOIN dict.faction f ON f.id = c.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
        " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND f.key = %s",
        (mkey, fkey))}
    firsts = start_firsts(con, mkey, fkey)
    camps = []
    for g in gains_all(con):
        if (g["campaign_map"] or "") != mkey or g["faction"] != fkey:
            continue
        cr = by_camp.get(g["campaign_key"])
        f = firsts.get(ids.get(g["campaign_key"]) or -1, {})
        camps.append(StartCampaign(
            campaign=_camp(g["campaign_key"]), ts=_f(g["first_ts"]),
            turns=(cr.turns if cr else _i(g["turns_reached"])),
            reward=_f(g["reward"]), settlements_gained=_f(g["settlements_gained"]),
            levels_gained=_f(g["levels_gained"]),
            outcome=cr.outcome if cr else None,
            outcome_state=cr.outcome_state if cr else "neutral",
            ended_because=cr.ended_because if cr else None,
            decisions=cr.decisions if cr else 0,
            confirm_rate=cr.confirm_rate if cr else None,
            first_research=_open_ident("research", (f.get("research") or (None,))[0]),
            first_skill=_open_ident("skills", (f.get("skills") or (None,))[0]),
            first_building=_open_ident("building", (f.get("building") or (None,))[0])))
    return camps


def _start_gains(con, mkey: str, fkey: str) -> list:
    out = []
    for r in con.execute(
            "SELECT g.*, o.key AS outcome FROM (" + _GAINS_SQL + ") g"
            " JOIN corpus.campaign c USING (campaign_id)"
            " LEFT JOIN dict.enum o ON o.enum_id = c.outcome_id"
            " WHERE g.campaign_map IS NOT DISTINCT FROM NULLIF(%s, '')"
            " AND g.faction = %s ORDER BY g.first_ts", (mkey, fkey)):
        d = dict(r)
        d["reward"] = _weighted_reward(d["settlements_gained"], d["levels_gained"],
                                       d["allies_gained"], d["vassals_gained"])
        out.append(d)
    return out


def _pearson(xs, ys):
    n = len(xs)
    if n < 12:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / math.sqrt(sxx * syy), 3)


@db.timed
def start_performance(con, mkey: str, fkey: str, gains=None, cx=None) -> dict:
    gains = gains_all(con) if gains is None else gains
    mine = _start_gains(con, mkey, fkey)
    pool_vals = [_f(g["reward"], 0.0) or 0.0 for g in gains]
    mine_vals = [_f(g["reward"], 0.0) or 0.0 for g in mine]
    top = max([int(round(v)) for v in pool_vals + mine_vals] + [0])
    pop = _bins(pool_vals, top)
    k = max(1, math.ceil(len(mine) / PERF_BARS))
    bars, totals = [], [g["reward"] for g in mine]
    for i0 in range(0, len(mine), k):
        chunk = mine[i0:i0 + k]
        setts = sum((_f(g["settlements_gained"], 0.0) or 0.0) for g in chunk) / len(chunk)
        lvls = sum((_f(g["levels_gained"], 0.0) or 0.0) for g in chunk) / len(chunk)
        i1 = i0 + len(chunk) - 1
        lo = max(0, i1 - 4)
        bars.append(PerfBar(
            id=chunk[-1]["campaign_key"],
            label=("campaign %d" % (i0 + 1) if k == 1
                   else "campaigns %d-%d" % (i0 + 1, i1 + 1)),
            ts=_f(chunk[-1]["first_ts"]), settlements=round(setts, 2),
            levels=round(lvls, 2),
            total_max=round(max(g["reward"] for g in chunk), 1), n=len(chunk),
            trail=round(sum(totals[lo:i1 + 1]) / len(totals[lo:i1 + 1]), 2)))
    max_t = max([_i(g["turns_reached"], 0) or 0 for g in mine] + [1])
    hist = [0] * min(max_t, 40)
    for g in mine:
        t = _i(g["turns_reached"], 0) or 0
        if t >= 1:
            hist[min(t, len(hist)) - 1] += 1
    outs: dict = {}
    for g in mine:
        o = g["outcome"] or "no_ending_recorded"
        outs.setdefault(o, []).append(_i(g["turns_reached"], 0) or 0)
    outcomes = [OutcomeCount(outcome=_phrase(o), state=_OUTCOME_STATE.get(o, "neutral"),
                             n=len(ts), avg_turns=round(sum(ts) / len(ts), 1) if ts else None)
                for o, ts in sorted(outs.items(), key=lambda kv: -len(kv[1]))]
    bands = []
    for label, (lo_t, hi_t) in (("short · 1-3 turns", (1, 3)), ("mid · 4-6 turns", (4, 6)),
                                ("long · 7+ turns", (7, 10 ** 6))):
        sel = [g for g in mine if lo_t <= (_i(g["turns_reached"], 0) or 0) <= hi_t]
        rpt = [g["reward"] / (_i(g["turns_reached"], 0) or 1) for g in sel]
        bands.append(LengthBand(
            label=label, n=len(sel),
            avg_reward=round(sum(g["reward"] for g in sel) / len(sel), 2) if sel else None,
            reward_per_turn=round(sum(rpt) / len(rpt), 2) if rpt else None))
    pairs = [(g["reward"], _i(g["turns_reached"], 0) or 0) for g in mine
             if (_i(g["turns_reached"], 0) or 0) >= 1]
    return {"bucket": k, "bars": bars, "reward_bins": _bins(mine_vals, top),
            "population_bins": pop,
            "pool_mean": round(sum(pool_vals) / len(pool_vals), 2) if pool_vals else None,
            "turns_hist": hist, "outcomes": outcomes, "bands": bands,
            "reward_turns_r": _pearson([p[0] for p in pairs], [p[1] for p in pairs])}


@db.timed
def start_openings(con, mkey: str, fkey: str, band: str = "all") -> dict:
    meta = {}
    for g in _start_gains(con, mkey, fkey):
        t = _i(g["turns_reached"], 0) or 0
        lo, hi = OPEN_BANDS.get(band, (0, 10 ** 6))
        if band == "all" or lo <= t <= hi:
            meta[_i(g["campaign_id"], 0)] = (g["reward"], t, g["campaign_key"])
    rewards = [v[0] for v in meta.values()]
    mean_r = sum(rewards) / len(rewards) if rewards else None
    sd_r = statistics.pstdev(rewards) if len(rewards) > 1 else None
    firsts = start_firsts(con, mkey, fkey)
    offered: dict = {}
    for r in con.execute(
            "WITH f AS ("
            " SELECT DISTINCT ON (t.campaign_id, at.key)"
            "  t.campaign_id, at.key AS action_type, at.id AS type_id, t.decision_id"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
            " JOIN dict.faction fc ON fc.id = c.faction_id"
            " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
            " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND fc.key = %s"
            "   AND t.counted AND at.key = ANY(%s)"
            " ORDER BY t.campaign_id, at.key, t.decision_id)"
            " SELECT f.action_type, a2.action_key,"
            "        COUNT(DISTINCT f.campaign_id) offered"
            " FROM f JOIN corpus.offer o ON o.decision_id = f.decision_id"
            " JOIN dict.action a2 ON a2.action_id = o.action_id"
            "  AND a2.action_type_id = f.type_id"
            " GROUP BY 1, 2", (mkey, fkey, list(OPEN_FAMILIES))):
        offered[(r["action_type"], r["action_key"])] = _i(r["offered"], 0) or 0
    families = []
    for fam in OPEN_FAMILIES:
        by_key: dict = {}
        for cid, picks in firsts.items():
            if cid not in meta or fam not in picks:
                continue
            by_key.setdefault(picks[fam][0], []).append(meta[cid])
        covered = sum(len(v) for v in by_key.values())
        stats = []
        for key, vals in by_key.items():
            rs = [v[0] for v in vals]
            ts = [max(1, v[1]) for v in vals]
            n = len(vals)
            avg = sum(rs) / n
            stats.append(OpeningBranch(
                key=key, label=labels.name_for(fam, key) or labels.pretty(key), n=n,
                share=round(100.0 * n / covered, 1) if covered else None,
                avg_reward=round(avg, 2),
                delta_mean=round(avg - mean_r, 2) if mean_r is not None else None,
                avg_turns=round(sum(ts) / n, 1),
                reward_per_turn=round(sum(r / t for r, t in zip(rs, ts)) / n, 2),
                offered=offered.get((fam, key), 0), taken=n))
        stats.sort(key=lambda b: -b.n)
        head, tail = stats[:8], stats[8:]
        pooled = None
        if tail:
            pn = sum(b.n for b in tail)
            pooled = OpeningBranch(
                key="", label="other (%d branches)" % len(tail), n=pn,
                share=round(100.0 * pn / covered, 1) if covered else None,
                avg_reward=round(sum(b.avg_reward * b.n for b in tail) / pn, 2)
                if pn else None)
        offers = [OpeningOffer(key=k2, label=labels.name_for(fam, k2) or labels.pretty(k2),
                               offered=off, taken=next((b.n for b in stats if b.key == k2), 0),
                               avg_reward_taken=next(
                                   (b.avg_reward for b in stats if b.key == k2), None))
                  for (f2, k2), off in offered.items() if f2 == fam and off >= 10]
        offers.sort(key=lambda o: (-o.offered, o.taken))
        big = [b for b in head if b.n >= 10]
        spread = (round(max(b.avg_reward for b in big) - min(b.avg_reward for b in big), 2)
                  if len(big) >= 2 else None)
        pairs = sum(off for (f3, _k3), off in offered.items() if f3 == fam)
        families.append(OpeningFamily(
            family=fam, label=FAMILY_LABEL[fam],
            coverage=Rate(n=covered, of=len(meta), noun="campaigns",
                          population="in this band that took a first %s" % fam),
            avg_offers=round(pairs / covered, 1) if covered else None,
            spread=spread, branches=head, pooled=pooled, offers=offers[:12]))
    families.sort(key=lambda f: -(f.spread if f.spread is not None else -1))
    ordered = sorted(meta.items(), key=lambda kv: kv[1][2])
    seq = []
    for cid, _m in ordered:
        picks = firsts.get(cid) or {}
        seq.append((picks.get("building") or (None,))[0])
    counts: dict = {}
    for k4 in seq:
        if k4:
            counts[k4] = counts.get(k4, 0) + 1
    rib_keys = [k5 for k5, _n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    bsize = max(RIBBON_BUCKET_MIN, math.ceil(len(seq) / RIBBON_BUCKETS_MAX) if seq else 1)
    ribbon = []
    for i0 in range(0, len(seq), bsize):
        chunk = seq[i0:i0 + bsize]
        shares = [round(100.0 * sum(1 for s in chunk if s == k6) / len(chunk), 1)
                  for k6 in rib_keys]
        ribbon.append(RibbonBucket(label="c%d-%d" % (i0 + 1, i0 + len(chunk)),
                                   shares=shares))
    steps: dict = {}
    conquered = set()
    for r in con.execute(
            "SELECT t.campaign_id, a.action_key, s.turn,"
            "  ROW_NUMBER() OVER (PARTITION BY t.campaign_id ORDER BY t.decision_id) step"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
            " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
            " JOIN dict.faction fc ON fc.id = c.faction_id"
            " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
            " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND fc.key = %s"
            "   AND t.counted AND at.key = 'attack_settlement'", (mkey, fkey)):
        cid = _i(r["campaign_id"], 0)
        if cid not in meta:
            continue
        conquered.add(cid)
        s = _i(r["step"], 0) or 0
        if 1 <= s <= 6:
            steps.setdefault(s, []).append((r["action_key"], _i(r["turn"])))
    conquest = []
    for s in sorted(steps):
        rows_ = steps[s]
        keys = {}
        for k7, _t in rows_:
            keys[k7] = keys.get(k7, 0) + 1
        mode = max(keys.items(), key=lambda kv: kv[1])[0]
        turns = sorted(t for _k, t in rows_ if t is not None)
        conquest.append(ConquestStep(
            step=s, key=mode,
            label=labels.name_for("attack_settlement", mode) or labels.pretty(mode),
            reached=len(rows_), of=len(conquered),
            median_turn=float(turns[len(turns) // 2]) if turns else None))
    return {"band": band, "campaigns": len(meta),
            "mean_reward": round(mean_r, 2) if mean_r is not None else None,
            "sd_reward": round(sd_r, 2) if sd_r is not None else None,
            "families": families, "ribbon_family": "building",
            "ribbon_keys": rib_keys,
            "ribbon_labels": [labels.name_for("building", k8) or labels.pretty(k8)
                              for k8 in rib_keys],
            "ribbon": ribbon, "conquest": conquest,
            "no_settlement": len(meta) - len(conquered)}


ITEM_ACTIONS = ("items", "item_unequip")


def _parent_ident(parents, key):
    got = parents.get(key) or []
    if not got:
        return None
    names = [labels.tech_name(p) or labels.pretty(p) for p in got]
    return Ident(raw=got[0], label=" + ".join(n for n in names if n))


def _tech_depth(parents, keys) -> dict:
    depth: dict = {}

    def walk(k, seen):
        if k in depth:
            return depth[k]
        if k in seen:
            return 1
        ps = parents.get(k) or []
        depth[k] = 1 + max([walk(p, seen | {k}) for p in ps], default=0)
        return depth[k]

    for k in keys:
        walk(k, set())
    return depth


def _mean(vals, digits=2):
    return round(sum(vals) / len(vals), digits) if vals else None


def _camp_meta(con, force=False) -> dict:
    def build():
        camp = {}
        for r in con.execute(
                "SELECT g.campaign_id, g.campaign_key, g.faction, g.campaign_map,"
                " c.leader, g.settlements_gained, g.levels_gained,"
                " g.allies_gained, g.vassals_gained, g.turns_reached, g.first_ts"
                " FROM (" + _GAINS_SQL + ") g"
                " JOIN corpus.campaign c USING (campaign_id)"):
            d = dict(r)
            d["reward"] = _weighted_reward(d["settlements_gained"],
                                           d["levels_gained"],
                                           d["allies_gained"],
                                           d["vassals_gained"])
            camp[_i(r["campaign_id"], 0)] = d
        return camp
    return _stamped_slow("camp_meta", build, force=force)


def _start_of(c) -> tuple:
    return ((c["campaign_map"] or ""), c["faction"])


def _item_ident(key):
    return labels.name_for("items", key) or labels.pretty(key)


def _fact_item_rows(mkey=None, fkey=None, min_side=5) -> list:
    extra = ""
    params = _fact_params()
    if fkey is not None:
        extra = " WHERE m.campaign_map = %(mkey)s AND m.faction = %(fkey)s"
        params.update(mkey=mkey or "", fkey=fkey)
    got = _rows(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, dk.key, BOOL_OR(a.acquired_turn IS NOT NULL) worn"
        " FROM acquisition a" + _acq_join("items") +
        " WHERE a.family = 'items' GROUP BY 1, 2)"
        " SELECT p.key, COUNT(*) held,"
        "  COUNT(*) FILTER (WHERE p.worn) eq,"
        "  AVG(m.reward) FILTER (WHERE p.worn) req,"
        "  AVG(m.reward) FILTER (WHERE NOT p.worn) rb"
        " FROM per p JOIN camps m USING (campaign_id)" + extra +
        " GROUP BY p.key", params)
    res = labels.item_resources()
    out = []
    for r in got:
        held, eq = _i(r["held"], 0) or 0, _i(r["eq"], 0) or 0
        req, rb = _f(r["req"]), _f(r["rb"])
        req = round(req, 2) if req is not None else None
        rb = round(rb, 2) if rb is not None else None
        both = eq >= min_side and (held - eq) >= min_side
        out.append(ItemRow(
            key=r["key"], label=_item_ident(r["key"]),
            category=labels.item_category(r["key"]),
            resources=res.get(r["key"]) or {},
            held_in=held, equipped_in=eq, benched_in=held - eq,
            avg_reward_equipped=req, avg_reward_benched=rb,
            delta=round(req - rb, 2)
            if both and req is not None and rb is not None else None))
    out.sort(key=lambda r2: (-r2.equipped_in, -r2.held_in, r2.key))
    return out


def _fact_item_counts(mkey=None, fkey=None) -> dict:
    extra = ""
    params: dict = {}
    if fkey is not None:
        extra = (" JOIN corpus.campaign c ON c.campaign_id = e.campaign_id"
                 " JOIN dict.faction cf ON cf.id = c.faction_id"
                 " LEFT JOIN dict.campaign_map cm ON cm.id = c.campaign_map_id"
                 " WHERE COALESCE(cm.key, '') = %(mkey)s"
                 " AND cf.key = %(fkey)s")
        params.update(mkey=mkey or "", fkey=fkey)
    out: dict = {}
    for r in _rows(
            "SELECT e.campaign_id cid, dk.key,"
            " COUNT(*) FILTER (WHERE e.kind = 'on') ons,"
            " COUNT(*) FILTER (WHERE e.kind = 'off') offs"
            " FROM item_event e"
            " JOIN dict.ancillary dk ON dk.id = e.ancillary_id"
            + extra + " GROUP BY 1, 2", params):
        out[(int(r["cid"]), r["key"])] = (_i(r["ons"], 0) or 0,
                                          _i(r["offs"], 0) or 0)
    return out


def _resource_columns(rows, floor=2, cap=40) -> list:
    counts: dict = {}
    for r in rows:
        for name in r.resources:
            counts[name] = counts.get(name, 0) + 1
    got = sorted((n2 for n2 in counts.items() if n2[1] >= floor),
                 key=lambda kv: -kv[1])
    return [name for name, _c in got[:cap]]


def start_items(con, mkey: str, fkey: str) -> dict:
    rewards = {int(r["campaign_id"]): _f(r["reward"]) for r in _rows(
        "WITH " + _FACT_CAMPS + " SELECT campaign_id, reward FROM camps"
        " WHERE campaign_map = %(mkey)s AND faction = %(fkey)s",
        _fact_params(mkey=mkey or "", fkey=fkey))}
    per_c: dict = {}
    for r in _rows(
            "SELECT a.campaign_id cid,"
            " COUNT(*) held,"
            " COUNT(*) FILTER (WHERE a.acquired_turn IS NOT NULL) eq"
            " FROM acquisition a WHERE a.family = 'items'"
            " AND a.campaign_id = ANY(%(cids)s) GROUP BY 1",
            {"cids": sorted(rewards)}):
        per_c[int(r["cid"])] = {"held": _i(r["held"], 0) or 0,
                                "eq": _i(r["eq"], 0) or 0,
                                "equips": 0, "uneq": 0, "churn": False}
    for (cid, _key), (ons, offs) in _fact_item_counts(mkey, fkey).items():
        pc = per_c.get(cid)
        if pc is None:
            continue
        pc["equips"] += ons
        pc["uneq"] += offs
        if ons >= 2 and offs >= 1:
            pc["churn"] = True
    groups = (("wore something from every held item", lambda p: p["held"] == p["eq"]),
              ("left held items benched", lambda p: p["eq"] < p["held"]),
              ("churned the same item", lambda p: p["churn"]))
    behaviour = []
    for label, fn in groups:
        sel = [(cid, p) for cid, p in per_c.items() if fn(p)]
        behaviour.append(BehaviourRow(
            label=label, campaigns=len(sel),
            avg_reward=_mean([rewards[cid] for cid, _p in sel
                              if rewards.get(cid) is not None]),
            avg_equips=_mean([p["equips"] for _c, p in sel], 1),
            avg_unequips=_mean([p["uneq"] for _c, p in sel], 1)))
    rows = _fact_item_rows(mkey, fkey)
    return {"rows": rows, "resources": _resource_columns(rows),
            "behaviour": behaviour}


SWAP_GAP_TURNS = 2
SWAP_FLAP_TURNS = 1


@db.timed
def _fact_item_swaps() -> dict:
    camp = {int(r["campaign_id"]): r for r in _rows(
        "WITH " + _FACT_CAMPS +
        " SELECT campaign_id, campaign_map, faction, reward, turns_reached"
        " FROM camps", _fact_params())}
    means: dict = {}
    for c in camp.values():
        means.setdefault((c["campaign_map"], c["faction"]), []).append(
            _f(c["reward"], 0.0) or 0.0)
    means = {k: sum(v) / len(v) for k, v in means.items()}
    per: dict = {}
    for r in _rows(
            "SELECT e.campaign_id cid, e.character_id ctx, dk.key, e.kind,"
            " e.snapshot_id did, e.turn"
            " FROM item_event e"
            " JOIN dict.ancillary dk ON dk.id = e.ancillary_id"
            " ORDER BY e.campaign_id, e.character_id, e.snapshot_id, e.event_id"):
        per.setdefault((int(r["cid"]), str(r["ctx"])), []).append(
            (str(r["kind"]), r["key"], _i(r["turn"]), _i(r["did"], 0)))
    events = 0
    pairs: dict = {}
    for (cid, _ctx), evs in per.items():
        end_turn = _i((camp.get(cid) or {}).get("turns_reached"))
        offs: dict = {}
        ons: dict = {}
        for kind, key, turn, did in evs:
            (offs if kind == "off" else ons).setdefault(key, []).append(
                (turn, did))
        flap_off: set = set()
        flap_on: set = set()
        for key, alist in offs.items():
            for aturn, adid in alist:
                if aturn is None:
                    continue
                back = [(t2, d2) for t2, d2 in ons.get(key, ())
                        if d2 > adid and t2 is not None
                        and t2 - aturn <= SWAP_FLAP_TURNS]
                if back:
                    flap_off.add((key, adid))
                    flap_on.add((key, min(back)[1]))
        for ak, alist in offs.items():
            cat = labels.item_category(ak)
            if not cat:
                continue
            for aturn, adid in alist:
                if aturn is None or (ak, adid) in flap_off:
                    continue
                best = None
                for bk, blist in ons.items():
                    if bk == ak or labels.item_category(bk) != cat:
                        continue
                    for bturn, bdid in blist:
                        if bturn is None or bdid < adid \
                                or (bk, bdid) in flap_on:
                            continue
                        if bturn - aturn > SWAP_GAP_TURNS:
                            continue
                        if best is None or bdid < best[2]:
                            best = (bk, bturn, bdid)
                if best is None:
                    continue
                bk, bturn, bdid = best
                later_offs = [t2 for t2, d2 in offs.get(bk, ())
                              if d2 > bdid and (bk, d2) not in flap_off]
                kept_to_end = not later_offs
                kept_until = min(later_offs) if later_offs else end_turn
                events += 1
                p = pairs.setdefault((ak, bk), {
                    "cids": set(), "turns": [], "gaps": [], "kept": [],
                    "to_end": 0, "n": 0, "cat": cat})
                p["cids"].add(cid)
                p["n"] += 1
                p["turns"].append(aturn)
                p["gaps"].append(bturn - aturn)
                if kept_to_end:
                    p["to_end"] += 1
                if kept_until is not None:
                    p["kept"].append(max(0, kept_until - bturn))
    rows = []
    for (ak, bk), p in pairs.items():
        rewards = [_f(camp[cid]["reward"]) for cid in p["cids"] if cid in camp]
        deltas = []
        for cid in p["cids"]:
            c = camp.get(cid)
            if not c:
                continue
            sm = means.get((c["campaign_map"], c["faction"]))
            if sm is not None:
                deltas.append((_f(c["reward"], 0.0) or 0.0) - sm)
        rows.append(ItemSwapRow(
            removed=Ident(raw=ak, label=_item_ident(ak) or ak),
            equipped=Ident(raw=bk, label=_item_ident(bk) or bk),
            category=p["cat"],
            campaigns=len(p["cids"]), events=p["n"],
            avg_turn=_mean(p["turns"], 1),
            avg_gap=_mean(p["gaps"], 1),
            kept_rate=Rate(n=p["to_end"], of=p["n"], noun="swaps",
                           population="where the new item stayed on to the "
                                      "end of the campaign"),
            avg_kept_turns=_mean(p["kept"], 1),
            avg_reward=_mean(rewards),
            delta_mean=_mean(deltas)))
    rows.sort(key=lambda r2: (-r2.campaigns, r2.removed.raw))
    return {"events": events, "rows": rows}


def items_page(con) -> dict:
    rows = _fact_item_rows()
    cats = sorted({r.category for r in rows if r.category})
    return {"total": len(rows), "categories": cats,
            "resources": _resource_columns(rows), "rows": rows}


def item_page(con, key: str) -> dict | None:
    params = _fact_params(key=key)
    per = _rows(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, BOOL_OR(a.acquired_turn IS NOT NULL) worn,"
        "  MIN(a.acquired_turn) turn"
        " FROM acquisition a" + _acq_join("items") +
        " WHERE a.family = 'items' AND dk.key = %(key)s"
        " GROUP BY 1)"
        " SELECT m.campaign_map, m.faction, MAX(m.leader) leader,"
        "  COUNT(*) held, COUNT(*) FILTER (WHERE p.worn) eq,"
        "  AVG(m.reward) FILTER (WHERE p.worn) req,"
        "  COUNT(*) FILTER (WHERE NOT p.worn) rb_n,"
        "  AVG(m.reward) FILTER (WHERE NOT p.worn) rb,"
        "  AVG(p.turn) FILTER (WHERE p.worn) avg_eq_turn"
        " FROM per p JOIN camps m USING (campaign_id)"
        " GROUP BY 1, 2", params)
    if not per:
        return None
    held_all = sum(_i(r["held"], 0) or 0 for r in per)
    eq_all = sum(_i(r["eq"], 0) or 0 for r in per)
    rb_all = held_all - eq_all
    tot = _one(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, BOOL_OR(a.acquired_turn IS NOT NULL) worn,"
        "  MIN(a.acquired_turn) turn"
        " FROM acquisition a" + _acq_join("items") +
        " WHERE a.family = 'items' AND dk.key = %(key)s"
        " GROUP BY 1)"
        " SELECT AVG(m.reward) FILTER (WHERE p.worn) req,"
        "  AVG(m.reward) FILTER (WHERE NOT p.worn) rb,"
        "  AVG(p.turn) FILTER (WHERE p.worn) avg_eq_turn"
        " FROM per p JOIN camps m USING (campaign_id)", params) or {}
    rows = []
    for r in sorted(per, key=lambda r2: -(_i(r2["held"], 0) or 0)):
        req, rb = _f(r["req"]), _f(r["rb"])
        req = round(req, 2) if req is not None else None
        rb = round(rb, 2) if rb is not None else None
        eq = _i(r["eq"], 0) or 0
        held = _i(r["held"], 0) or 0
        both = eq >= 3 and (held - eq) >= 3
        rows.append(ItemStartRow(
            campaign_map=(_id(ident.campaign_map(r["campaign_map"]))
                          if r["campaign_map"] else None),
            faction=_fac(r["faction"]), leader=r["leader"], held_in=held,
            equipped_in=eq,
            avg_reward_equipped=req, avg_reward_benched=rb,
            delta=round(req - rb, 2)
            if both and req is not None and rb is not None else None))
    ev = _one(
        "SELECT COUNT(*) FILTER (WHERE e.kind = 'on' AND NOT cs.is_hero) l,"
        " COUNT(*) FILTER (WHERE e.kind = 'on') n"
        " FROM item_event e"
        " JOIN dict.ancillary dk ON dk.id = e.ancillary_id"
        " LEFT JOIN corpus.char_state cs ON cs.snapshot_id = e.snapshot_id"
        "  AND cs.character_id = e.character_id"
        " WHERE dk.key = %(key)s", {"key": key}) or {}
    churned = 0
    for r in _rows(
            "SELECT e.campaign_id, COUNT(*) FILTER (WHERE e.kind = 'on') ons,"
            " COUNT(*) FILTER (WHERE e.kind = 'off') offs"
            " FROM item_event e JOIN dict.ancillary dk ON dk.id = e.ancillary_id"
            " WHERE dk.key = %(key)s GROUP BY 1", {"key": key}):
        if (_i(r["ons"], 0) or 0) >= 2 and (_i(r["offs"], 0) or 0) >= 1:
            churned += 1
    recent = [ItemCampaignRow(
        campaign=_camp(r["campaign_key"]), ts=_f(r["first_ts"]),
        leader=r["leader"], equip_turn=_i(r["turn"]),
        turns_worn=(max(0, (_i(r["turns_reached"], 0) or 0) - _i(r["turn"], 0) + 1)
                    if r["turn"] is not None else None),
        reward=round(_f(r["reward"]) or 0, 3))
        for r in _rows(
            "WITH " + _FACT_CAMPS + ", per AS ("
            " SELECT a.campaign_id, MIN(a.acquired_turn) turn"
            " FROM acquisition a" + _acq_join("items") +
            " WHERE a.family = 'items' AND dk.key = %(key)s"
            " AND a.acquired_turn IS NOT NULL GROUP BY 1)"
            " SELECT m.campaign_key, m.first_ts, m.leader, m.turns_reached,"
            "  p.turn, m.reward"
            " FROM per p JOIN camps m USING (campaign_id)"
            " ORDER BY m.first_ts DESC NULLS LAST LIMIT 8", params)]
    req_m, rb_m = _f(tot.get("req")), _f(tot.get("rb"))
    return {
        "key": key, "label": _item_ident(key), "category": labels.item_category(key),
        "effects": labels.item_effect_rows(key),
        "description": labels.item_description(key),
        "acquisition": labels._loc("ancillaries_explanation_text_" + key),
        "lord_share": round(100.0 * (_i(ev.get("l"), 0) or 0)
                            / (_i(ev.get("n"), 0) or 1), 0)
        if (_i(ev.get("n"), 0) or 0) else None,
        "held_in": held_all, "starts": len(per),
        "equip_rate": Rate(n=eq_all, of=held_all, noun="campaigns",
                           population="that held it and wore it"),
        "delta": round(req_m - rb_m, 2)
        if req_m is not None and rb_m is not None and eq_all >= 5
        and rb_all >= 5 else None,
        "avg_equip_turn": (round(_f(tot.get("avg_eq_turn")) or 0, 1)
                           if tot.get("avg_eq_turn") is not None else None),
        "churned_in": churned,
        "by_start": rows, "recent": recent}


@db.timed
def start_research(con, mkey: str, fkey: str) -> dict:
    params = _fact_params(mkey=mkey or "", fkey=fkey)
    base = _rows(
        "WITH " + _FACT_CAMPS + " SELECT campaign_id, reward FROM camps"
        " WHERE campaign_map = %(mkey)s AND faction = %(fkey)s", params)
    mean_r = _mean([_f(r["reward"]) for r in base])
    of_n = len(base)
    per = {r["key"]: r for r in _rows(
        "WITH " + _FACT_CAMPS + " SELECT dk.key,"
        " COUNT(*) FILTER (WHERE a.acquired_turn IS NOT NULL) tn,"
        " AVG(a.acquired_turn) FILTER (WHERE a.acquired_turn IS NOT NULL) avg_turn,"
        " AVG(m.reward) FILTER (WHERE a.acquired_turn IS NOT NULL) rt"
        " FROM acquisition a" + _acq_join("research") +
        " JOIN camps m USING (campaign_id)"
        " WHERE m.campaign_map = %(mkey)s AND m.faction = %(fkey)s"
        " AND a.family = 'research' GROUP BY dk.key", params)}
    universe = labels.tech_universe(set(per))
    known = {u["key"] for u in universe}
    for k in set(per) - known:
        universe.append({"key": k, "technology_key": k, "tier": None,
                         "research_points_required": None})
    parents = labels.tech_parents()
    depth = _tech_depth(parents, [u["key"] for u in universe])
    groups = labels.tech_groups()
    rows = []
    for u in universe:
        p = per.get(u["key"]) or {}
        tn = _i(p.get("tn"), 0) or 0
        avg_r = _f(p.get("rt"))
        avg_r = round(avg_r, 2) if avg_r is not None else None
        rows.append(TechRow(
            key=u["key"],
            label=labels.tech_name(u["key"], u.get("technology_key")),
            parent=_parent_ident(parents, u["key"]),
            line=labels.tech_group_name(groups.get(u["key"])),
            tier=depth.get(u["key"]),
            points=_i(u["research_points_required"]),
            took=Rate(n=tn, of=of_n, noun="campaigns",
                      population="of this start that started it"),
            avg_turn=(round(_f(p.get("avg_turn")) or 0, 1)
                      if p.get("avg_turn") is not None else None),
            avg_reward=avg_r,
            delta_mean=round(avg_r - mean_r, 2)
            if avg_r is not None and mean_r is not None else None))
    rows.sort(key=lambda r2: (-(r2.took.n if r2.took else 0), r2.tier or 999, r2.key))
    return {"mean_reward": mean_r,
            "started_ever": sum(1 for p in per.values()
                                if (_i(p.get("tn"), 0) or 0) > 0),
            "universe": len(rows),
            "has_parents": bool(parents), "rows": rows}


def _start_snapshots(con, mkey: str, fkey: str) -> list:
    heads = con.execute(
        "SELECT * FROM (SELECT DISTINCT ON (cs.character_id)"
        " ch.campaign_id cid, cs.is_hero, ch.cqi, sub.key AS subtype,"
        " cs.rank, cs.skill_points, cs.skill_set_id"
        " FROM corpus.campaign c"
        " JOIN dict.faction f ON f.id = c.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
        " JOIN corpus.character ch USING (campaign_id)"
        " JOIN corpus.char_state cs USING (character_id)"
        " LEFT JOIN dict.agent_subtype sub ON sub.id = cs.subtype_id"
        " WHERE m.key IS NOT DISTINCT FROM NULLIF(%s, '') AND f.key = %s"
        " ORDER BY cs.character_id, cs.snapshot_id DESC) h"
        " ORDER BY h.cid, h.cqi::text",
        (mkey, fkey)).fetchall()
    skills: dict = {}
    set_ids = sorted({r["skill_set_id"] for r in heads
                      if r["skill_set_id"] is not None})
    for i in range(0, len(set_ids), 5000):
        for m in con.execute(
                "SELECT sm.set_id, dk.key, sm.level, sm.tier, sm.total_levels"
                " FROM corpus.skill_set_member sm"
                " JOIN dict.skill dk ON dk.id = sm.skill_id"
                " WHERE sm.set_id = ANY(%s) ORDER BY sm.set_id, sm.ord",
                (set_ids[i:i + 5000],)):
            skills.setdefault(m["set_id"], []).append(
                {"key": m["key"], "level": m["level"], "tier": m["tier"],
                 "total_levels": m["total_levels"]})
    out = []
    for r in heads:
        out.append({"cid": _i(r["cid"], 0),
                    "kind": "hero" if r["is_hero"] else "lord",
                    "cqi": str(r["cqi"]),
                    "z": {"subtype": r["subtype"], "rank": r["rank"],
                          "skill_points": r["skill_points"],
                          "skills": skills.get(r["skill_set_id"]) or []}})
    return out


def start_skills(con, mkey: str, fkey: str, subtype: str | None = None) -> dict:
    snaps = _start_snapshots(con, mkey, fkey)
    mine = _start_gains(con, mkey, fkey)
    rewards = {_i(g["campaign_id"], 0): g["reward"] for g in mine}
    mean_r = _mean(list(rewards.values()))
    per_sub: dict = {}
    for s in snaps:
        st = str(s["z"].get("subtype") or "")
        if not st:
            continue
        e = per_sub.setdefault(st, {"kind": s["kind"], "camps": {}})
        pc = e["camps"].setdefault(s["cid"], {"levels": {}, "rank": 0, "pts": None,
                                              "cqis": set()})
        pc["cqis"].add(s["cqi"])
        pc["rank"] = max(pc["rank"], _i(s["z"].get("rank"), 0) or 0)
        pts = _i(s["z"].get("skill_points"))
        pc["pts"] = pts if pc["pts"] is None else min(pc["pts"], pts or 0)
        for node in s["z"].get("skills") or []:
            k = str(node.get("key") or "")
            lv = _i(node.get("level"), 0) or 0
            if k:
                pc["levels"][k] = max(pc["levels"].get(k, 0), lv)
    characters = []
    for st, e in per_sub.items():
        camps = e["camps"]
        counts: dict = {}
        for pc in camps.values():
            for k, lv in pc["levels"].items():
                if lv:
                    counts[k] = counts.get(k, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        characters.append(StartCharacterRow(
            subtype=st, label=labels.subtype_name(st), kind=e["kind"],
            campaigns=len(camps),
            avg_rank=_mean([pc["rank"] for pc in camps.values()], 1),
            avg_unspent=_mean([pc["pts"] for pc in camps.values()
                               if pc["pts"] is not None], 1),
            avg_ranked=_mean([sum(1 for lv in pc["levels"].values() if lv)
                              for pc in camps.values()], 1),
            top=[Ident(raw=k, label=labels.name_for("skills", k) or labels.pretty(k))
                 for k, _n in top]))
    characters.sort(key=lambda c: (c.kind != "lord", -c.campaigns))
    chosen = subtype if subtype in per_sub else (characters[0].subtype
                                                 if characters else None)
    if not chosen:
        return {"mean_reward": mean_r, "characters": [], "subtype": None,
                "avg_rank": None, "avg_unspent": None, "taken_ever": 0, "rows": []}
    per_camp = per_sub[chosen]["camps"]
    sel = [s for s in snaps if str(s["z"].get("subtype") or "") == chosen]
    struct: dict = {}
    for s in sorted(sel, key=lambda s2: -s2["cid"]):
        for node in s["z"].get("skills") or []:
            k = str(node.get("key") or "")
            if k and k not in struct:
                struct[k] = {"tier": _i(node.get("tier")),
                             "max": _i(node.get("total_levels"))}
    tree_keys = set(struct)
    all_parents = labels.skill_parents_for(chosen)
    lines = labels.skill_lines(chosen)
    first_turn: dict = {}
    for r in _rows(
            "SELECT dk.key, AVG(a.acquired_turn) t FROM acquisition a"
            + _acq_join("skills") +
            " LEFT JOIN dict.agent_subtype sub ON sub.id = a.sub_id"
            " JOIN corpus.campaign c ON c.campaign_id = a.campaign_id"
            " JOIN dict.faction cf ON cf.id = c.faction_id"
            " LEFT JOIN dict.campaign_map cm ON cm.id = c.campaign_map_id"
            " WHERE COALESCE(cm.key, '') = %(mkey)s"
            " AND cf.key = %(fkey)s AND a.family = 'skills'"
            " AND sub.key = %(sub)s AND a.acquired_turn IS NOT NULL"
            " GROUP BY 1", {"mkey": mkey or "", "fkey": fkey, "sub": chosen}):
        if r["t"] is not None:
            first_turn[r["key"]] = round(_f(r["t"]) or 0, 1)
    rows = []
    of_n = len(per_camp)
    for k, st in struct.items():
        took = [cid for cid, pc in per_camp.items() if pc["levels"].get(k)]
        rs = [rewards[cid] for cid in took if cid in rewards]
        avg_r = _mean(rs)
        ps = [p for p in all_parents.get(k) or [] if p in tree_keys]
        rows.append(SkillRow(
            key=k, label=labels.name_for("skills", k) or labels.pretty(k),
            parents=[Ident(raw=p,
                           label=labels.name_for("skills", p) or labels.pretty(p))
                     for p in ps],
            line=labels.skill_line_of(lines, k),
            tier=st["tier"], max_ranks=st["max"],
            took=Rate(n=len(took), of=of_n, noun="campaigns",
                      population="with this character that ranked it"),
            avg_ranks=_mean([per_camp[cid]["levels"][k] for cid in took], 1),
            avg_turn=first_turn.get(k),
            avg_reward=avg_r,
            delta_mean=round(avg_r - mean_r, 2)
            if avg_r is not None and mean_r is not None else None))
    rows.sort(key=lambda r2: (-(r2.took.n if r2.took else 0), r2.tier or 99, r2.key))
    return {"mean_reward": mean_r, "characters": characters, "subtype": chosen,
            "avg_rank": _mean([pc["rank"] for pc in per_camp.values()], 1),
            "avg_unspent": _mean([pc["pts"] for pc in per_camp.values()
                                  if pc["pts"] is not None], 1),
            "taken_ever": sum(1 for r2 in rows if r2.took and r2.took.n),
            "rows": rows}


USAGE_FAMILIES = {
    "building": {"noun": "constructed"},
    "research": {"noun": "started"},
    "skills": {"noun": "ranked"},
    "traits": {"noun": "developed"},
}


def _building_label(key):
    return labels.name_for("building", key) or labels.pretty(key)


def _building_level(info):
    lv = _i(info.get("level"))
    return lv + 1 if lv is not None else None


def _fam_label(family, key):
    if family == "building":
        return _building_label(key)
    if family == "research":
        return labels.tech_name(key) or labels.pretty(key)
    return labels.name_for(family, key) or labels.pretty(key)


@db.timed
def start_buildings(con, mkey: str, fkey: str) -> dict:
    params = _fact_params(mkey=mkey or "", fkey=fkey)
    base = _rows(
        "WITH " + _FACT_CAMPS + " SELECT campaign_id, reward FROM camps"
        " WHERE campaign_map = %(mkey)s AND faction = %(fkey)s", params)
    mean_r = _mean([_f(r["reward"]) for r in base])
    per = _rows(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, dk.key,"
        "  BOOL_OR(a.acquired_turn IS NOT NULL) taken,"
        "  MIN(a.acquired_turn) turn"
        " FROM acquisition a" + _acq_join("building") +
        " JOIN camps m ON m.campaign_id = a.campaign_id"
        " WHERE m.campaign_map = %(mkey)s AND m.faction = %(fkey)s"
        " AND a.family = 'building' GROUP BY 1, 2)"
        " SELECT p.key, COUNT(*) FILTER (WHERE p.taken) tn, COUNT(*) onn,"
        "  AVG(p.turn) FILTER (WHERE p.taken) avg_turn,"
        "  AVG(m.reward) FILTER (WHERE p.taken) rt"
        " FROM per p JOIN camps m USING (campaign_id)"
        " GROUP BY p.key", params)
    info = labels.building_info({r["key"] for r in per})
    rows = []
    constructed = 0
    for r in per:
        tn, onn = _i(r["tn"], 0) or 0, _i(r["onn"], 0) or 0
        if tn:
            constructed += 1
        avg_r = _f(r["rt"])
        avg_r = round(avg_r, 2) if avg_r is not None else None
        i = info.get(r["key"]) or {}
        rows.append(BuildingRow(
            key=r["key"], label=_building_label(r["key"]),
            category=(i.get("chain_category") or "").replace("_", " ") or None,
            level=_building_level(i), cost=_i(i.get("create_cost")),
            offered_in=onn,
            took=Rate(n=tn, of=max(onn, tn), noun="campaigns",
                      population="of this start it was available in"),
            avg_turn=(round(_f(r["avg_turn"]) or 0, 1)
                      if r["avg_turn"] is not None else None),
            avg_reward=avg_r,
            delta_mean=round(avg_r - mean_r, 2)
            if avg_r is not None and mean_r is not None else None))
    rows.sort(key=lambda r2: (-(r2.took.n if r2.took else 0), r2.key))
    return {"mean_reward": mean_r, "constructed_ever": constructed,
            "universe": len(rows), "rows": rows}


_BUILDING_LEDGER = ("building", "horde_building", "building_repair",
                    "building_dismantle")


@db.timed
def campaign_buildings(con, campaign_key: str) -> dict | None:
    meta = _campaign_id_of(con, campaign_key)
    if not meta:
        return None
    got = con.execute(
        "SELECT s.turn, at.key kind, t.decision_id,"
        " CASE WHEN at.key IN ('building_repair', 'building_dismantle')"
        "  THEN COALESCE(dbk.key, a.action_key) ELSE a.action_key END AS action_key,"
        " o.slot_index, dr.key AS region,"
        " bm.cost AS b_cost, ss.empty AS slot_empty, ss.repair_cost, ss.refund"
        " FROM corpus.taken t"
        " JOIN dict.action a ON a.action_id = t.action_id"
        " JOIN dict.action_type at ON at.id = a.action_type_id"
        " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
        " LEFT JOIN corpus.offer o ON o.decision_id = t.decision_id"
        "  AND o.offer_seq = t.offer_seq"
        " LEFT JOIN corpus.province_state ps ON ps.snapshot_id = t.decision_id"
        "  AND ps.entity_seq = t.entity_seq"
        " LEFT JOIN dict.region dr ON dr.id = ps.region_id"
        " LEFT JOIN LATERAL (SELECT CASE"
        "   WHEN at.key IN ('building_repair', 'building_dismantle')"
        "    AND position('@' IN a.action_key) > 0"
        "   THEN NULLIF(split_part(a.action_key, '@', 2), '')::int"
        "   ELSE o.slot_index END AS idx) sl ON true"
        " LEFT JOIN corpus.slot_state_set_member ss ON ss.set_id = ps.slot_state_set_id"
        "  AND ss.index = sl.idx"
        " LEFT JOIN dict.building dbk ON dbk.id = ss.building_id"
        " LEFT JOIN dict.building db ON db.key = a.action_key"
        " LEFT JOIN corpus.buildable_set_member bm ON bm.set_id = ps.buildable_set_id"
        "  AND bm.building_id = db.id AND bm.slot_index IS NOT DISTINCT FROM o.slot_index"
        " WHERE t.campaign_id = %s AND t.counted AND at.key = ANY(%s)"
        " ORDER BY t.decision_id",
        (_i(meta["campaign_id"], 0), list(_BUILDING_LEDGER))).fetchall()
    info = labels.building_info({str(r["action_key"] or "") for r in got})
    rows = []
    constructed = 0
    cost_total = 0.0

    def region_of(key):
        k = str(key or "")
        return (labels.name_for("garrison", k) or labels.pretty(k)) if k else None

    for r in got:
        atype = r["kind"]
        key = str(r["action_key"] or "")
        cost = None
        region = None
        if atype == "building":
            kind = "upgrade" if not r["slot_empty"] and r["slot_empty"] is not None                 else "construct"
            cost = _f(r["b_cost"])
            region = region_of(r["region"])
        elif atype == "horde_building":
            kind = "horde"
            cost = None
        elif atype == "building_repair":
            kind = "repair"
            cost = _f(r["repair_cost"])
            region = region_of(r["region"])
        else:
            kind = "dismantle"
            refund = _f(r["refund"])
            cost = -refund if refund is not None else None
            region = region_of(r["region"])
        if kind in ("construct", "upgrade", "horde"):
            constructed += 1
        if cost is not None:
            cost_total += cost
        i = info.get(key) or {}
        rows.append(CampaignBuildingRow(
            turn=_i(r["turn"]), kind=kind, key=key, label=_building_label(key),
            category=(i.get("chain_category") or "").replace("_", " ") or None,
            level=_building_level(i), region=region, cost=cost))
    return {"rows": rows, "constructed": constructed,
            "total_cost": round(cost_total, 0) if rows else None}


def _catalog_ref(family, keys) -> dict:
    out: dict = {key: {} for key in keys}
    if family == "building":
        info = labels.building_info(keys)
        for key in keys:
            i = info.get(key) or {}
            out[key] = {"category": (i.get("chain_category") or "")
                        .replace("_", " ") or None,
                        "level": _building_level(i), "cost": _i(i.get("create_cost"))}
    elif family == "research":
        uni = {u["key"]: u for u in labels.tech_rows_for(keys)}
        for key in keys:
            u = uni.get(key) or {}
            out[key] = {"tier": _i(u.get("tier")),
                        "points": _i(u.get("research_points_required"))}
    elif family == "skills":
        unlocks = labels.skill_unlock_ranks(keys)
        for key in keys:
            out[key] = {"unlock_rank": _i(unlocks.get(key)) or None}
    elif family == "traits":
        lv = labels.trait_levels_for(keys)
        cats = labels.trait_categories(keys)
        for key in keys:
            rows = lv.get(key) or []
            out[key] = {"levels": len(rows) or None,
                        "threshold": _i(rows[0]["threshold"]) if rows else None,
                        "category": cats.get(key)}
    return out


@db.timed
def catalog_index(con, family: str) -> dict:
    per = _rows(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, dk.key,"
        "  BOOL_OR(a.acquired_turn IS NOT NULL) taken,"
        "  MIN(a.acquired_turn) turn, MAX(a.ranks) ranks"
        " FROM acquisition a" + _acq_join(family) +
        " WHERE a.family = %(fam)s GROUP BY 1, 2)"
        " SELECT p.key,"
        "  COUNT(*) FILTER (WHERE p.taken) took_n,"
        "  COUNT(*) of_n,"
        "  COUNT(DISTINCT m.campaign_map || '|' || m.faction)"
        "   FILTER (WHERE p.taken) starts,"
        "  AVG(p.turn) FILTER (WHERE p.taken) avg_turn,"
        "  AVG(m.reward) FILTER (WHERE p.taken) rt,"
        "  AVG(m.reward) FILTER (WHERE NOT p.taken) rp,"
        "  AVG(p.ranks) FILTER (WHERE p.taken) avg_ranks"
        " FROM per p JOIN camps m USING (campaign_id)"
        " GROUP BY p.key", _fact_params(fam=family))
    ref = _catalog_ref(family, {r["key"] for r in per})
    races = _fact_key_cultures(family) if family in ("research", "traits") else {}
    noun = USAGE_FAMILIES[family]["noun"]
    rows = []
    for p in per:
        key = p["key"]
        took_n = _i(p["took_n"], 0) or 0
        of_n = _i(p["of_n"], 0) or 0
        passed_n = of_n - took_n
        r = ref.get(key) or {}
        rt, rp = _f(p["rt"]), _f(p["rp"])
        if rt is not None:
            rt = round(rt, 2)
        if rp is not None:
            rp = round(rp, 2)
        rows.append(CatalogIndexRow(
            key=key, label=_fam_label(family, key), race=races.get(key),
            category=r.get("category"),
            level=r.get("level"), cost=r.get("cost"), line=r.get("line"),
            tier=r.get("tier"), points=r.get("points"),
            unlock_rank=r.get("unlock_rank"),
            levels=r.get("levels"), threshold=r.get("threshold"),
            avg_ranks=(round(_f(p["avg_ranks"]) or 0, 1)
                       if family in ("skills", "traits")
                       and p["avg_ranks"] is not None
                       else None),
            took=Rate(n=took_n, of=max(of_n, took_n), noun="campaigns",
                      population="it was available in that %s it" % noun),
            starts=_i(p["starts"], 0) or 0,
            avg_turn=(round(_f(p["avg_turn"]) or 0, 1)
                      if p["avg_turn"] is not None else None),
            avg_reward_took=rt, avg_reward_passed=rp,
            delta=round(rt - rp, 2)
            if rt is not None and rp is not None and took_n >= 5
            and passed_n >= 5 else None))
    rows.sort(key=lambda r2: (-(r2.took.n if r2.took else 0), r2.key))
    cats = sorted({r2.category for r2 in rows if r2.category}) \
        if family in ("building", "traits") else \
        sorted({r2.line for r2 in rows if r2.line}) if family == "research" else []
    return {"family": family, "total": len(rows),
            "campaigns": _fact_campaign_count(),
            "categories": cats, "rows": rows}


def _fact_key_cultures(family) -> dict:
    per: dict = {}
    cul_of: dict = {}
    for r in _rows(
            "WITH " + _FACT_CAMPS + " SELECT dk.key, m.faction,"
            " COUNT(DISTINCT a.campaign_id) n"
            " FROM acquisition a" + _acq_join(family) +
            " JOIN camps m USING (campaign_id)"
            " WHERE a.family = %(fam)s GROUP BY 1, 2",
            _fact_params(fam=family)):
        fk = str(r["faction"])
        if fk not in cul_of:
            cul_of[fk] = _fac(fk).culture
        cul = cul_of[fk]
        if cul:
            e = per.setdefault(r["key"], {})
            e[cul] = e.get(cul, 0) + (_i(r["n"], 0) or 0)
    return {k: next(iter(v)) for k, v in per.items() if len(v) == 1}


def _fact_key_stats(family, keys) -> dict:
    ks = sorted({str(k) for k in keys if k})
    if not ks:
        return {}
    got = _rows(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, dk.key,"
        "  BOOL_OR(a.acquired_turn IS NOT NULL) taken,"
        "  MIN(a.acquired_turn) turn"
        " FROM acquisition a" + _acq_join(family) +
        " WHERE a.family = %(fam)s AND dk.key = ANY(%(keys)s)"
        " GROUP BY 1, 2)"
        " SELECT p.key, COUNT(*) FILTER (WHERE p.taken) tn, COUNT(*) onn,"
        "  AVG(p.turn) FILTER (WHERE p.taken) avg_turn,"
        "  AVG(m.reward) FILTER (WHERE p.taken) rt,"
        "  AVG(m.reward) FILTER (WHERE NOT p.taken) rp"
        " FROM per p JOIN camps m USING (campaign_id) GROUP BY 1",
        _fact_params(fam=family, keys=ks))
    out = {}
    for r in got:
        tn, onn = _i(r["tn"], 0) or 0, _i(r["onn"], 0) or 0
        rt, rp = _f(r["rt"]), _f(r["rp"])
        rt = round(rt, 2) if rt is not None else None
        rp = round(rp, 2) if rp is not None else None
        out[r["key"]] = {
            "tn": tn, "onn": onn,
            "avg_turn": (round(_f(r["avg_turn"]) or 0, 1)
                         if r["avg_turn"] is not None else None),
            "rt": rt, "rp": rp,
            "delta": round(rt - rp, 2)
            if rt is not None and rp is not None and tn >= 5
            and (onn - tn) >= 5 else None}
    return out


def _fact_turn_states() -> tuple:
    states: dict = {}
    for r in _rows(
            "SELECT DISTINCT ON (s.campaign_id, s.turn) s.campaign_id, s.turn,"
            " sc.settlements, sc.lord_level, sc.allies, sc.vassals"
            " FROM corpus.snapshot s"
            " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
            " JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = s.snapshot_id"
            " ORDER BY s.campaign_id, s.turn, s.snapshot_id"):
        states.setdefault(int(r["campaign_id"]), []).append(
            (int(r["turn"]), _f(r["settlements"], 0.0) or 0.0,
             _f(r["lord_level"], 0.0) or 0.0,
             _f(r["allies"], 0.0) or 0.0, _f(r["vassals"], 0.0) or 0.0))
    peaks = {int(r["campaign_id"]): (
        _f(r["ps"], 0.0) or 0.0, _f(r["pl"], 0.0) or 0.0,
        _f(r["pa"], 0.0) or 0.0, _f(r["pv"], 0.0) or 0.0)
        for r in _rows(
            "SELECT s.campaign_id, MAX(sc.settlements) ps, MAX(sc.lord_level) pl,"
            " MAX(sc.allies) pa, MAX(sc.vassals) pv"
            " FROM corpus.snapshot s"
            " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
            " JOIN corpus.snapshot_campaign sc ON sc.snapshot_id = s.snapshot_id"
            " GROUP BY 1")}
    return states, peaks


def _future_reward(states, peaks, cid, anchor_turn):
    pk = peaks.get(cid)
    seq = states.get(cid)
    if pk is None or not seq or anchor_turn is None:
        return None
    anchor = None
    for row in seq:
        if row[0] >= anchor_turn:
            anchor = row
            break
    if anchor is None:
        anchor = seq[-1]
    w = reward_weights()
    return (w["settlements"] * max(0.0, pk[0] - anchor[1])
            + w["lord_levels"] * max(0.0, pk[1] - anchor[2])
            + w["allies"] * max(0.0, pk[2] - anchor[3])
            + w["vassals"] * max(0.0, pk[3] - anchor[4]))


def _fork_specs(family) -> list:
    out = []
    if family == "research":
        for parent, kids in labels.tech_children().items():
            if len(kids) > 1:
                out.append((parent, parent, sorted(set(kids))))
    return out


def _arm_note(arms, arm, cid, reached, t, camp, states, peaks):
    e = arms.setdefault(arm, {"n": 0, "reached": [], "picked": [],
                              "rewards": [], "futures": []})
    e["n"] += 1
    if reached is not None:
        e["reached"].append(reached)
    if t is not None:
        e["picked"].append(t)
    e["rewards"].append(_f(camp[cid]["reward"], 0.0) or 0.0)
    fut = _future_reward(states, peaks, cid, reached)
    if fut is not None:
        e["futures"].append(fut)


def _member_arm(got, kids, parent, reached):
    for k in kids:
        g = got.get(k)
        if g and g[2] is not None and g[2] == g[3]:
            return "unobserved", None, None
    taken = [(g[3], g[1], k) for k in kids
             for g in [got.get(k)] if g and g[3] is not None
             and g[2] is not None and g[3] > g[2]
             and (g[1] is None or reached is None or g[1] >= reached)]
    if not taken:
        return "neither", None, None
    _d, t, k = min(taken)
    return "picked", k, t


def _start_tag(starts) -> str | None:
    if not starts:
        return None
    got = sorted(starts.items(), key=lambda kv: (-kv[1]["n"], kv[0]))
    top = got[0][1]
    tag = top["leader"] or _fac(got[0][0][1]).label
    if len(got) > 1:
        tag += " +%d more" % (len(got) - 1)
    return tag


def _fork_note(bucket, c):
    sk = (c["campaign_map"], c["faction"])
    e = bucket["starts"].setdefault(sk, {"n": 0, "leader": None})
    e["n"] += 1
    e["leader"] = e["leader"] or c["leader"]
    cul = _fac(c["faction"]).culture
    if cul:
        bucket["culs"][cul] = bucket["culs"].get(cul, 0) + 1


@db.timed
def choices_page(con, family: str) -> dict:
    camp = {int(r["campaign_id"]): r for r in _rows(
        "WITH " + _FACT_CAMPS +
        " SELECT campaign_id, reward, turns_reached, faction,"
        " campaign_map, leader FROM camps",
        _fact_params())}
    states, peaks = _fact_turn_states()
    forks = []
    if family == "building":
        per_region: dict = {}
        for r in _rows(
                "SELECT a.campaign_id cid, a.ctx, dk.key, MIN(a.first_seen_turn) st,"
                " MIN(a.first_seen_snapshot) sd, MIN(a.acquired_turn) turn,"
                " MIN(a.acquired_snapshot) dec"
                " FROM acquisition a" + _acq_join("building") +
                " WHERE a.family = 'building' AND a.ctx <> ''"
                " GROUP BY 1, 2, 3"):
            per_region.setdefault(str(r["ctx"]), {}).setdefault(
                int(r["cid"]), []).append(
                (r["key"], _i(r["st"]), _i(r["sd"]), _i(r["turn"]),
                 _i(r["dec"])))
        info = labels.building_info(
            {k for camps in per_region.values() for rows in camps.values()
             for k, _st, _sd, _t, _d in rows})
        chain_label: dict = {}
        for region, camps in per_region.items():
            arms: dict = {}
            cohort = 0
            who = {"starts": {}, "culs": {}}
            for cid, rows in camps.items():
                if cid not in camp:
                    continue
                cohort += 1
                _fork_note(who, camp[cid])
                reached = min((st for _k, st, _sd, _t, _d in rows
                               if st is not None), default=None)
                built = [(d, t, k) for k, _st, sd, t, d in rows
                         if d is not None and sd is not None and d > sd]
                if built:
                    _d, t, k = min(built)
                    chain = str((info.get(k) or {}).get("building_chain")
                                or k)
                    chain_label.setdefault(chain, _building_label(k))
                    arm = chain
                else:
                    arm, t = None, None
                _arm_note(arms, arm, cid, reached, t, camp, states, peaks)
            if arms:
                forks.append((region,
                              labels.name_for("attack_settlement", region)
                              or labels.pretty(region), cohort, arms,
                              chain_label, who))
    else:
        per_char = family == "skills"
        if per_char:
            specs = [(parent + "@" + sub, parent, kids, sub)
                     for sub, parent, kids in labels.skill_forks()]
        else:
            specs = [(fk, parent, kids, None)
                     for fk, parent, kids in _fork_specs(family)]
        keys = sorted({k for _f2, _p, kids, _s in specs for k in kids}
                      | {p for _f2, p, _k, _s in specs if p})
        per_member: dict = {}
        member_sub: dict = {}
        for r in _rows(
                "SELECT a.campaign_id cid, a.ctx, sub.key AS sub, dk.key,"
                " MIN(a.first_seen_turn) st, MIN(a.first_seen_snapshot) sd,"
                " MIN(a.acquired_turn) turn, MIN(a.acquired_snapshot) dec"
                " FROM acquisition a" + _acq_join(family) +
                " LEFT JOIN dict.agent_subtype sub ON sub.id = a.sub_id"
                " WHERE a.family = %(fam)s"
                " AND dk.key = ANY(%(keys)s) GROUP BY 1, 2, 3, 4",
                {"fam": family, "keys": keys}):
            member = ((int(r["cid"]), str(r["ctx"])) if per_char
                      else int(r["cid"]))
            per_member.setdefault(member, {})[r["key"]] = (
                _i(r["st"]), _i(r["turn"]), _i(r["sd"]), _i(r["dec"]))
            if per_char and r["sub"]:
                member_sub[member] = str(r["sub"])
        members_by_sub: dict = {}
        for member in per_member:
            members_by_sub.setdefault(
                member_sub.get(member) if per_char else None,
                []).append(member)
        for fork_key, parent, kids, want_sub in specs:
            bucket = {"arms": {}, "cohort": 0, "starts": {}, "culs": {}}
            for member in members_by_sub.get(want_sub, []):
                got = per_member[member]
                cid = member[0] if per_char else member
                if cid not in camp:
                    continue
                pa = got.get(parent)
                if not pa or pa[1] is None:
                    continue
                reached = pa[1]
                verdict, k, t = _member_arm(got, kids, parent, reached)
                if verdict == "unobserved":
                    continue
                bucket["cohort"] += 1
                _fork_note(bucket, camp[cid])
                _arm_note(bucket["arms"], k, cid, reached, t, camp, states,
                          peaks)
            if bucket["arms"]:
                label = _fam_label(family, parent)
                if want_sub:
                    label += " · " + (labels.subtype_name(want_sub)
                                      or labels.pretty(want_sub))
                forks.append((fork_key, label, bucket["cohort"],
                              bucket["arms"], {}, bucket))
    forks.sort(key=lambda f2: -f2[2])
    out = []
    for fork_key, label, cohort, arms, chain_label, who in forks:
        arm_rows = []
        for arm, e in sorted(arms.items(),
                             key=lambda kv: (kv[0] is None, -kv[1]["n"])):
            others = [f2 for a2, e2 in arms.items() if a2 != arm
                      for f2 in e2["futures"]]
            mine_f = _mean(e["futures"])
            other_f = _mean(others)
            arm_rows.append(ForkArmRow(
                key=arm,
                label=("didn't continue" if arm is None
                       else chain_label.get(arm, arm)
                       if family == "building"
                       else _fam_label(family, arm)),
                n=e["n"],
                avg_reached_turn=_mean(e["reached"], 1),
                avg_picked_turn=_mean(e["picked"], 1),
                avg_reward=_mean(e["rewards"]),
                avg_future=mine_f,
                delta_future=round(mine_f - other_f, 2)
                if mine_f is not None and other_f is not None else None))
        out.append(ForkRow(fork=fork_key, label=label, cohort=cohort,
                           race=(next(iter(who["culs"]))
                                 if len(who["culs"]) == 1 else None),
                           starts=_start_tag(who["starts"]),
                           n_starts=len(who["starts"]),
                           arms=arm_rows))
    return {"forks": out}


@db.timed
def catalog_overtime(con, family: str, race: str | None = None,
                     lord: str | None = None, start: str | None = None) -> dict:
    camps = [(r, _fac(r["faction"]).culture) for r in _rows(
        "WITH " + _FACT_CAMPS +
        " SELECT campaign_id, faction, campaign_map, leader, first_ts"
        " FROM camps ORDER BY campaign_id", _fact_params())]
    races = sorted({cul for _r, cul in camps if cul})
    lords = sorted({r["leader"] for r, _cul in camps if r["leader"]})
    opts: dict = {}
    for r, cul in camps:
        sk = "%s|%s" % (r["campaign_map"], r["faction"])
        if sk not in opts:
            m = (_id(ident.campaign_map(r["campaign_map"]))
                 if r["campaign_map"] else None)
            who = r["leader"] or _fac(r["faction"]).label
            opts[sk] = PositionFacetOption(
                key=sk, label=who + (" · " + m.label if m else ""), culture=cul)
        opts[sk].campaigns += 1
    starts = sorted(opts.values(), key=lambda o: o.label)
    if race:
        camps = [(r, cul) for r, cul in camps if (cul or "") == race]
    if lord:
        camps = [(r, cul) for r, cul in camps if (r["leader"] or "") == lord]
    if start:
        camps = [(r, cul) for r, cul in camps
                 if "%s|%s" % (r["campaign_map"], r["faction"]) == start]
    firsts: dict = {}
    for r in con.execute(
            "SELECT DISTINCT ON (t.campaign_id) t.campaign_id, a.action_key"
            " FROM corpus.taken t JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " WHERE t.counted AND at.key = %s"
            " ORDER BY t.campaign_id, t.decision_id", (family,)):
        firsts[_i(r["campaign_id"], 0)] = r["action_key"]
    ordered = sorted((r2 for r2, _c2 in camps),
                     key=lambda r2: _f(r2["first_ts"]) or 0.0)
    seq = [firsts.get(_i(r3["campaign_id"], 0)) for r3 in ordered]
    counts: dict = {}
    for k in seq:
        if k:
            counts[k] = counts.get(k, 0) + 1
    rib_keys = [k2 for k2, _n2 in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    bsize = max(RIBBON_BUCKET_MIN, math.ceil(len(seq) / RIBBON_BUCKETS_MAX) if seq else 1)
    ribbon = []
    for i0 in range(0, len(seq), bsize):
        chunk = seq[i0:i0 + bsize]
        shares = [round(100.0 * sum(1 for s in chunk if s == k3) / len(chunk), 1)
                  for k3 in rib_keys]
        ribbon.append(RibbonBucket(label="c%d-%d" % (i0 + 1, i0 + len(chunk)),
                                   shares=shares))
    return {"campaigns": len(seq), "ribbon_family": family,
            "ribbon_keys": rib_keys,
            "ribbon_labels": [labels.name_for(family, k4) or labels.pretty(k4)
                              for k4 in rib_keys],
            "ribbon": ribbon,
            "races": races, "lords": lords, "starts": starts}


@db.timed
def catalog_key_page(con, family: str, key: str) -> dict | None:
    params = _fact_params(fam=family, key=key)
    per = _rows(
        "WITH " + _FACT_CAMPS + ", per AS ("
        " SELECT a.campaign_id, BOOL_OR(a.acquired_turn IS NOT NULL) taken,"
        "  MIN(a.acquired_turn) turn"
        " FROM acquisition a" + _acq_join(family) +
        " WHERE a.family = %(fam)s AND dk.key = %(key)s"
        " GROUP BY 1)"
        " SELECT m.campaign_map, m.faction, MAX(m.leader) leader,"
        "  COUNT(*) of_n, COUNT(*) FILTER (WHERE p.taken) took_n,"
        "  AVG(p.turn) FILTER (WHERE p.taken) avg_turn,"
        "  AVG(m.reward) FILTER (WHERE p.taken) avg_r"
        " FROM per p JOIN camps m USING (campaign_id)"
        " GROUP BY 1, 2", params)
    if not per:
        return None
    means = {(r["campaign_map"], r["faction"]): _f(r["mr"]) for r in _rows(
        "WITH " + _FACT_CAMPS +
        " SELECT campaign_map, faction, AVG(reward) mr FROM camps"
        " GROUP BY 1, 2", _fact_params())}
    start_rows = []
    for r in sorted(per, key=lambda r2: -(_i(r2["took_n"], 0) or 0)):
        tn, on = _i(r["took_n"], 0) or 0, _i(r["of_n"], 0) or 0
        avg_r = _f(r["avg_r"])
        avg_r = round(avg_r, 2) if avg_r is not None else None
        sm = means.get((r["campaign_map"], r["faction"]))
        start_rows.append(CatalogStartRow(
            campaign_map=(_id(ident.campaign_map(r["campaign_map"]))
                          if r["campaign_map"] else None),
            faction=_fac(r["faction"]), leader=r["leader"],
            took=Rate(n=tn, of=max(on, tn), noun="campaigns",
                      population="of this start it was available in"),
            offered_in=on,
            avg_turn=(round(_f(r["avg_turn"]) or 0, 1)
                      if r["avg_turn"] is not None else None),
            avg_reward=avg_r,
            delta_mean=round(avg_r - sm, 2)
            if avg_r is not None and sm is not None else None))
    recent = [CatalogCampaignRow(
        campaign=_camp(r["campaign_key"]), ts=_f(r["first_ts"]),
        leader=r["leader"], turn=_i(r["turn"]),
        reward=round(_f(r["reward"]) or 0, 3))
        for r in _rows(
            "WITH " + _FACT_CAMPS + ", per AS ("
            " SELECT a.campaign_id, MIN(a.acquired_turn) turn"
            " FROM acquisition a" + _acq_join(family) +
            " WHERE a.family = %(fam)s AND dk.key = %(key)s"
            " AND a.acquired_turn IS NOT NULL GROUP BY 1)"
            " SELECT m.campaign_key, m.first_ts, m.leader, p.turn, m.reward"
            " FROM per p JOIN camps m USING (campaign_id)"
            " ORDER BY m.first_ts DESC NULLS LAST LIMIT 8", params)]
    noun = USAGE_FAMILIES[family]["noun"]
    mine = _fact_key_stats(family, [key]).get(key) or {}
    tn_all = mine.get("tn", 0)
    on_all = mine.get("onn", 0)
    rt, rp = mine.get("rt"), mine.get("rp")

    def took_rate(k, stats) -> Rate:
        st = stats.get(k) or {}
        return Rate(n=st.get("tn", 0), of=max(st.get("onn", 0), st.get("tn", 0)),
                    noun="campaigns",
                    population="it was available in that took it")

    out = {
        "family": family, "key": key, "label": _fam_label(family, key),
        "took_in": tn_all,
        "took": Rate(n=tn_all, of=max(on_all, tn_all),
                     noun="campaigns",
                     population="it was available in that %s it" % noun),
        "starts": sum(1 for r in per if (_i(r["took_n"], 0) or 0) > 0),
        "avg_turn": mine.get("avg_turn"),
        "avg_reward_took": rt, "avg_reward_passed": rp,
        "delta": mine.get("delta"),
        "by_start": start_rows, "recent": recent}

    def related_rows(parents, children, refs):
        rel = []
        stats = _fact_key_stats(family, (parents or []) + (children or []))
        for kind, keys2 in (("requires", parents), ("unlocks", children)):
            got = sorted(set(keys2 or []),
                         key=lambda k2: -(stats.get(k2) or {}).get("tn", 0))
            for k2 in got:
                r2 = refs.get(k2) or {}
                st2 = stats.get(k2) or {}
                rel.append(RelatedKey(
                    key=k2, label=_fam_label(family, k2), kind=kind,
                    tier=r2.get("tier"), points=r2.get("points"),
                    unlock_rank=r2.get("unlock_rank"),
                    took_in=st2.get("tn", 0), took=took_rate(k2, stats),
                    avg_reward_took=st2.get("rt"),
                    avg_reward_passed=st2.get("rp"), delta=st2.get("delta")))
        return rel

    if family == "building":
        info = labels.building_info([key]).get(key) or {}
        out.update(category=(info.get("chain_category") or "")
                   .replace("_", " ") or None,
                   level=_building_level(info), cost=_i(info.get("create_cost")),
                   upkeep=_i(info.get("upkeep_cost")) or None,
                   turns_to_build=_i(info.get("create_time")))
        levels = labels.building_chain_levels(
            str(info.get("building_chain") or ""))
        chain_stats = _fact_key_stats(family, [lv["key"] for lv in levels])
        chain = []
        for lv in levels:
            st2 = chain_stats.get(lv["key"]) or {}
            chain.append(ChainLevel(
                key=lv["key"], label=_building_label(lv["key"]),
                level=(_i(lv.get("level")) + 1
                       if lv.get("level") is not None else None),
                cost=_i(lv.get("create_cost")),
                constructed_in=st2.get("tn", 0),
                took=took_rate(lv["key"], chain_stats),
                avg_reward_took=st2.get("rt"), avg_reward_passed=st2.get("rp"),
                delta=st2.get("delta"), this=lv["key"] == key))
        out["chain"] = chain
    elif family == "research":
        ref = _catalog_ref("research", [key]).get(key) or {}
        tkey = next((u.get("technology_key")
                     for u in labels.tech_rows_for([key])), None)
        parents = labels.tech_parents().get(key) or []
        children = labels.tech_children().get(key) or []
        out.update(tier=ref.get("tier"), points=ref.get("points"),
                   parent=_parent_ident(labels.tech_parents(), key),
                   description=labels.tech_description(key, tkey),
                   related=related_rows(
                       parents, children,
                       _catalog_ref("research", set(parents) | set(children))))
    elif family == "traits":
        ref = _catalog_ref("traits", [key]).get(key) or {}
        out.update(category=ref.get("category"),
                   description=labels.trait_flavour(key))
        out["trait_levels"] = [
            TraitLevelRow(level=_i(lv["level"], 0) or 0, name=lv["name"],
                          threshold=_i(lv["threshold"]),
                          effects=[ItemEffect(**e) for e in lv["effects"]])
            for lv in labels.trait_level_rows(key)]
        out["by_character"] = [
            SkillCharacterRow(
                subtype=r["sub"], label=labels.subtype_name(r["sub"]),
                kind=r["kind"] or "lord",
                campaigns=_i(r["tn"], 0) or 0,
                ranked=Rate(n=_i(r["tn"], 0) or 0,
                            of=max(_i(r["onn"], 0) or 0, _i(r["tn"], 0) or 0),
                            noun="campaigns",
                            population="that fielded this character and"
                                       " developed it"),
                avg_ranks=(round(_f(r["avg_ranks"]) or 0, 1)
                           if r["avg_ranks"] is not None else None),
                avg_turn=(round(_f(r["avg_turn"]) or 0, 1)
                          if r["avg_turn"] is not None else None))
            for r in _rows(
                "SELECT sub.key AS sub, MAX(ek.key) kind,"
                " COUNT(DISTINCT a.campaign_id)"
                "  FILTER (WHERE a.acquired_turn IS NOT NULL) tn,"
                " COUNT(DISTINCT a.campaign_id) onn,"
                " AVG(a.ranks) FILTER (WHERE a.acquired_turn IS NOT NULL) avg_ranks,"
                " AVG(a.acquired_turn) avg_turn"
                " FROM acquisition a" + _acq_join("traits") +
                " JOIN dict.agent_subtype sub ON sub.id = a.sub_id"
                " LEFT JOIN dict.enum ek ON ek.enum_id = a.kind_id"
                " WHERE a.family = 'traits' AND dk.key = %(key)s"
                " GROUP BY sub.key ORDER BY tn DESC", {"key": key})
            if (_i(r["onn"], 0) or 0) > 0]
        anti = labels.trait_antitraits(key)
        out["related"] = related_rows(anti, [], _catalog_ref("traits", set(anti)))
        out["related"] = [r.model_copy(update={"kind": "antitrait"})
                          for r in out["related"]]
    else:
        out["by_character"] = [
            SkillCharacterRow(
                subtype=r["sub"], label=labels.subtype_name(r["sub"]),
                kind=r["kind"] or "lord",
                campaigns=_i(r["tn"], 0) or 0,
                ranked=Rate(n=_i(r["tn"], 0) or 0,
                            of=max(_i(r["onn"], 0) or 0, _i(r["tn"], 0) or 0),
                            noun="campaigns",
                            population="that fielded this character and ranked it"),
                avg_ranks=(round(_f(r["avg_ranks"]) or 0, 1)
                           if r["avg_ranks"] is not None else None),
                avg_turn=(round(_f(r["avg_turn"]) or 0, 1)
                          if r["avg_turn"] is not None else None))
            for r in _rows(
                "SELECT sub.key AS sub, MAX(ek.key) kind,"
                " COUNT(DISTINCT a.campaign_id)"
                "  FILTER (WHERE a.acquired_turn IS NOT NULL) tn,"
                " COUNT(DISTINCT a.campaign_id) onn,"
                " AVG(a.ranks) FILTER (WHERE a.acquired_turn IS NOT NULL) avg_ranks,"
                " AVG(a.acquired_turn) avg_turn"
                " FROM acquisition a" + _acq_join("skills") +
                " JOIN dict.agent_subtype sub ON sub.id = a.sub_id"
                " LEFT JOIN dict.enum ek ON ek.enum_id = a.kind_id"
                " WHERE a.family = 'skills' AND dk.key = %(key)s"
                " GROUP BY sub.key ORDER BY tn DESC", {"key": key})
            if (_i(r["tn"], 0) or 0) > 0]
        out["unlock_rank"] = _i(labels.skill_unlock_ranks([key]).get(key))
        out["description"] = labels.skill_description(key)
        subs = [r["sub"] for r in _rows(
            "SELECT DISTINCT sub.key AS sub FROM acquisition a" + _acq_join("skills") +
            " JOIN dict.agent_subtype sub ON sub.id = a.sub_id"
            " WHERE a.family = 'skills' AND dk.key = %(key)s",
            {"key": key})]
        parents, children = labels.skill_neighbors(key, subs)
        out["related"] = related_rows(
            parents, children,
            _catalog_ref("skills", set(parents) | set(children)))
    return out


POSITION_SCALARS = {"turn": 2, "settlements": 3, "income": 4, "power_rank": 5,
                    "lord_level": 6, "treasury": 7, "armies": 8, "heroes": 9,
                    "allies": 10, "vassals": 11}
POSITION_FLAGS = {"is_researching": 1, "ll_wounded": 2}
POSITION_KEYS_SHOWN = 12
_SETT_TYPES = ("attack_settlement", "colonize")
POSITION_HISTORY = ("settlement", "building", "research", "skills", "items")


def _positions_data(con, force=False) -> dict:
    def build():
        camps = {}
        for r in con.execute(
                "SELECT c.campaign_id, f.key AS faction, m.key AS campaign_map,"
                " c.leader, o.key AS outcome FROM corpus.campaign c"
                " JOIN dict.faction f ON f.id = c.faction_id"
                " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
                " LEFT JOIN dict.enum o ON o.enum_id = c.outcome_id"
                " WHERE c.note IS NULL ORDER BY c.campaign_id"):
            camps[_i(r["campaign_id"], 0)] = (r["faction"], r["campaign_map"] or "",
                                              r["leader"], r["outcome"])
        per_fac: dict = {}
        for fk, _mk, leader, _oc in camps.values():
            e = per_fac.setdefault(fk, {"n": 0, "leader": None})
            e["n"] += 1
            e["leader"] = e["leader"] or leader
        factions = []
        for fk, e in per_fac.items():
            fac = _fac(fk)
            factions.append(PositionFacetOption(
                key=fk, label=e["leader"] or fac.label, culture=fac.culture,
                campaigns=e["n"]))
        factions.sort(key=lambda o: o.label)
        maps = [_id(ident.campaign_map(mk))
                for mk in sorted({mk for _f2, mk, _l, _oc in camps.values() if mk})]
        rewards = {cid2: c2["reward"] for cid2, c2 in _camp_meta(con).items()}
        takes = {}
        for r in con.execute(
                "SELECT t.decision_id, at.key AS action_type, a.action_key"
                " FROM corpus.taken t"
                " JOIN dict.action a ON a.action_id = t.action_id"
                " JOIN dict.action_type at ON at.id = a.action_type_id"
                " WHERE t.counted"):
            takes[_i(r["decision_id"], 0)] = (
                sys.intern(str(r["action_type"])), r["action_key"])
        decs = []
        peaks: dict = {}
        extras: dict = {}
        res_camps: dict = {}
        hero_camps: dict = {}
        res_sets: dict = {}
        for r in con.execute(
                "SELECT m.set_id, pr.key, m.value"
                " FROM corpus.resource_set_member m"
                " JOIN dict.pooled_resource pr ON pr.id = m.resource_id"
                " WHERE m.set_id IN (SELECT DISTINCT resource_set_id"
                "  FROM corpus.snapshot_campaign WHERE resource_set_id IS NOT NULL)"
                " ORDER BY m.set_id, m.ord"):
            res_sets.setdefault(r["set_id"], []).append((str(r["key"]),
                                                         _f(r["value"])))
        hero_sets: dict = {}
        for r in con.execute(
                "SELECT m.set_id, at.key, m.n"
                " FROM corpus.hero_count_set_member m"
                " JOIN dict.agent_type at ON at.id = m.agent_type_id"
                " WHERE m.set_id IN (SELECT DISTINCT hero_count_set_id"
                "  FROM corpus.snapshot_campaign"
                "  WHERE hero_count_set_id IS NOT NULL)"
                " ORDER BY m.set_id, m.ord"):
            hero_sets.setdefault(r["set_id"], []).append((str(r["key"]),
                                                          _f(r["n"])))
        for r in con.execute(
                "SELECT s.snapshot_id AS decision_id, s.campaign_id, s.turn,"
                " sc.settlements, sc.income, sc.power_rank, sc.lord_level,"
                " sc.allies, sc.vassals, sc.treasury, sc.armies,"
                " sc.is_researching, sc.ll_wounded,"
                " sc.resource_set_id, sc.hero_count_set_id"
                " FROM corpus.snapshot s"
                " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
                " JOIN corpus.snapshot_campaign sc"
                "  ON sc.snapshot_id = s.snapshot_id"):
            did = _i(r["decision_id"], 0)
            cid = _i(r["campaign_id"], 0)
            heroes = hero_sets.get(r["hero_count_set_id"]) or []
            resources = res_sets.get(r["resource_set_id"]) or []
            flags = ((1 if r["is_researching"] else 0)
                     | (2 if r["ll_wounded"] else 0))
            state = (_f(r["settlements"]), _f(r["lord_level"]),
                     _f(r["allies"]), _f(r["vassals"]))
            decs.append((did, cid, _f(r["turn"]), state[0],
                         _f(r["income"]), _f(r["power_rank"]), state[1],
                         _f(r["treasury"]), _f(r["armies"]),
                         _f(sum(v or 0.0 for _k, v in heroes)),
                         state[2], state[3], flags))
            pk = peaks.setdefault(cid, [None, None, None, None])
            for i2 in range(4):
                v2 = state[i2]
                if v2 is not None and (pk[i2] is None or v2 > pk[i2]):
                    pk[i2] = v2
            ex = {}
            for k, fv in resources:
                if fv is not None:
                    ex["res:" + k] = fv
                    res_camps.setdefault(k, set()).add(cid)
            for k, fv in heroes:
                if fv is not None:
                    ex["hero:" + k] = fv
                    hero_camps.setdefault(k, set()).add(cid)
            if ex:
                extras[did] = ex
        resources = [
            PositionFacetOption(key=k, label=labels.pooled_resource_name(k),
                                campaigns=len(cs))
            for k, cs in sorted(res_camps.items(), key=lambda kv: -len(kv[1]))]
        hero_types = [
            PositionFacetOption(key=k, label=labels.pretty(k).title(),
                                campaigns=len(cs))
            for k, cs in sorted(hero_camps.items(), key=lambda kv: -len(kv[1]))]
        captures: dict = {}
        sett_camps: dict = {}
        for r in con.execute(
                "SELECT t.campaign_id cid, a.action_key key, MIN(s.turn) turn"
                " FROM corpus.taken t"
                " JOIN dict.action a ON a.action_id = t.action_id"
                " JOIN dict.action_type at ON at.id = a.action_type_id"
                " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
                " WHERE t.counted AND at.key = ANY(%s)"
                " GROUP BY 1, 2", (list(_SETT_TYPES),)):
            key = str(r["key"] or "").split(":", 1)[-1]
            if not key:
                continue
            cid = _i(r["cid"], 0)
            t = _i(r["turn"])
            e = captures.setdefault(cid, {})
            if key not in e or (t is not None and (e[key] is None or t < e[key])):
                e[key] = t
            sett_camps[key] = sett_camps.get(key, 0) + 1
        settlements = [
            PositionFacetOption(
                key=k, label=labels.name_for("attack_settlement", k)
                or labels.pretty(k), campaigns=n2)
            for k, n2 in sorted(sett_camps.items(), key=lambda kv: -kv[1])]
        return {"camps": camps, "factions": factions, "maps": maps,
                "cultures": sorted({o.culture for o in factions if o.culture}),
                "rewards": rewards, "peaks": peaks, "takes": takes, "decs": decs,
                "extras": extras,
                "resources": resources, "hero_types": hero_types,
                "captures": captures, "settlements": settlements}
    return _stamped_slow("positions_data", build, force=force)


def _hist_map(con, family: str, force=False) -> dict:
    def build():
        out: dict = {}
        for r in _rows(
                "SELECT a.campaign_id cid, dk.key, MIN(a.acquired_turn) turn"
                " FROM acquisition a" + _acq_join(family) +
                " WHERE a.family = %(fam)s"
                " AND a.acquired_turn IS NOT NULL GROUP BY 1, 2",
                {"fam": family}):
            out.setdefault(int(r["cid"]), {})[r["key"]] = _i(r["turn"])
        return out
    return _stamped_slow(("hist", family), build, force=force)


def _parse_conditions(raw) -> list:
    out = []
    for c in raw or []:
        parts = str(c).split(":")
        head = parts[0]
        if head in ("has", "not") and len(parts) >= 3 \
                and parts[1] in POSITION_HISTORY:
            out.append(("hist", head == "has", parts[1], ":".join(parts[2:])))
        elif head == "flag" and len(parts) == 3 and parts[1] in POSITION_FLAGS:
            out.append(("flag", POSITION_FLAGS[parts[1]], parts[2] == "1"))
        elif head in ("res", "hero") and len(parts) >= 2 and parts[1]:
            lo = _f(parts[2]) if len(parts) > 2 and parts[2] != "" else None
            hi = _f(parts[3]) if len(parts) > 3 and parts[3] != "" else None
            if lo is not None or hi is not None:
                out.append(("sparse", head + ":" + parts[1], lo, hi))
        elif head in POSITION_SCALARS:
            lo = _f(parts[1]) if len(parts) > 1 and parts[1] != "" else None
            hi = _f(parts[2]) if len(parts) > 2 and parts[2] != "" else None
            if lo is not None or hi is not None:
                out.append(("range", POSITION_SCALARS[head], lo, hi))
    return out


def _matching_decisions(con, data, filters, conditions):
    camps = data["camps"]
    fac = filters.get("faction")
    mp = filters.get("map")
    cul_fac = None
    if filters.get("culture"):
        cul_fac = {o.key for o in data["factions"]
                   if (o.culture or "") == filters["culture"]}
    conds = _parse_conditions(conditions)
    extras = data["extras"]
    hist_cache: dict = {}
    for cond in conds:
        if cond[0] == "hist":
            hist_cache[cond[2]] = _hist_map(con, cond[2])
    empty: dict = {}
    for dec in data["decs"]:
        cid = dec[1]
        c = camps.get(cid)
        if fac and (not c or c[0] != fac):
            continue
        if cul_fac is not None and (not c or c[0] not in cul_fac):
            continue
        if mp and (not c or c[1] != mp):
            continue
        did = dec[0]
        ok = True
        for cond in conds:
            kind = cond[0]
            if kind == "range":
                v = dec[cond[1]]
                if v is None or (cond[2] is not None and v < cond[2]) \
                        or (cond[3] is not None and v > cond[3]):
                    ok = False
                    break
            elif kind == "flag":
                if bool(dec[12] & cond[1]) != cond[2]:
                    ok = False
                    break
            elif kind == "sparse":
                v = extras.get(did, empty).get(cond[1], 0.0)
                if (cond[2] is not None and v < cond[2]) \
                        or (cond[3] is not None and v > cond[3]):
                    ok = False
                    break
            else:
                got = hist_cache[cond[2]].get(cid, empty)
                t_ev = got.get(cond[3], "no")
                has = t_ev != "no" and (t_ev is None or dec[2] is None
                                        or t_ev <= dec[2])
                if has != cond[1]:
                    ok = False
                    break
        if ok:
            yield dec


def _position_facets(data) -> dict:
    return {"factions": data["factions"], "cultures": data["cultures"],
            "maps": data["maps"], "settlements": data["settlements"],
            "resources": data["resources"], "hero_types": data["hero_types"]}


@db.timed
def lookup_facets(con) -> dict:
    return _position_facets(_positions_data(con))


@db.timed
def positions_page(con, filters, conditions=None) -> dict:
    data = _positions_data(con)
    rewards = data["rewards"]
    peaks = data["peaks"]
    takes = data["takes"]
    w = reward_weights()
    w_vec = (w["settlements"], w["lord_levels"], w["allies"], w["vassals"])
    n_dec = 0
    camp_ids: set = set()
    fut_sum, fut_n = 0.0, 0
    agg: dict = {}
    for dec in _matching_decisions(con, data, filters, conditions):
        cid = dec[1]
        did = dec[0]
        n_dec += 1
        camp_ids.add(cid)
        fut = None
        pk = peaks.get(cid)
        if pk and pk[0] is not None and dec[3] is not None \
                and pk[1] is not None and dec[6] is not None:
            fut = (w_vec[0] * (pk[0] - dec[3]) + w_vec[1] * (pk[1] - dec[6]))
            if pk[2] is not None and dec[10] is not None:
                fut += w_vec[2] * (pk[2] - dec[10])
            if pk[3] is not None and dec[11] is not None:
                fut += w_vec[3] * (pk[3] - dec[11])
            fut_sum += fut
            fut_n += 1
        tk = takes.get(did)
        if tk is None:
            continue
        atype, akey = tk
        rew = rewards.get(cid)
        e = agg.setdefault((atype, akey), [0, 0.0, 0, 0.0, 0])
        e[0] += 1
        if rew is not None:
            e[1] += rew
            e[2] += 1
        if fut is not None:
            e[3] += fut
            e[4] += 1
    by_type: dict = {}
    for (atype, akey), e in agg.items():
        by_type.setdefault(atype, []).append((akey, e))
    takes_n = sum(e[0] for e in agg.values())

    sit_fut = fut_sum / fut_n if fut_n else None

    def pooled(items):
        n2 = sum(e[0] for _k, e in items)
        rn = sum(e[2] for _k, e in items)
        fn = sum(e[4] for _k, e in items)
        rew = sum(e[1] for _k, e in items) / rn if rn else None
        fut = sum(e[3] for _k, e in items) / fn if fn else None
        return n2, rew, fut

    rows = []

    def key_row(akey, e, label):
        return PositionKeyRow(
            key=akey, label=label, n=e[0],
            avg_reward=round(e[1] / e[2], 2) if e[2] else None,
            avg_future=round(e[3] / e[4], 2) if e[4] else None,
            delta_future=round(e[3] / e[4] - sit_fut, 2)
            if e[4] and sit_fut is not None else None)

    for atype, items in by_type.items():
        items.sort(key=lambda ke: -ke[1][0])
        keys = []
        for akey, e in items[:POSITION_KEYS_SHOWN]:
            keys.append(key_row(
                akey, e, labels.target_for(atype, akey) or labels.pretty(akey)))
        rest = items[POSITION_KEYS_SHOWN:]
        if rest:
            pooled_rest = [0, 0.0, 0, 0.0, 0]
            for _k, e in rest:
                for i2 in range(5):
                    pooled_rest[i2] += e[i2]
            keys.append(key_row("", pooled_rest, "… %d more keys" % len(rest)))
        n2, rew, fut = pooled(items)
        rows.append(PositionTypeRow(
            action_type=_phrase(atype), n=n2,
            share=round(100.0 * n2 / takes_n, 1) if takes_n else None,
            avg_reward=round(rew, 2) if rew is not None else None,
            avg_future=round(fut, 2) if fut is not None else None,
            delta_future=round(fut - sit_fut, 2)
            if fut is not None and sit_fut is not None else None, keys=keys))
    rows.sort(key=lambda r2: -r2.n)
    camp_rewards = [rewards[c2] for c2 in camp_ids if rewards.get(c2) is not None]
    return dict(
        decisions=n_dec, campaigns=len(camp_ids), takes=takes_n,
        mean_reward=_mean(camp_rewards),
        mean_future=round(sit_fut, 2) if sit_fut is not None else None,
        rows=rows)


_LOOKUP_SORTS = {
    "when": lambda r: r.ts,
    "campaign": lambda r: (r.campaign.label or "").lower(),
    "start": lambda r: (r.leader or r.faction.label or "").lower(),
    "race": lambda r: (r.faction.culture or "").lower(),
    "first": lambda r: r.first_turn,
    "matched": lambda r: r.matched,
    "turns": lambda r: r.turns,
    "reward": lambda r: r.reward,
    "sett": lambda r: r.settlements_gained,
    "lvl": lambda r: r.levels_gained,
    "outcome": lambda r: (r.outcome.label if r.outcome else ""),
}


@db.timed
def campaign_lookup(con, filters, conditions=None, sort=None, desc=True,
                    search=None, page=0, page_size=25) -> dict:
    data = _positions_data(con)
    camp = _camp_meta(con)
    camps = data["camps"]
    per: dict = {}
    n_dec = 0
    for dec in _matching_decisions(con, data, filters, conditions):
        n_dec += 1
        cid = dec[1]
        e = per.setdefault(cid, {"n": 0, "turn": None})
        e["n"] += 1
        t = dec[2]
        if t is not None and (e["turn"] is None or t < e["turn"]):
            e["turn"] = t
    rows = []
    rewards = []
    turns_all = []
    for cid, e in per.items():
        m = camp.get(cid)
        info = camps.get(cid)
        if not m:
            continue
        oc = info[3] if info else None
        rows.append(LookupCampaignRow(
            campaign=_camp(m["campaign_key"]), ts=_f(m["first_ts"]),
            leader=m["leader"],
            campaign_map=_id(ident.campaign_map(m["campaign_map"]))
            if m["campaign_map"] else None,
            faction=_fac(m["faction"]),
            first_turn=_i(e["turn"]), matched=e["n"],
            turns=_i(m["turns_reached"]), reward=_f(m["reward"]),
            settlements_gained=_f(m["settlements_gained"]),
            levels_gained=_f(m["levels_gained"]),
            outcome=_phrase(oc) if oc else None,
            outcome_state=_OUTCOME_STATE.get(str(oc or ""), "neutral")))
        rewards.append(m["reward"])
        if m["turns_reached"] is not None:
            turns_all.append(_i(m["turns_reached"], 0) or 0)
    rows.sort(key=lambda r2: -(r2.ts or 0.0))

    def texts(r):
        return (r.leader, r.campaign.label, r.campaign.raw, r.faction.label,
                r.faction.culture, r.outcome.label if r.outcome else None)

    matched = len(rows)
    got, total, at, size = _slice_rows(rows, _LOOKUP_SORTS, sort, desc,
                                       search, texts, page, page_size)
    return dict(
        campaigns=matched, decisions=n_dec,
        mean_reward=_mean(rewards), mean_turns=_mean(turns_all, 1),
        total=total, page=at, page_size=size,
        rows=got)


def _campaign_id_of(con, campaign_key: str):
    row = con.execute(
        "SELECT c.campaign_id, f.key AS faction, m.key AS campaign_map, c.leader"
        " FROM corpus.campaign c"
        " JOIN dict.faction f ON f.id = c.faction_id"
        " LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id"
        " WHERE c.campaign_key = %s", (campaign_key,)).fetchone()
    return dict(row) if row else None


@db.timed
def campaign_research(con, campaign_key: str) -> dict | None:
    meta = _campaign_id_of(con, campaign_key)
    if not meta:
        return None
    cid = _i(meta["campaign_id"], 0)
    took = [(_i(r["turn"]), r["key"]) for r in con.execute(
        "SELECT s.turn, a.action_key key FROM corpus.taken t"
        " JOIN dict.action a ON a.action_id = t.action_id"
        " JOIN dict.action_type at ON at.id = a.action_type_id"
        " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
        " WHERE t.campaign_id = %s AND t.counted AND at.key = 'research'"
        " ORDER BY t.decision_id", (cid,))]
    sets: dict = {}
    for r in con.execute(
            "WITH f AS (SELECT DISTINCT ON (s.turn) s.turn,"
            "  s.snapshot_id AS decision_id FROM corpus.snapshot s"
            " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
            " WHERE s.campaign_id = %s ORDER BY s.turn, s.snapshot_id)"
            " SELECT f.turn, a.action_key key FROM f"
            " JOIN corpus.offer o ON o.decision_id = f.decision_id"
            " JOIN dict.action a ON a.action_id = o.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " WHERE at.key = 'research'", (cid,)):
        sets.setdefault(_i(r["turn"], 0), set()).add(r["key"])
    offered_all = {k for s in sets.values() for k in s} | {k for _t, k in took}
    universe = {u["key"]: u for u in labels.tech_universe(offered_all)}
    parents = labels.tech_parents()
    depth = _tech_depth(parents, list(universe))
    rows = []
    for turn, key in took:
        done = None
        seen_later = False
        for t2 in sorted(sets):
            if turn is not None and t2 > turn and sets[t2]:
                seen_later = True
                if key not in sets[t2]:
                    done = t2
                    break
        u = universe.get(key) or {}
        rows.append(CampaignTechRow(
            turn=turn, key=key,
            label=labels.tech_name(key, u.get("technology_key")),
            parent=_parent_ident(parents, key),
            line=labels.tech_group_name(labels.tech_groups().get(key)),
            tier=depth.get(key),
            points=_i(u.get("research_points_required")),
            completed_turn=done, in_progress=done is None and not seen_later))
    return {"rows": rows, "completed": sum(1 for r2 in rows if r2.completed_turn),
            "universe": len(universe)}


def _campaign_chars(con, campaign_key: str) -> list:
    meta = _campaign_id_of(con, campaign_key)
    if not meta:
        return []
    out = []
    latest = con.execute(
        "SELECT * FROM (SELECT DISTINCT ON (cs.character_id) cs.snapshot_id,"
        " ch.cqi, cs.is_hero, cs.rank, cs.skill_points, sub.key AS subtype,"
        " cs.equipped_set_id"
        " FROM corpus.character ch"
        " JOIN corpus.char_state cs USING (character_id)"
        " LEFT JOIN dict.agent_subtype sub ON sub.id = cs.subtype_id"
        " WHERE ch.campaign_id = %s"
        " ORDER BY cs.character_id, cs.snapshot_id DESC) h"
        " ORDER BY h.cqi::text",
        (_i(meta["campaign_id"], 0),)).fetchall()
    eq_sets = {}
    set_ids = sorted({r["equipped_set_id"] for r in latest
                      if r["equipped_set_id"] is not None})
    if set_ids:
        for m in con.execute(
                "SELECT i.set_id, a.key, i.name"
                " FROM corpus.item_slot_set_member i"
                " LEFT JOIN dict.ancillary a ON a.id = i.ancillary_id"
                " WHERE i.set_id = ANY(%s) ORDER BY i.set_id, i.ord", (set_ids,)):
            eq_sets.setdefault(m["set_id"], []).append(m)
    for r in latest:
        sub = str(r["subtype"] or "")
        label = labels.subtype_name(sub) if sub else None
        eq = eq_sets.get(r["equipped_set_id"]) or []
        out.append(CampaignCharacter(
            cqi=str(r["cqi"]), kind="hero" if r["is_hero"] else "lord",
            label=label,
            rank=_i(r["rank"]), points_unspent=_i(r["skill_points"]),
            slots=len(eq),
            wearing=[Ident(raw=str(m["key"] or ""),
                           label=str(m["name"] or "")
                           or _item_ident(str(m["key"] or "")))
                     for m in eq]))
    out.sort(key=lambda c2: (c2.kind != "lord", -(c2.rank or 0)))
    if meta["leader"]:
        lead = next((c2 for c2 in out if c2.kind == "lord"), None)
        if lead:
            lead.label = meta["leader"]
    return out


@db.timed
def campaign_skills(con, campaign_key: str) -> dict | None:
    meta = _campaign_id_of(con, campaign_key)
    if not meta:
        return None
    chars = _campaign_chars(con, campaign_key)
    label_of = {c.cqi: c.label for c in chars}
    rows = []
    for r in con.execute(
            "SELECT s.turn, ch.cqi, a.action_key key, sm.level, sm.total_levels"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
            " LEFT JOIN corpus.char_state cs ON cs.snapshot_id = t.decision_id"
            "  AND cs.entity_seq = t.entity_seq"
            " LEFT JOIN corpus.character ch ON ch.character_id = cs.character_id"
            " LEFT JOIN dict.skill dk ON dk.key = a.action_key"
            " LEFT JOIN corpus.skill_set_member sm ON sm.set_id = cs.skill_set_id"
            "  AND sm.skill_id = dk.id"
            " WHERE t.campaign_id = %s AND t.counted AND at.key = 'skills'"
            " ORDER BY t.decision_id", (_i(meta["campaign_id"], 0),)):
        rows.append(CampaignSkillRow(
            turn=_i(r["turn"]), character=label_of.get(str(r["cqi"])),
            key=r["key"],
            label=labels.name_for("skills", r["key"]) or labels.pretty(r["key"]),
            rank=(_i(r["level"], 0) or 0) + 1,
            max_ranks=_i(r["total_levels"])))
    return {"characters": chars, "rows": rows}


@db.timed
def campaign_items(con, campaign_key: str) -> dict | None:
    meta = _campaign_id_of(con, campaign_key)
    if not meta:
        return None
    chars = _campaign_chars(con, campaign_key)
    label_of = {c.cqi: c.label for c in chars}
    events = []
    for r in con.execute(
            "SELECT s.turn, ch.cqi, at.key kind, a.action_key key"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at ON at.id = a.action_type_id"
            " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
            " LEFT JOIN corpus.char_state cs ON cs.snapshot_id = t.decision_id"
            "  AND cs.entity_seq = t.entity_seq"
            " LEFT JOIN corpus.character ch ON ch.character_id = cs.character_id"
            " WHERE t.campaign_id = %s AND t.counted"
            " AND at.key = ANY(%s)"
            " ORDER BY t.decision_id",
            (_i(meta["campaign_id"], 0), list(ITEM_ACTIONS))):
        events.append(CampaignItemEvent(
            turn=_i(r["turn"]), character=label_of.get(str(r["cqi"])),
            action="equip" if r["kind"] == "items" else "unequip",
            key=r["key"], label=_item_ident(r["key"]),
            category=labels.item_category(r["key"])))
    pool_row = con.execute(
        "SELECT MAX(o.decision_id) did FROM corpus.offer o"
        " JOIN dict.action a ON a.action_id = o.action_id"
        " JOIN dict.action_type at ON at.id = a.action_type_id"
        " JOIN corpus.snapshot s ON s.snapshot_id = o.decision_id"
        " WHERE at.key = 'items' AND s.campaign_id = %s",
        (_i(meta["campaign_id"], 0),)).fetchone()
    pool = []
    if pool_row and pool_row["did"]:
        pool = [Ident(raw=r["key"], label=_item_ident(r["key"]))
                for r in con.execute(
                    "SELECT DISTINCT a.action_key key FROM corpus.offer o"
                    " JOIN dict.action a ON a.action_id = o.action_id"
                    " JOIN dict.action_type at ON at.id = a.action_type_id"
                    " WHERE at.key = 'items' AND o.decision_id = %s"
                    " ORDER BY 1", (pool_row["did"],))]
    return {"events": events, "characters": chars, "pool": pool}


_VERDICT_RE = re.compile(r"^(\w+):?\s*\{", re.S)
_VERDICT_NUM = re.compile(r"'(\w+)':\s*(-?\d+(?:\.\d+)?)")
_VERDICT_ROOT = re.compile(r"'([\w./-]+)'")


def campaign_verdict(text) -> Verdict | None:
    if not text:
        return None
    raw = str(text).strip()
    m = _VERDICT_RE.match(raw)
    if not m:
        return Verdict(kind=None, text=raw)
    kind = m.group(1)
    body = raw[m.end() - 1:]
    try:
        d = ast.literal_eval(body)
        if not isinstance(d, dict):
            d = {}
    except (ValueError, SyntaxError):
        d = {k: float(v) for k, v in _VERDICT_NUM.findall(body)}
        ri = body.find("'roots'")
        if ri >= 0:
            d["roots"] = _VERDICT_ROOT.findall(body[body.find("[", ri):])
    roots = [str(r) for r in (d.get("roots") or [])]
    if kind == "turn_time_cap":
        turn, ts_, cap = _f(d.get("turn")), _f(d.get("turn_s")), _f(d.get("cap_s"))
        return Verdict(
            kind=kind,
            text="Turn %s hit the turn-time cap."
                 % (int(turn) if turn is not None else "?"),
            detail=("%.1fs elapsed of the %.1fs budget" % (ts_, cap))
            if ts_ is not None and cap else None,
            pct=round(100.0 * ts_ / cap, 0) if ts_ is not None and cap else None,
            roots=roots)
    return Verdict(kind=kind, text=kind.replace("_", " "),
                   detail="; ".join("%s %s" % (k, v) for k, v in d.items()
                                    if k != "roots") or None,
                   roots=roots)


@db.timed
def campaign_turn_rollup(con, campaign_key: str) -> list:
    return [TurnRollup(turn=_i(r["turn"], 0) or 0, decisions=_i(r["n"], 0) or 0,
                       confirmed=_i(r["conf"], 0) or 0, refused=_i(r["refused"], 0) or 0)
            for r in con.execute(
                "SELECT s.turn, COUNT(*) n,"
                " COUNT(*) FILTER (WHERE t.counted) conf,"
                " COUNT(*) FILTER (WHERE t.refusal_id IS NOT NULL"
                "  AND t.refusal_id <> ALL(%s)) refused"
                " FROM corpus.taken t"
                " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
                " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
                " WHERE c.campaign_key = %s"
                " GROUP BY s.turn ORDER BY s.turn DESC",
                (_skip_refusals(), campaign_key))]


def _result_of(row) -> tuple:
    refusal = row["refusal"] if "refusal" in row.keys() else None
    if refusal == "awaiting_execution":
        return "awaiting execution", "neutral"
    if refusal == "campaign_died":
        return "campaign died", "neutral"
    if _i(row["counted"], 0):
        return "confirmed", "ok"
    if refusal:
        return "refused", "bad"
    if _i(row["executed"], 0):
        return "executed, unconfirmed", "warn"
    return "no action", "neutral"


def ranked_pairs() -> list:
    return [(pair_key(a, b), a, b) for a, b in PAIRS]


def pair_options() -> list:
    comparable = {}
    for r in _rows("SELECT pair, comparable FROM agreement_summary"
                   " WHERE scope='all'"):
        comparable[r["pair"]] = _i(r["comparable"], 0) or 0
    return [PairOption(key=k, a=a, b=b,
                       comparable=Count(value=comparable.get(k, 0), noun="decisions",
                                        population="where both arms ranked at least three "
                                                   "of the same offers"))
            for k, a, b in ranked_pairs()]


def resolve_pair(pair=None):
    opts = pair_options()
    for o in opts:
        if o.key == pair:
            return o, opts
    for o in opts:
        if o.comparable.value > 0:
            return o, opts
    return opts[0], opts


_RANK_FIELD = {"greedy_catboost": "rank", "marwil_gnn": "gnn_rank", "greedy_gnn": "ggnn_rank"}


@db.timed
def decisions_page(con, offset=0, limit=DECISIONS_PAGE, action_type=None, policy=None,
                   result=None, campaign=None, q=None):
    where, args = [], []
    if action_type:
        where.append("at2.key = %s")
        args.append(action_type)
    if policy:
        raw = [k for k in _enum("policy") if arms.arm_of(k) == policy]
        if not raw:
            raw = [policy]
        where.append("pp.key IN (%s)" % ",".join(["%s"] * len(raw)))
        args += raw
    if campaign:
        where.append("c.campaign_key = %s")
        args.append(campaign)
    if q:
        where.append("(a.action_key ILIKE %s OR c.campaign_key ILIKE %s)")
        args += ["%%%s%%" % q, "%%%s%%" % q]
    if result == "confirmed":
        where.append("t.counted")
    elif result == "refused":
        where.append("t.refusal_id IS NOT NULL AND t.refusal_id <> ALL(%s)")
        args.append(_skip_refusals())
    elif result == "awaiting":
        where.append("t.refusal_id = %s")
        args.append(_enum("refusal")["awaiting_execution"])
    elif result == "campaign_died":
        where.append("t.refusal_id = %s")
        args.append(_enum("refusal")["campaign_died"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    base = (" FROM corpus.taken t"
            " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
            " JOIN corpus.decision d ON d.decision_id = t.decision_id"
            " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
            " JOIN dict.enum pp ON pp.enum_id = t.policy_id"
            " LEFT JOIN dict.enum rr ON rr.enum_id = t.refusal_id"
            " LEFT JOIN corpus.snapshot_entity se ON se.snapshot_id = t.decision_id"
            "  AND se.entity_seq = t.entity_seq"
            " LEFT JOIN dict.enum ek ON ek.enum_id = se.kind_id"
            " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
            " LEFT JOIN dict.region dr ON dr.id = se.region_id"
            " LEFT JOIN dict.faction cf ON cf.id = c.faction_id")
    total = _i(con.execute(
        "SELECT COUNT(*)" + (base + clause if clause else " FROM corpus.taken"),
        args).fetchone()[0], 0) or 0

    rows = con.execute(
        "SELECT t.decision_id, t.ts, ek.key AS context_kind,"
        "       COALESCE(ch.cqi::text, dr.key,"
        "        CASE WHEN ek.key = 'campaign' THEN cf.key END) AS context_id,"
        "       at2.key AS action_type, a.action_key, t.executed, t.confirmed,"
        "       t.counted, rr.key AS refusal, t.latency_ms,"
        "       (t.decision_id * %d + t.offer_seq) offer_id,"
        "       pp.key AS policy, c.campaign_key campaign_id, s.turn, d.n_offers"
        % MAX_OFFERS_PER_DECISION + base + clause +
        " ORDER BY t.decision_id DESC LIMIT %s OFFSET %s", args + [limit, offset]).fetchall()

    offer_by_id = {}
    ids = sorted({r["offer_id"] for r in rows if r["offer_id"] is not None})
    dids = sorted({r["decision_id"] for r in rows if r["offer_id"] is not None})
    if ids:
        for o in con.execute(
                "SELECT (o.decision_id * %d + o.offer_seq) offer_id, o.exploit,"
                " o.pct_global, o.rank, o.gnn_impact, o.gnn_rank,"
                " o.ggnn_score, o.ggnn_rank"
                " FROM corpus.offer o WHERE o.decision_id = ANY(%%s)"
                " AND (o.decision_id * %d + o.offer_seq) = ANY(%%s)"
                % (MAX_OFFERS_PER_DECISION, MAX_OFFERS_PER_DECISION),
                (dids, ids)):
            offer_by_id[o["offer_id"]] = o

    out = []
    for r in rows:
        res, state = _result_of(r)
        o = offer_by_id.get(r["offer_id"])
        gnn_rank = _i(o["gnn_rank"]) if o else None
        cat_rank = _i(o["rank"]) if o else None
        ggnn_rank = _i(o["ggnn_rank"]) if o else None
        n_off = _i(r["n_offers"])
        out.append(DecisionRow(
            decision_id=_i(r["decision_id"], 0) or 0, ts=_f(r["ts"]),
            campaign=_camp(r["campaign_id"]), turn=_i(r["turn"]), offers=n_off,
            entity="%s %s" % (r["context_kind"] or "", r["context_id"] or ""),
            action_type=_phrase(r["action_type"]), action_key=r["action_key"],
            target=labels.target_for(r["action_type"], r["action_key"]),
            result=_phrase(res), result_state=state, refusal=_phrase(r["refusal"]),
            policy=_phrase(r["policy"]),
            exploit=_f(o["exploit"]) if o else None,
            pct_global=_f(o["pct_global"]) if o else None,
            cat_rank=cat_rank, gnn_impact=_f(o["gnn_impact"]) if o else None,
            gnn_rank=gnn_rank, ggnn_score=_f(o["ggnn_score"]) if o else None,
            ggnn_rank=ggnn_rank, latency_ms=_f(r["latency_ms"])))
    return out, total


@db.timed
def decision_facets(con) -> dict:
    at = [r[0] for r in con.execute(
        "SELECT at2.key FROM dict.action_type at2"
        " WHERE EXISTS (SELECT 1 FROM corpus.taken t"
        "  JOIN dict.action a ON a.action_id = t.action_id"
        "  WHERE a.action_type_id = at2.id) ORDER BY at2.key")]
    po = sorted({arms.arm_of(r[0]) for r in con.execute(
        "SELECT DISTINCT pp.key FROM corpus.taken t"
        " JOIN dict.enum pp ON pp.enum_id = t.policy_id")} - {None})
    return {"action_types": [_phrase(a) for a in at],
            "policies": [_phrase(p) for p in po],
            "results": [_phrase(x) for x in ("confirmed", "refused", "awaiting",
                                             "campaign_died")]}


def decision_detail(con, decision_id: int):
    row = con.execute(
        "SELECT s.snapshot_id AS decision_id, t.ts, ek.key AS context_kind,"
        "       COALESCE(ch.cqi::text, dr.key,"
        "        CASE WHEN ek.key = 'campaign' THEN cf.key END) AS context_id,"
        "       at2.key AS action_type, a.action_key, t.executed, t.confirmed,"
        "       t.counted, rr.key AS refusal, t.latency_ms,"
        "       (t.decision_id * %d + t.offer_seq) offer_id, t.total_ms,"
        "       pp.key AS policy, c.campaign_key campaign_id, s.turn, d.n_offers,"
        "       dt.collect_ms, dt.score_ms, dt.roundtrip_ms"
        % MAX_OFFERS_PER_DECISION +
        " FROM corpus.snapshot s"
        " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
        " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
        " LEFT JOIN corpus.decision_timing dt ON dt.decision_id = s.snapshot_id"
        " LEFT JOIN corpus.taken t ON t.decision_id = s.snapshot_id"
        " LEFT JOIN dict.action a ON a.action_id = t.action_id"
        " LEFT JOIN dict.action_type at2 ON at2.id = a.action_type_id"
        " LEFT JOIN dict.enum pp ON pp.enum_id = t.policy_id"
        " LEFT JOIN dict.enum rr ON rr.enum_id = t.refusal_id"
        " LEFT JOIN corpus.snapshot_entity se ON se.snapshot_id = t.decision_id"
        "  AND se.entity_seq = t.entity_seq"
        " LEFT JOIN dict.enum ek ON ek.enum_id = se.kind_id"
        " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
        " LEFT JOIN dict.region dr ON dr.id = se.region_id"
        " LEFT JOIN dict.faction cf ON cf.id = c.faction_id"
        " WHERE s.snapshot_id = %s", (decision_id,)).fetchone()
    if not row:
        return None
    ts = _f(row["ts"])
    res, state = _result_of(row)
    head = DecisionRow(
        decision_id=_i(row["decision_id"], 0) or 0, ts=ts,
        campaign=_camp(row["campaign_id"]), turn=_i(row["turn"]),
        offers=_i(row["n_offers"]),
        entity="%s %s" % (row["context_kind"] or "", row["context_id"] or ""),
        action_type=_phrase(row["action_type"]), action_key=row["action_key"],
        result=_phrase(res), result_state=state, refusal=_phrase(row["refusal"]),
        policy=_phrase(row["policy"]), latency_ms=_f(row["latency_ms"]))

    taken_offer = row["offer_id"]
    offers = []
    for o in con.execute(
            "SELECT (o.decision_id * %d + o.offer_seq) offer_id,"
            "       ek.key AS context_kind,"
            "       COALESCE(ch.cqi::text, dr.key,"
            "        CASE WHEN ek.key = 'campaign' THEN cf.key END) AS context_id,"
            "       at2.key AS action_type, a.action_key, o.exploit,"
            "       o.pct_global, o.rank, o.gnn_impact, o.gnn_rank, o.ggnn_score,"
            "       o.ggnn_rank" % MAX_OFFERS_PER_DECISION +
            " FROM corpus.offer o"
            " JOIN dict.action a ON a.action_id = o.action_id"
            " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
            " LEFT JOIN corpus.snapshot_entity se ON se.snapshot_id = o.decision_id"
            "  AND se.entity_seq = o.entity_seq"
            " LEFT JOIN dict.enum ek ON ek.enum_id = se.kind_id"
            " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
            " LEFT JOIN dict.region dr ON dr.id = se.region_id"
            " LEFT JOIN corpus.snapshot s2 ON s2.snapshot_id = o.decision_id"
            " LEFT JOIN corpus.campaign c2 ON c2.campaign_id = s2.campaign_id"
            " LEFT JOIN dict.faction cf ON cf.id = c2.faction_id"
            " WHERE o.decision_id = %s"
            " ORDER BY COALESCE(o.rank, 9999), o.offer_seq", (decision_id,)):
        offers.append(OfferRow(
            rank=_i(o["rank"]), entity="%s %s" % (o["context_kind"] or "", o["context_id"] or ""),
            action_type=_phrase(o["action_type"]), action_key=o["action_key"],
            exploit=_f(o["exploit"]), pct_global=_f(o["pct_global"]),
            gnn_impact=_f(o["gnn_impact"]),
            gnn_rank=_i(o["gnn_rank"]), ggnn_score=_f(o["ggnn_score"]),
            ggnn_rank=_i(o["ggnn_rank"]), taken=(o["offer_id"] == taken_offer)))

    ents = []
    rec = hydrate.record(con, decision_id, legacy=True)
    for e in (rec.get("entities") or [])[:40]:
        ents.append(EntityState(context_kind=e.get("context_kind") or "",
                                context_id=str(e.get("context_id") or ""),
                                features=e.get("state") or {}))
    return head, offers, ents, _phases(row)


def _phases(row) -> list:
    collect = _f(row["collect_ms"], 0.0) or 0.0
    score = _f(row["score_ms"], 0.0) or 0.0
    round_trip = _f(row["roundtrip_ms"], 0.0) or 0.0
    verify = _f(row["total_ms"], 0.0) or 0.0
    queue = max(0.0, round_trip - collect - score)
    out = []
    for name, ms in (("collect", collect), ("queue", queue), ("score", score),
                     ("verify", verify)):
        if ms:
            out.append(PhaseSpan(phase=name, ms=round(ms, 1)))
    return out


@db.timed
def actions_summary(con):
    types: dict = {}
    for r in con.execute(
            "SELECT at2.key atype,"
            " COUNT(*) FILTER (WHERE t.refusal_id IS NULL"
            "  OR t.refusal_id <> ALL(%s)) tried,"
            " COUNT(*) FILTER (WHERE t.counted) ok"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
            " GROUP BY 1", (_skip_refusals(),)):
        slot = types.setdefault(r["atype"], [0, 0])
        slot[0] += _i(r["tried"], 0) or 0
        slot[1] += _i(r["ok"], 0) or 0
    refus_n: dict = {}
    for r in con.execute(
            "SELECT at2.key atype, rr.key refusal, COUNT(*) n FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
            " JOIN dict.enum rr ON rr.enum_id = t.refusal_id"
            " WHERE t.refusal_id <> ALL(%s)"
            " GROUP BY 1, 2", (_skip_refusals(),)):
        key = (r["atype"], r["refusal"])
        refus_n[key] = refus_n.get(key, 0) + (_i(r["n"], 0) or 0)
    pol: dict = {}
    for r in con.execute(
            "SELECT COALESCE(pp.key, '(unrecorded)') p, COUNT(*) n"
            " FROM corpus.taken t"
            " LEFT JOIN dict.enum pp ON pp.enum_id = t.policy_id GROUP BY 1"):
        pol[r["p"]] = pol.get(r["p"], 0) + (_i(r["n"], 0) or 0)
    tr = con.execute(
        "SELECT COUNT(*) n,"
        " COUNT(*) FILTER (WHERE refusal_id IS NULL"
        "  OR refusal_id <> ALL(%s)) attempted,"
        " COUNT(*) FILTER (WHERE counted) confirmed FROM corpus.taken",
        (_skip_refusals(),)).fetchone()
    tot = [_i(tr["n"], 0) or 0, _i(tr["attempted"], 0) or 0, _i(tr["confirmed"], 0) or 0]
    rows = []
    for atype, (tried, ok) in types.items():
        rate = Rate(n=ok, of=tried, noun="actions", population="attempted of type %s" % atype)
        rows.append(ActionTypeRow(action_type=_phrase(atype), rate=rate))
    rows.sort(key=lambda r: (r.rate.pct if r.rate.pct is not None else 999, -r.rate.of))

    refus = {}
    for (atype, refusal), n in refus_n.items():
        refus.setdefault(atype, []).append((n, refusal))
    for row in rows:
        got = sorted(refus.get(row.action_type.raw, []), reverse=True)[:3]
        row.refusals = [_phrase(x[1]) for x in got]

    pol_rows = _by_arm(sorted(({"p": p, "n": n} for p, n in pol.items()),
                              key=lambda r: -r["n"]))
    drawn = sum(n for p, n, _fb in pol_rows
                if p not in ("forced_end_turn", "(unrecorded)"))
    policies = []
    for p, n, fell in pol_rows:
        note = None
        if p == "forced_end_turn":
            note = "not a strategy draw -- the loop ended the turn"
        elif fell:
            note = ("%d of these were drawn but could not score, so random picked instead"
                    % fell)
        policies.append(PolicyRow(
            policy=_phrase(p), picks=n,
            share=Rate(n=n, of=drawn or 1, noun="picks",
                       population="drawn from the strategy mix"),
            note=note))

    all_rows, attempted, confirmed = tot
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


_INTERRUPT_SCORERS = (("greedy_catboost", "exploit"), ("marwil_gnn", "gnn"))


def _interrupt_coverage(con) -> dict:
    kinds: dict = {}
    last_id = None
    opts: list = []
    b = None

    def fold(bucket, options):
        bucket["rows"] += 1
        bests = {}
        for arm, key in _INTERRUPT_SCORERS:
            got = [(k, o) for k, o in options if o.get(key) is not None]
            if got:
                bucket["scored"][arm] = bucket["scored"].get(arm, 0) + 1
                bests[arm] = max(got, key=lambda kv: _f(kv[1].get(key), -1e9))[0]
        if len(bests) >= 2:
            bucket["cmp"] += 1
            if len(set(bests.values())) == 1:
                bucket["agree"] += 1

    for r in con.execute(
            "SELECT i.interrupt_id, ek.key AS kind, o.option_key, o.exploit, o.gnn"
            " FROM corpus.interrupt i"
            " JOIN dict.enum ek ON ek.enum_id = i.kind_id"
            " LEFT JOIN corpus.interrupt_option o ON o.interrupt_id = i.interrupt_id"
            " ORDER BY i.interrupt_id, o.ord"):
        if r["interrupt_id"] != last_id:
            if last_id is not None:
                fold(b, opts)
            last_id = r["interrupt_id"]
            opts = []
            b = kinds.setdefault(r["kind"], {"rows": 0, "scored": {},
                                             "agree": 0, "cmp": 0})
        if r["option_key"] is not None:
            opts.append((r["option_key"], {"exploit": r["exploit"],
                                           "gnn": r["gnn"]}))
    if last_id is not None:
        fold(b, opts)
    return kinds


@db.timed
def menus(con):
    total = _i(con.execute("SELECT COUNT(*) FROM corpus.interrupt").fetchone()[0], 0) or 0
    by_screen = [Count(value=_i(r["n"], 0) or 0, noun=str(r["kind"] or "screens"),
                       population="blocking-menu decisions of this kind")
                 for r in con.execute(
                     "SELECT ek.key AS kind, COUNT(*) n FROM corpus.interrupt i"
                     " JOIN dict.enum ek ON ek.enum_id = i.kind_id"
                     " GROUP BY 1 ORDER BY n DESC")]

    pol_rows = _by_arm(con.execute(
        "SELECT COALESCE(pp.key, '(unrecorded)') p, COUNT(*) n"
        " FROM corpus.interrupt i"
        " LEFT JOIN dict.enum pp ON pp.enum_id = i.policy_id"
        " GROUP BY 1 ORDER BY n DESC").fetchall())
    tot_pol = sum(n for _p, n, _fb in pol_rows) or 1
    policies = [PolicyRow(policy=_phrase(p), picks=n,
                          share=Rate(n=n, of=tot_pol, noun="picks",
                                     population="on blocking-menu decisions"),
                          note=("%d of these were drawn but could not score, so random "
                                "picked instead" % fell) if fell else None)
                for p, n, fell in pol_rows]

    heads = con.execute(
        "SELECT i.interrupt_id, i.legacy_interrupt_id, s.ts, ek.key AS kind,"
        " i.root, c.campaign_key campaign_id, s.turn, i.chosen, i.executed,"
        " i.confirmed, i.counted, rr.key AS refusal, i.latency_ms,"
        " pp.key AS policy,"
        " (SELECT COUNT(*) FROM corpus.interrupt_option o"
        "   WHERE o.interrupt_id = i.interrupt_id) n_options"
        " FROM corpus.interrupt i"
        " JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id"
        " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
        " JOIN dict.enum ek ON ek.enum_id = i.kind_id"
        " LEFT JOIN dict.enum pp ON pp.enum_id = i.policy_id"
        " LEFT JOIN dict.enum rr ON rr.enum_id = i.refusal_id"
        " ORDER BY s.ts DESC, i.interrupt_id DESC LIMIT %s",
        (MENUS_ROWS,)).fetchall()
    opt_rows: dict = {}
    ids = [r["interrupt_id"] for r in heads]
    if ids:
        for o in con.execute(
                "SELECT interrupt_id, option_key, exploit, gnn"
                " FROM corpus.interrupt_option WHERE interrupt_id = ANY(%s)"
                " ORDER BY interrupt_id, ord", (ids,)):
            opt_rows.setdefault(o["interrupt_id"], []).append(o)
    rows = []
    for r in heads:
        res, state = _result_of(r)
        chosen = r["chosen"]
        options = [InterruptOption(label=_phrase(o["option_key"]),
                                   exploit=_f(o["exploit"]),
                                   gnn=_f(o["gnn"]),
                                   chosen=(str(o["option_key"]) == str(chosen)))
                   for o in (opt_rows.get(r["interrupt_id"]) or [])[:24]]
        rows.append(InterruptRow(
            interrupt_id=_i(r["interrupt_id"], 0) or 0,
            legacy_interrupt_id=_i(r["legacy_interrupt_id"]),
            ts=_f(r["ts"]),
            kind=_phrase(r["kind"]), root=r["root"], campaign=_camp(r["campaign_id"]),
            turn=_i(r["turn"]), result=_phrase(res), result_state=state,
            chosen=_phrase(chosen), n_options=_i(r["n_options"]),
            policy=_phrase(r["policy"]), latency_ms=_f(r["latency_ms"]), options=options))

    cover = _interrupt_coverage(con)
    coverage = [ArmCoverage(
        screen=_phrase(k), rows=v["rows"], scored=v["scored"], compared=v["cmp"],
        agree=Rate(n=v["agree"], of=v["cmp"], noun="screens",
                   population="scored by two or more arms")) for k, v in sorted(cover.items())]
    coverage.sort(key=lambda c: -c.rows)

    return (Count(value=total, noun="decisions", population="on blocking menus in this run dir"),
            by_screen, policies, coverage, rows)


@db.timed
def timeline(con) -> list:
    rows = con.execute(
        "SELECT t.decision_id, t.ts, at2.key AS action_type, a.action_key,"
        "       t.executed, t.confirmed, t.counted, rr.key AS refusal, t.total_ms,"
        "       c.campaign_key campaign_id, s.turn,"
        "       dt.collect_ms, dt.score_ms, dt.roundtrip_ms"
        " FROM corpus.taken t"
        " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
        " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
        " JOIN dict.action a ON a.action_id = t.action_id"
        " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
        " LEFT JOIN dict.enum rr ON rr.enum_id = t.refusal_id"
        " LEFT JOIN corpus.decision_timing dt ON dt.decision_id = t.decision_id"
        " ORDER BY t.decision_id DESC LIMIT %s", (TIMELINE_DECISIONS,)).fetchall()
    lanes: dict = {}
    prev_ts: dict = {}
    for r in reversed(rows):
        key = (r["campaign_id"], _i(r["turn"], 0) or 0)
        lane = lanes.setdefault(key, {"actions": [], "ok": 0, "n": 0, "t0": None, "t1": None})
        res, state = _result_of(r)
        phases = _phases(r)
        total = sum(p.ms for p in phases)
        ts = _f(r["ts"]) or 0.0
        gap = None
        last = prev_ts.get(r["campaign_id"])
        if last is not None:
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


_MODEL_DIRS = (
    ("greedy_catboost", common.MODEL_GLOBAL, "catboost",
     "One reward regression over the action and game-state features: it predicts the "
     "return of each offered action directly, and the arm takes the highest prediction. "
     "No state-only model, no advantage -- the catboost twin of greedy_gnn."),
    ("greedy_catboost interrupt", common.MODEL_INTERRUPT, "catboost",
     "One reward regression over the screen, game-state and option features for blocking "
     "screens -- battles, dilemmas, occupation choices. The only interrupt model: the "
     "interrupt mix draws from greedy_catboost, random and ruleset."),
    ("greedy_gnn", common.MODEL_MAPGRAPH_GREEDY, "mapgraph",
     "The graph encoder with a reward head: one regression of the return per action "
     "node, fit by MSE on the action that was taken. No state-only model, no advantage, "
     "no value head -- the arm takes the action with the highest predicted return."),
)


def model_cards() -> list:
    return _model_cards()


@db.timed
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
            want = ("model.pt",) if family == "mapgraph" else ("model.cbm",)
            missing = [f for f in want if not os.path.exists(os.path.join(d, f))]
            if missing:
                status, note = "incomplete", "missing on disk: %s" % ", ".join(missing)
            elif family == "mapgraph" and meta.get("schema_hash") != _graph_schema_hash():
                status = "stale schema"
                note = ("trained on graph schema %s, the code builds %s -- the ranker "
                        "refuses it until the next retrain"
                        % (str(meta.get("schema_hash"))[:8], _graph_schema_hash()[:8]))
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
                fit = meta.get("fit") if isinstance(meta.get("fit"), dict) else {}
                mse, sd = _f(fit.get("val_mse")), _f(meta.get("y_sd"))
                if mse is not None and sd is not None and mse >= 0:
                    rows.append(("held-out RMSE", "%.4f" % ((mse ** 0.5) * sd)))
                if fit.get("val_r2") is not None:
                    rows.append(("held-out R²", "%+.3f" % (_f(fit["val_r2"]) or 0)))
                if fit.get("val_rows") is not None:
                    rows.append(("held-out rows", "{:,}".format(_i(fit["val_rows"], 0) or 0)))
                for k, label, fmt in (("val_listwise_nll", "held-out listwise NLL", "%.4f"),
                                      ("val_value_mse", "held-out value MSE", "%.4f")):
                    if fit.get(k) is not None:
                        rows.append((label, fmt % (_f(fit[k]) or 0)))
                for k, label in (("epochs_run", "epochs"), ("stopped_by", "stopped by"),
                                 ("device", "trained on")):
                    if fit.get(k) is not None:
                        rows.append((label, str(fit[k])))
                rows.append(("graph schema", "v%s %s" % (meta.get("schema_version"),
                                                         str(meta.get("schema_hash"))[:8])))
            else:
                cfit = (meta.get("fit") or {}).get("model") or {}
                if cfit.get("val_rmse") is not None:
                    rows.append(("held-out RMSE", "%.4f" % (_f(cfit["val_rmse"]) or 0)))
                r2 = _f(cfit.get("val_r2"))
                if r2 is not None:
                    rows.append(("held-out R²", "%+.3f" % r2))
                held = cfit.get("val_rows")
                if held is not None:
                    rows.append(("held-out rows", "{:,}".format(_i(held, 0) or 0)))
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


_SCHEMA_HASH = []


def _graph_schema_hash() -> str:
    if not _SCHEMA_HASH:
        sys.path.insert(0, common.ROOT)
        from advisor.mapgraph import schema as GS
        _SCHEMA_HASH.append(GS.schema_hash())
    return _SCHEMA_HASH[0]


def _meta_json(model_dir) -> dict:
    try:
        return json.load(open(os.path.join(common.native(model_dir), "meta.json"),
                              encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@db.timed
def _fit_config() -> list:
    from advisor_api.models import FitConfigRow
    out = []
    for arm, path, role in (
            ("greedy_gnn", common.MODEL_MAPGRAPH_GREEDY,
             "one reward regression per action node over the graph encoder"),):
        g = _meta_json(path)
        cfg = g.get("cfg") or {}
        out.append(FitConfigRow(
            family=arm, role=role,
            hyperparameters={k: cfg[k] for k in sorted(cfg)},
            compute={"device": (g.get("fit") or {}).get("device", "auto"),
                     "rows": g.get("rows"), "schema": g.get("schema_version")}))
    c = _meta_json(common.MODEL_GLOBAL)
    out.append(FitConfigRow(
        family="greedy_catboost", role="one reward regression over action and game-state features",
        hyperparameters={k: c[k] for k in ("short_horizon", "short_weight",
                                           "pred_lo", "pred_hi", "target") if k in c},
        compute={"rows": c.get("rows"),
                 "features": len(c.get("num") or []) + len(c.get("cat") or [])}))
    return out


@db.timed
def model_versions() -> list:
    from advisor_api.models import ModelVersion
    rows = _rows("SELECT trial, generation, seg_from_ts, seg_to_ts, campaigns,"
                 " corpus_decisions FROM model_generation ORDER BY seg_from_ts")
    versions, cur = [], None
    for r in rows:
        gen = _i(r["generation"], 0) or 0
        if gen > 0 or cur is None:
            cur = {"version": str(r["trial"]) if gen > 0 else "before-%s" % r["trial"],
                   "trained": gen > 0, "trained_ts": _f(r["seg_from_ts"]),
                   "corpus": _i(r["corpus_decisions"]), "windows": [],
                   "trials": 0, "campaigns": 0}
            versions.append(cur)
        cur["windows"].append([_f(r["seg_from_ts"], 0.0) or 0.0,
                               _f(r["seg_to_ts"], 0.0) or 0.0])
        cur["trials"] += 1
        cur["campaigns"] += _i(r["campaigns"], 0) or 0
    out = []
    for v in reversed(versions):
        stamp = time.strftime("%m-%d %H:%M", time.localtime(v["trained_ts"] or 0.0))
        head = ("trained %s" % stamp if v["trained"] else "in force at %s (trained earlier)"
                % stamp)
        corpus = ("  corpus %s" % "{:,}".format(v["corpus"]) if v["corpus"] else "")
        out.append(ModelVersion(
            version=v["version"],
            label="%s%s  %d trial%s  %d campaigns" % (
                head, corpus, v["trials"], "" if v["trials"] == 1 else "s", v["campaigns"]),
            trained=v["trained"], trained_ts=v["trained_ts"], corpus_decisions=v["corpus"],
            trials=v["trials"], campaigns=v["campaigns"],
            from_ts=min(w[0] for w in v["windows"]), to_ts=max(w[1] for w in v["windows"]),
            windows=v["windows"]))
    return out


def _version_windows(version):
    if not version:
        return None
    vs = model_versions()
    for i, v in enumerate(vs):
        if v.version == version:
            lo = v.from_ts if i + 1 < len(vs) else 0.0
            hi = vs[i - 1].from_ts if i > 0 else math.inf
            return [[lo, hi]]
    return [[math.inf, math.inf]]


def _ts_in(windows):
    return "(" + " OR ".join("(t.ts >= %s AND t.ts < %s)" for _ in windows) + ")"


def _diplomacy_partials(con) -> dict:
    out: dict = {}
    for r in con.execute(
            "SELECT c.campaign_key ckey, a.action_key,"
            " COALESCE(pp.key, '(unrecorded)') p, MIN(t.ts) ts,"
            " COUNT(*) FILTER (WHERE t.refusal_id IS NULL"
            "  OR t.refusal_id <> ALL(%s)) attempted,"
            " COUNT(*) FILTER (WHERE t.counted) confirmed"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
            " JOIN corpus.campaign c ON c.campaign_id = t.campaign_id"
            " LEFT JOIN dict.enum pp ON pp.enum_id = t.policy_id"
            " WHERE at2.key = 'diplomacy'"
            " GROUP BY 1, 2, 3", (_skip_refusals(),)):
        k = str(r["action_key"] or "")
        term = k.split(":", 1)[1] if ":" in k else k
        part = out.setdefault(r["ckey"], {"ts": None, "cells": {}})
        ts = _f(r["ts"])
        if ts is not None and (part["ts"] is None or ts < part["ts"]):
            part["ts"] = ts
        cell = part["cells"].setdefault((term, r["p"]), [0, 0])
        cell[0] += _i(r["attempted"], 0) or 0
        cell[1] += _i(r["confirmed"], 0) or 0
    return out


@db.timed
def diplomacy_mix(con, version=None):
    from advisor_api.models import DiplomacyCell, DiplomacyRow
    windows = _version_windows(version)
    cells: dict = {}
    per_source: dict = {}
    for part in _diplomacy_partials(con).values():
        ts = part["ts"] or 0.0
        if windows and not any(a <= ts < b for a, b in windows):
            continue
        for (term, p), (att, ok) in part["cells"].items():
            arm = arms.arm_of(p) or arms.UNRECORDED
            c = cells.setdefault(term, {}).setdefault(arm, [0, 0])
            c[0] += att
            c[1] += ok
            per_source[arm] = per_source.get(arm, 0) + att
    sources = sorted(per_source, key=lambda a: -per_source[a])
    total = sum(per_source.values())
    rows = []
    for term, by in sorted(cells.items(), key=lambda kv: -sum(v[0] for v in kv[1].values())):
        att = sum(v[0] for v in by.values())
        ok = sum(v[1] for v in by.values())
        rows.append(DiplomacyRow(
            term=_phrase(term), attempted=att, confirmed=ok,
            share=Rate(n=att, of=total or 1, noun="attempts",
                       population="of every diplomatic action attempted"),
            by_source=[DiplomacyCell(
                source=_phrase(a), attempted=by.get(a, [0, 0])[0],
                confirmed=by.get(a, [0, 0])[1],
                share=(round(by.get(a, [0, 0])[0] / att, 4)
                       if att and by.get(a, [0, 0])[0] else None)) for a in sources]))
    return [_phrase(a) for a in sources], total, rows


@db.timed
def forcing(con, version=None):
    windows = _version_windows(version)
    base = ("SELECT COALESCE(pp.key, '(unrecorded)') p, at2.key atype, COUNT(*) n"
            " FROM corpus.taken t"
            " JOIN dict.action a ON a.action_id = t.action_id"
            " JOIN dict.action_type at2 ON at2.id = a.action_type_id"
            " LEFT JOIN dict.enum pp ON pp.enum_id = t.policy_id")
    if windows:
        rows = con.execute(base + " WHERE " + _ts_in(windows) + " GROUP BY p, atype",
                           [t for w in windows for t in w]).fetchall()
    else:
        rows = con.execute(base + " GROUP BY p, atype").fetchall()
    by_arm: dict = {}
    for r in rows:
        atype = r["atype"]
        if not atype:
            continue
        arm = arms.arm_of(r["p"]) or arms.UNRECORDED
        mix = by_arm.setdefault(arm, {})
        mix[atype] = mix.get(atype, 0) + (_i(r["n"], 0) or 0)
    tiles = []
    for arm in arms.TRAINABLE:
        mix = by_arm.get(arm) or {}
        tot = sum(mix.values())
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
    n_dec = sum(sum((by_arm.get(a) or {}).values()) for a in arms.TRAINABLE)
    return tiles, Count(value=n_dec, noun="decisions",
                        population="drawn by a model arm in this run dir")


def _wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return max(0.0, (c - s) / d), min(1.0, (c + s) / d)


def _freshness(tenant: str):
    from advisor_api.models import AnalyticsFreshness
    st = _one("SELECT * FROM state WHERE tenant = %s", (tenant,)) or {}
    watermark = _i(st.get("watermark"), 0) or 0
    behind = 0
    if tenant == "model_agreement":
        corpus_hi = _i(db.connect().execute(
            "SELECT MAX(decision_id) FROM corpus.decision").fetchone()[0], 0) or 0
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
        computed_through=watermark or None, age_seconds=(round(age, 1) if age else None),
        formula_version=_i(st.get("formula_version"), 0) or 0,
        state=state, detail=detail)


def _excluded_counts(s: dict, a: str, b: str) -> list:
    return [
        Count(value=_i(s.get("missing_b"), 0) or 0, noun="decisions",
              population="where %s stored no ranking, so only %s ranked them" % (b, a)),
        Count(value=_i(s.get("no_scores"), 0) or 0, noun="decisions",
              population="carrying no stored scores at all"),
    ]


def agreement_matrices() -> list:
    from advisor_api.models import AgreementMatrix, AgreementMatrixCell
    arms_ = list(RANKED_ARMS)
    summary = {r["pair"]: r for r in _rows(
        "SELECT pair, rho_median, comparable FROM agreement_summary WHERE scope='all'")}
    latest = {}
    for r in _rows(
            "SELECT pair, seq, rho_median, decisions, gate, trial, generation, from_ts,"
            " retrained FROM agreement_series WHERE axis='generation'"
            " ORDER BY pair, seq"):
        latest[r["pair"]] = r
    all_cells, gen_cells = [], []
    gen_from, gen_trial = None, None
    for key, a, b in ranked_pairs():
        s = summary.get(key) or {}
        n_all = _i(s.get("comparable"), 0) or 0
        all_cells.append(AgreementMatrixCell(
            a=a, b=b, pair=key, rho_median=_f(s.get("rho_median")),
            decisions=Count(value=n_all, noun="decisions",
                            population="comparable, over the whole run dir"),
            note=(None if n_all else "no decision carries both rankings yet")))
        g = latest.get(key) or {}
        n_gen = _i(g.get("decisions"), 0) or 0
        if g.get("from_ts") is not None and gen_from is None:
            gen_from, gen_trial = _f(g.get("from_ts")), g.get("trial")
        gen_cells.append(AgreementMatrixCell(
            a=a, b=b, pair=key, rho_median=_f(g.get("rho_median")),
            decisions=Count(value=n_gen, noun="decisions",
                            population="comparable, under the current model version"),
            note=(g.get("gate") if g.get("gate") else
                  (None if n_gen else "no comparable decision under this version yet"))))
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(gen_from)) if gen_from else None)
    return [
        AgreementMatrix(
            key="generation", title="current model version", arms=arms_, cells=gen_cells,
            detail=("in force since %s (%s)" % (when, gen_trial)
                    if when else "no model version recorded yet")),
        AgreementMatrix(
            key="all", title="all time", arms=arms_, cells=all_cells,
            detail="every comparable decision in this run dir, across every model version"),
    ]


def _stale_analytics(e) -> str:
    return ("the analytics tables predate the pair formula (%s) -- restart the analytics "
            "service (`python -m analytics.runner`) and it rebuilds them" % str(e)[:80])


_SECONDARY_NOTE = "a supplement to the rank correlation above, not a substitute for it"


def _secondary(s: dict) -> list:
    from advisor_api.models import SecondaryMeasure
    out = []
    if s.get("rbo_median") is not None:
        out.append(SecondaryMeasure(
            measure="rank-biased overlap (p=0.9), median",
            value="%.3f" % _f(s["rbo_median"], 0.0)))
    return out


@db.timed
def agreement_page(pair: str | None = None):
    from advisor_api.models import (AgreementPage, AgreementRankRow, AgreementSummary,
                                    CorrelationSummary, RhoBin)
    fresh = _freshness("model_agreement")
    po, opts = resolve_pair(pair)
    a, b = po.a, po.b
    head = dict(pair=po.key, a=a, b=b, pairs=opts)
    scope = Scope(text="rank correlation between %s and %s, over the offers both ranked"
                       % (a, b),
                  detail="every decision in this run dir, precomputed; pick any two of the "
                         "arms that store a per-offer ranking")
    s = _one("SELECT * FROM agreement_summary WHERE scope='all' AND pair=%s",
             (po.key,))
    if not s:
        return AgreementPage(
            scope=scope, freshness=fresh, summary=[], rows=[],
            matrices=agreement_matrices(),
            empty_reason=("nothing has been folded in for this run dir yet -- the analytics "
                          "service builds it within a few seconds of starting"), **head)
    comparable = _i(s.get("comparable"), 0) or 0
    decisions = _i(db.connect().execute(
        "SELECT COUNT(*) FROM corpus.decision").fetchone()[0], 0) or 0
    if not comparable:
        return AgreementPage(
            scope=scope, freshness=fresh, summary=[], rows=[],
            matrices=agreement_matrices(),
            empty_reason=("no decision in this run dir carries both a %s rank and a %s "
                          "rank, so there is nothing to correlate" % (a, b)), **head)
    same = int(round((_f(s.get("top1_rate"), 0.0) or 0.0) * comparable))
    corr = CorrelationSummary(
        compared=Count(value=comparable, noun="decisions",
                       population="where both arms ranked at least three of the same "
                                  "offers"),
        coverage=Rate(n=comparable, of=decisions, noun="decisions",
                      population="recorded in this run dir"),
        rho_median=_f(s.get("rho_median")), rho_mean=_f(s.get("rho_mean")),
        tau_median=_f(s.get("tau_median")),
        same_best=Rate(n=same, of=comparable, noun="decisions", population="comparable"),
        excluded=_excluded_counts(s, a, b))
    summary = [
        AgreementSummary(measure="decisions compared", value="{:,}".format(comparable),
                         help=None),
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
        rho_median=_f(r["rho_median"]))
        for r in _rows("SELECT * FROM agreement_breakdown WHERE dim='arm' AND pair=%s"
                       " ORDER BY decisions DESC", (po.key,))]
    bins = [RhoBin(lo=_f(h["lo"], 0.0), hi=_f(h["hi"], 0.0),
                   decisions=_i(h["n"], 0) or 0)
            for h in _rows("SELECT * FROM agreement_hist WHERE pair=%s ORDER BY bucket",
                           (po.key,))]
    return AgreementPage(scope=scope, freshness=fresh, correlation=corr, rho_bins=bins,
                         summary=summary, rows=rows, secondary=_secondary(s),
                         matrices=agreement_matrices(), **head)


_GENERATION_CAVEAT = (
    "Which model version ranked a decision is matched by TIMESTAMP, not recorded: nothing "
    "in the corpus joins a stored ranking to the weights that produced it. Windows come "
    "from the training ledger's own start and flush times, clipped so a decision lands in "
    "exactly one. A version runs from one retrain to the next; a session that starts on "
    "an already-retrained model (g0) is folded into the version trained before it.")


@db.timed
def agreement_series(axis: str = "window", pair: str | None = None):
    from advisor_api.models import (AgreementSeriesPage, AgreementSeriesPoint,
                                    GenerationRow)
    axis = "generation" if axis == "generation" else "window"
    fresh = _freshness("model_agreement")
    po, opts = resolve_pair(pair)
    head = dict(pair=po.key, a=po.a, b=po.b, pairs=opts)
    pts = _rows("SELECT * FROM agreement_series WHERE axis=%s AND pair=%s ORDER BY seq",
                (axis, po.key))
    scope = Scope(
        text=("median rank correlation of %s and %s per model version" % (po.a, po.b)
              if axis == "generation"
              else "median rank correlation of %s and %s over the run, newest last"
              % (po.a, po.b)),
        detail=("one window per retrain, from the training ledger's own start and flush "
                "times; sessions that start on an already-trained model fold in"
                if axis == "generation"
                else "equal-count buckets of decision id -- wall-clock buckets would put "
                     "three decisions beside three hundred, because a retrain takes the "
                     "game down for tens of minutes"))

    def point(r):
        n = _i(r["decisions"], 0) or 0
        return AgreementSeriesPoint(
            label=(r["trial"] or ("#%s" % r["from_decision"])), seq=_i(r["seq"], 0) or 0,
            decisions=Count(value=n, noun="decisions",
                            population="comparable, in this bucket"),
            from_decision=_i(r["from_decision"]), to_decision=_i(r["to_decision"]),
            from_ts=_f(r["from_ts"]),
            rho_median=_f(r["rho_median"]),
            same_top=Rate(n=0, of=n, noun="decisions",
                          population="comparable, in this bucket"),
            gate=r["gate"])

    gens = []
    for r in _rows("SELECT * FROM agreement_series WHERE axis='generation' AND pair=%s"
                   " ORDER BY seq", (po.key,)):
        n = _i(r["decisions"], 0) or 0
        gens.append(GenerationRow(
            trial=_phrase(r["trial"] or "unstamped"), generation=_i(r["generation"]),
            retrained=bool(r["retrained"]), from_ts=_f(r["from_ts"]),
            decisions=Count(value=n, noun="decisions",
                            population="comparable, inside this version's window"),
            rho_median=_f(r["rho_median"]),
            same_top=Rate(n=0, of=n, noun="decisions",
                          population="comparable, inside this version's window")))
    drawable = [p for p in pts if p["rho_median"] is not None]
    return AgreementSeriesPage(
        scope=scope, freshness=fresh, axis=axis, is_alignment=(axis == "generation"),
        **head,
        caveat=(_GENERATION_CAVEAT if axis == "generation" else None),
        bucket_decisions=(_i(pts[0]["bucket_size"]) if pts else None),
        ambiguous=Count(value=0, noun="decisions",
                        population="whose timestamp falls inside more than one training "
                                   "window, so which model version ranked them is "
                                   "ambiguous"),
        points=[point(r) for r in pts], generations=gens,
        empty_reason=(None if drawable else
                      ("no bucket has enough comparable decisions for a median to mean "
                       "anything yet")))


def decision_agreement(decision_id: int) -> list:
    from advisor_api.models import DecisionAgreement
    by_pair = {r["pair"]: r for r in _rows(
        "SELECT * FROM model_agreement WHERE decision_id=%s", (int(decision_id),))}
    out = []
    for key, a, b in ranked_pairs():
        r = by_pair.get(key)
        if not r:
            continue
        status = r["status"] or ""
        note = {
            "missing_a": "%s stored no ranking for this decision, so only %s ranked it"
                         % (a, b),
            "missing_b": "%s stored no ranking for this decision, so only %s ranked it"
                         % (b, a),
            "too_few": "the two arms ranked fewer than three of the same offers -- over "
                       "two, a rank correlation can only be +1 or -1",
            "no_scores": "no scores were stored for this decision",
        }.get(status)
        out.append(DecisionAgreement(
            pair=key, a=a, b=b,
            n=Count(value=_i(r["n"], 0) or 0, noun="offers",
                    population="on this decision that both arms ranked"),
            status=status, rho=_f(r["rho"]), tau_b=_f(r["tau"]), rbo=_f(r["rbo"]),
            top1_same=(bool(r["top1_agree"]) if r["top1_agree"] is not None else None),
            top3_overlap=None,
            note=note))
    return out


@db.timed
def correlations(con, version=None) -> list:
    windows = _version_windows(version)
    gains = {}
    for g in gains_all(con):
        ts = _f(g["first_ts"], 0.0) or 0.0
        if windows and not any(a <= ts < b for a, b in windows):
            continue
        gains[g["campaign_key"]] = (_f(g["settlements_gained"], 0.0) or 0.0,
                                    _f(g["levels_gained"], 0.0) or 0.0)
    tiles = []
    keys = _campaign_keys(con)
    for label, sql in (
            ("action ranker",
             "SELECT COALESCE(pp.key, '(unrecorded)') arm, t.campaign_id, COUNT(*) n"
             " FROM corpus.taken t"
             " LEFT JOIN dict.enum pp ON pp.enum_id = t.policy_id"
             " GROUP BY arm, t.campaign_id"),
            ("interrupt model",
             "SELECT COALESCE(pp.key, '(unrecorded)') arm, s.campaign_id, COUNT(*) n"
             " FROM corpus.interrupt i"
             " JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id"
             " LEFT JOIN dict.enum pp ON pp.enum_id = i.policy_id"
             " GROUP BY arm, s.campaign_id")):
        per: dict = {}
        totals: dict = {}
        for r in con.execute(sql):
            ckey = keys.get(r["campaign_id"])
            if ckey not in gains:
                continue
            arm = arms.arm_of(r["arm"]) or arms.UNRECORDED
            n = _i(r["n"], 0) or 0
            cells = per.setdefault(arm, {})
            cells[ckey] = cells.get(ckey, 0) + n
            totals[ckey] = totals.get(ckey, 0) + n
        camps = sorted(k for k, tot in totals.items() if tot)
        rewards = [gains[k][0] + gains[k][1] for k in camps]
        setts = [gains[k][0] for k in camps]
        lords = [gains[k][1] for k in camps]
        rows = []
        for arm, cells in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
            shares = [cells.get(k, 0) / totals[k] for k in camps]
            r_r, g_r = _pearson_gated(shares, rewards)
            r_s, g_s = _pearson_gated(shares, setts)
            r_l, g_l = _pearson_gated(shares, lords)
            picks = sum(cells.values())
            played = sum(1 for k in camps if cells.get(k))
            rows.append(CorrelationRow(
                arm=_phrase(arm), campaigns=len(camps),
                share=Rate(n=picks, of=sum(totals.values()) or 1, noun="picks",
                           population="on %s decisions across these campaigns" % label),
                per_campaign=round(picks / played, 1) if played else None,
                reward_r=r_r, reward_gate=g_r,
                settlements_r=r_s, settlements_gate=g_s, lord_r=r_l, lord_gate=g_l))
        tiles.append(CorrelationTile(label=label, rows=rows))
    return tiles


def _pearson_gated(xs, ys, min_n=12):
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
    return _training_history()


@db.timed
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
            rt = camp.get("retrain") or {}
            irt = camp.get("retrain_interrupt") or {}
            gnn = camp.get("retrain_gnn") or {}
            ggnn = camp.get("retrain_greedy_gnn") or {}
            if not (rt or irt or gnn or ggnn):
                continue
            gen += 1
            cbfit = (rt.get("fit") or {}).get("model") or {}
            gfit, ggfit = (gnn.get("fit") or {}), (ggnn.get("fit") or {})
            corpus_rows = _i(rt.get("rows") or gnn.get("rows") or ggnn.get("rows"))
            corpus_campaigns = _i(rt.get("campaigns") or gnn.get("campaigns")
                                  or ggnn.get("campaigns"))
            groups = {
                "corpus": _clean({
                    "rows": corpus_rows,
                    "campaigns": corpus_campaigns,
                    "decisions": _i(rt.get("n_decisions")),
                    "seconds": _f(rt.get("seconds")),
                }),
                "greedy_catboost": _clean({
                    "rmse": _f(cbfit.get("val_rmse")),
                    "R2": _f(cbfit.get("val_r2")),
                    "val rows": _i(cbfit.get("val_rows")),
                    "best iter": _i(cbfit.get("best_iteration")),
                    "in-sample MAE": _f(rt.get("mae_in_sample")),
                }),
                "greedy_catboost interrupt": _clean({
                    "rows": _i(irt.get("rows")),
                    "screens": (len(irt.get("screens")) if isinstance(irt.get("screens"), (list, dict))
                                else _i(irt.get("screens"))),
                }),
                "marwil_gnn": _clean({
                    "rows": _i(gnn.get("rows")),
                    "listwise NLL": _f(gfit.get("val_listwise_nll")),
                    "value MSE": _f(gfit.get("val_value_mse")),
                    "epochs": _i(gfit.get("epochs_run")),
                    "device": gfit.get("device"),
                    "stopped by": gfit.get("stopped_by"),
                    "seconds": _f(gnn.get("seconds")),
                }),
                "greedy_gnn": _clean({
                    "rows": _i(ggnn.get("rows")),
                    "reward MSE": _f(ggfit.get("val_mse")),
                    "R2": _f(ggfit.get("val_r2")),
                    "epochs": _i(ggfit.get("epochs_run")),
                    "device": ggfit.get("device"),
                    "stopped by": ggfit.get("stopped_by"),
                    "seconds": _f(ggnn.get("seconds")),
                }),
            }
            out.append(TrainingEvent(
                when=time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(_f(camp.get("started"), 0.0) or 0.0)),
                trial="%s-g%d" % (stamp, gen),
                corpus_rows=corpus_rows,
                corpus_campaigns=corpus_campaigns,
                groups={k: v for k, v in groups.items() if v}))
    out.reverse()
    return out


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


TRIAL_CORR_MIN_N = 2


@db.timed
def _campaign_arm_shares(con) -> dict:
    keys = _campaign_keys(con)
    per: dict = {}
    totals: dict = {}
    for r in con.execute(
            "SELECT COALESCE(pp.key, '(unrecorded)') arm, t.campaign_id, COUNT(*) n"
            " FROM corpus.taken t"
            " LEFT JOIN dict.enum pp ON pp.enum_id = t.policy_id"
            " GROUP BY arm, t.campaign_id"):
        arm = arms.arm_of(r["arm"]) or arms.UNRECORDED
        if arm in arms.NOT_A_DRAW:
            continue
        ckey = keys.get(r["campaign_id"])
        if not ckey:
            continue
        n = _i(r["n"], 0) or 0
        per.setdefault(ckey, {})[arm] = per.get(ckey, {}).get(arm, 0) + n
        totals[ckey] = totals.get(ckey, 0) + n
    return {c: {a: n / totals[c] for a, n in cells.items()}
            for c, cells in per.items() if totals.get(c)}


@db.timed
def _campaign_settlement_growth(con) -> dict:
    out = {}
    for ckey, row in _growth_all(con, _decs_all(con)).items():
        g = row.get("settlements_growth")
        if g is not None:
            out[ckey] = float(g)
    return out


@db.timed
def campaign_reward_series(con) -> list:
    if con is None:
        return []
    cids = {r["campaign_key"]: _i(r["campaign_id"], 0) or 0 for r in con.execute(
        "SELECT campaign_id, campaign_key FROM corpus.campaign")}
    rows = [g for g in gains_all(con)
            if (_i(g["turns_reached"], 0) or 0) > 1 and g["campaign_key"] in cids]
    rows.sort(key=lambda g: cids[g["campaign_key"]])
    out = []
    for g in rows:
        sett = _f(g["settlements_gained"], 0.0) or 0.0
        lord = _f(g["levels_gained"], 0.0) or 0.0
        vas = _f(g["vassals_gained"], 0.0) or 0.0
        ally = _f(g["allies_gained"], 0.0) or 0.0
        out.append(CampaignReward(
            seq=len(out) + 1, campaign_id=cids[g["campaign_key"]], faction=g["faction"],
            settlements=sett, lord_level=lord, vassals=vas, allies=ally,
            total=_weighted_reward(sett, lord, ally, vas),
            turns=Count(value=_i(g["turns_reached"], 0) or 0, noun="turns",
                        population="reached in this campaign")))
    return out


def _growth_corr(uuids, shares, growth) -> dict:
    pairs = [(shares[u], growth[u]) for u in (uuids or [])
             if u in shares and u in growth]
    ys = [g for _, g in pairs]
    over = Count(value=len(pairs), noun="campaigns",
                 population="in this trial with both a recorded pick share and a "
                            "measured growth span")
    out = {}
    for arm in arms.NAMES:
        xs = [s.get(arm, 0.0) for s, _ in pairs]
        r, gate = _pearson_gated(xs, ys, min_n=TRIAL_CORR_MIN_N)
        out[arm] = TrialCorr(r=r, gate=gate, over=over)
    return out


TRIAL_LIVE_WINDOW_S = 900.0


@db.timed
def trials(con=None):
    out, meta = _trials()
    now = time.time()
    live = set()
    claimed = [m for m in meta if m.get("running")]
    if claimed:
        newest = max(claimed, key=lambda m: float(m.get("ts") or 0))
        if now - float(newest.get("ts") or 0) <= TRIAL_LIVE_WINDOW_S:
            live = {str(newest.get("trial") or "")}
    shares = _campaign_arm_shares(con) if con is not None else {}
    growth = _campaign_settlement_growth(con) if con is not None else {}
    by_trial = {m.get("trial"): m for m in meta}
    for row in out:
        row.live = row.trial in live
        row.growth_corr = _growth_corr(
            (by_trial.get(row.trial) or {}).get("campaign_uuids"), shares, growth)
    return out


@db.timed
def _trials() -> tuple:
    mixes: dict = {}
    for r in _rows(
            "SELECT tp.trial, tp.scope, pp.key AS policy, tp.weight"
            " FROM ops.trial_policy tp"
            " JOIN dict.enum pp ON pp.enum_id = tp.policy_id"
            " ORDER BY tp.trial, tp.scope, pp.key"):
        mixes.setdefault(r["trial"], {}).setdefault(r["scope"], {})[
            r["policy"]] = _f(r["weight"])
    outcomes: dict = {}
    for r in _rows(
            "SELECT to2.trial, oo.key AS outcome, to2.n FROM ops.trial_outcome to2"
            " JOIN dict.enum oo ON oo.enum_id = to2.outcome_id"
            " ORDER BY to2.trial, oo.key"):
        outcomes.setdefault(r["trial"], {})[r["outcome"]] = _i(r["n"], 0) or 0
    uuids: dict = {}
    for r in _rows(
            "SELECT tc.trial, c.campaign_key FROM ops.trial_campaign tc"
            " JOIN corpus.campaign c USING (campaign_id) ORDER BY tc.trial"):
        uuids.setdefault(r["trial"], []).append(r["campaign_key"])
    out = []
    meta = []
    rows = _rows("SELECT * FROM ops.trial WHERE NOT archived ORDER BY ts, trial")
    for d in rows:
        trial = str(d["trial"] or "")
        mix = mixes.get(trial) or {}
        meta.append({"trial": trial, "ts": d["ts"], "running": d["running"],
                     "campaign_uuids": uuids.get(trial) or []})
        row = TrialRow(
            trial=trial,
            mix={arms.canonical(k): v for k, v in (mix.get("main") or {}).items()},
            interrupt_mix={arms.canonical(k): v for k, v in
                           (mix.get("interrupt") or mix.get("main") or {}).items()},
            ruleset=None,
            campaigns=_i(d["campaigns"]),
            corpus=_i(d["corpus_rows"]),
            settlements_per_campaign=_f(d["sett_mean"]),
            settlements_total=_f(d["sett_total"]),
            grew=(Rate(n=_i(d["sett_campaigns_gained"], 0) or 0,
                       of=_i(d["sett_campaigns_measured"], 0) or 0,
                       noun="campaigns",
                       population="in this trial with a measurable growth span")
                  if d["sett_campaigns_measured"] is not None else None),
            shrank=(Rate(n=_i(d["sett_campaigns_lost"], 0) or 0,
                         of=_i(d["sett_campaigns_measured"], 0) or 0,
                         noun="campaigns",
                         population="in this trial with a measurable growth span")
                    if d["sett_campaigns_measured"] is not None else None),
            growth_baseline=_text(d["baseline"]),
            lord_per_campaign=_f(d["ll_mean"]),
            reward_per_campaign=(round(_f(d["sett_mean"]) + _f(d["ll_mean"]), 3)
                                 if d["sett_mean"] is not None
                                 and d["ll_mean"] is not None else None),
            turns_per_campaign=_f(d["turns_per_campaign"]),
            seconds_per_campaign=_f(d["timing_s_per_campaign"]),
            seconds_per_turn=_f(d["timing_s_per_turn"]),
            notes=(", ".join("%s %s" % (k, v)
                             for k, v in (outcomes.get(trial) or {}).items()) or None))
        row.snapshots = _i(d["snapshots"], 1)
        out.append(row)
    out.reverse()
    return out[:200], meta


def reward_series(con, campaign_key: str):
    rows = con.execute(
        "SELECT o.turn, o.income, o.settlements, o.allies, o.vassals, o.power_rank"
        " FROM corpus.turn_open o JOIN corpus.campaign c USING (campaign_id)"
        " WHERE c.campaign_key = %s ORDER BY o.turn", (campaign_key,)).fetchall()
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


@db.timed
def diplomacy_tail(con, campaign_key: str | None = None) -> list:
    base = (
        "SELECT e.ts, e.turn, k.key AS kind, ch.key AS channel, f.key AS faction,"
        " e.success_chance, e.term_ids, e.speech, e.failed_at, e.accepted"
        " FROM corpus.diplomacy_event e"
        " JOIN dict.enum k ON k.enum_id = e.kind_id"
        " LEFT JOIN dict.enum ch ON ch.enum_id = e.channel_id"
        " LEFT JOIN dict.faction f ON f.id = e.faction_id")
    if campaign_key:
        rows = con.execute(
            base + " JOIN corpus.campaign c ON c.campaign_id = e.campaign_id"
            " WHERE c.campaign_key = %s ORDER BY e.event_id DESC LIMIT %s",
            (campaign_key, DIPLO_TAIL)).fetchall()
    else:
        rows = con.execute(
            base + " ORDER BY e.event_id DESC LIMIT %s", (DIPLO_TAIL,)).fetchall()
    term_keys = _term_keys()
    out = []
    for r in rows:
        terms = [term_keys.get(t) for t in (r["term_ids"] or [])]
        out.append(DiploEvent(
            turn=_i(r["turn"]), channel=_phrase(r["channel"] or r["kind"]),
            faction=(_fac(r["faction"]) if r["faction"] else None),
            terms=_text([t for t in terms if t] or r["speech"])))
    return out[:200]


_TERM_KEYS: dict = {}


def _term_keys() -> dict:
    if not _TERM_KEYS:
        for k, i in _enum("diplo_term").items():
            _TERM_KEYS[i] = k
    return _TERM_KEYS


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


LOG_CHUNK = 1 << 18
LOG_SCAN_CAP = 8 << 20
LOG_MAX_LINES = 2000


def log_files(limit=20) -> list:
    d = common.LOGS_ADVISOR
    try:
        names = [f for f in os.listdir(d)
                 if f.startswith("session_") and f.endswith(".log")]
    except OSError:
        return []
    names.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    return names[:limit]


def _log_stamp(line):
    s = line[:23]
    if len(s) >= 19 and s[4] == "-" and s[7] == "-" and s[10] == "T":
        return s
    return None


def _bisect_log(fh, size, stamp):
    lo, hi = 0, size
    while hi - lo > LOG_CHUNK:
        mid = (lo + hi) // 2
        fh.seek(mid)
        fh.readline()
        ts = None
        while ts is None:
            raw = fh.readline()
            if not raw:
                break
            ts = _log_stamp(raw.decode("utf-8", "replace"))
        if ts is None or ts >= stamp:
            hi = mid
        else:
            lo = mid
    return lo


def read_session_log(file=None, q=None, t0=None, t1=None, limit=500, cursor=None) -> dict:
    files = log_files()
    if file:
        if file not in files:
            raise ValueError("unknown log file %r -- pick one of the listed session logs"
                             % file)
        path = os.path.join(common.LOGS_ADVISOR, file)
    else:
        path = session_log_path()
        file = os.path.basename(path) if path else None
    if not path or not os.path.exists(path):
        return {"file": None, "files": files, "size": 0, "lines": [],
                "cursor": None, "scanned": 0}
    limit = max(1, min(int(limit or 500), LOG_MAX_LINES))
    ql = (q or "").strip().lower() or None
    t1x = (t1 + "~") if t1 else None
    size = os.path.getsize(path)
    out = []
    scanned = 0
    with open(path, "rb") as fh:
        end = size
        if t1x:
            end = min(end, _bisect_log(fh, size, t1x) + LOG_CHUNK)
            end = min(end, size)
        if cursor is not None:
            end = min(end, max(0, int(cursor)))
        start = 0
        if t0:
            start = max(0, _bisect_log(fh, size, t0) - LOG_CHUNK)
        off = end
        carry = b""
        hit_t0 = False
        while off > start and len(out) < limit and scanned <= LOG_SCAN_CAP:
            take = min(LOG_CHUNK, off - start)
            off -= take
            fh.seek(off)
            data = fh.read(take) + carry
            parts = data.split(b"\n")
            carry = parts[0]
            first = 1 if off > start else 0
            for raw in reversed(parts[first:]):
                if len(out) >= limit:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r")
                if not line.strip():
                    continue
                ts = _log_stamp(line)
                if ts:
                    if t1x and ts > t1x:
                        continue
                    if t0 and ts < t0:
                        hit_t0 = True
                        break
                if ql and ql not in line.lower():
                    continue
                out.append(line)
            if hit_t0:
                break
            scanned += take
    out.reverse()
    more = (off > start) and not hit_t0
    return {"file": file, "files": files, "size": size, "lines": out,
            "cursor": (off if more else None), "scanned": scanned}


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


def _start_leaders(con) -> dict:
    return {((r["m"] or ""), r["f"]): r["l"] for r in con.execute(
        "SELECT cm.key m, cf.key f, MAX(c.leader) l FROM corpus.campaign c"
        " JOIN dict.faction cf ON cf.id = c.faction_id"
        " LEFT JOIN dict.campaign_map cm ON cm.id = c.campaign_map_id"
        " WHERE c.leader IS NOT NULL GROUP BY cm.key, cf.key")}


PULSE = 50


def _camps_meta(con, outcomes) -> dict:
    out = {}
    now = time.time()
    for ckey, turns, t1 in con.execute(
            "SELECT c.campaign_key, c.turns, MAX(s.ts) t1 FROM corpus.campaign c"
            " JOIN corpus.snapshot s ON s.campaign_id = c.campaign_id"
            " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
            " GROUP BY c.campaign_key, c.turns"):
        pm = outcomes.get(ckey)
        outcome, state = None, "neutral"
        if pm:
            raw = str(pm.get("outcome") or "")
            verdict = str((pm.get("plausibility") or {}).get("verdict") or "")
            suspicious = ("harness_failure_likely" in verdict) or ("ambiguous" in verdict)
            outcome = _phrase(raw)
            state = "bad" if suspicious else _OUTCOME_STATE.get(raw, "neutral")
        elif now - (_f(t1) or 0.0) > LIVE_WINDOW_S:
            outcome = _phrase("no_ending_recorded")
            state = _OUTCOME_STATE["no_ending_recorded"]
        out[ckey] = (_i(turns), outcome, state)
    return out


@db.timed
def ucb_pick_series(con, gains=None, camp_rows=None, produced=None) -> list:
    leaders = _start_leaders(con)
    top: dict = {}
    for r in con.execute("SELECT pick_id, rank, score FROM corpus.ucb_pick_row"
                         " WHERE rank <= 2"):
        top.setdefault(_i(r["pick_id"], 0), {})[_i(r["rank"], 0)] = _f(r["score"])
    ns = {_i(r["pick_id"], 0): [_i(n, 0) or 0 for n in r["nl"]] for r in con.execute(
        "SELECT pick_id, ARRAY_AGG(n) nl FROM corpus.ucb_pick_row GROUP BY pick_id")}
    if produced is None:
        produced = pick_campaigns(con)
    if gains is None:
        gains = gains_all(con)
    if camp_rows is not None:
        meta = {r.campaign.raw: (r.turns, r.outcome, r.outcome_state) for r in camp_rows}
    else:
        meta = _camps_meta(con, outcome_join(con)[0])
    gains = {g["campaign_key"]: g for g in gains}
    rows = [dict(r) for r in con.execute(
        "SELECT p.pick_id, p.ts, p.c, p.total_plays, cm.key AS campaign_map,"
        " cf.key AS faction, p.n, p.mean, p.explore, p.score, p.tied, p.blend,"
        " p.entropy, p.std, p.adjust FROM corpus.ucb_pick p"
        " JOIN dict.faction cf ON cf.id = p.faction_id"
        " LEFT JOIN dict.campaign_map cm ON cm.id = p.campaign_map_id"
        " ORDER BY p.pick_id")]
    keys = [(r["campaign_map"], r["faction"]) for r in rows]
    seen: set = set()
    out = []
    for i, r in enumerate(rows):
        pid = _i(r["pick_id"], 0)
        key = keys[i]
        seen.add(key)
        lo = max(0, i - PULSE + 1)
        win = keys[lo:i + 1]
        reps = [1 if j > 0 and keys[j] == keys[j - 1] else 0 for j in range(max(1, lo), i + 1)]
        t = top.get(pid) or {}
        s1, s2 = t.get(1), t.get(2)
        score, explore = _f(r["score"]), _f(r["explore"])
        blend = _f(r.get("blend"))
        if blend is None and score is not None and explore is not None:
            blend = round(score - explore - (_f(r.get("adjust")) or 0.0), 4)
        n_list = ns.get(pid) or []
        prod = None
        ck = produced.get(pid)
        if ck:
            g = gains.get(ck) or {}
            cm = meta.get(ck)
            prod = ProducedCampaign(
                campaign=_camp(ck),
                reward=(round((_f(g.get("settlements_gained"), 0.0) or 0.0)
                              + (_f(g.get("levels_gained"), 0.0) or 0.0), 3)
                        if g else None),
                turns=(cm[0] if cm else _i(g.get("turns_reached"))),
                outcome=cm[1] if cm else None,
                outcome_state=cm[2] if cm else "neutral")
        out.append(UcbPick(
            leader=leaders.get(((r["campaign_map"] or ""), r["faction"])),
            pick_id=pid, ts=_f(r["ts"]), c=_f(r["c"]),
            total_plays=_i(r["total_plays"], 0), faction=_fac(r["faction"]),
            campaign_map=(_id(ident.campaign_map(r["campaign_map"]))
                          if r["campaign_map"] else None),
            n=_i(r["n"], 0), mean=_f(r["mean"]), blend=blend,
            entropy=_f(r.get("entropy")), std=_f(r.get("std")),
            explore=explore, score=score, adjust=_f(r.get("adjust")),
            margin=(round(s1 - s2, 4) if s1 is not None and s2 is not None else None),
            tied=_i(r["tied"], 0), starts=len(n_list),
            repeat=bool(i > 0 and keys[i - 1] == key), produced=prod,
            distinct_50=len(set(win)),
            repeat_50=(round(sum(reps) / len(reps), 3) if reps else None),
            cum_distinct=len(seen),
            gini=(round(UCB.gini(n_list), 3) if n_list else None),
            under_min=sum(1 for n_ in n_list if n_ < UCB.MIN_PLAYS)))
    return out


WINDOW_EDGE = 10


@db.timed
def ucb_window_edges(con, gains) -> tuple:
    if len(gains) <= UCB.WINDOW:
        return [], []
    leaders = _start_leaders(con)
    window = gains[:UCB.WINDOW]
    counts: dict = {}
    for g in window:
        k = (g["campaign_map"], g["faction"])
        counts[k] = counts.get(k, 0) + 1

    def row(g, away):
        return WindowEdgeRow(
            campaign=_camp(g["campaign_key"]),
            leader=leaders.get(((g["campaign_map"] or ""), g["faction"])),
            faction=_fac(g["faction"]),
            campaign_map=(_id(ident.campaign_map(g["campaign_map"]))
                          if g["campaign_map"] else None),
            played_ts=_f(g["first_ts"]), turns=_i(g.get("turns_reached")),
            reward=round((_f(g.get("settlements_gained"), 0.0) or 0.0)
                         + (_f(g.get("levels_gained"), 0.0) or 0.0), 3),
            start_n=counts.get((g["campaign_map"], g["faction"]), 0),
            campaigns_away=away)

    dropped = [row(g, i + 1) for i, g in
               enumerate(gains[UCB.WINDOW:UCB.WINDOW + WINDOW_EDGE])]
    nxt = [row(g, i + 1) for i, g in
           enumerate(reversed(window[-WINDOW_EDGE:]))]
    return dropped, nxt


def ucb_picks(series, limit: int = 200, before: int | None = None) -> list:
    desc = list(reversed(series))
    if before is not None:
        desc = [p for p in desc if p.pick_id < int(before)]
    return desc[:int(limit)]


@db.timed
def ucb_tiles(series, cx) -> list:
    if not series:
        return []
    last = series[-1]
    chosen = {(p.campaign_map.raw if p.campaign_map else "", p.faction.raw) for p in series}
    never = sum(1 for k in cx["pool"] if k not in chosen)
    tied = sum(1 for p in series if p.tied > 1)
    reps = sum(1 for p in series if p.repeat)
    margins = [p.margin for p in series[-PULSE:] if p.margin is not None]
    realized = [p.produced.reward for p in series if p.produced and p.produced.reward is not None]
    expected = [p.mean for p in series if p.produced and p.produced.reward is not None
                and p.mean is not None]
    return [
        Metric(label="picks", value=len(series),
               sub="%d produced a campaign" % sum(1 for p in series if p.produced)),
        Metric(label="C now", value=last.c, sub="%d plays in window" % last.total_plays),
        Metric(label="starts chosen", value=len(chosen), unit="/ %d" % len(cx["pool"]),
               sub="%d never chosen" % never, state="warn" if never else "neutral"),
        Metric(label="repeat of previous", value=round(100.0 * reps / len(series)), unit="%",
               sub="%d picks" % reps),
        Metric(label="tied at the top", value=round(100.0 * tied / len(series)), unit="%",
               sub="%d picks drawn by lot" % tied),
        Metric(label="margin to #2", value=(round(statistics.median(margins), 3)
                                            if margins else None),
               sub="median over the last %d picks" % PULSE),
        Metric(label="realised − expected",
               value=(round(sum(realized) / len(realized) - sum(expected) / len(expected), 2)
                      if realized and expected else None),
               sub="%d joined picks" % len(realized)),
    ]


@db.timed
def ucb_pick_rows(con, pick_id: int) -> tuple:
    head = next((p for p in ucb_pick_series(con) if p.pick_id == int(pick_id)), None)
    if head is None:
        return None, [], 0
    leaders = _start_leaders(con)
    raw = [dict(r) for r in con.execute(
        "SELECT p.rank, cm.key AS campaign_map, cf.key AS faction, p.n, p.mean,"
        " p.explore, p.score, p.chosen, p.blend, p.entropy, p.std, p.adjust"
        " FROM corpus.ucb_pick_row p"
        " JOIN dict.faction cf ON cf.id = p.faction_id"
        " LEFT JOIN dict.campaign_map cm ON cm.id = p.campaign_map_id"
        " WHERE p.pick_id = %s ORDER BY p.rank", (int(pick_id),))]
    top_score = next((_f(r["score"]) for r in raw if _f(r["score"]) is not None), None)
    rows = []
    for r in raw:
        score, explore = _f(r["score"]), _f(r["explore"])
        blend = _f(r.get("blend"))
        if blend is None and score is not None and explore is not None:
            blend = round(score - explore - (_f(r.get("adjust")) or 0.0), 4)
        rows.append(UcbRow(
            leader=leaders.get(((r["campaign_map"] or ""), r["faction"])),
            rank=_i(r["rank"], 0), faction=_fac(r["faction"]),
            campaign_map=(_id(ident.campaign_map(r["campaign_map"]))
                          if r["campaign_map"] else None),
            n=_i(r["n"], 0), mean=_f(r["mean"]), entropy=_f(r.get("entropy")),
            std=_f(r.get("std")),
            blend=blend, explore=explore, score=score, adjust=_f(r.get("adjust")),
            delta=(round(score - top_score, 4) if score is not None
                   and top_score is not None else None),
            chosen=bool(r["chosen"])))
    under = sum(1 for r in rows if r.n < UCB.MIN_PLAYS)
    return head, rows, under
