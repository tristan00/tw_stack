
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
import run_config
import ucb_stats as UCB
from advisor_api import analytics_db as adb
from advisor_api import db, ident
from advisor_api.models import (
    ActionTypeRow, ActivityRow, AgreementPage, AgreementRankRow, AgreementSummary,
    ArmCoverage, CampaignRow, Count, CorrelationRow, CorrelationTile, Current,
    CampaignReward, DecisionRow, DiploEvent, EntityState, ForcingBar, ForcingTile, Ident,
    InterruptOption, InterruptRow, Metric, ModelCard, OfferRow, OutcomeTally, PairOption,
    PhaseSpan, PolicyRow, Rate, RewardPoint, Scope, Service, StartRow, TimelineAction,
    TimelineLane, TimingRow, TrainingEvent, TrialCorr, TrialRow, UcbPick, UcbRow,
    HistBin, MatrixCell, ProducedCampaign, StartCampaign, StartPickPoint,
)
from decisions import store_schema as SS

DECISIONS_PAGE = 50
TIMELINE_DECISIONS = 200
MENUS_ROWS = 60
DIPLO_TAIL = 600
REWARD_CAMPAIGNS = 10

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


@db.cached
def postmortems(con) -> list:
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
    claimed = {}
    for pm in postmortems(con):
        key = pm.get("campaign_key")
        if key:
            claimed[key] = pm
    return claimed


@db.cached
def unjoined_endings(con) -> int:
    keys = {r[0] for r in con.execute("SELECT campaign_key FROM campaigns")}
    return sum(1 for pm in postmortems(con)
               if not pm.get("campaign_key") or pm["campaign_key"] not in keys)


@db.cached
def current(con) -> Current:
    row = con.execute(
        "SELECT campaign_id, turn, settlements, power_rank, lord_level, ts"
        " FROM turn_close ORDER BY decision_id DESC LIMIT 1").fetchone()
    if not row:
        return Current()
    leader = con.execute("SELECT leader FROM campaigns WHERE campaign_key=?",
                         (row["campaign_id"],)).fetchone()
    stored = con.execute("SELECT SUM(n) FROM start_counts").fetchone()
    return Current(campaign=_camp(row["campaign_id"]), turn=_i(row["turn"]),
                   leader=leader["leader"] if leader else None,
                   settlements=_f(row["settlements"]), power_rank=_f(row["power_rank"]),
                   lord_level=_f(row["lord_level"]),
                   stored_campaigns=_i(stored[0]) if stored else None,
                   age_seconds=max(0.0, time.time() - (_f(row["ts"]) or 0.0)))


@db.cached
def totals(con) -> list:
    q = lambda s: con.execute(s).fetchone()[0] or 0
    return [
        Count(value=q("SELECT COUNT(*) FROM (SELECT campaign_id FROM decisions"
                      " GROUP BY campaign_id HAVING COUNT(*) >= 2)"),
              noun="campaigns", population="with two or more decisions in this run dir"),
        Count(value=q("SELECT COUNT(*) FROM decision_points"),
              noun="decisions", population="recorded in this run dir"),
        Count(value=q("SELECT COUNT(*) FROM action_offers"),
              noun="offers", population="scored across those decisions"),
        Count(value=q("SELECT COUNT(*) FROM action_taken WHERE counted=1"),
              noun="actions", population="confirmed by the game"),
    ]


@db.cached
def throughput(con) -> list:
    import time
    now = time.time()
    since = now - 3600.0
    rows = con.execute(
        "SELECT ts, campaign_id, turn FROM decision_points"
        " WHERE ts >= ? ORDER BY decision_id DESC", (since,)).fetchall()
    out = []
    if not rows:
        return out
    span_h = max(1e-6, (now - (_f(rows[-1]["ts"]) or now)) / 3600.0)
    camps = len({r["campaign_id"] for r in rows})
    turns = len({(r["campaign_id"], r["turn"]) for r in rows})
    taken = con.execute(
        "SELECT COUNT(*) a,"
        " SUM(CASE WHEN counted=1 THEN 1 ELSE 0 END) c"
        " FROM action_taken WHERE refusal IS NOT 'awaiting_execution' AND ts >= ?",
        (since,)).fetchone()
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


@db.cached
def _confirm_spark(con, buckets=SPARK_BUCKETS):
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


@db.cached
def campaign_rows(con) -> list:
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
        "SELECT campaign_id, campaign_key, faction, turns, campaign_map, presave_radius, "
        "leader FROM campaigns")}

    outcomes = join_outcomes(con)
    produced_by = {v: k for k, v in pick_campaigns(con).items()}
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
            growth_state=g.get("growth_state") or CG.NO_TURN_ROWS,
        )
        sg, lg = _f(g.get("settlements_growth")), _f(g.get("lord_growth"))
        if sg is not None and lg is not None:
            row.settlements_gained, row.levels_gained = sg, lg
            row.reward = round(sg + lg, 3)
        row.pick_id = produced_by.get(ckey)
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


@db.cached
def outcome_headline(con) -> list:
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


PICK_JOIN_S = 120.0


@db.cached_per_campaign
def gains_all(con) -> list:
    return [dict(r) for r in con.execute(
        "SELECT campaign_map, faction, campaign_key, first_ts, turns_reached,"
        " settlements_gained, levels_gained,"
        " settlements_gained + levels_gained AS reward"
        " FROM campaign_gains ORDER BY first_ts DESC")]


def _pool() -> dict:
    try:
        import presaves as PS
        radius = run_config.RUN.get("presave_radius")
        return {(p["campaign_map"], p["faction"]): p
                for p in PS.list_presaves(radius=radius)}
    except Exception:
        return {}


