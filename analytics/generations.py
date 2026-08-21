from __future__ import annotations


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
        if str(r.get("trial") or "") in live:
            ended = max(ended, now)
        rows.append({
            "trial": str(r.get("trial") or ""), "generation": r.get("generation"),
            "session": r.get("session"), "started_ts": started, "ended_ts": ended,
            "retrained": 1 if int(r.get("generation") or 0) > 0 else 0,
            "feature_version": r.get("feature_version"),
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
    try:
        return len(metrics_db.trials() or [])
    except Exception:
        return 0


def source_stats(src, hi):
    return None


def step(src, an, lo, hi):
    rows = _windows(common.RUN_DIR)
    an.execute("DELETE FROM model_generations")
    now = time.time()
    an.executemany(
        "INSERT INTO model_generations(trial, generation, session, started_ts, ended_ts,"
        " seg_from_ts, seg_to_ts, overlapped_by, retrained, feature_version,"
        " corpus_decisions, campaigns, computed_ts)"
        " VALUES(:trial, :generation, :session, :started_ts, :ended_ts, :seg_from_ts,"
        " :seg_to_ts, :overlapped_by, :retrained, :feature_version,"
        " :corpus_decisions, :campaigns, :computed_ts)",
        [dict(r, computed_ts=now) for r in rows])
    return max(hi, len(rows)), len(rows)


GAINS_MIN_TRIALS = 10

_GAINS_SQL = """
SELECT campaign_key, first_ts, settlements_gained, levels_gained,
       allies_gained, vassals_gained
FROM campaign_gains
"""


def _ro(path):
    import sqlite3
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=5)


def generation_gains(run_dir=None, min_trials=GAINS_MIN_TRIALS):
    run_dir = common.native(run_dir or common.RUN_DIR)
    an = _ro(os.path.join(run_dir, "analytics.sqlite"))
    trains = an.execute("SELECT trial, seg_from_ts, corpus_decisions FROM model_generations"
                        " WHERE retrained=1 ORDER BY seg_from_ts").fetchall()
    an.close()
    models = [("baseline", float("-inf"), None)] + [tuple(r) for r in trains]
    from decisions import dbopen
    con = dbopen.connect(os.path.join(run_dir, "decisions.sqlite"), timeout=5)
    camps = con.execute(_GAINS_SQL).fetchall()
    con.close()
    agg = {}
    for cid, t0, sg, lg, ag2, vg in camps:
        if t0 is None:
            continue
        model = None
        for trial, lo, corpus in models:
            if t0 >= lo:
                model = (trial, lo, corpus)
        b = agg.setdefault(model, {"n": 0, "s": 0.0, "l": 0.0, "a": 0.0, "v": 0.0})
        b["n"] += 1
        b["s"] += sg or 0
        b["l"] += lg or 0
        b["a"] += ag2 or 0
        b["v"] += vg or 0
    out = []
    for (trial, lo, corpus), b in sorted(agg.items(), key=lambda kv: kv[0][1]):
        if b["n"] < min_trials:
            continue
        out.append({"trial": trial, "trained_ts": None if lo == float("-inf") else lo,
                    "train_records": corpus, "n": b["n"],
                    "settlements_gained": round(b["s"] / b["n"], 2),
                    "levels_gained": round(b["l"] / b["n"], 2),
                    "allies_gained": round(b["a"] / b["n"], 2),
                    "vassals_gained": round(b["v"] / b["n"], 2)})
    return out


def _gains_table(rows):
    head = ("%-24s %-12s %10s %5s %12s %8s %8s %8s"
            % ("model (retrain)", "trained", "train_rows", "n", "settlements", "levels",
               "allies", "vassals"))
    lines = [head]
    for r in rows:
        day = (time.strftime("%m-%d %H:%M", time.localtime(r["trained_ts"]))
               if r["trained_ts"] else "-")
        lines.append("%-24s %-12s %10s %5d %12.2f %8.2f %8.2f %8.2f"
                     % (r["trial"][:24], day,
                        r["train_records"] if r["train_records"] is not None else "-",
                        r["n"], r["settlements_gained"], r["levels_gained"],
                        r["allies_gained"], r["vassals_gained"]))
    return lines


if __name__ == "__main__":
    for _line in _gains_table(generation_gains()):
        print(_line)
