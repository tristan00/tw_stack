from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import metrics as M
from analytics import store as _store

NAME = "model_agreement"
FORMULA_VERSION = 4
DEPENDS_ON = ()
TABLES = ("model_agreement",)

RANKED_ARMS = ("greedy_catboost", "marwil_gnn", "greedy_gnn")
PAIRS = tuple((a, b) for i, a in enumerate(RANKED_ARMS) for b in RANKED_ARMS[i + 1:])
PAIR_KEYS = tuple("%s|%s" % (a, b) for a, b in PAIRS)

OK, MISSING_A, MISSING_B, NO_SCORES, TOO_FEW = ("ok", "missing_a", "missing_b",
                                                "no_scores", "too_few")

RANK_COLUMN = {"greedy_catboost": "rank", "marwil_gnn": "gnn_rank",
               "greedy_gnn": "ggnn_rank"}

_SELECT = (
    "SELECT s.snapshot_id AS decision_id, s.ts, s.campaign_id, d.n_offers,"
    "       t.offer_seq AS taken_seq, t.policy_id, a.action_type_id,"
    "       se.kind_id AS entity_kind_id"
    "  FROM corpus.snapshot s"
    "  JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
    "  LEFT JOIN corpus.taken t ON t.decision_id = s.snapshot_id"
    "  LEFT JOIN dict.action a ON a.action_id = t.action_id"
    "  LEFT JOIN corpus.snapshot_entity se ON se.snapshot_id = s.snapshot_id"
    "   AND se.entity_seq = t.entity_seq"
    " WHERE s.snapshot_id > %s AND s.snapshot_id <= %s"
    " ORDER BY s.snapshot_id")

_COLUMNS = ("decision_id", "pair", "status", "n", "rho", "tau", "rbo",
            "top1_agree", "top5_overlap", "top10_overlap", "taken_rank_a",
            "taken_rank_b", "ts", "campaign_id", "policy_id", "action_type_id",
            "entity_kind_id")

_INSERT = ("INSERT INTO model_agreement(%s) VALUES(%s)"
           " ON CONFLICT(decision_id, pair) DO UPDATE SET %s"
           % (", ".join(_COLUMNS), ", ".join(["%s"] * len(_COLUMNS)),
              ", ".join("%s=excluded.%s" % (c, c) for c in _COLUMNS[2:])))

_CHUNK = 2000
_BATCH = 2000


def log(msg):
    sys.stderr.write("%.3f  agree %s\n" % (time.time(), msg))


def safe_hi(src, an=None) -> int:
    row = src.execute("SELECT MAX(decision_id) m FROM corpus.decision").fetchone()
    return max(0, int(row[0] or 0) - 1)


def _score_vectors(src, lo, hi):
    out = {}
    for did, seq, rank, gnn_rank, ggnn_rank in src.execute(
            "SELECT decision_id, offer_seq, rank, gnn_rank, ggnn_rank"
            " FROM corpus.offer"
            " WHERE decision_id > %s AND decision_id <= %s", (lo, hi)):
        d = out.setdefault(did, {})
        d.setdefault("greedy_catboost", {})[seq] = rank
        d.setdefault("marwil_gnn", {})[seq] = gnn_rank
        d.setdefault("greedy_gnn", {})[seq] = ggnn_rank
    return out


def rank_vectors(row, vecs) -> dict:
    n_offers = int(row["n_offers"] or 0)
    got = vecs.get(row["decision_id"]) or {}
    out = {}
    for arm in RANKED_ARMS:
        by_seq = got.get(arm)
        if by_seq is None:
            continue
        v = np.full(n_offers, np.nan)
        for seq, val in by_seq.items():
            if val is not None and 0 <= seq < n_offers:
                v[seq] = val
        out[arm] = v
    return out


def _rows(row, vecs) -> list:
    base = {c: None for c in _COLUMNS}
    base.update(decision_id=int(row["decision_id"]), ts=row["ts"],
                campaign_id=row["campaign_id"], n=0, status=NO_SCORES,
                policy_id=row["policy_id"], action_type_id=row["action_type_id"],
                entity_kind_id=row["entity_kind_id"])
    vecs = rank_vectors(row, vecs)
    seq = row["taken_seq"]
    n_offers = int(row["n_offers"] or 0)
    out = []
    for a, b in PAIRS:
        rec = dict(base, pair="%s|%s" % (a, b))
        va, vb = vecs.get(a), vecs.get(b)
        if va is None and vb is None:
            out.append(rec)
            continue
        ok_a = ~np.isnan(va) if va is not None else None
        ok_b = ~np.isnan(vb) if vb is not None else None
        n_a = int(ok_a.sum()) if ok_a is not None else 0
        n_b = int(ok_b.sum()) if ok_b is not None else 0
        if seq is not None and 0 <= int(seq) < n_offers:
            si = int(seq)
            if ok_a is not None and ok_a[si]:
                rec["taken_rank_a"] = int(va[si])
            if ok_b is not None and ok_b[si]:
                rec["taken_rank_b"] = int(vb[si])
        if n_a == 0:
            rec["status"] = MISSING_A
            out.append(rec)
            continue
        if n_b == 0:
            rec["status"] = MISSING_B
            out.append(rec)
            continue
        both = ok_a & ok_b
        n = int(both.sum())
        rec["n"] = n
        if n < M.MIN_N:
            rec["status"] = TOO_FEW
            out.append(rec)
            continue
        rec["status"] = OK
        cmp = M.compare(va[both], vb[both])
        rec.update(rho=cmp["rho"], tau=cmp["tau_b"], rbo=cmp["rbo"],
                   top1_agree=bool(cmp["top1_same"]),
                   top5_overlap=cmp.get("top5_overlap"),
                   top10_overlap=cmp.get("top10_overlap"))
        out.append(rec)
    return out


def step(src, an, lo, hi):
    t0 = time.time()
    log("step enter lo=%d hi=%d" % (lo, hi))
    written = 0
    a = lo
    while a < hi:
        b = min(a + _CHUNK, hi)
        vecs = _score_vectors(src, a, b)
        batch = []
        for row in src.execute(_SELECT, (a, b)).fetchall():
            for rec in _rows(row, vecs):
                batch.append(tuple(rec[c] for c in _COLUMNS))
            if len(batch) >= _BATCH:
                _store.executemany(an, _INSERT, batch)
                written += len(batch)
                batch = []
        if batch:
            _store.executemany(an, _INSERT, batch)
            written += len(batch)
        a = b
    log("step exit %.0f ms written=%d" % ((time.time() - t0) * 1000, written))
    return hi, written
