from __future__ import annotations

"""Per-campaign growth. One row per campaign, from the shared definition.

The formula is not here. It is in the root `campaign_growth.py`, imported below, because
`advisor/session.py` needs the same numbers for the training ledger and the two must not be
able to disagree -- they disagreed for months, which is the bug this whole change exists to
fix. This module runs that SQL and stores the result; it does not define growth.

A running campaign's growth changes every turn it plays, so this tenant re-folds rather
than watermarking forward: a watermark would freeze the in-flight campaign at whatever it
happened to be on the pass that first saw it, and never look again. The source is one pass
over `target_rows` (1139 rows today), so re-folding is cheaper than the bookkeeping needed
to avoid it.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import campaign_growth as G

NAME = "campaign_growth"
FORMULA_VERSION = 3
SOURCE = "decisions"
DEPENDS_ON = ()
REBUILD_EVERY_PASS = True

DDL = """
CREATE TABLE IF NOT EXISTS campaign_growth(
  campaign_id        INTEGER PRIMARY KEY,
  campaign_key       TEXT NOT NULL,
  faction            TEXT,
  outcome            TEXT,
  last_decided_turn  INTEGER,
  turn_rows          INTEGER NOT NULL DEFAULT 0,
  first_turn         INTEGER,
  last_measured_turn INTEGER,
  first_settlements  REAL, final_settlements REAL, peak_settlements REAL,
  first_lord_level   REAL, final_lord_level  REAL, peak_lord_level  REAL,
  peak_power_rank    REAL, final_power_rank  REAL, final_income     REAL,
  growth_state       TEXT NOT NULL,
  computed_ts        REAL NOT NULL);

CREATE INDEX IF NOT EXISTS ix_cg_key   ON campaign_growth(campaign_key);
CREATE INDEX IF NOT EXISTS ix_cg_state ON campaign_growth(growth_state);
"""

_COLUMNS = ("campaign_id", "campaign_key", "faction", "outcome", "last_decided_turn",
            "turn_rows", "first_turn", "last_measured_turn",
            "first_settlements", "final_settlements", "peak_settlements",
            "first_lord_level", "final_lord_level", "peak_lord_level",
            "peak_power_rank", "final_power_rank", "final_income",
            "growth_state", "computed_ts")

_INSERT = ("INSERT INTO campaign_growth(%s) VALUES(%s)"
           " ON CONFLICT(campaign_id) DO UPDATE SET %s"
           % (", ".join(_COLUMNS), ", ".join("?" * len(_COLUMNS)),
              ", ".join("%s=excluded.%s" % (c, c) for c in _COLUMNS[1:])))


def safe_hi(src, an=None) -> int:
    row = src.execute("SELECT MAX(campaign_id) FROM campaigns").fetchone()
    return int(row[0] or 0)


def source_stats(src, hi):
    row = src.execute("SELECT COUNT(*), MIN(campaign_id) FROM campaigns"
                      " WHERE campaign_id <= ?", (hi,)).fetchone()
    return int(row[0] or 0), row[1]


def step(src, an, lo, hi):
    traj = G.trajectories(src)
    decided = {int(r[0]): r[1] for r in src.execute(
        "SELECT campaign_id, MAX(turn) FROM decisions GROUP BY campaign_id")}
    now = time.time()
    batch = []
    for c in src.execute("SELECT campaign_id, campaign_key, faction, outcome FROM campaigns"
                         " WHERE campaign_id <= ? ORDER BY campaign_id", (hi,)):
        t = traj.get(c["campaign_key"]) or {}
        rec = {
            "campaign_id": int(c["campaign_id"]), "campaign_key": c["campaign_key"],
            "faction": c["faction"], "outcome": c["outcome"],
            "last_decided_turn": decided.get(int(c["campaign_id"])),
            "turn_rows": int(t.get("turn_rows") or 0),
            "computed_ts": now,
        }
        for k in ("first_turn", "last_measured_turn", "first_settlements",
                  "final_settlements", "peak_settlements", "first_lord_level",
                  "final_lord_level", "peak_lord_level", "peak_power_rank",
                  "final_power_rank", "final_income"):
            rec[k] = t.get(k)
        rec["growth_state"] = G.state_of(rec["turn_rows"])
        batch.append(tuple(rec[c2] for c2 in _COLUMNS))
    if batch:
        an.executemany(_INSERT, batch)
    return hi, len(batch)