@db.cached
def pick_campaigns(con) -> dict:
    by_key: dict = {}
    for r in con.execute(
            "SELECT picked_ts, campaign_map, faction, campaign_key FROM campaigns"
            " WHERE picked_ts IS NOT NULL ORDER BY picked_ts"):
        by_key.setdefault((r["campaign_map"], r["faction"]), []).append(
            (_f(r["picked_ts"]) or 0.0, r["campaign_key"]))
    used, out = set(), {}
    for r in con.execute("SELECT pick_id, ts, campaign_map, faction FROM ucb_picks"
                         " ORDER BY pick_id"):
        ts = _f(r["ts"]) or 0.0
        for cts, key in by_key.get((r["campaign_map"], r["faction"]), []):
            if key in used:
                continue
            dt = cts - ts
            if dt > PICK_JOIN_S:
                break
            if dt >= -1.0:
                used.add(key)
                out[_i(r["pick_id"], 0)] = key
                break
    return out


@db.cached
def ucb_context(con) -> dict:
    rewards: dict = {}
    for g in gains_all(con)[:UCB.WINDOW]:
        rewards.setdefault((g["campaign_map"], g["faction"]), []).append(
            float(g["reward"] or 0.0))
    stats = UCB.start_stats(rewards)
    z = UCB.zscores(stats)
    total = max(1, sum(d["n"] for d in stats.values()))
    last = con.execute("SELECT c FROM ucb_picks ORDER BY pick_id DESC LIMIT 1").fetchone()
    c = _f(last["c"]) if last else _f(run_config.RUN.get("ucb"))
    pool = _pool()
    scored = {}
    for key in set(pool) | set(stats):
        d = stats.get(key) or dict(UCB.EMPTY)
        played = d["n"] >= UCB.MIN_PLAYS
        if c is None:
            b, e, s = (UCB.blend(d, z) if played else None), None, None
        else:
            b, e, s = UCB.score(d, z, c, total)
        scored[key] = {"d": d, "z": UCB.zparts(d, z) if played else None,
                       "blend": b if played else None, "explore": e, "score": s}
    order = sorted(pool, key=lambda k: (
        -(scored[k]["score"] if scored[k]["score"] is not None else float("-inf")),
        pool[k].get("file") or ""))
    rank = {k: i + 1 for i, k in enumerate(order)}
    picks = [((r["campaign_map"], r["faction"])) for r in con.execute(
        "SELECT campaign_map, faction FROM ucb_picks ORDER BY pick_id")]
    pick_count, last_pick = {}, {}
    for i, key in enumerate(picks):
        pick_count[key] = pick_count.get(key, 0) + 1
        last_pick[key] = i
    plays_ago = {}
    for i, g in enumerate(gains_all(con)):
        plays_ago.setdefault((g["campaign_map"], g["faction"]), i)
    top = max([int(round(max(v))) for v in rewards.values()] + [0])
    return {"rewards": rewards, "stats": stats, "z": z, "total": total, "c": c,
            "pool": pool, "scored": scored, "rank": rank, "n_picks": len(picks),
            "pick_count": pick_count, "last_pick": last_pick, "plays_ago": plays_ago,
            "top_reward": top}


def _bins(vals, top) -> list:
    out = [0] * (top + 1)
    for v in vals:
        k = min(top, max(0, int(round(v))))
        out[k] += 1
    return out


@db.cached
def starts_rows(con) -> list:
    per = {}
    for row in campaign_rows(con):
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
        "SELECT campaign_map, faction, n FROM start_counts")}
    leaders = _start_leaders(con)
    cx = ucb_context(con)
    window = gains_all(con)[:UCB.WINDOW]
    win_rows: dict = {}
    for g in window:
        win_rows.setdefault((g["campaign_map"], g["faction"]), []).append(g)
    best = {}
    for g in gains_all(con):
        k = (g["campaign_map"], g["faction"])
        best[k] = max(best.get(k, 0.0), _f(g["reward"], 0.0) or 0.0)
    out = []
    for key in set(per) | set(cx["pool"]) | set(cx["stats"]):
        mkey, fkey = key
        b = per.get(key) or {"n": 0, "turns": [], "span_min": 0.0, "att": 0, "conf": 0}
        sc = cx["scored"].get(key) or {"d": dict(UCB.EMPTY), "z": None, "blend": None,
                                       "explore": None, "score": None}
        d = sc["d"]
        rewards = cx["rewards"].get(key) or []
        wr = win_rows.get(key) or []
        fin = lambda v: None if v is None or v == float("inf") else round(float(v), 4)
        z = sc["z"] or {}
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
            z_mean=fin(z.get("mean")), z_entropy=fin(z.get("entropy")),
            z_std=fin(z.get("std")),
            blend=fin(sc["blend"]), explore=fin(sc["explore"]), score=fin(sc["score"]),
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
            confirm_rate=Rate(n=b["conf"], of=b["att"], noun="actions",
                              population="attempted across this start's campaigns")))
    out.sort(key=lambda r: (r.rank if r.rank is not None else 10 ** 6, -r.n,
                            r.faction.label))
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


@db.cached
def starts_page_extras(con) -> dict:
    cx = ucb_context(con)
    window = gains_all(con)[:UCB.WINDOW]
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
               sub="of %d campaigns all time" % len(gains_all(con))),
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
    ]
    return {"tiles": tiles, "maps": [_id(ident.campaign_map(m)) for m in maps if m],
            "reward_bins": _hist(window, "reward", cx["top_reward"]),
            "turns_bins": _hist(window, "turns_reached")}


@db.cached
def ucb_pick_counts(con) -> dict:
    return {_i(r["pick_id"], 0): _i(r["n"], 0) for r in con.execute(
        "SELECT pick_id, COUNT(*) n FROM ucb_pick_rows GROUP BY pick_id")}


