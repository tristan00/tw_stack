"""Every query the dashboard can ask, with its cost bounded and its population named.

Three rules hold throughout.

**Aggregate in SQL, not in Python.** The corpus is 230k offer rows and grows toward a
500-campaign target. Pulling rows into Python to count them is what made the old
crosstab materialise the entire corpus per request.

**Bound everything that can be unbounded.** A log is paged, a timeline is windowed, an
agreement comparison walks a fixed number of recent decisions. An unbounded scan is fine
at 127 campaigns and is not fine at 500, and the difference does not announce itself.

**Name the population at the point the number is produced.** `Count` and `Rate` require
it, so a count's meaning is decided here, next to the SQL that defines it, rather than by
whoever renders it later.

THE JOIN TRAP, which is load-bearing. `target_rows.campaign_id` holds the campaign KEY
string while `campaigns.campaign_id` holds an integer surrogate; the natural-looking join
`ON t.campaign_id = c.campaign_id` returns ZERO rows and raises nothing. Measured on the
live corpus: 0 rows the wrong way, 567 the right way. Every join to target_rows or to the
v1 views goes through `campaigns.campaign_key`, and a test asserts the wrong form stays
empty so nobody "fixes" it back.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from advisor_api import db, ident
from advisor_api.models import (
    ActionTypeRow, ActivityRow, AgreementPage, AgreementRankRow, AgreementSummary,
    ArmCoverage, CampaignRow, Count, CorrelationRow, CorrelationTile, Current,
    DecisionRow, DiploEvent, EntityState, ForcingBar, ForcingTile, Ident, InterruptOption,
    InterruptRow, Metric, ModelCard, OfferRow, OutcomeTally, PhaseSpan, PolicyRow, Rate,
    RewardPoint, Scope, Service, Signal, StartRow, TimelineAction, TimelineLane,
    TimingRow, TrainingEvent, TrialRow,
)

DECISIONS_PAGE = 50
TIMELINE_DECISIONS = 200
AGREE_LOOKBACK = 600
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
}


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
    """Parse a stored JSON column into a container, never into None.

    A column holding the literal text `null` parses to None, and every caller here goes
    straight on to .get() -- so the timing panel died on one row whose timing was `null`
    rather than absent. Coercing non-containers to {} keeps a malformed row from taking
    down the panel that merely mentions it.
    """
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
    """Whatever a stream wrote, as a string a cell can hold.

    These payloads are hand-written by several producers and the same field arrives as a
    string from one and a list from another (`terms` is `['declare_war']` on the deal
    stream). Coercing here beats a validation error that blanks the whole panel.
    """
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


def _rate_state(pct, ok=70.0, warn=40.0):
    if pct is None:
        return "neutral"
    return "ok" if pct >= ok else ("warn" if pct >= warn else "bad")


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

def _postmortem_path():
    return os.path.join(common.native(common.RUNS_ROOT), "postmortems.jsonl")


def _experiments_path():
    return os.path.join(common.native(common.METRICS_DIR), "experiments.jsonl")


def postmortems() -> list:
    """Every recorded campaign ending, newest last.

    The file spans earlier run directories, so it holds MORE endings than the current
    corpus holds campaigns. That is not a discrepancy to reconcile -- it is why every
    count drawn from here names its population explicitly.
    """
    return _postmortems_cached()


@db.cached_files(os.path.join(common.native(common.RUNS_ROOT), "postmortems.jsonl"))
def _postmortems_cached() -> list:
    out = []
    try:
        with open(_postmortem_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    # A torn last line is normal while the session is mid-write. Skipping
                    # it loses one ending for a few seconds; failing the page loses all.
                    continue
    except OSError:
        return []
    return out


def _campaign_spans(con) -> dict:
    """campaign_key -> (first ts, last ts, faction). One grouped query."""
    rows = con.execute(
        "SELECT c.campaign_key, c.faction, MIN(dp.ts) t0, MAX(dp.ts) t1"
        " FROM campaigns c JOIN decision_points dp ON dp.campaign_id = c.campaign_key"
        " GROUP BY c.campaign_key").fetchall()
    return {r["campaign_key"]: (r["t0"], r["t1"], r["faction"]) for r in rows}


# How far after a campaign's last decision its postmortem may be written. A postmortem is
# produced during teardown -- screenshots, log flush, growth evaluation -- which is
# seconds to minutes, never a quarter of an hour.
_POSTMORTEM_LAG_S = 900


def join_outcomes(con) -> dict:
    """campaign_key -> the postmortem that ended it.

    The postmortem log carries no campaign key -- only a faction and a wall-clock time --
    which is precisely why the old dashboard showed endings and per-campaign metrics as
    two tables that could not be joined, and so could not answer the question the page
    existed to ask.

    They do share an identity: a campaign is the last one of its faction to record a
    decision before its own postmortem is written. Matching on (faction, most recent
    preceding campaign, within the teardown window) resolves 119 of 136 endings on the
    live corpus and claims no campaign twice. The 17 that do not resolve belong to earlier
    run directories, and are reported as a named count rather than dropped.

    A collision would mean two endings claiming one campaign, i.e. the key is not a key.
    test_api asserts that stays at zero.
    """
    return _join_outcomes(con)


@db.cached
def _join_outcomes(con) -> dict:
    spans = _campaign_spans(con)
    by_faction = {}
    for key, (_t0, t1, faction) in spans.items():
        by_faction.setdefault(faction, []).append((t1 or 0.0, key))
    for lst in by_faction.values():
        lst.sort()
    claimed = {}
    for pm in postmortems():
        faction, ts = pm.get("faction"), _f(pm.get("ts"), 0.0) or 0.0
        best = None
        for t1, key in by_faction.get(faction, ()):
            # +5s: the postmortem clock and the decision clock are sampled separately.
            if t1 <= ts + 5:
                best = (t1, key)
            else:
                break
        if not best or ts - best[0] > _POSTMORTEM_LAG_S:
            continue
        # Later postmortem wins: a campaign key is unique per run, so a second match on
        # the same key means the first was the wrong one.
        claimed[best[1]] = pm
    return claimed


def unjoined_endings(con) -> int:
    return max(0, len(postmortems()) - len(join_outcomes(con)))


# ----------------------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------------------

@db.cached
def current(con) -> Current:
    """The campaign being played right now, resolved once.

    Newest by clock, not by turn number: the run directory is reused across hundreds of
    campaigns, so the highest turn ever reached usually belongs to a campaign that ended
    days ago.

    This is the ONLY source for these values. The old dashboard resolved them twice --
    once from the corpus for its header, once from a session-log parse for its live tab --
    and rendered both at once, disagreeing about which faction was playing and what turn
    it was. One accessor makes that class of contradiction unrepresentable.
    """
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
    """The corpus totals, each naming the population it counted.

    These four numbers count four different things and are not expected to agree. Saying
    so on the number is the whole point.
    """
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
    out.append(Metric(label="campaigns/hr", value=round(camps / span_h, 1),
                      sub="over the last %d decisions" % len(rows)))
    out.append(Metric(label="turns/hr", value=round(turns / span_h, 1),
                      sub="over the last %d decisions" % len(rows)))
    out.append(Metric(label="confirm rate", value=(round(pct, 1) if pct is not None else None),
                      unit="%", sub="%d of %d attempted actions" % (confirmed, attempted),
                      state=_rate_state(pct)))
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
    """Median ms per execution stage over the recent window.

    Reported with its maximum beside it. A stage whose median is 100 ms and whose worst
    case is 6 s is a different problem from one that is uniformly slow, and a median
    alone cannot tell them apart -- the old panel buried its single largest number
    (a 6.3 s outlier) inside a dim run-on sentence under a table reading 103.
    """
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
def signals(con) -> list:
    """Things worth noticing, with severity decided here rather than in the client."""
    out = []
    pm = postmortems()[-12:]
    bad = [p for p in pm if (p.get("outcome") or "") in ("error", "stuck", "unhandled_screen")]
    if bad:
        out.append(Signal(
            text="%d of the last %d recorded endings were harness faults" % (len(bad), len(pm)),
            state="bad",
            detail=", ".join(sorted({str(p.get("outcome")) for p in bad}))))
    run = _i(con.execute("SELECT COUNT(*) FROM action_taken"
                         " WHERE refusal='awaiting_execution'").fetchone()[0], 0)
    if run:
        out.append(Signal(text="%d actions awaiting execution" % run, state="warn",
                          detail="attempted but not yet resolved by the game"))
    silent = _i(con.execute("SELECT COUNT(*) FROM action_taken"
                            " WHERE refusal='command_silently_refused'").fetchone()[0], 0)
    att = _i(con.execute("SELECT COUNT(*) FROM action_taken"
                         " WHERE refusal IS NOT 'awaiting_execution'").fetchone()[0], 0)
    if silent and att:
        out.append(Signal(
            text="%d of %d attempted actions were silently refused by the game"
                 % (silent, att),
            state="warn" if silent * 5 < att else "bad",
            detail="the command was accepted and had no effect"))
    if not out:
        out.append(Signal(text="nothing notable", state="ok"))
    return out


# ----------------------------------------------------------------------------------------
# campaigns
# ----------------------------------------------------------------------------------------

@db.cached
def campaign_rows(con) -> list:
    """One row per campaign: its decisions, its trajectory, and its outcome.

    Four grouped queries and one window function, then a dict merge -- not a query per
    campaign. The final-state values come from ROW_NUMBER() OVER (PARTITION BY campaign)
    rather than a correlated subquery evaluated once per row of target_rows.
    """
    decs = {r["ckey"]: r for r in con.execute(
        "SELECT campaign_id ckey, COUNT(*) n, MIN(ts) t0, MAX(ts) t1"
        " FROM decision_points GROUP BY campaign_id")}
    acts = {r["ckey"]: r for r in con.execute(
        "SELECT dp.campaign_id ckey,"
        "       COUNT(*) rows_,"
        "       SUM(CASE WHEN at.refusal IS 'awaiting_execution' THEN 0 ELSE 1 END) attempted,"
        "       SUM(CASE WHEN at.counted=1 THEN 1 ELSE 0 END) confirmed"
        " FROM action_taken at JOIN decision_points dp ON dp.decision_id = at.decision_id"
        " GROUP BY dp.campaign_id")}
    peaks = {r["ckey"]: r for r in con.execute(
        "SELECT campaign_id ckey, MAX(turn) turns, MAX(settlements) pset,"
        "       MIN(power_rank) prank, MAX(lord_level) plord"
        " FROM target_rows GROUP BY campaign_id")}
    finals = {r["ckey"]: r for r in con.execute(
        "SELECT ckey, settlements, power_rank, income FROM ("
        "  SELECT campaign_id ckey, settlements, power_rank, income,"
        "         ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY turn DESC) rn"
        "  FROM target_rows) WHERE rn=1")}
    meta = {r["campaign_key"]: r for r in con.execute(
        "SELECT campaign_id, campaign_key, faction, turns FROM campaigns")}

    outcomes = join_outcomes(con)
    out = []
    for ckey, d in decs.items():
        m = meta.get(ckey)
        a = acts.get(ckey)
        pk = peaks.get(ckey)
        fn = finals.get(ckey)
        attempted = _i(a["attempted"], 0) if a else 0
        confirmed = _i(a["confirmed"], 0) if a else 0
        rows_ = _i(a["rows_"], 0) if a else 0
        n_dec = _i(d["n"], 0) or 0
        row = CampaignRow(
            campaign_id=_i(m["campaign_id"], 0) if m else 0,
            campaign=_camp(ckey),
            turns=_i(pk["turns"]) if pk else (_i(m["turns"]) if m else None),
            decisions=n_dec,
            # A decision point that produced no action row at all is not a failed action;
            # it is a decision where nothing was offered or nothing was chosen.
            no_action=max(0, n_dec - rows_),
            attempted=attempted,
            confirmed=confirmed,
            confirm_rate=Rate(n=confirmed, of=attempted, noun="actions",
                              population="attempted in this campaign"),
            span_min=round(((_f(d["t1"]) or 0) - (_f(d["t0"]) or 0)) / 60.0, 1),
            peak_settlements=_f(pk["pset"]) if pk else None,
            peak_power_rank=_f(pk["prank"]) if pk else None,
            peak_lord_level=_f(pk["plord"]) if pk else None,
            final_settlements=_f(fn["settlements"]) if fn else None,
            final_power_rank=_f(fn["power_rank"]) if fn else None,
            final_income=_f(fn["income"]) if fn else None,
        )
        pm = outcomes.get(ckey)
        if pm:
            outcome = str(pm.get("outcome") or "")
            row.outcome = _phrase(outcome)
            row.outcome_state = _OUTCOME_STATE.get(outcome, "neutral")
            row.ended_when = pm.get("when")
            growth = pm.get("growth") or {}
            row.ended_at_turn = _i(growth.get("turn"), _i(pm.get("turn_at_death")))
            mets = (growth.get("metrics") or {})
            s, l = mets.get("settlements") or {}, mets.get("lord_level") or {}
            # Decomposed from the structured growth record, not parsed out of the verdict
            # sentence. The same fact rendered as prose repeated near-verbatim on fifteen
            # consecutive rows; as four numbers it is sortable and it is one glance.
            row.settlements_from, row.settlements_to = _f(s.get("then")), _f(s.get("now"))
            row.lord_from, row.lord_to = _f(l.get("then")), _f(l.get("now"))
            verdict = str((pm.get("plausibility") or {}).get("verdict") or "")
            row.suspicious = ("harness_failure_likely" in verdict) or ("ambiguous" in verdict)
            if row.suspicious:
                row.outcome_state = "bad"
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
    """Per-faction start quality. One grouped query per fact, never one per campaign.

    `n` leads every row because most starts have n=1: on the live corpus 38 of 61 starts
    have a single campaign and 53 have two or fewer, so "avg turns" and "best power rank"
    are usually one observation wearing an average's name. Marking that is more honest
    than a paragraph apologising for it underneath.
    """
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
    """faction x action-type crosstab, and the totals row that makes it readable.

    Aggregated with one GROUP BY rather than by materialising every joined row in Python.

    The totals row is the point. Across every faction, `diplomacy` confirms 73 of 425
    attempts while every other action type sits between 75% and 100%; spread over 61
    alphabetical rows and 22 columns that is 1342 cells and the finding is invisible.
    Sorted worst-first, it is the first line on the page.
    """
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
    """The options on a blocking screen, as (label, payload) pairs.

    Stored as an OBJECT KEYED BY LABEL -- {"button_retreat": {"exploit":…, "gnn":…}} --
    not as a list of option records. Reading it as a list yields nothing and raises
    nothing, so every per-option model score silently disappeared from the panel while
    the row count stayed right. The list form is still accepted in case an older run dir
    carries it.
    """
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
    """The decision log, one page at a time.

    Deliberately two bounded queries plus a keyed lookup, never a join to the offer view:
    the offer columns used to arrive via a correlated MIN(offer_id) subquery against a
    view, measured at 0.01 s for the base rows and 20 s for the same rows with that join.
    The page's offers are fetched by decision id, for the fifty ids on the page only.
    """
    where, args = [], []
    if action_type:
        where.append("at.action_type = ?")
        args.append(action_type)
    if policy:
        where.append("COALESCE(at.policy, dp.policy) = ?")
        args.append(policy)
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

    out = []
    for r in rows:
        res, state = _result_of(r)
        o = offer_by_id.get(r["offer_id"])
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
            gnn_rank=gnn_rank, delta_pct=delta, latency_ms=_f(r["latency_ms"])))
    return out, total


@db.cached
def decision_facets(con) -> dict:
    """The distinct values worth filtering by, so the client never invents a filter."""
    at = [r[0] for r in con.execute(
        "SELECT DISTINCT action_type FROM action_taken WHERE action_type IS NOT NULL"
        " ORDER BY action_type")]
    po = [r[0] for r in con.execute(
        "SELECT DISTINCT policy FROM action_taken WHERE policy IS NOT NULL ORDER BY policy")]
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
    """The four phases of one action, in ms.

    collect  -- the recorder reading the game
    queue    -- the request round trip minus the work inside it
    score    -- featurize and rank (graph build and forward, when the gnn is drawn)
    verify   -- execute and confirm

    They are a decomposition of one wall clock, so `queue` is derived by subtraction and
    clamped at zero: the two timers are sampled by different processes and can cross.
    """
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
    """Confirm rate per action type, plus the policy tally, plus every denominator named.

    Three different decision denominators legitimately coexist here -- all rows, rows that
    were actually attempted, and rows drawn by a strategy -- and the old panel printed all
    three as "decisions" without saying which was which. They are returned as named counts
    so the difference is on the page instead of in the reader's head.
    """
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
        rows.append(ActionTypeRow(action_type=_phrase(r["action_type"]), rate=rate,
                                  state=_rate_state(rate.pct)))
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

    pol_rows = con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, COUNT(*) n FROM action_taken"
        " GROUP BY p ORDER BY n DESC").fetchall()
    drawn = sum(_i(r["n"], 0) or 0 for r in pol_rows
                if r["p"] not in ("forced_end_turn", "(unrecorded)"))
    policies = []
    for r in pol_rows:
        n = _i(r["n"], 0) or 0
        note = None
        if r["p"] == "forced_end_turn":
            # Not a strategy draw: the loop ends the turn when nothing is eligible, so
            # counting it inside the mix would understate every real arm's share.
            note = "not a strategy draw -- the loop ended the turn"
        policies.append(PolicyRow(
            policy=_phrase(r["p"]), picks=n,
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
        Metric(label="confirmed", value=confirmed, sub="of %d attempted" % attempted,
               state=_rate_state((100.0 * confirmed / attempted) if attempted else None)),
        Metric(label="attempted", value=attempted, sub="of %d recorded" % all_rows),
        Metric(label="action types", value=len(rows), sub="seen in this run dir"),
    ]
    return tiles, rows, policies, denominators


# ----------------------------------------------------------------------------------------
# blocking screens
# ----------------------------------------------------------------------------------------

@db.cached
def menus(con):
    """Blocking-screen decisions, with every per-option model score as data.

    The scores previously existed only inside hover text -- the heading said as much --
    which makes them unselectable, unsearchable and absent on touch. They are fields here,
    so the client can put them in cells and sort by them.
    """
    total = _i(con.execute("SELECT COUNT(*) FROM interrupt_decisions").fetchone()[0], 0) or 0
    by_screen = [Count(value=_i(r["n"], 0) or 0, noun=str(r["kind"] or "screens"),
                       population="blocking-menu decisions of this kind")
                 for r in con.execute(
                     "SELECT kind, COUNT(*) n FROM interrupt_decisions"
                     " GROUP BY kind ORDER BY n DESC")]

    pol_rows = con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, COUNT(*) n FROM interrupt_decisions"
        " GROUP BY p ORDER BY n DESC").fetchall()
    tot_pol = sum(_i(r["n"], 0) or 0 for r in pol_rows) or 1
    policies = [PolicyRow(policy=_phrase(r["p"]), picks=_i(r["n"], 0) or 0,
                          share=Rate(n=_i(r["n"], 0) or 0, of=tot_pol, noun="picks",
                                     population="on blocking-menu decisions"))
                for r in pol_rows]

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
    """The most recent actions, grouped into (campaign, turn) lanes.

    Turn numbers restart with every campaign, so the lane key is the pair. One bounded
    query; the phase strings are a fixed legend on the page rather than an attribute
    repeated on every bar -- the old fragment carried 846 copies of six strings, which was
    most of its 147 KB.
    """
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
    ("global", common.MODEL_GLOBAL, "catboost",
     "Ranks every offered action across the whole faction. The main action ranker."),
    ("local", common.MODEL_LOCAL, "catboost",
     "Ranks actions within one entity's own option set, so a lord's choices compete "
     "against each other rather than against the whole map."),
    ("interrupt", common.MODEL_INTERRUPT, "catboost",
     "Answers blocking screens -- battles, dilemmas, occupation choices."),
    ("gnn", common.MODEL_MAPGRAPH, "mapgraph",
     "The graph model for actions: the map and its entities as a graph, the action as a "
     "node in it."),
    ("gnn interrupt", common.MODEL_MAPGRAPH_INTERRUPT, "mapgraph",
     "The graph model for blocking screens. Same architecture as the action graph model, "
     "different decision family, its own weights."),
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
    """What each model wants to do: the action-type mix each arm actually picked.

    Bounded to the decisions each arm drew, with a Wilson interval per bar so a 2-of-3
    share is visibly not the same evidence as a 200-of-300 share.
    """
    rows = con.execute(
        "SELECT COALESCE(policy,'(unrecorded)') p, action_type, COUNT(*) n"
        " FROM action_taken WHERE action_type IS NOT NULL GROUP BY p, action_type").fetchall()
    by_arm: dict = {}
    for r in rows:
        by_arm.setdefault(r["p"], {})[r["action_type"]] = _i(r["n"], 0) or 0
    tiles = []
    for arm in ("exploit_tree", "gnn_marwil"):
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
    n_dec = sum(sum((by_arm.get(a) or {}).values()) for a in ("exploit_tree", "gnn_marwil"))
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


@db.cached
def agreement(con):
    """Where each model ranked the action that was actually taken.

    Bounded to the most recent AGREE_LOOKBACK decision ids: an unbounded
    `WHERE gnn_rank IS NOT NULL` walks every offer row in the corpus on every request,
    and the bound keeps the query on the offer index.
    """
    hi = _i(con.execute("SELECT MAX(decision_id) FROM action_taken").fetchone()[0], 0) or 0
    lo = max(0, hi - AGREE_LOOKBACK)
    # Two bounded queries and a dict, never a range join against the offer view.
    #
    # action_offers decompresses a blob per row, so its cost is per row TOUCHED, not per
    # row returned. Joining `... JOIN action_offers o ON o.offer_id = at.offer_id` with
    # only `at` bounded decompressed the whole corpus: 7.6 s. Bounding the view by its own
    # decision_id as well brought that to 631 ms -- still ~33k rows decompressed, because
    # 600 decisions carry ~55 offers each and we want exactly one of them.
    #
    # Fetching the taken rows first and then looking their offers up BY offer_id
    # decompresses ~600 rows instead of ~33k. Same answer, and it is the access pattern
    # rather than the bound that decides the cost.
    base = con.execute(
        "SELECT at.decision_id, at.offer_id, COALESCE(at.policy, dp.policy) policy,"
        "       dp.n_offers"
        " FROM action_taken at"
        " JOIN decision_points dp ON dp.decision_id = at.decision_id"
        " WHERE at.decision_id > ? AND at.offer_id IS NOT NULL", (lo,)).fetchall()
    ranks = {}
    ids = [r["offer_id"] for r in base]
    for i in range(0, len(ids), 400):                     # under SQLite's variable limit
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for o in con.execute(
                "SELECT offer_id, rank, gnn_rank FROM action_offers"
                " WHERE offer_id IN (%s)" % marks, chunk):
            ranks[o["offer_id"]] = (o["rank"], o["gnn_rank"])
    rows = []
    for r in base:
        cat, gnn = ranks.get(r["offer_id"], (None, None))
        if cat is None or gnn is None:
            continue
        rows.append({"decision_id": r["decision_id"], "policy": r["policy"],
                     "n_offers": r["n_offers"], "cat_rank": cat, "gnn_rank": gnn})
    if not rows:
        return [], [], None, ("no decision in the recent window carries both a catboost "
                              "rank and a graph rank, so there is nothing to compare")
    by_pol: dict = {}
    deltas = []
    same = 0
    for r in rows:
        n = _i(r["n_offers"], 0) or 0
        cr, gr = _i(r["cat_rank"]), _i(r["gnn_rank"])
        if not cr or not gr:
            continue
        cpct = 100.0 * (cr - 1) / max(1, n - 1)
        gpct = 100.0 * (gr - 1) / max(1, n - 1)
        b = by_pol.setdefault(r["policy"] or "(unrecorded)",
                              {"n": 0, "cr": [], "gr": [], "cp": [], "gp": []})
        b["n"] += 1
        b["cr"].append(cr)
        b["gr"].append(gr)
        b["cp"].append(cpct)
        b["gp"].append(gpct)
        deltas.append(gpct - cpct)
        same += 1 if (cr == 1 and gr == 1) else 0
    summary = [
        AgreementSummary(measure="decisions compared", value="{:,}".format(len(deltas)),
                         help="decisions in the recent window where both models ranked the "
                              "action that was taken"),
        AgreementSummary(measure="both picked the same best action",
                         value="%d of %d" % (same, len(deltas))),
    ]
    if deltas:
        summary.append(AgreementSummary(
            measure="median rank gap (percentile)",
            value="%+.1f" % statistics.median(deltas),
            help="graph minus tree. Positive means the graph model rated the taken action "
                 "higher than the tree did."))
    out = []
    for pol, b in sorted(by_pol.items(), key=lambda kv: -kv[1]["n"]):
        out.append(AgreementRankRow(
            picked_by=_phrase(pol), decisions=b["n"],
            cat_rank=round(statistics.median(b["cr"]), 1),
            cat_pct=round(statistics.median(b["cp"]), 1),
            gnn_rank=round(statistics.median(b["gr"]), 1),
            gnn_pct=round(statistics.median(b["gp"]), 1),
            delta_pct=round(statistics.median(b["gp"]) - statistics.median(b["cp"]), 1)))
    return summary, out, None, None


@db.cached
def correlations(con) -> list:
    """Does an arm's share of a campaign track how that campaign went?

    Correlated per TURN inside a campaign, not per campaign: a campaign is one sample and
    there are too few of them, while turns inside a campaign are the unit at which an
    arm's share actually varies.

    Two tiles, labelled "action ranker" and "interrupt model". They share arm names, so a
    single search over a merged page finds the action row every time and never inspects
    the interrupt one -- which is the table that was wrong.
    """
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
            per.setdefault(r["arm"], {})[k] = _i(r["n"], 0) or 0
            turn_totals[k] = turn_totals.get(k, 0) + (_i(r["n"], 0) or 0)

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
    """Pearson r, or None with the reason it was not computed.

    Reporting a correlation over eight points as though it were a finding is worse than
    reporting nothing, so the gate is part of the return value rather than a footnote.
    """
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


def trials():
    return _trials()


@db.cached_files(os.path.join(common.native(common.METRICS_DIR), "experiments.jsonl"))
def _trials() -> list:
    out = []
    try:
        with open(_experiments_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                out.append(TrialRow(
                    trial=str(d.get("trial") or d.get("name") or ""),
                    backend=d.get("backend"), cfg=d.get("cfg") if isinstance(d.get("cfg"), str)
                    else (json.dumps(d.get("cfg")) if d.get("cfg") else None),
                    mix=d.get("mix") or d.get("strategies") or {},
                    # ruleset is written as a bare name by older rows and as
                    # {name, sha} by newer ones. The name is what identifies the run.
                    ruleset=_text(d.get("ruleset")),
                    campaigns=_i(d.get("campaigns")), corpus=_i(d.get("corpus")),
                    settlements_per_campaign=_f(d.get("sett_per_camp")),
                    settlements_total=_f(d.get("sett_total")),
                    grew=str(d.get("grew")) if d.get("grew") is not None else None,
                    lord_per_campaign=_f(d.get("lord_per_camp")),
                    turns_per_campaign=_f(d.get("turns_per_camp")),
                    seconds_per_campaign=_f(d.get("s_per_camp")),
                    seconds_per_turn=_f(d.get("s_per_turn")),
                    notes=d.get("notes")))
    except OSError:
        return []
    out.reverse()
    return out[:200]


# ----------------------------------------------------------------------------------------
# campaign detail
# ----------------------------------------------------------------------------------------

def reward_series(con, campaign_key: str):
    """The turn series for one campaign, plus the columns that carry no signal.

    A column holding one distinct value across the whole series is reported so the client
    can hide it by default. `allies` and `vassals` are constant zero across every recorded
    turn today, and a linter that only looks for blank cells never noticed, because a
    column full of real zeroes is full.
    """
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


def diplomacy_tail(campaign_key: str | None = None) -> list:
    """Recent deal events, read from the tail of the stream.

    `deal score` is the game's own success_chance readout and is NOT a percentage -- it
    runs past -1000. Its sign is what carries: negative and the AI refuses. The client is
    told the sign as state and shows the raw number as a number.
    """
    path = os.path.join(common.native(common.RUN_DIR), "diplomacy.jsonl")
    out = []
    for d in _tail_jsonl(path, DIPLO_TAIL):
        ckey = d.get("campaign_id") or d.get("campaign")
        if campaign_key and ckey != campaign_key:
            continue
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
            faction=_fac(d.get("target") or d.get("faction") or ""),
            outcome=_phrase(outcome), deal_score=score, standing=_f(d.get("standing")),
            terms=_text(d.get("terms") or d.get("speech")), state=state))
    out.reverse()
    return out[:200]


def _tail_jsonl(path: str, n: int) -> list:
    """The last n json lines, read backwards.

    These streams reach hundreds of megabytes -- the request log is ~300 MB -- so reading
    forward to reach the end is not an option. Seek back in growing steps until n newlines
    are in hand, capped so a pathological file cannot pull the whole thing into memory.
    """
    out: list = []
    try:
        size = os.path.getsize(path)
    except OSError:
        return out
    step, cap = 1 << 22, 1 << 26
    have = min(size, step)
    while True:
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, size - have))
                chunk = fh.read(have)
        except OSError:
            return out
        lines = chunk.split(b"\n")
        if have >= size or len(lines) > n + 1 or have >= cap:
            break
        have = min(size, have * 4)
    parsed = []
    for raw in lines[-(n + 1):]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(json.loads(raw.decode("utf-8", "replace")))
        except ValueError:
            continue
    return parsed


# ----------------------------------------------------------------------------------------
# infra
# ----------------------------------------------------------------------------------------

_ACTIVITY = (
    ("session log", None),
    ("decisions_requests.jsonl", "decisions_requests.jsonl"),
    ("decisions_responses.jsonl", "decisions_responses.jsonl"),
    ("trace.jsonl", "trace.jsonl"),
    ("decisions_stream.jsonl", "decisions_stream.jsonl"),
    ("diplomacy.jsonl", "diplomacy.jsonl"),
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
