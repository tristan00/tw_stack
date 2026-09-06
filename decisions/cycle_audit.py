from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import common
from decisions import pg

FAILED = []


def check(cond, what):
    print("  %-4s %s" % ("ok" if cond else "FAIL", what))
    if not cond:
        FAILED.append(what)


SKIP = ("SELECT ARRAY_AGG(enum_id) FROM dict.enum WHERE domain = 'refusal'"
        " AND key IN ('awaiting_execution', 'campaign_died')")

OVERLAP = """
WITH t AS (
  SELECT t.decision_id, s.campaign_id, s.ts AS dts, t.ts AS pick_ts, t.total_ms AS tot
  FROM corpus.taken t JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id
  WHERE (t.refusal_id IS NULL OR t.refusal_id <> ALL(%s))
), w AS (
  SELECT campaign_id, pick_ts + COALESCE(tot, 0) / 1000.0 AS end_ts,
         LEAD(dts) OVER (PARTITION BY campaign_id ORDER BY decision_id) AS next_dts
  FROM t
)
SELECT COUNT(*) FROM w WHERE next_dts IS NOT NULL AND next_dts < end_ts - 0.05
"""

ACTED_PAST = """
SELECT COUNT(*) FROM corpus.taken t
WHERE t.refusal_id = ANY(%s)
AND EXISTS (SELECT 1 FROM corpus.taken t2
            WHERE t2.campaign_id = t.campaign_id AND t2.decision_id > t.decision_id)
"""

STALE_AWAITING = """
SELECT COUNT(*) FROM corpus.taken t
WHERE t.refusal_id = (SELECT enum_id FROM dict.enum
                      WHERE domain = 'refusal' AND key = 'awaiting_execution')
AND t.campaign_id IS DISTINCT FROM
    (SELECT t3.campaign_id FROM corpus.taken t3
     ORDER BY t3.decision_id DESC LIMIT 1)
"""


def main():
    t0 = time.time()
    try:
        con = pg.connect(app_name="tw-cycleaudit", autocommit=True, readonly=True,
                         search_path=pg.CORPUS_PATH)
    except Exception as e:
        print("cycle audit SKIPPED: postgres unreachable -> %s" % repr(e)[:120])
        return 0
    try:
        got = con.execute("SELECT to_regclass('corpus.taken')").fetchone()[0]
        if not got:
            print("cycle audit SKIPPED: no decision store schema")
            return 0
        skip = con.execute(SKIP).fetchone()[0]
        n_pairs = con.execute(
            "SELECT COUNT(*) FROM corpus.taken WHERE (refusal_id IS NULL OR"
            " refusal_id <> ALL(%s))", (skip,)).fetchone()[0]
        overlapped = con.execute(OVERLAP, (skip,)).fetchone()[0]
        check(overlapped == 0,
              "no action starts before the previous action's validation ended "
              "(%d violations over %d actions)" % (overlapped, n_pairs))
        acted_past = con.execute(ACTED_PAST, (skip,)).fetchone()[0]
        check(acted_past == 0,
              "no campaign acts past an unresolved action (%d violations)" % acted_past)
        stale = con.execute(STALE_AWAITING).fetchone()[0]
        check(stale == 0,
              "awaiting_execution exists only for the action in flight right now "
              "(%d stale rows -- the recorder finalizes them to campaign_died at boot)"
              % stale)
    finally:
        con.close()
    print("\n%s (%.1f s)" % ("cycle audit OK" if not FAILED
                             else "%d FAILED" % len(FAILED), time.time() - t0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    common.require_venv()
    raise SystemExit(main())