def start_detail(con, mkey: str, fkey: str):
    row = next((r for r in starts_rows(con)
                if (r.campaign_map.raw if r.campaign_map else "") == mkey
                and r.faction.raw == fkey), None)
    if row is None:
        return None
    by_camp = {r.campaign.raw: r for r in campaign_rows(con)}
    produced_by = {v: k for k, v in pick_campaigns(con).items()}
    camps = []
    for i, g in enumerate(gains_all(con)):
        if (g["campaign_map"] or "") != mkey or g["faction"] != fkey:
            continue
        cr = by_camp.get(g["campaign_key"])
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
            pick_id=produced_by.get(g["campaign_key"]),
            in_window=i < UCB.WINDOW))
    counts = ucb_pick_counts(con)
    traj = []
    blend_col = "r.blend" if "blend" in db.columns(con, "ucb_pick_rows") else "NULL"
    for r in con.execute(
            "SELECT r.pick_id pick_id, p.ts ts, p.c c, r.rank rank, r.n n, r.mean mean,"
            " %s blend, r.explore explore, r.score score, r.chosen chosen"
            " FROM ucb_pick_rows r JOIN ucb_picks p ON p.pick_id = r.pick_id"
            " WHERE r.campaign_map = ? AND r.faction = ? ORDER BY r.pick_id" % blend_col,
            (mkey, fkey)):
        score, explore = _f(r["score"]), _f(r["explore"])
        blend = _f(r["blend"])
        if blend is None and score is not None and explore is not None:
            blend = round(score - explore, 4)
        traj.append(StartPickPoint(
            pick_id=_i(r["pick_id"], 0), ts=_f(r["ts"]), c=_f(r["c"]),
            rank=_i(r["rank"], 0), ranked=counts.get(_i(r["pick_id"], 0), 0),
            n=_i(r["n"], 0), mean=_f(r["mean"]), blend=blend, explore=explore,
            score=score, chosen=bool(r["chosen"])))
    cx = ucb_context(con)
    pop = [0] * (cx["top_reward"] + 1)
    for vals in cx["rewards"].values():
        for i, c in enumerate(_bins(vals, cx["top_reward"])):
            pop[i] += c
    grid, _ = matrix(con, "action")
    cells = []
    for atype, (tried, ok, ms) in sorted((grid.get(fkey) or {}).items()):
        rate = Rate(n=ok, of=tried, noun="actions",
                    population="attempted of this type by this faction")
        cells.append(MatrixCell(action_type=_phrase(atype), rate=rate,
                                total_ms=round(ms, 0) or None,
                                per_try_ms=round(ms / tried, 0) if tried else None))
    cells.sort(key=lambda c: (c.rate.pct if c.rate.pct is not None else 999, -c.rate.of))
    return row, camps, traj, pop, cells


@db.cached
def matrix(con, kind: str = "action"):
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


def _options_of(options_json) -> list:
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


def ranked_pairs() -> list:
    return [(SS.pair_key(a, b), a, b) for a, b in SS.PAIRS]


def pair_options() -> list:
    comparable = {}
    try:
        for r in adb.rows("SELECT pair, comparable FROM agreement_summary WHERE scope='all'"):
            comparable[r["pair"]] = _i(r["comparable"], 0) or 0
    except Exception:
        comparable = {}
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


def _ggnn_cols(con) -> str:
    cols = {r[1] for r in con.execute("PRAGMA table_info(action_offers)")}
    if "ggnn_score" in cols:
        return "ggnn_score, ggnn_rank"
    return "NULL AS ggnn_score, NULL AS ggnn_rank"


def decisions_page(con, offset=0, limit=DECISIONS_PAGE, action_type=None, policy=None,
                   result=None, campaign=None, q=None):
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
                "SELECT offer_id, exploit, pct_global, pct_local, rank, gnn_impact, gnn_rank,"
                "       %s FROM action_offers WHERE offer_id IN (%s)"
                % (_ggnn_cols(con), marks), ids):
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
            result=_phrase(res), result_state=state, refusal=_phrase(r["refusal"]),
            policy=_phrase(r["policy"]),
            exploit=_f(o["exploit"]) if o else None,
            pct_global=_f(o["pct_global"]) if o else None,
            pct_local=_f(o["pct_local"]) if o else None,
            cat_rank=cat_rank, gnn_impact=_f(o["gnn_impact"]) if o else None,
            gnn_rank=gnn_rank, ggnn_score=_f(o["ggnn_score"]) if o else None,
            ggnn_rank=ggnn_rank, latency_ms=_f(r["latency_ms"])))
    return out, total


@db.cached
def decision_facets(con) -> dict:
    at = [r[0] for r in con.execute(
        "SELECT DISTINCT action_type FROM action_taken WHERE action_type IS NOT NULL"
        " ORDER BY action_type")]
    po = sorted({arms.arm_of(r[0]) for r in con.execute(
        "SELECT DISTINCT policy FROM action_taken WHERE policy IS NOT NULL")} - {None})
    return {"action_types": [_phrase(a) for a in at],
            "policies": [_phrase(p) for p in po],
            "results": [_phrase(x) for x in ("confirmed", "refused", "awaiting")]}


def decision_detail(con, decision_id: int):
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
            "       pct_global, pct_local, rank, gnn_impact, gnn_rank, %s"
            " FROM action_offers WHERE decision_id = ?"
            " ORDER BY COALESCE(rank, 9999), offer_id" % _ggnn_cols(con), (decision_id,)):
        offers.append(OfferRow(
            rank=_i(o["rank"]), entity="%s %s" % (o["context_kind"] or "", o["context_id"] or ""),
            action_type=_phrase(o["action_type"]), action_key=o["action_key"],
            exploit=_f(o["exploit"]), pct_global=_f(o["pct_global"]),
            pct_local=_f(o["pct_local"]), gnn_impact=_f(o["gnn_impact"]),
            gnn_rank=_i(o["gnn_rank"]), ggnn_score=_f(o["ggnn_score"]),
            ggnn_rank=_i(o["ggnn_rank"]), taken=(o["offer_id"] == taken_offer)))

    ents = []
    for e in con.execute(
            "SELECT context_kind, context_id, features FROM entity_snapshots"
            " WHERE decision_id = ? LIMIT 40", (decision_id,)):
        ents.append(EntityState(context_kind=e["context_kind"] or "",
                                context_id=str(e["context_id"] or ""),
                                features=_jload(e["features"])))
    return head, offers, ents, _phases(row)


