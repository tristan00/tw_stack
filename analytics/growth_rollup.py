from __future__ import annotations


import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import campaign_growth as G
from analytics import store as _store

FORMULA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS growth_summary(
  scope TEXT PRIMARY KEY, computed_ts DOUBLE PRECISION NOT NULL,
  campaigns INTEGER NOT NULL,
  measured INTEGER NOT NULL, single_turn INTEGER NOT NULL, no_turn_rows INTEGER NOT NULL,
  settlements_up INTEGER, settlements_flat INTEGER, settlements_down INTEGER,
  lord_up INTEGER, lord_flat INTEGER, lord_down INTEGER,
  settlements_mean DOUBLE PRECISION, lord_mean DOUBLE PRECISION,
  settlements_per_turn_mean DOUBLE PRECISION, lord_per_turn_mean DOUBLE PRECISION,
  span_median DOUBLE PRECISION,
  turn_rows_behind_decided INTEGER);
"""


class _GrowthSummary:
    NAME = "growth_summary"
    FORMULA_VERSION = FORMULA_VERSION
    SOURCE = "analytics"
    DEPENDS_ON = ("campaign_growth",)
    DDL = DDL

    def safe_hi(self, src, an=None):
        return int(_store.state(an, "campaign_growth")["watermark"])

    def source_stats(self, src, hi):
        return None

    def step(self, src, an, lo, hi):
        an.execute("DELETE FROM growth_summary")
        rows = [G.enrich(dict(r)) for r in an.execute("SELECT * FROM campaign_growth")]
        states = {s: 0 for s in (G.MEASURED, G.SINGLE_TURN, G.NO_TURN_ROWS)}
        for r in rows:
            states[r["growth_state"]] = states.get(r["growth_state"], 0) + 1

        def split(key):
            vals = [r[key] for r in rows if r[key] is not None]
            return (sum(1 for v in vals if v > 0), sum(1 for v in vals if v == 0),
                    sum(1 for v in vals if v < 0),
                    (sum(vals) / len(vals) if vals else None))

        s_up, s_flat, s_down, s_mean = split("settlements_growth")
        l_up, l_flat, l_down, l_mean = split("lord_growth")
        spt = [r["settlements_per_turn"] for r in rows if r["settlements_per_turn"] is not None]
        lpt = [r["lord_per_turn"] for r in rows if r["lord_per_turn"] is not None]
        spans = sorted(r["growth_span_turns"] for r in rows
                       if r["growth_span_turns"] is not None)
        behind = sum(1 for r in rows
                     if r.get("last_decided_turn") is not None
                     and r.get("last_measured_turn") is not None
                     and int(r["last_decided_turn"]) > int(r["last_measured_turn"]))
        an.execute("INSERT INTO growth_summary VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   ("all", time.time(), len(rows), states[G.MEASURED],
                    states[G.SINGLE_TURN], states[G.NO_TURN_ROWS],
                    s_up, s_flat, s_down, l_up, l_flat, l_down, s_mean, l_mean,
                    (sum(spt) / len(spt) if spt else None),
                    (sum(lpt) / len(lpt) if lpt else None),
                    (float(spans[len(spans) // 2]) if spans else None), behind))
        return hi, len(rows)


SUMMARY = _GrowthSummary()

TENANTS = (SUMMARY,)
