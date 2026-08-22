from __future__ import annotations


import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import store as _store
from decisions import store_schema as S

FORMULA_VERSION = 3

TARGET_POINTS = 40
MIN_BUCKET = 50

PAIR_KEYS = tuple(S.pair_key(a, b) for a, b in S.PAIRS)


def _pct_at(sorted_vals, p):
    if not sorted_vals:
        return None
    return float(sorted_vals[int(round(p * (len(sorted_vals) - 1)))])


def _spread(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None, None, None, None
    return (float(statistics.median(v)), float(statistics.fmean(v)),
            _pct_at(v, 0.25), _pct_at(v, 0.75))


def _mean(vals):
    v = [x for x in vals if x is not None]
    return float(statistics.fmean(v)) if v else None


def _med(vals):
    v = sorted(x for x in vals if x is not None)
    return float(statistics.median(v)) if v else None


def bucket_size(comparable: int) -> int:
    return max(MIN_BUCKET, -(-comparable // TARGET_POINTS)) if comparable else MIN_BUCKET


def min_decisions(size: int) -> int:
    return max(10, size // 2)


class _Rollup:
    SOURCE = "analytics"
    FORMULA_VERSION = FORMULA_VERSION
    DEPENDS_ON = ("model_agreement",)

    def safe_hi(self, src, an=None):
        return int(_store.state(an, self.DEPENDS_ON[0])["watermark"])

    def source_stats(self, src, hi):
        return None


_SUMMARY_DDL = """
CREATE TABLE IF NOT EXISTS agreement_summary(
  pair TEXT NOT NULL, scope TEXT NOT NULL, computed_ts REAL NOT NULL,
  decisions INTEGER, comparable INTEGER,
  missing_a INTEGER, missing_b INTEGER, too_few INTEGER, no_scores INTEGER,
  rho_median REAL, rho_mean REAL, rho_q1 REAL, rho_q3 REAL,
  tau_median REAL, tau_mean REAL, rbo_mean REAL,
  top1_same INTEGER, top3_mean REAL, top5_mean REAL, top10_mean REAL,
  overlap_median REAL, from_decision INTEGER, to_decision INTEGER,
  fell_back INTEGER, ambiguous INTEGER,
  PRIMARY KEY(pair, scope));

CREATE TABLE IF NOT EXISTS agreement_hist(
  pair TEXT NOT NULL, bucket INTEGER NOT NULL, lo REAL NOT NULL, hi REAL NOT NULL,
  decisions INTEGER NOT NULL,
  PRIMARY KEY(pair, bucket));
"""

HIST_BINS = 20


class _Summary(_Rollup):
    NAME = "agreement_summary"
    TABLES = ("agreement_summary", "agreement_hist")
    DDL = _SUMMARY_DDL
    DEPENDS_ON = ("model_agreement", "model_generations")

    def step(self, src, an, lo, hi):
        an.execute("DELETE FROM agreement_summary")
        an.execute("DELETE FROM agreement_hist")
        total = 0
        for pair in PAIR_KEYS:
            counts = {r["status"]: int(r["n"]) for r in an.execute(
                "SELECT status, COUNT(*) n FROM model_agreement WHERE pair=?"
                " GROUP BY status", (pair,))}
            rows = an.execute(
                "SELECT decision_id, rho, tau_b, rbo, top1_same, top3_overlap, top5_overlap,"
                " top10_overlap, n FROM model_agreement WHERE pair=? AND status='ok'"
                " ORDER BY decision_id", (pair,)).fetchall()
            rho_m, rho_mean, q1, q3 = _spread([r["rho"] for r in rows])
            tau_m, tau_mean, _, _ = _spread([r["tau_b"] for r in rows])
            ov = sorted(int(r["n"]) for r in rows)
            fell = int(an.execute(
                "SELECT COUNT(*) FROM model_agreement WHERE pair=? AND fell_back=1",
                (pair,)).fetchone()[0])
            an.execute(
                "INSERT INTO agreement_summary VALUES"
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pair, "all", time.time(), sum(counts.values()), len(rows),
                 counts.get("missing_a", 0), counts.get("missing_b", 0),
                 counts.get("too_few", 0), counts.get("no_scores", 0),
                 rho_m, rho_mean, q1, q3, tau_m, tau_mean, _mean([r["rbo"] for r in rows]),
                 sum(1 for r in rows if r["top1_same"]),
                 _mean([r["top3_overlap"] for r in rows]),
                 _mean([r["top5_overlap"] for r in rows]),
                 _mean([r["top10_overlap"] for r in rows]),
                 (float(statistics.median(ov)) if ov else None),
                 (rows[0]["decision_id"] if rows else None),
                 (rows[-1]["decision_id"] if rows else None),
                 fell, _ambiguous(an, pair)))
            width = 2.0 / HIST_BINS
            hist = [0] * HIST_BINS
            for r in rows:
                if r["rho"] is None:
                    continue
                b = min(HIST_BINS - 1, max(0, int((float(r["rho"]) + 1.0) / width)))
                hist[b] += 1
            an.executemany("INSERT INTO agreement_hist VALUES(?,?,?,?,?)",
                           [(pair, i, -1.0 + i * width, -1.0 + (i + 1) * width, c)
                            for i, c in enumerate(hist)])
            total += len(rows)
        return hi, total


def _ambiguous(an, pair) -> int:
    try:
        return int(an.execute(
            "SELECT COUNT(*) FROM (SELECT a.decision_id FROM model_agreement a"
            " JOIN model_generations g ON a.ts >= g.started_ts AND a.ts < g.ended_ts"
            " WHERE a.pair=? AND a.status='ok' GROUP BY a.decision_id HAVING COUNT(*) > 1)",
            (pair,)).fetchone()[0])
    except Exception:
        return 0


_SERIES_DDL = """
CREATE TABLE IF NOT EXISTS agreement_series(
  pair TEXT NOT NULL, axis TEXT NOT NULL, seq INTEGER NOT NULL, label TEXT,
  from_decision INTEGER, to_decision INTEGER, from_ts REAL, to_ts REAL,
  decisions INTEGER NOT NULL,
  rho_median REAL, rho_mean REAL, rho_q1 REAL, rho_q3 REAL,
  tau_mean REAL, rbo_mean REAL, same_top INTEGER, gate TEXT,
  trial TEXT, generation INTEGER, retrained INTEGER, overlapped_by TEXT,
  bucket_decisions INTEGER,
  PRIMARY KEY(pair, axis, seq));
"""


class _Series(_Rollup):
    NAME = "agreement_series"
    TABLES = ("agreement_series",)
    DDL = _SERIES_DDL
    DEPENDS_ON = ("model_agreement", "model_generations")

    def step(self, src, an, lo, hi):
        an.execute("DELETE FROM agreement_series")
        total = 0
        gens = an.execute("SELECT * FROM model_generations ORDER BY seg_from_ts").fetchall()
        for pair in PAIR_KEYS:
            rows = an.execute(
                "SELECT decision_id, ts, rho, tau_b, rbo, top1_same FROM model_agreement"
                " WHERE pair=? AND status='ok' ORDER BY decision_id", (pair,)).fetchall()
            size = bucket_size(len(rows))
            gate = min_decisions(size)
            out = []
            for seq, i in enumerate(range(0, len(rows), size)):
                out.append(_point(pair, "window", seq, None, rows[i:i + size], gate))
            for seq, g in enumerate(gens):
                chunk = an.execute(
                    "SELECT decision_id, ts, rho, tau_b, rbo, top1_same FROM model_agreement"
                    " WHERE pair=? AND status='ok' AND ts >= ? AND ts < ?"
                    " ORDER BY decision_id",
                    (pair, g["seg_from_ts"], g["seg_to_ts"])).fetchall()
                p = _point(pair, "generation", seq, g["trial"], chunk, gate)
                p.update(trial=g["trial"], generation=g["generation"],
                         retrained=g["retrained"], overlapped_by=g["overlapped_by"],
                         from_ts=g["seg_from_ts"], to_ts=g["seg_to_ts"])
                out.append(p)
            an.executemany(
                "INSERT INTO agreement_series(pair, axis, seq, label, from_decision,"
                " to_decision, from_ts, to_ts, decisions, rho_median, rho_mean, rho_q1,"
                " rho_q3, tau_mean, rbo_mean, same_top, gate, trial, generation, retrained,"
                " overlapped_by, bucket_decisions)"
                " VALUES(:pair,:axis,:seq,:label,:from_decision,:to_decision,:from_ts,"
                ":to_ts,:decisions,:rho_median,:rho_mean,:rho_q1,:rho_q3,:tau_mean,"
                ":rbo_mean,:same_top,:gate,:trial,:generation,:retrained,:overlapped_by,"
                ":bucket_decisions)",
                [dict(p, bucket_decisions=size) for p in out])
            total += len(out)
        return hi, total


def _point(pair, axis, seq, label, chunk, gate) -> dict:
    p = {"pair": pair, "axis": axis, "seq": seq, "label": label, "trial": None,
         "generation": None, "retrained": None, "overlapped_by": None,
         "from_decision": (chunk[0]["decision_id"] if chunk else None),
         "to_decision": (chunk[-1]["decision_id"] if chunk else None),
         "from_ts": (chunk[0]["ts"] if chunk else None),
         "to_ts": (chunk[-1]["ts"] if chunk else None),
         "decisions": len(chunk), "same_top": sum(1 for r in chunk if r["top1_same"]),
         "rho_median": None, "rho_mean": None, "rho_q1": None, "rho_q3": None,
         "tau_mean": None, "rbo_mean": None, "gate": None}
    if len(chunk) < gate:
        p["gate"] = "%d of %d needed" % (len(chunk), gate)
        return p
    m, mean, q1, q3 = _spread([r["rho"] for r in chunk])
    p.update(rho_median=m, rho_mean=mean, rho_q1=q1, rho_q3=q3,
             tau_mean=_mean([r["tau_b"] for r in chunk]),
             rbo_mean=_mean([r["rbo"] for r in chunk]))
    return p


_BREAKDOWN_DDL = """
CREATE TABLE IF NOT EXISTS agreement_breakdown(
  pair TEXT NOT NULL, dim TEXT NOT NULL, key TEXT NOT NULL, decisions INTEGER NOT NULL,
  rho_median REAL, rho_mean REAL, tau_mean REAL, rbo_mean REAL, same_top INTEGER,
  a_rank REAL, a_pct REAL, b_rank REAL, b_pct REAL, delta_pct REAL,
  fell_back INTEGER,
  PRIMARY KEY(pair, dim, key));
"""

DIMS = ("arm", "action_type", "context_kind")


class _Breakdown(_Rollup):
    NAME = "agreement_breakdown"
    TABLES = ("agreement_breakdown",)
    DDL = _BREAKDOWN_DDL

    def step(self, src, an, lo, hi):
        an.execute("DELETE FROM agreement_breakdown")
        out = []
        for pair in PAIR_KEYS:
            for dim in DIMS:
                groups = {}
                for r in an.execute(
                        "SELECT %s k, rho, tau_b, rbo, top1_same, taken_a_rank, taken_a_pct,"
                        " taken_b_rank, taken_b_pct, fell_back FROM model_agreement"
                        " WHERE pair=? AND status='ok' AND %s IS NOT NULL" % (dim, dim),
                        (pair,)):
                    groups.setdefault(r["k"], []).append(r)
                for k, rs in groups.items():
                    m, mean, _, _ = _spread([r["rho"] for r in rs])
                    a_pct = _med([r["taken_a_pct"] for r in rs])
                    b_pct = _med([r["taken_b_pct"] for r in rs])
                    out.append((
                        pair, dim, str(k), len(rs), m, mean,
                        _mean([r["tau_b"] for r in rs]), _mean([r["rbo"] for r in rs]),
                        sum(1 for r in rs if r["top1_same"]),
                        _med([r["taken_a_rank"] for r in rs]), a_pct,
                        _med([r["taken_b_rank"] for r in rs]), b_pct,
                        (None if a_pct is None or b_pct is None else b_pct - a_pct),
                        sum(1 for r in rs if r["fell_back"])))
        an.executemany(
            "INSERT INTO agreement_breakdown VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
        return hi, len(out)


SUMMARY = _Summary()
SERIES = _Series()
BREAKDOWN = _Breakdown()

TENANTS = (SUMMARY, SERIES, BREAKDOWN)