def _phases(row) -> list:
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


_INTERRUPT_SCORERS = (("greedy_catboost", "exploit"), ("marwil_gnn", "gnn"))


@db.cached
def menus(con):
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

    for r in con.execute("SELECT kind, options_json FROM interrupt_decisions"):
        b = cover.setdefault(r["kind"], {"rows": 0, "scored": {}, "agree": 0, "cmp": 0})
        b["rows"] += 1
        opts = _options_of(r["options_json"])
        bests = {}
        for arm, key in _INTERRUPT_SCORERS:
            got = [(k, o) for k, o in opts if o.get(key) is not None]
            if got:
                b["scored"][arm] = b["scored"].get(arm, 0) + 1
                bests[arm] = max(got, key=lambda kv: _f(kv[1].get(key), -1e9))[0]
        if len(bests) >= 2:
            b["cmp"] += 1
            if len(set(bests.values())) == 1:
                b["agree"] += 1
    coverage = [ArmCoverage(
        screen=_phrase(k), rows=v["rows"], scored=v["scored"], compared=v["cmp"],
        agree=Rate(n=v["agree"], of=v["cmp"], noun="screens",
                   population="scored by two or more arms")) for k, v in sorted(cover.items())]
    coverage.sort(key=lambda c: -c.rows)

    return (Count(value=total, noun="decisions", population="on blocking menus in this run dir"),
            by_screen, policies, coverage, rows)


@db.cached
def timeline(con) -> list:
    rows = con.execute(
        "SELECT at.decision_id, at.ts, at.action_type, at.action_key, at.executed,"
        "       at.confirmed, at.counted, at.refusal, at.timing,"
        "       dp.campaign_id, dp.turn, dp.timings"
        " FROM action_taken at JOIN decision_points dp ON dp.decision_id = at.decision_id"
        " ORDER BY at.decision_id DESC LIMIT ?", (TIMELINE_DECISIONS,)).fetchall()
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
    ("greedy_catboost global", common.MODEL_GLOBAL, "catboost",
     "The advantage model the greedy_catboost arm ranks on: e1 predicts the return with "
     "the action, e2 the same state without it, and e1 - e2 is the advantage. Ranks every "
     "offered action across the whole faction."),
    ("greedy_catboost local", common.MODEL_LOCAL, "catboost",
     "The same advantage within one entity's own option set, so a lord's choices compete "
     "against each other rather than against the whole map. Blended into the global rank."),
    ("greedy_catboost interrupt", common.MODEL_INTERRUPT, "catboost",
     "The advantage model for blocking screens -- battles, dilemmas, occupation choices. "
     "The only interrupt model: the interrupt mix draws from greedy_catboost, random and "
     "ruleset."),
    ("marwil_gnn", common.MODEL_MAPGRAPH, "mapgraph",
     "MARWIL/AWR over the graph encoder: the map and its entities as a graph, the action a "
     "node in it, trained by exponentially advantage-weighted imitation of the logged "
     "action rather than by maximising the advantage."),
    ("greedy_gnn", common.MODEL_MAPGRAPH_GREEDY, "mapgraph",
     "The same graph encoder with a reward head: one regression of the return per action "
     "node, fit by MSE on the action that was taken. No state-only model, no advantage, "
     "no value head -- the arm takes the action with the highest predicted return."),
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
            want = ("model.pt",) if family == "mapgraph" else ("e1.cbm",)
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
                for k, label, fmt in (("val_listwise_nll", "held-out listwise NLL", "%.4f"),
                                      ("val_value_mse", "held-out value MSE (z)", "%.4f"),
                                      ("val_mse", "held-out reward MSE (z)", "%.4f"),
                                      ("val_r2", "held-out R²", "%+.3f")):
                    if fit.get(k) is not None:
                        rows.append((label, fmt % (_f(fit[k]) or 0)))
                if fit.get("val_rows") is not None:
                    rows.append(("held-out rows", "{:,}".format(_i(fit["val_rows"], 0) or 0)))
                for k, label in (("epochs_run", "epochs"), ("stopped_by", "stopped by"),
                                 ("device", "trained on")):
                    if fit.get(k) is not None:
                        rows.append((label, str(fit[k])))
                rows.append(("graph schema", "v%s %s" % (meta.get("schema_version"),
                                                         str(meta.get("schema_hash"))[:8])))
            else:
                cfit = meta.get("fit") or {}
                for tag, label in (("e1", "held-out RMSE (state+action)"),
                                   ("local_e1", "held-out RMSE (state+action)"),
                                   ("e2", "held-out RMSE (state only)"),
                                   ("local_e2", "held-out RMSE (state only)")):
                    part = cfit.get(tag) or {}
                    if part.get("val_rmse") is not None:
                        rows.append((label, "%.4f over %s val rows"
                                     % (_f(part["val_rmse"]) or 0, part.get("val_rows"))))
                if meta.get("mae_in_sample") is not None:
                    rows.append(("in-sample MAE", "%.4f" % (_f(meta["mae_in_sample"]) or 0)))
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


@db.cached_files(os.path.join(common.native(common.MODEL_MAPGRAPH), "meta.json"),
                 os.path.join(common.native(common.MODEL_MAPGRAPH_GREEDY), "meta.json"),
                 os.path.join(common.native(common.MODEL_GLOBAL), "meta.json"))
