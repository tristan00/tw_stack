from __future__ import annotations

"""Which model generation was playing when, as an ALIGNMENT -- never as a recorded fact.

Nothing in the corpus joins a stored ranking to the weights that produced it. There is no
such column, and the two candidates that look like one are empty: `collector_versions` has
zero rows and `decisions.version_id` is NULL on every decision. So "how has agreement
tracked by model version" cannot be answered by a join. It can only be answered by matching
decision timestamps against when each retrain happened, and saying so.

The training ledger records what is needed for that. `advisor/session.py:_flush_generation`
writes a trial for generation N and THEN retrains, so a trial describes the stretch played
under the fit recorded in its own `fit` key, spanning [started, ts].

Those windows do not always tile cleanly -- two of them genuinely overlap on the current
ledger. Rather than hide that, each window is clipped to the next one's start so the join
becomes a partition (a decision lands in exactly one generation, never two), and the trial
that did the clipping is recorded in `overlapped_by`. A view built on this is required to
carry the caveat; `advisor_api` enforces that with a non-optional field.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
import metrics_db

NAME = "model_generations"
FORMULA_VERSION = 1
SOURCE = "metrics"
DEPENDS_ON = ()
REBUILD_EVERY_PASS = True

DDL = """
CREATE TABLE IF NOT EXISTS model_generations(
  trial            TEXT PRIMARY KEY,
  generation       INTEGER,
  session          TEXT,
  started_ts       REAL NOT NULL,
  ended_ts         REAL NOT NULL,
  seg_from_ts      REAL NOT NULL,
  seg_to_ts        REAL NOT NULL,
  overlapped_by    TEXT,
  retrained        INTEGER NOT NULL,
  backend          TEXT,
  feature_version  TEXT,
  corpus_decisions INTEGER,
  campaigns        INTEGER,
  computed_ts      REAL NOT NULL);

CREATE INDEX IF NOT EXISTS ix_gen_seg ON model_generations(seg_from_ts);
"""


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p or ""))).replace("\\", "/")


def _mine(row, run_dir) -> bool:
    dirs = row.get("run_dirs") or []
    if not dirs:
        return False
    return _norm(run_dir) in {_norm(d) for d in dirs}


def _windows(run_dir):
    """[(trial, started, ended, ...)] for this run dir, sorted, clipped to a partition."""
    ledger = metrics_db.trials() or []
    now = time.time()
    live = metrics_db.live_trials(ledger, now)
    rows = []
    for r in ledger:
        if not _mine(r, run_dir):
            continue
        started, ended = r.get("started"), r.get("ts")
        if started is None or ended is None:
            continue
        started, ended = float(started), float(ended)
        if ended < started:
            started, ended = ended, started
        # The generation playing now is still producing decisions. Ending its window at its
        # last ledger write would leave every decision made since outside every window, and
        # a join over them would answer for the previous model.
        if str(r.get("trial") or "") in live:
            ended = max(ended, now)
        rows.append({
            "trial": str(r.get("trial") or ""), "generation": r.get("generation"),
            "session": r.get("session"), "started_ts": started, "ended_ts": ended,
            "retrained": 1 if int(r.get("generation") or 0) > 0 else 0,
            "backend": r.get("backend"), "feature_version": r.get("feature_version"),
            "corpus_decisions": (r.get("corpus_at_train") or {}).get("n_decisions"),
            "campaigns": r.get("campaigns"),
        })
    rows.sort(key=lambda x: (x["started_ts"], x["trial"]))
    for i, w in enumerate(rows):
        w["seg_from_ts"] = w["started_ts"]
        w["seg_to_ts"] = w["ended_ts"]
        w["overlapped_by"] = None
        if i + 1 < len(rows):
            nxt = rows[i + 1]
            if nxt["started_ts"] < w["seg_to_ts"]:
                w["seg_to_ts"] = nxt["started_ts"]
                w["overlapped_by"] = nxt["trial"]
        if w["seg_to_ts"] < w["seg_from_ts"]:
            w["seg_to_ts"] = w["seg_from_ts"]
    return rows


def safe_hi(src, an=None) -> int:
    """The ledger has no row id. Its size stands in: a changed ledger means a new pass."""
    try:
        return len(metrics_db.trials() or [])
    except Exception:
        return 0


def source_stats(src, hi):
    """No count check: this table holds only the trials that touched THIS run dir, which is"""
    return None


def step(src, an, lo, hi):
    rows = _windows(common.RUN_DIR)
    an.execute("DELETE FROM model_generations")
    now = time.time()
    an.executemany(
        "INSERT INTO model_generations(trial, generation, session, started_ts, ended_ts,"
        " seg_from_ts, seg_to_ts, overlapped_by, retrained, backend, feature_version,"
        " corpus_decisions, campaigns, computed_ts)"
        " VALUES(:trial, :generation, :session, :started_ts, :ended_ts, :seg_from_ts,"
        " :seg_to_ts, :overlapped_by, :retrained, :backend, :feature_version,"
        " :corpus_decisions, :campaigns, :computed_ts)",
        [dict(r, computed_ts=now) for r in rows])
    return max(hi, len(rows)), len(rows)
