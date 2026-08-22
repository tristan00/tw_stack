from __future__ import annotations


import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms
from analytics import metrics as M
from decisions import store_schema as S

NAME = "model_agreement"
FORMULA_VERSION = 3
SOURCE = "decisions"
DEPENDS_ON = ()
TABLES = ("model_agreement",)

PAIRS = S.PAIRS
PAIR_KEYS = tuple(S.pair_key(a, b) for a, b in PAIRS)
_WIDTH = len(S.SCORE_FIELDS)
_MWIDTH = len(S.MODEL_SCORE_FIELDS)

OK, MISSING_A, MISSING_B, NO_SCORES, TOO_FEW = ("ok", "missing_a", "missing_b",
                                                "no_scores", "too_few")

DDL = """
CREATE TABLE IF NOT EXISTS model_agreement(
  decision_id     INTEGER NOT NULL,
  pair            TEXT    NOT NULL,
  computed_ts     REAL    NOT NULL,
  ts              REAL,
  turn            INTEGER,
  campaign_id     INTEGER,
  n_offers        INTEGER NOT NULL,
  n_a             INTEGER NOT NULL,
  n_b             INTEGER NOT NULL,
  n               INTEGER NOT NULL,
  status          TEXT    NOT NULL,
  rho             REAL,
  tau_b           REAL,
  rbo             REAL,
  top1_same       INTEGER,
  top3_overlap    REAL,
  top5_overlap    REAL,
  top10_overlap   REAL,
  a_top_in_b      INTEGER,
  b_top_in_a      INTEGER,
  taken_offer_seq INTEGER,
  taken_a_rank    INTEGER,
  taken_b_rank    INTEGER,
  taken_a_pct     REAL,
  taken_b_pct     REAL,
  arm             TEXT,
  fell_back       INTEGER NOT NULL DEFAULT 0,
  action_type     TEXT,
  context_kind    TEXT,
  PRIMARY KEY(decision_id, pair)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_ma_pair_ts    ON model_agreement(pair, ts);
CREATE INDEX IF NOT EXISTS ix_ma_pair_rho   ON model_agreement(pair, status, rho);
CREATE INDEX IF NOT EXISTS ix_ma_pair_atype ON model_agreement(pair, status, action_type);
CREATE INDEX IF NOT EXISTS ix_ma_pair_arm   ON model_agreement(pair, status, arm);
"""

_MODEL_TABLE_ARMS = tuple(a for a in S.RANKED_ARMS if S.RANK_SOURCE[a][0] == "model_scores")

def _select(src) -> str:
    have = {r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='model_scores'")}
    extra = "".join(
        (",       (SELECT packed FROM model_scores ms WHERE ms.decision_id ="
         " d.decision_id AND ms.model = '%s') AS ms_%s" % (arm, arm))
        if have else ",       NULL AS ms_%s" % arm
        for arm in _MODEL_TABLE_ARMS)
    return ("SELECT d.decision_id, s.packed, d.ts, d.turn, d.campaign_id, d.n_offers,"
            "       t.offer_seq AS taken_seq, COALESCE(t.policy, d.policy) AS policy,"
            "       a.action_type, a.context_kind" + extra
            + "  FROM decisions d"
            "  LEFT JOIN scores  s ON s.decision_id = d.decision_id"
            "  LEFT JOIN taken   t ON t.decision_id = d.decision_id"
            "  LEFT JOIN actions a ON a.action_id   = t.action_id"
            " WHERE d.decision_id > ? AND d.decision_id <= ?"
            " ORDER BY d.decision_id")

_COLUMNS = ("decision_id", "pair", "computed_ts", "ts", "turn", "campaign_id", "n_offers",
            "n_a", "n_b", "n", "status", "rho", "tau_b", "rbo", "top1_same",
            "top3_overlap", "top5_overlap", "top10_overlap", "a_top_in_b", "b_top_in_a",
            "taken_offer_seq", "taken_a_rank", "taken_b_rank", "taken_a_pct", "taken_b_pct",
            "arm", "fell_back", "action_type", "context_kind")

_INSERT = ("INSERT INTO model_agreement(%s) VALUES(%s)"
           " ON CONFLICT(decision_id, pair) DO UPDATE SET %s"
           % (", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS)),
              ", ".join("%s=excluded.%s" % (c, c) for c in _COLUMNS[2:])))

_BATCH = 2000


def safe_hi(src, an=None) -> int:
    row = src.execute("SELECT MAX(decision_id) FROM decisions").fetchone()
    return max(0, int(row[0] or 0) - 1)


def source_stats(src, hi):
    row = src.execute("SELECT COUNT(*), MIN(decision_id) FROM decisions"
                      " WHERE decision_id <= ?", (hi,)).fetchone()
    return int(row[0] or 0) * len(PAIRS), row[1]


def _pct(rank, n):
    if rank is None or n is None or n < 2:
        return None
    return round(100.0 * (float(rank) - 1.0) / (float(n) - 1.0), 3)


def _column(packed, width, col, n_offers):
    if packed is None:
        return None
    buf = np.frombuffer(packed, dtype="<f4")
    if buf.size == 0 or buf.size % width or buf.size // width != n_offers:
        return None
    return buf.reshape(-1, width)[:, col].astype(np.float64)


def rank_vectors(row) -> dict:
    n_offers = int(row["n_offers"] or 0)
    out = {}
    for arm in S.RANKED_ARMS:
        table, col = S.RANK_SOURCE[arm]
        packed = row["packed"] if table == "scores" else row["ms_%s" % arm]
        v = _column(packed, _WIDTH if table == "scores" else _MWIDTH, col, n_offers)
        if v is not None:
            out[arm] = v
    return out


def _rows(row) -> list:
    base = {c: None for c in _COLUMNS}
    base.update(decision_id=int(row["decision_id"]), computed_ts=time.time(),
                ts=row["ts"], turn=row["turn"], campaign_id=row["campaign_id"],
                n_offers=int(row["n_offers"] or 0), n_a=0, n_b=0, n=0,
                status=NO_SCORES, top1_same=None,
                action_type=row["action_type"], context_kind=row["context_kind"],
                arm=arms.arm_of(row["policy"]),
                fell_back=(1 if arms.fell_back(row["policy"]) else 0))
    vecs = rank_vectors(row)
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


def step(src, an, lo, hi):
    cur = src.execute(_select(src), (lo, hi))
    batch, written = [], 0
    for row in cur:
        for rec in _rows(row):
            batch.append(tuple(rec[c] for c in _COLUMNS))
        if len(batch) >= _BATCH:
            an.executemany(_INSERT, batch)
            written += len(batch)
            batch = []
    if batch:
        an.executemany(_INSERT, batch)
        written += len(batch)
    return hi, written
