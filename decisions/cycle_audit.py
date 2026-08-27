from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import common
from decisions import pg

FAILED = []


def check(cond, what):
    print("  %-4s %s" % ("ok" if cond else "FAIL", what))
    if not cond:
        FAILED.append(what)


OVERLAP = """
WITH t AS (
  SELECT t.decision_id, d.campaign_id, d.ts AS dts, t.ts AS pick_ts,
         NULLIF(t.timing, 'null')::jsonb->>'total_ms' AS tot
  FROM taken t JOIN decisions d ON d.decision_id=t.decision_id
  WHERE (t.refusal IS NULL OR
         t.refusal NOT IN ('awaiting_execution','campaign_died'))
), w AS (
  SELECT campaign_id, pick_ts + COALESCE(tot::float, 0) / 1000.0 AS end_ts,
         LEAD(dts) OVER (PARTITION BY campaign_id ORDER BY decision_id) AS next_dts
  FROM t
)
SELECT COUNT(*) FROM w WHERE next_dts IS NOT NULL AND next_dts < end_ts - 0.05
"""

ACTED_PAST = """
SELECT COUNT(*) FROM taken t
JOIN decisions d ON d.decision_id=t.decision_id
WHERE t.refusal IN ('awaiting_execution','campaign_died')
AND EXISTS (SELECT 1 FROM taken t2 JOIN decisions d2 ON d2.decision_id=t2.decision_id
            WHERE d2.campaign_id=d.campaign_id AND t2.decision_id > t.decision_id)
"""

STALE_AWAITING = """
SELECT COUNT(*) FROM taken t
JOIN decisions d ON d.decision_id=t.decision_id
WHERE t.refusal = 'awaiting_execution'
AND d.campaign_id IS DISTINCT FROM
    (SELECT d3.campaign_id FROM taken t3 JOIN decisions d3 ON d3.decision_id=t3.decision_id
     ORDER BY t3.decision_id DESC LIMIT 1)
"""


def main():
    try:
        con = pg.connect(autocommit=True, readonly=True)
    except Exception as e:
        print("cycle audit SKIPPED: postgres unreachable -> %s" % repr(e)[:120])
        return 0
    try:
        got = con.execute("SELECT to_regclass('public.taken')").fetchone()[0]
        if not got:
            print("cycle audit SKIPPED: no decision store schema")
            return 0
        n_pairs = con.execute(
            "SELECT COUNT(*) FROM taken WHERE (refusal IS NULL OR"
            " refusal NOT IN ('awaiting_execution','campaign_died'))").fetchone()[0]
        overlapped = con.execute(OVERLAP).fetchone()[0]
        check(overlapped == 0,
              "no action starts before the previous action's validation ended "
              "(%d violations over %d actions)" % (overlapped, n_pairs))
        acted_past = con.execute(ACTED_PAST).fetchone()[0]
        check(acted_past == 0,
              "no campaign acts past an unresolved action (%d violations)" % acted_past)
        stale = con.execute(STALE_AWAITING).fetchone()[0]
        check(stale == 0,
              "awaiting_execution exists only for the action in flight right now "
              "(%d stale rows -- the recorder finalizes them to campaign_died at boot)"
              % stale)
    finally:
        con.close()
    print("\n%s" % ("cycle audit OK" if not FAILED else "%d FAILED" % len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
