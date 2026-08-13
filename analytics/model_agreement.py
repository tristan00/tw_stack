from __future__ import annotations

"""How alike the two models ranked each decision. One row per decision.

This is the restoration of a metric that was deleted with the old dashboard in 7a2d3d0.
The rewrite kept the agreement panel but dropped every rank correlation, leaving only where
each model ranked the single action that was taken -- which cannot answer whether the two
models are ranking on the same thing at all.

WHY IT READS BLOBS AND NOT THE VIEW. `action_offers` unpacks `scores.packed` with seven
correlated `f32()` subqueries per row, each re-reading the whole blob (store_schema.py says
so out loud). Over this corpus that path costs 7.6 seconds. Reading the blobs directly and
reshaping with numpy costs about 1.2 seconds for every metric on every decision -- roughly
0.2 ms each. The view is the right tool for reading a handful of offers; it is the wrong
tool for touching every offer in the corpus.

EVERY DECISION GETS A ROW. A decision that cannot be compared gets one whose `status` says
why. "Computed" and "comparable" are then separate facts, and neither has to be inferred
from the other's absence -- the mistake that made the old campaign growth column
unreadable, where a blank meant four different things.
"""

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arms
from analytics import metrics as M
from decisions import store_schema as S

NAME = "model_agreement"
# 2: the stored `arm` is the canonical strategy name, so rows folded before the arms were
#    renamed carry values no page looks up any more. Bumping wipes and recomputes rather
#    than leaving two spellings of one arm in one column.
FORMULA_VERSION = 2
SOURCE = "decisions"
DEPENDS_ON = ()

# Resolved from the collector's own field list, never written as literals. If a score field
# is ever inserted, these move with it instead of silently reading the neighbouring column.
_CAT = S.SCORE_FIELDS.index("rank")
_GNN = S.SCORE_FIELDS.index("gnn_rank")
_WIDTH = len(S.SCORE_FIELDS)

OK, NO_GNN, NO_SCORES, TOO_FEW = "ok", "no_gnn", "no_scores", "too_few"

DDL = """
CREATE TABLE IF NOT EXISTS model_agreement(
  decision_id     INTEGER PRIMARY KEY,
  computed_ts     REAL    NOT NULL,
  ts              REAL,
  turn            INTEGER,
  campaign_id     INTEGER,
  n_offers        INTEGER NOT NULL,
  n_cat           INTEGER NOT NULL,
  n_gnn           INTEGER NOT NULL,
  n               INTEGER NOT NULL,
  status          TEXT    NOT NULL,
  rho             REAL,
  tau_b           REAL,
  rbo             REAL,
  top1_same       INTEGER,
  top3_overlap    REAL,
  top5_overlap    REAL,
  top10_overlap   REAL,
  cat_top_in_gnn  INTEGER,
  gnn_top_in_cat  INTEGER,
  taken_offer_seq INTEGER,
  taken_cat_rank  INTEGER,
  taken_gnn_rank  INTEGER,
  taken_cat_pct   REAL,
  taken_gnn_pct   REAL,
  arm             TEXT,
  fell_back       INTEGER NOT NULL DEFAULT 0,
  action_type     TEXT,
  context_kind    TEXT
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_ma_ts     ON model_agreement(ts);
CREATE INDEX IF NOT EXISTS ix_ma_rho    ON model_agreement(status, rho);
CREATE INDEX IF NOT EXISTS ix_ma_atype  ON model_agreement(status, action_type);
CREATE INDEX IF NOT EXISTS ix_ma_arm    ON model_agreement(status, arm);
"""

_SELECT = """
SELECT d.decision_id, s.packed, d.ts, d.turn, d.campaign_id, d.n_offers,
       t.offer_seq AS taken_seq, COALESCE(t.policy, d.policy) AS policy,
       a.action_type, a.context_kind
  FROM decisions d
  LEFT JOIN scores  s ON s.decision_id = d.decision_id
  LEFT JOIN taken   t ON t.decision_id = d.decision_id
  LEFT JOIN actions a ON a.action_id   = t.action_id
 WHERE d.decision_id > ? AND d.decision_id <= ?
 ORDER BY d.decision_id
"""

