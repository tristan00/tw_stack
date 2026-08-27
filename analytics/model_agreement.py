from __future__ import annotations


import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms
from analytics import metrics as M
from analytics import store as _store
from decisions import pg_schema as S

NAME = "model_agreement"
FORMULA_VERSION = 3
SOURCE = "decisions"
DEPENDS_ON = ()
TABLES = ("model_agreement",)

PAIRS = S.PAIRS
PAIR_KEYS = tuple(S.pair_key(a, b) for a, b in PAIRS)

OK, MISSING_A, MISSING_B, NO_SCORES, TOO_FEW = ("ok", "missing_a", "missing_b",
                                                "no_scores", "too_few")

DDL = """
CREATE TABLE IF NOT EXISTS model_agreement(
  decision_id     BIGINT  NOT NULL,
  pair            TEXT    NOT NULL,
  computed_ts     DOUBLE PRECISION NOT NULL,
  ts              DOUBLE PRECISION,
  turn            INTEGER,
  campaign_id     BIGINT,
  n_offers        INTEGER NOT NULL,
  n_a             INTEGER NOT NULL,
  n_b             INTEGER NOT NULL,
  n               INTEGER NOT NULL,
  status          TEXT    NOT NULL,
  rho             DOUBLE PRECISION,
  tau_b           DOUBLE PRECISION,
  rbo             DOUBLE PRECISION,
  top1_same       INTEGER,
  top3_overlap    DOUBLE PRECISION,
  top5_overlap    DOUBLE PRECISION,
  top10_overlap   DOUBLE PRECISION,
  a_top_in_b      INTEGER,
  b_top_in_a      INTEGER,
  taken_offer_seq INTEGER,
  taken_a_rank    INTEGER,
  taken_b_rank    INTEGER,
  taken_a_pct     DOUBLE PRECISION,
  taken_b_pct     DOUBLE PRECISION,
  arm             TEXT,
  fell_back       INTEGER NOT NULL DEFAULT 0,
  action_type     TEXT,
  context_kind    TEXT,
  PRIMARY KEY(decision_id, pair)
);

CREATE INDEX IF NOT EXISTS ix_ma_pair_ts    ON model_agreement(pair, ts);
CREATE INDEX IF NOT EXISTS ix_ma_pair_rho   ON model_agreement(pair, status, rho);
CREATE INDEX IF NOT EXISTS ix_ma_pair_atype ON model_agreement(pair, status, action_type);
CREATE INDEX IF NOT EXISTS ix_ma_pair_arm   ON model_agreement(pair, status, arm);
"""

RANK_COLUMN = {"greedy_catboost": "rank", "marwil_gnn": "gnn_rank"}
MODEL_TABLE_ARMS = tuple(a for a in S.RANKED_ARMS if a not in RANK_COLUMN)

_SELECT = ("SELECT d.decision_id, d.ts, d.turn, d.campaign_id, d.n_offers,"
           "       t.offer_seq AS taken_seq, COALESCE(t.policy, d.policy) AS policy,"
           "       a.action_type, a.context_kind"
           "  FROM decisions d"
           "  LEFT JOIN taken   t ON t.decision_id = d.decision_id"
           "  LEFT JOIN actions a ON a.action_id   = t.action_id"
           " WHERE d.decision_id > %s AND d.decision_id <= %s"
           " ORDER BY d.decision_id")

_COLUMNS = ("decision_id", "pair", "computed_ts", "ts", "turn", "campaign_id", "n_offers",
            "n_a", "n_b", "n", "status", "rho", "tau_b", "rbo", "top1_same",
            "top3_overlap", "top5_overlap", "top10_overlap", "a_top_in_b", "b_top_in_a",
            "taken_offer_seq", "taken_a_rank", "taken_b_rank", "taken_a_pct", "taken_b_pct",
            "arm", "fell_back", "action_type", "context_kind")