def _fit_config() -> list:
    from advisor_api.models import FitConfigRow
    out = []
    for arm, path, role in (
            ("marwil_gnn", common.MODEL_MAPGRAPH,
             "advantage-weighted imitation over the graph encoder"),
            ("greedy_gnn", common.MODEL_MAPGRAPH_GREEDY,
             "one reward regression per action node over the same graph encoder")):
        g = _meta_json(path)
        cfg = g.get("cfg") or {}
        out.append(FitConfigRow(
            family=arm, role=role,
            hyperparameters={k: cfg[k] for k in sorted(cfg)},
            compute={"device": (g.get("fit") or {}).get("device", "auto"),
                     "rows": g.get("rows"), "schema": g.get("schema_version")}))
    c = _meta_json(common.MODEL_GLOBAL)
    out.append(FitConfigRow(
        family="greedy_catboost", role="E1 - E2 advantage ranker, global blended with local",
        hyperparameters={k: c[k] for k in ("short_horizon", "short_weight", "w_local",
                                           "exp_lo", "exp_hi", "target") if k in c},
        compute={"rows": c.get("rows"),
                 "features": len(c.get("num") or []) + len(c.get("cat") or [])}))
    return out


@db.cached
def forcing(con):
    rows = con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, action_type, COUNT(*) n"
        " FROM action_taken WHERE action_type IS NOT NULL GROUP BY p, action_type").fetchall()
    by_arm: dict = {}
    for r in rows:
        arm = arms.arm_of(r["p"]) or arms.UNRECORDED
        mix = by_arm.setdefault(arm, {})
        mix[r["action_type"]] = mix.get(r["action_type"], 0) + (_i(r["n"], 0) or 0)
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


def _excluded_counts(s: dict, a: str, b: str) -> list:
    return [
        Count(value=_i(s.get("missing_a"), 0) or 0, noun="decisions",
              population="where %s stored no ranking, so only %s ranked them" % (a, b)),
        Count(value=_i(s.get("missing_b"), 0) or 0, noun="decisions",
              population="where %s stored no ranking, so only %s ranked them" % (b, a)),
        Count(value=_i(s.get("too_few"), 0) or 0, noun="decisions",
              population="where the two arms ranked fewer than three of the same offers"),
        Count(value=_i(s.get("no_scores"), 0) or 0, noun="decisions",
              population="carrying no stored scores at all"),
    ]


def agreement_matrices() -> list:
    from advisor_api.models import AgreementMatrix, AgreementMatrixCell
    arms_ = list(SS.RANKED_ARMS)
    try:
        summary = {r["pair"]: r for r in adb.rows(
            "SELECT pair, rho_median, comparable FROM agreement_summary WHERE scope='all'")}
        latest = {}
        for r in adb.rows(
                "SELECT pair, seq, rho_median, decisions, gate, trial, generation, from_ts,"
                " retrained FROM agreement_series WHERE axis='generation'"
                " ORDER BY pair, seq"):
            latest[r["pair"]] = r
    except Exception:
        return []
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
                            population="comparable, inside the current model generation"),
            note=(g.get("gate") if g.get("gate") else
                  (None if n_gen else "no comparable decision in this generation yet"))))
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(gen_from)) if gen_from else None)
    return [
        AgreementMatrix(
            key="generation", title="current model generation", arms=arms_, cells=gen_cells,
            detail=("the newest generation window, open since %s (%s)" % (when, gen_trial)
                    if when else "no generation window recorded yet")),
        AgreementMatrix(
            key="all", title="all time", arms=arms_, cells=all_cells,
            detail="every comparable decision in this run dir, across every generation"),
    ]


def _stale_analytics(e) -> str:
    return ("the analytics tables predate the pair formula (%s) -- restart the analytics "
            "service (`python -m analytics.runner`) and it rebuilds them" % str(e)[:80])


_SECONDARY_NOTE = "a supplement to the rank correlation above, not a substitute for it"


def _secondary(s: dict) -> list:
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
    try:
        s = adb.one("SELECT * FROM agreement_summary WHERE scope='all' AND pair=?",
                    (po.key,))
    except Exception as e:
        return AgreementPage(scope=scope, freshness=fresh, summary=[], rows=[],
                             empty_reason=_stale_analytics(e), **head)
    if not s:
        return AgreementPage(
            scope=scope, freshness=fresh, summary=[], rows=[],
            matrices=agreement_matrices(),
            empty_reason=("nothing has been folded in for this run dir yet -- the analytics "
                          "service builds it within a few seconds of starting"), **head)
    comparable = _i(s.get("comparable"), 0) or 0
    decisions = _i(s.get("decisions"), 0) or 0
    if not comparable:
        return AgreementPage(
            scope=scope, freshness=fresh, summary=[], rows=[],
            matrices=agreement_matrices(),
            empty_reason=("no decision in this run dir carries both a %s rank and a %s "
                          "rank, so there is nothing to correlate" % (a, b)), **head)
    same = _i(s.get("top1_same"), 0) or 0
    corr = CorrelationSummary(
        compared=Count(value=comparable, noun="decisions",
                       population="where both arms ranked at least three of the same "
                                  "offers"),
        coverage=Rate(n=comparable, of=decisions, noun="decisions",
                      population="recorded in this run dir"),
        rho_median=_f(s.get("rho_median")), rho_mean=_f(s.get("rho_mean")),
        rho_q1=_f(s.get("rho_q1")), rho_q3=_f(s.get("rho_q3")),
        tau_median=_f(s.get("tau_median")), tau_mean=_f(s.get("tau_mean")),
        same_best=Rate(n=same, of=comparable, noun="decisions", population="comparable"),
        overlap_median=_f(s.get("overlap_median")),
        from_decision=_i(s.get("from_decision")), to_decision=_i(s.get("to_decision")),
        excluded=_excluded_counts(s, a, b))
    summary = [
        AgreementSummary(measure="decisions compared", value="{:,}".format(comparable),
                         help=None),
        AgreementSummary(measure="offers both arms ranked (median)",
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
        a_rank=_f(r["a_rank"]), a_pct=_f(r["a_pct"]),
        b_rank=_f(r["b_rank"]), b_pct=_f(r["b_pct"]),
        delta_pct=_f(r["delta_pct"]), rho_median=_f(r["rho_median"]),
        fell_back=_i(r["fell_back"], 0) or 0)
        for r in adb.rows("SELECT * FROM agreement_breakdown WHERE dim='arm' AND pair=?"
                          " ORDER BY decisions DESC", (po.key,))]
    bins = [RhoBin(lo=_f(h["lo"], 0.0), hi=_f(h["hi"], 0.0),
                   decisions=_i(h["decisions"], 0) or 0)
            for h in adb.rows("SELECT * FROM agreement_hist WHERE pair=? ORDER BY bucket",
                              (po.key,))]
    return AgreementPage(scope=scope, freshness=fresh, correlation=corr, rho_bins=bins,
                         summary=summary, rows=rows, secondary=_secondary(s),
                         matrices=agreement_matrices(), **head)