_COLUMNS = ("decision_id", "computed_ts", "ts", "turn", "campaign_id", "n_offers",
            "n_cat", "n_gnn", "n", "status", "rho", "tau_b", "rbo", "top1_same",
            "top3_overlap", "top5_overlap", "top10_overlap", "cat_top_in_gnn",
            "gnn_top_in_cat", "taken_offer_seq", "taken_cat_rank", "taken_gnn_rank",
            "taken_cat_pct", "taken_gnn_pct", "arm", "fell_back", "action_type",
            "context_kind")

_INSERT = ("INSERT INTO model_agreement(%s) VALUES(%s)"
           " ON CONFLICT(decision_id) DO UPDATE SET %s"
           % (", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS)),
              ", ".join("%s=excluded.%s" % (c, c) for c in _COLUMNS[1:])))

_BATCH = 2000


def safe_hi(src, an=None) -> int:
    """The newest decision is held back by one."""
    row = src.execute("SELECT MAX(decision_id) FROM decisions").fetchone()
    return max(0, int(row[0] or 0) - 1)


def source_stats(src, hi):
    row = src.execute("SELECT COUNT(*), MIN(decision_id) FROM decisions"
                      " WHERE decision_id <= ?", (hi,)).fetchone()
    return int(row[0] or 0), row[1]


def _pct(rank, n):
    """Where a rank sits as a percentile, so decisions of different sizes compare."""
    if rank is None or n is None or n < 2:
        return None
    return round(100.0 * (float(rank) - 1.0) / (float(n) - 1.0), 3)


def _one(row) -> dict:
    """The full fact row for one decision. Pure apart from the clock."""
    out = {c: None for c in _COLUMNS}
    out.update(decision_id=int(row["decision_id"]), computed_ts=time.time(),
               ts=row["ts"], turn=row["turn"], campaign_id=row["campaign_id"],
               n_offers=int(row["n_offers"] or 0), n_cat=0, n_gnn=0, n=0,
               status=NO_SCORES, top1_same=None, fell_back=0,
               action_type=row["action_type"], context_kind=row["context_kind"])
    out["arm"] = arms.arm_of(row["policy"])
    out["fell_back"] = 1 if arms.fell_back(row["policy"]) else 0

    packed = row["packed"]
    if packed is None:
        return out
    buf = np.frombuffer(packed, dtype="<f4")
    if buf.size == 0 or buf.size % _WIDTH:
        # Never reshaped on a guess: a blob that is not a whole number of offers is a
        # corrupt record, and silently truncating it would produce a plausible ranking.
        return out
    m = buf.reshape(-1, _WIDTH)
    cat = m[:, _CAT].astype(np.float64)
    gnn = m[:, _GNN].astype(np.float64)
    ok_cat, ok_gnn = ~np.isnan(cat), ~np.isnan(gnn)
    both = ok_cat & ok_gnn
    n_cat, n_gnn, n = int(ok_cat.sum()), int(ok_gnn.sum()), int(both.sum())
    out.update(n_cat=n_cat, n_gnn=n_gnn, n=n)

    seq = row["taken_seq"]
    if seq is not None and 0 <= int(seq) < m.shape[0]:
        s = int(seq)
        out["taken_offer_seq"] = s
        if ok_cat[s]:
            out["taken_cat_rank"] = int(cat[s])
            out["taken_cat_pct"] = _pct(int(cat[s]), n_cat)
        if ok_gnn[s]:
            out["taken_gnn_rank"] = int(gnn[s])
            out["taken_gnn_pct"] = _pct(int(gnn[s]), n_gnn)

    if n_gnn == 0:
        out["status"] = NO_GNN
        return out
    if n < M.MIN_N:
        out["status"] = TOO_FEW
        return out
    out["status"] = OK
    out.update({k: v for k, v in M.compare(cat[both], gnn[both]).items() if k in out})
    return out


def step(src, an, lo, hi):
    """Fold every decision in (lo, hi] into the table. Streams; never loads the corpus."""
    cur = src.execute(_SELECT, (lo, hi))
    batch, written = [], 0
    for row in cur:
        rec = _one(row)
        batch.append(tuple(rec[c] for c in _COLUMNS))
        if len(batch) >= _BATCH:
            an.executemany(_INSERT, batch)
            written += len(batch)
            batch = []
    if batch:
        an.executemany(_INSERT, batch)
        written += len(batch)
    return hi, written
