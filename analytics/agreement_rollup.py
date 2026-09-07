from __future__ import annotations

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms
from analytics import store as _store
from analytics.model_agreement import PAIR_KEYS

FORMULA_VERSION = 6

TARGET_POINTS = 40
MIN_BUCKET = 50
HIST_BINS = 20


def log(msg):
    sys.stderr.write("%.3f  rollup %s\n" % (time.time(), msg))


def _med(vals):
    v = sorted(x for x in vals if x is not None)
    return float(statistics.median(v)) if v else None


def _mean(vals):
    v = [x for x in vals if x is not None]
    return float(statistics.fmean(v)) if v else None


def _quartiles(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None, None
    at = lambda p: float(v[int(round(p * (len(v) - 1)))])
    return at(0.25), at(0.75)


def _pct(rank, n):
    if rank is None or n is None or n < 2:
        return None
    return 100.0 * (float(rank) - 1.0) / (float(n) - 1.0)


def bucket_size(comparable: int) -> int:
    return max(MIN_BUCKET, -(-comparable // TARGET_POINTS)) if comparable else MIN_BUCKET


def min_decisions(size: int) -> int:
    return max(10, size // 2)


class _Rollup:
    FORMULA_VERSION = FORMULA_VERSION
    DEPENDS_ON = ("model_agreement",)

    def safe_hi(self, src, an=None):
        return int(_store.state(an, "model_agreement")["watermark"])


class _Summary(_Rollup):
    NAME = "agreement_summary"
    TABLES = ("agreement_summary", "agreement_hist")

    def step(self, src, an, lo, hi):
        t0 = time.time()
        an.execute("DELETE FROM agreement_summary")
        an.execute("DELETE FROM agreement_hist")
        total = 0
        for pair in PAIR_KEYS:
            counts = {r["status"]: int(r["n"]) for r in an.execute(
                "SELECT status, COUNT(*) n FROM model_agreement WHERE pair=%s"
                " GROUP BY status", (pair,))}
            rows = an.execute(
                "SELECT rho, tau, rbo, top1_agree FROM model_agreement"
                " WHERE pair=%s AND status='ok' ORDER BY decision_id",
                (pair,)).fetchall()
            comparable = len(rows)
            top1 = sum(1 for r in rows if r["top1_agree"])
            q1, q3 = _quartiles([r["rho"] for r in rows])
            an.execute(
                "INSERT INTO agreement_summary(pair, scope, comparable, rho_median,"
                " rho_mean, rho_q1, rho_q3, tau_median, rbo_median, top1_rate,"
                " missing_b, no_scores)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pair, "all", comparable,
                 _med([r["rho"] for r in rows]), _mean([r["rho"] for r in rows]),
                 q1, q3,
                 _med([r["tau"] for r in rows]), _med([r["rbo"] for r in rows]),
                 (top1 / comparable if comparable else None),
                 counts.get("missing_b", 0), counts.get("no_scores", 0)))
            width = 2.0 / HIST_BINS
            hist = [0] * HIST_BINS
            for r in rows:
                if r["rho"] is None:
                    continue
                b = min(HIST_BINS - 1, max(0, int((float(r["rho"]) + 1.0) / width)))
                hist[b] += 1
            _store.executemany(
                an, "INSERT INTO agreement_hist VALUES(%s,%s,%s,%s,%s)",
                [(pair, i, -1.0 + i * width, -1.0 + (i + 1) * width, c)
                 for i, c in enumerate(hist)])
            total += comparable
        log("summary exit %.0f ms comparable=%d" % ((time.time() - t0) * 1000, total))
        return hi, total


def model_version_windows(gens) -> list:
    out = []
    for g in gens:
        retrained = (g["generation"] or 0) > 0
        if retrained or not out:
            out.append({
                "trial": (g["trial"] if retrained else "before-%s" % g["trial"]),
                "generation": g["generation"], "retrained": retrained,
                "seg_from_ts": g["seg_from_ts"], "seg_to_ts": g["seg_to_ts"]})
        else:
            out[-1]["seg_to_ts"] = g["seg_to_ts"]
    return out


class _Series(_Rollup):
    NAME = "agreement_series"
    TABLES = ("agreement_series",)

    def step(self, src, an, lo, hi):
        t0 = time.time()
        an.execute("DELETE FROM agreement_series")
        total = 0
        gens = model_version_windows(an.execute(
            "SELECT trial, generation, seg_from_ts,"
            " COALESCE(seg_to_ts, 'infinity'::float8) seg_to_ts"
            " FROM model_generation ORDER BY seg_from_ts").fetchall())
        for pair in PAIR_KEYS:
            rows = an.execute(
                "SELECT decision_id, ts, rho FROM model_agreement"
                " WHERE pair=%s AND status='ok' ORDER BY decision_id",
                (pair,)).fetchall()
            size = bucket_size(len(rows))
            gate = min_decisions(size)
            out = []
            for seq, i in enumerate(range(0, len(rows), size)):
                out.append(_point(pair, "window", seq, rows[i:i + size], gate))
            for seq, g in enumerate(gens):
                chunk = [r for r in rows
                         if g["seg_from_ts"] <= (r["ts"] or 0) < g["seg_to_ts"]]
                p = _point(pair, "generation", seq, chunk, gate)
                p.update(trial=g["trial"], generation=g["generation"],
                         retrained=g["retrained"], from_ts=g["seg_from_ts"])
                out.append(p)
            _store.executemany(
                an,
                "INSERT INTO agreement_series(pair, axis, seq, from_decision,"
                " to_decision, from_ts, decisions, rho_median, rho_q1, rho_q3,"
                " gate, trial, generation, retrained, bucket_size)"
                " VALUES(%(pair)s,%(axis)s,%(seq)s,%(from_decision)s,"
                "%(to_decision)s,%(from_ts)s,%(decisions)s,%(rho_median)s,"
                "%(rho_q1)s,%(rho_q3)s,"
                "%(gate)s,%(trial)s,%(generation)s,%(retrained)s,%(bucket_size)s)",
                [dict(p, bucket_size=size) for p in out])
            total += len(out)
        log("series exit %.0f ms points=%d" % ((time.time() - t0) * 1000, total))
        return hi, total


def _point(pair, axis, seq, chunk, gate) -> dict:
    p = {"pair": pair, "axis": axis, "seq": seq, "trial": None,
         "generation": None, "retrained": None,
         "from_decision": (chunk[0]["decision_id"] if chunk else None),
         "to_decision": (chunk[-1]["decision_id"] if chunk else None),
         "from_ts": (chunk[0]["ts"] if chunk else None),
         "decisions": len(chunk), "rho_median": None, "rho_q1": None,
         "rho_q3": None, "gate": None}
    if len(chunk) < gate:
        p["gate"] = "%d of %d needed" % (len(chunk), gate)
        return p
    p["rho_median"] = _med([r["rho"] for r in chunk])
    p["rho_q1"], p["rho_q3"] = _quartiles([r["rho"] for r in chunk])
    return p


DIM_COLS = "a.rho, a.top1_agree, a.taken_rank_a, a.taken_rank_b, a.n_a, a.n_b"

DIM_SQL = {
    "arm": ("SELECT pp.key k, " + DIM_COLS + " FROM model_agreement a"
            " JOIN dict.enum pp ON pp.enum_id = a.policy_id"
            " WHERE a.pair=%s AND a.status='ok'"),
    "action_type": ("SELECT at.key k, " + DIM_COLS + " FROM model_agreement a"
                    " JOIN dict.action_type at ON at.id = a.action_type_id"
                    " WHERE a.pair=%s AND a.status='ok'"),
    "context_kind": ("SELECT ek.key k, " + DIM_COLS + " FROM model_agreement a"
                     " JOIN dict.enum ek ON ek.enum_id = a.entity_kind_id"
                     " WHERE a.pair=%s AND a.status='ok'"),
}


class _Breakdown(_Rollup):
    NAME = "agreement_breakdown"
    TABLES = ("agreement_breakdown",)
    DEPENDS_ON = ("model_agreement",)

    def step(self, src, an, lo, hi):
        t0 = time.time()
        an.execute("DELETE FROM agreement_breakdown")
        out = []
        for pair in PAIR_KEYS:
            for dim, sql in DIM_SQL.items():
                groups: dict = {}
                for r in an.execute(sql, (pair,)):
                    key = arms.arm_of(r["k"]) or r["k"] if dim == "arm" else r["k"]
                    groups.setdefault(key, []).append(r)
                for k, rs in groups.items():
                    top1 = sum(1 for r in rs if r["top1_agree"])
                    a_pct = _med([_pct(r["taken_rank_a"], r["n_a"]) for r in rs])
                    b_pct = _med([_pct(r["taken_rank_b"], r["n_b"]) for r in rs])
                    out.append((pair, dim, str(k), len(rs),
                                _med([r["rho"] for r in rs]),
                                top1 / len(rs) if rs else None,
                                _med([r["taken_rank_a"] for r in rs]), a_pct,
                                _med([r["taken_rank_b"] for r in rs]), b_pct,
                                (None if a_pct is None or b_pct is None
                                 else b_pct - a_pct),
                                sum(1 for r in rs if arms.fell_back(r["k"]))))
        _store.executemany(
            an, "INSERT INTO agreement_breakdown VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s,%s)", out)
        log("breakdown exit %.0f ms rows=%d" % ((time.time() - t0) * 1000, len(out)))
        return hi, len(out)


SUMMARY = _Summary()
SERIES = _Series()
BREAKDOWN = _Breakdown()

TENANTS = (SUMMARY, SERIES, BREAKDOWN)