ALIGNMENT_CAVEAT = None


_GENERATION_CAVEAT = (
    "Which generation ranked a decision is matched by TIMESTAMP, not recorded: nothing in "
    "the corpus joins a stored ranking to the weights that produced it. Windows come from "
    "the training ledger's own start and flush times, clipped so a decision lands in "
    "exactly one. A generation is numbered within its own session, so a session that "
    "starts on an already-retrained model begins again at g0.")


@adb.cached
def agreement_series(axis: str = "window", pair: str | None = None):
    from advisor_api.models import (AgreementSeriesPage, AgreementSeriesPoint,
                                    GenerationRow)
    axis = "generation" if axis == "generation" else "window"
    fresh = _freshness("model_agreement")
    po, opts = resolve_pair(pair)
    head = dict(pair=po.key, a=po.a, b=po.b, pairs=opts)
    try:
        pts = adb.rows("SELECT * FROM agreement_series WHERE axis=? AND pair=? ORDER BY seq",
                       (axis, po.key))
        s = adb.one("SELECT * FROM agreement_summary WHERE scope='all' AND pair=?",
                    (po.key,)) or {}
    except Exception as e:
        return AgreementSeriesPage(
            scope=Scope(text="median rank correlation over the run"), freshness=fresh,
            axis=axis, ambiguous=Count(value=0, noun="decisions", population="ambiguous"),
            empty_reason=_stale_analytics(e), **head)
    scope = Scope(
        text=("median rank correlation of %s and %s per model generation" % (po.a, po.b)
              if axis == "generation"
              else "median rank correlation of %s and %s over the run, newest last"
              % (po.a, po.b)),
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

    gens = []
    for r in adb.rows("SELECT * FROM agreement_series WHERE axis='generation' AND pair=?"
                      " ORDER BY seq", (po.key,)):
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
        **head,
        caveat=(_GENERATION_CAVEAT if axis == "generation" else None),
        bucket_decisions=(_i(pts[0]["bucket_decisions"]) if pts else None),
        ambiguous=Count(value=_i(s.get("ambiguous"), 0) or 0, noun="decisions",
                        population="whose timestamp falls inside more than one training "
                                   "window, so which generation ranked them is ambiguous"),
        points=[point(r) for r in pts], generations=gens,
        empty_reason=(None if drawable else
                      ("no bucket has enough comparable decisions for a median to mean "
                       "anything yet")))


@adb.cached
def agreement_breakdown(dim: str = "action_type", pair: str | None = None):
    from advisor_api.models import AgreementBreakdownPage, AgreementBreakdownRow
    if dim not in ("arm", "action_type", "context_kind"):
        dim = "action_type"
    po, opts = resolve_pair(pair)
    head = dict(pair=po.key, a=po.a, b=po.b, pairs=opts)
    try:
        rows = adb.rows("SELECT * FROM agreement_breakdown WHERE dim=? AND pair=?"
                        " ORDER BY decisions DESC", (dim, po.key))
    except Exception as e:
        return AgreementBreakdownPage(
            scope=Scope(text="rank correlation grouped by %s" % dim.replace("_", " ")),
            freshness=_freshness("model_agreement"), dim=dim, rows=[],
            empty_reason=_stale_analytics(e), **head)
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
        scope=Scope(text="rank correlation of %s and %s grouped by %s"
                         % (po.a, po.b, dim.replace("_", " ")),
                    detail="every comparable decision in this run dir"),
        freshness=_freshness("model_agreement"), dim=dim, rows=out,
        empty_reason=(None if out else "nothing comparable has been folded in yet"), **head)


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


def decision_agreement(decision_id: int) -> list:
    from advisor_api.models import DecisionAgreement
    try:
        by_pair = {r["pair"]: r for r in adb.rows(
            "SELECT * FROM model_agreement WHERE decision_id=?", (int(decision_id),))}
    except Exception:
        return []
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
            status=status, rho=_f(r["rho"]), tau_b=_f(r["tau_b"]), rbo=_f(r["rbo"]),
            top1_same=(bool(r["top1_same"]) if r["top1_same"] is not None else None),
            top3_overlap=_f(r["top3_overlap"]),
            a_top_in_b=_i(r["a_top_in_b"]), b_top_in_a=_i(r["b_top_in_a"]),
            note=note))
    return out


def rho_for(decision_ids, pair) -> dict:
    ids = [int(i) for i in decision_ids if i is not None]
    out = {}
    try:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for r in adb.rows("SELECT decision_id, rho, n FROM model_agreement"
                              " WHERE pair=? AND decision_id IN (%s)" % marks,
                              [pair] + chunk):
                out[_i(r["decision_id"])] = (_f(r["rho"]), _i(r["n"]))
    except Exception:
        return {}
    return out