_INSERT = ("INSERT INTO model_agreement(%s) VALUES(%s)"
           " ON CONFLICT(decision_id, pair) DO UPDATE SET %s"
           % (", ".join(_COLUMNS), ", ".join(["%s"] * len(_COLUMNS)),
              ", ".join("%s=excluded.%s" % (c, c) for c in _COLUMNS[2:])))

_BATCH = 2000


def safe_hi(src, an=None) -> int:
    row = src.execute("SELECT MAX(decision_id) m FROM decisions").fetchone()
    return max(0, int(row[0] or 0) - 1)


def source_stats(src, hi):
    row = src.execute("SELECT COUNT(*) c, MIN(decision_id) m FROM decisions"
                      " WHERE decision_id <= %s", (hi,)).fetchone()
    return int(row[0] or 0) * len(PAIRS), row[1]


def _pct(rank, n):
    if rank is None or n is None or n < 2:
        return None
    return round(100.0 * (float(rank) - 1.0) / (float(n) - 1.0), 3)


def _score_vectors(src, lo, hi):
    out = {}
    for did, seq, rank, gnn_rank in src.execute(
            "SELECT decision_id, offer_seq, rank, gnn_rank FROM offer_scores"
            " WHERE decision_id > %s AND decision_id <= %s", (lo, hi)):
        d = out.setdefault(did, {})
        d.setdefault("greedy_catboost", {})[seq] = rank
        d.setdefault("marwil_gnn", {})[seq] = gnn_rank
    for did, seq, model, rank in src.execute(
            "SELECT decision_id, offer_seq, model, rank FROM offer_model_scores"
            " WHERE decision_id > %s AND decision_id <= %s AND model = ANY(%s)",
            (lo, hi, list(MODEL_TABLE_ARMS))):
        out.setdefault(did, {}).setdefault(model, {})[seq] = rank
    return out


def rank_vectors(row, vecs) -> dict:
    n_offers = int(row["n_offers"] or 0)
    got = vecs.get(row["decision_id"]) or {}
    out = {}
    for arm in S.RANKED_ARMS:
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
    base.update(decision_id=int(row["decision_id"]), computed_ts=time.time(),
                ts=row["ts"], turn=row["turn"], campaign_id=row["campaign_id"],
                n_offers=int(row["n_offers"] or 0), n_a=0, n_b=0, n=0,
                status=NO_SCORES, top1_same=None,
                action_type=row["action_type"], context_kind=row["context_kind"],
                arm=arms.arm_of(row["policy"]),
                fell_back=(1 if arms.fell_back(row["policy"]) else 0))
    vecs = rank_vectors(row, vecs)
    seq = row["taken_seq"]
    out = []
    for a, b in PAIRS:
        rec = dict(base, pair=S.pair_key(a, b))
        va, vb = vecs.get(a), vecs.get(b)
        if va is None and vb is None:
            out.append(rec)
            continue
        ok_a = ~np.isnan(va) if va is not None else None
        ok_b = ~np.isnan(vb) if vb is not None else None
        n_a = int(ok_a.sum()) if ok_a is not None else 0
        n_b = int(ok_b.sum()) if ok_b is not None else 0
        rec.update(n_a=n_a, n_b=n_b)
        if seq is not None and 0 <= int(seq) < rec["n_offers"]:
            s = int(seq)
            rec["taken_offer_seq"] = s
            if ok_a is not None and ok_a[s]:
                rec["taken_a_rank"] = int(va[s])
                rec["taken_a_pct"] = _pct(int(va[s]), n_a)
            if ok_b is not None and ok_b[s]:
                rec["taken_b_rank"] = int(vb[s])
                rec["taken_b_pct"] = _pct(int(vb[s]), n_b)
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
        rec.update({k: v for k, v in cmp.items() if k in rec})
        rec["a_top_in_b"], rec["b_top_in_a"] = cmp["cat_top_in_gnn"], cmp["gnn_top_in_cat"]
        out.append(rec)
    return out


_CHUNK = 2000


def step(src, an, lo, hi):
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
    return hi, written