@db.cached
def correlations(con) -> list:
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
            arm = arms.arm_of(r["arm"]) or arms.UNRECORDED
            n = _i(r["n"], 0) or 0
            cells = per.setdefault(arm, {})
            cells[k] = cells.get(k, 0) + n
            turn_totals[k] = turn_totals.get(k, 0) + n

        target = {(r["campaign_id"], _i(r["turn"], 0) or 0):
                  (_f(r["settlements"]), _f(r["lord_level"]))
                  for r in con.execute("SELECT campaign_id, turn, settlements, lord_level"
                                       " FROM turn_open")}
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
            rt = camp.get("retrain") or {}
            irt = camp.get("retrain_interrupt") or {}
            gnn = camp.get("retrain_gnn") or {}
            ggnn = camp.get("retrain_greedy_gnn") or {}
            if not (rt or irt or gnn or ggnn):
                continue
            gen += 1
            local = rt.get("local") or {}
            fit = rt.get("fit") or {}
            e1, e2 = (fit.get("e1") or {}), (fit.get("e2") or {})
            r1, r2 = _f(e1.get("val_rmse")), _f(e2.get("val_rmse"))
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
                    "e1 rmse": r1,
                    "e2 rmse": r2,
                    "lift": (round(r2 - r1, 4) if (r1 is not None and r2 is not None)
                             else None),
                    "val rows": _i(e1.get("val_rows")),
                    "best iter": _i(e1.get("best_iteration")),
                    "in-sample MAE": _f(rt.get("mae_in_sample")),
                }),
                "greedy_catboost local": _clean({
                    "rows": _i(local.get("rows")),
                    "e1 rmse": _f(((local.get("fit") or {}).get("local_e1") or {})
                                  .get("val_rmse")),
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


@db.cached
def _campaign_arm_shares(con) -> dict:
    per: dict = {}
    totals: dict = {}
    for r in con.execute(
            "SELECT COALESCE(at.policy,'(unrecorded)') arm, dp.campaign_id ckey,"
            "       COUNT(*) n"
            " FROM action_taken at JOIN decision_points dp"
            "      ON dp.decision_id = at.decision_id"
            " GROUP BY arm, ckey"):
        arm = arms.arm_of(r["arm"]) or arms.UNRECORDED
        if arm in arms.NOT_A_DRAW:
            continue
        n = _i(r["n"], 0) or 0
        per.setdefault(r["ckey"], {})[arm] = per.get(r["ckey"], {}).get(arm, 0) + n
        totals[r["ckey"]] = totals.get(r["ckey"], 0) + n
    return {c: {a: n / totals[c] for a, n in cells.items()}
            for c, cells in per.items() if totals.get(c)}


@db.cached
def _campaign_settlement_growth(con) -> dict:
    out = {}
    for ckey, row in CG.trajectories(con).items():
        g = CG.enrich(row).get("settlements_growth")
        if g is not None:
            out[ckey] = float(g)
    return out


def _W(part: str) -> float:
    sys.path.insert(0, common.ADVISOR)
    import base_model
    return float(base_model.TARGET_WEIGHTS.get(part, 1.0))


_REWARD_SERIES = """
    SELECT * FROM (
      SELECT c.campaign_id AS cid, c.faction AS faction,
             g.settlements_gained AS sett,
             g.levels_gained      AS lord,
             g.vassals_gained     AS vas,
             g.allies_gained      AS ally,
             (SELECT COUNT(DISTINCT d.turn) FROM decisions d
               WHERE d.campaign_id = c.campaign_id) AS turns
        FROM campaigns c
        JOIN campaign_gains g ON g.campaign_key = c.campaign_key
    ) WHERE turns > 1
     ORDER BY cid
"""


def campaign_reward_series(con) -> list:
    if con is None:
        return []
    out = []
    for row in con.execute(_REWARD_SERIES):
        sett = float(row["sett"] or 0.0)
        lord = float(row["lord"] or 0.0)
        vas = float(row["vas"] or 0.0)
        ally = float(row["ally"] or 0.0)
        out.append(CampaignReward(
            seq=len(out) + 1, campaign_id=int(row["cid"]), faction=row["faction"],
            settlements=sett, lord_level=lord, vassals=vas, allies=ally,
            total=(_W("settlements") * sett + _W("lord_level") * lord
                   + _W("vassals") * vas + _W("allies") * ally),
            turns=Count(value=int(row["turns"]), noun="turns",
                        population="with a reward row in this campaign")))
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


def trials(con=None):
    out, meta = _trials()
    live = metrics_db.live_trials(meta)
    shares = _campaign_arm_shares(con) if con is not None else {}
    growth = _campaign_settlement_growth(con) if con is not None else {}
    by_trial = {m.get("trial"): m for m in meta}
    for row in out:
        row.live = row.trial in live
        row.growth_corr = _growth_corr(
            (by_trial.get(row.trial) or {}).get("campaign_uuids"), shares, growth)
    return out


@db.cached_files(metrics_db.DB_PATH, metrics_db.DB_PATH + "-wal")
def _trials() -> tuple:
    out = []
    rows = list(metrics_db.trials())
    meta = [{"trial": d.get("trial"), "ts": d.get("ts"), "running": d.get("running"),
             "campaign_uuids": d.get("campaign_uuids")} for d in rows]
    for d in rows:
        corpus = d.get("corpus_at_train") or {}
        setts = d.get("settlements") or {}
        lord = d.get("lord_level") or {}
        timing = d.get("timing") or {}
        row = TrialRow(
            trial=str(d.get("trial") or ""),
            mix={arms.canonical(k): v for k, v in (d.get("strategies") or {}).items()},
            interrupt_mix={arms.canonical(k): v for k, v in
                           (d.get("interrupt_strategies") or d.get("strategies") or {}).items()},
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
            reward_per_campaign=(round(_f(setts.get("mean")) + _f(lord.get("mean")), 3)
                                 if setts.get("mean") is not None
                                 and lord.get("mean") is not None else None),
            turns_per_campaign=_f(d.get("turns_per_campaign")),
            seconds_per_campaign=_f(timing.get("s_per_campaign")),
            seconds_per_turn=_f(timing.get("s_per_turn")),
            notes=(", ".join("%s %s" % (k, v)
                             for k, v in (d.get("outcomes") or {}).items()) or None))
        row.snapshots = _i(d.get("_snapshots"), 1)
        out.append(row)
    out.reverse()
    return out[:200], meta


def reward_series(con, campaign_key: str):
    rows = con.execute(
        "SELECT turn, income, settlements, allies, vassals, power_rank"
        " FROM turn_open WHERE campaign_id = ? ORDER BY turn", (campaign_key,)).fetchall()
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
            state = "bad"
        out.append(DiploEvent(
            turn=_i(d.get("turn")), channel=_phrase(d.get("channel") or d.get("kind")),
            faction=(_fac(_who) if (_who := (d.get("target") or d.get("faction"))) else None),
            outcome=_phrase(outcome), deal_score=score, standing=_f(d.get("standing")),
            terms=_text(d.get("terms") or d.get("speech")), state=state))
    return out[:200]


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
        "SELECT campaign_map m, faction f, MAX(leader) l FROM campaigns"
        " WHERE leader IS NOT NULL GROUP BY campaign_map, faction")}


PULSE = 50


@db.cached
def ucb_pick_series(con) -> list:
    leaders = _start_leaders(con)
    top: dict = {}
    for r in con.execute("SELECT pick_id, rank, score FROM ucb_pick_rows WHERE rank <= 2"):
        top.setdefault(_i(r["pick_id"], 0), {})[_i(r["rank"], 0)] = _f(r["score"])
    ns: dict = {}
    for r in con.execute("SELECT pick_id, n FROM ucb_pick_rows"):
        ns.setdefault(_i(r["pick_id"], 0), []).append(_i(r["n"], 0) or 0)
    produced = pick_campaigns(con)
    gains = {g["campaign_key"]: g for g in gains_all(con)}
    by_camp = {r.campaign.raw: r for r in campaign_rows(con)}
    rows = [dict(r) for r in con.execute("SELECT * FROM ucb_picks ORDER BY pick_id")]
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
            blend = round(score - explore, 4)
        n_list = ns.get(pid) or []
        prod = None
        ck = produced.get(pid)
        if ck:
            g = gains.get(ck) or {}
            cr = by_camp.get(ck)
            prod = ProducedCampaign(
                campaign=_camp(ck), reward=_f(g.get("reward")),
                turns=(cr.turns if cr else _i(g.get("turns_reached"))),
                outcome=cr.outcome if cr else None,
                outcome_state=cr.outcome_state if cr else "neutral")
        out.append(UcbPick(
            leader=leaders.get(((r["campaign_map"] or ""), r["faction"])),
            pick_id=pid, ts=_f(r["ts"]), c=_f(r["c"]),
            total_plays=_i(r["total_plays"], 0), faction=_fac(r["faction"]),
            campaign_map=(_id(ident.campaign_map(r["campaign_map"]))
                          if r["campaign_map"] else None),
            n=_i(r["n"], 0), mean=_f(r["mean"]), blend=blend,
            entropy=_f(r.get("entropy")), std=_f(r.get("std")),
            explore=explore, score=score,
            margin=(round(s1 - s2, 4) if s1 is not None and s2 is not None else None),
            tied=_i(r["tied"], 0), starts=len(n_list),
            repeat=bool(i > 0 and keys[i - 1] == key), produced=prod,
            distinct_50=len(set(win)),
            repeat_50=(round(sum(reps) / len(reps), 3) if reps else None),
            cum_distinct=len(seen),
            gini=(round(UCB.gini(n_list), 3) if n_list else None),
            under_min=sum(1 for n_ in n_list if n_ < UCB.MIN_PLAYS)))
    return out


def ucb_picks(con, limit: int = 200, before: int | None = None) -> list:
    series = ucb_pick_series(con)
    desc = list(reversed(series))
    if before is not None:
        desc = [p for p in desc if p.pick_id < int(before)]
    return desc[:int(limit)]


@db.cached
def ucb_tiles(con) -> list:
    series = ucb_pick_series(con)
    cx = ucb_context(con)
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


@db.cached
def ucb_pick_rows(con, pick_id: int) -> tuple:
    head = next((p for p in ucb_pick_series(con) if p.pick_id == int(pick_id)), None)
    if head is None:
        return None, [], 0
    leaders = _start_leaders(con)
    raw = [dict(r) for r in con.execute(
        "SELECT * FROM ucb_pick_rows WHERE pick_id = ? ORDER BY rank", (int(pick_id),))]
    full = all(r.get("entropy") is not None and r.get("std") is not None for r in raw)
    stats = {i: {"n": _i(r["n"], 0) or 0, "mean": _f(r["mean"], 0.0) or 0.0,
                 "entropy": _f(r.get("entropy"), 0.0) or 0.0,
                 "std": _f(r.get("std"), 0.0) or 0.0} for i, r in enumerate(raw)}
    z = UCB.zscores(stats) if full and raw else None
    top_score = next((_f(r["score"]) for r in raw if _f(r["score"]) is not None), None)
    rows = []
    for i, r in enumerate(raw):
        score, explore = _f(r["score"]), _f(r["explore"])
        blend = _f(r.get("blend"))
        if blend is None and score is not None and explore is not None:
            blend = round(score - explore, 4)
        zp = (UCB.zparts(stats[i], z) if z is not None
              and stats[i]["n"] >= UCB.MIN_PLAYS else {})
        rows.append(UcbRow(
            leader=leaders.get(((r["campaign_map"] or ""), r["faction"])),
            rank=_i(r["rank"], 0), faction=_fac(r["faction"]),
            campaign_map=(_id(ident.campaign_map(r["campaign_map"]))
                          if r["campaign_map"] else None),
            n=_i(r["n"], 0), mean=_f(r["mean"]), entropy=_f(r.get("entropy")),
            std=_f(r.get("std")),
            z_mean=(round(zp["mean"], 4) if zp else None),
            z_entropy=(round(zp["entropy"], 4) if zp else None),
            z_std=(round(zp["std"], 4) if zp else None),
            blend=blend, explore=explore, score=score,
            delta=(round(score - top_score, 4) if score is not None
                   and top_score is not None else None),
            chosen=bool(r["chosen"])))
    under = sum(1 for r in rows if r.n < UCB.MIN_PLAYS)
    return head, rows, under
