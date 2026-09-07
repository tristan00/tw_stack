from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg

KINDS = ("at_war", "allied", "trade", "their_vassal", "our_master", "nap",
         "mil_access", "mil_ally", "def_ally")

SQL = """
INSERT INTO analytics.diplomacy_state_change (campaign_id, faction_id, kind,
                                              from_turn, to_turn)
WITH base AS (
  SELECT s.campaign_id, s.turn, m.faction_id, k.kind, k.val
    FROM corpus.snapshot_world w
    JOIN corpus.snapshot s USING (snapshot_id)
    JOIN corpus.relation_set_member m ON m.set_id = w.relation_set_id
    CROSS JOIN LATERAL (VALUES
        ('at_war', m.at_war), ('allied', m.allied), ('trade', m.trade),
        ('their_vassal', m.their_vassal), ('our_master', m.our_master),
        ('nap', m.nap), ('mil_access', m.mil_access),
        ('mil_ally', m.mil_ally), ('def_ally', m.def_ally)) AS k(kind, val)
   WHERE w.relation_set_id IS NOT NULL
     AND s.campaign_id = ANY(%s)
),
per_turn AS (
  SELECT campaign_id, faction_id, kind, turn, bool_or(val) AS val
    FROM base GROUP BY 1, 2, 3, 4
),
marked AS (
  SELECT campaign_id, faction_id, kind, turn,
         turn - (ROW_NUMBER() OVER (PARTITION BY campaign_id, faction_id, kind
                                        ORDER BY turn))::int AS grp
    FROM per_turn WHERE val
)
SELECT campaign_id, faction_id, kind, MIN(turn), MAX(turn)
  FROM marked GROUP BY campaign_id, faction_id, kind, grp
ON CONFLICT (campaign_id, faction_id, kind, from_turn)
DO UPDATE SET to_turn = EXCLUDED.to_turn
"""

BATCH = 200


def log(msg):
    sys.stderr.write("%.3f  diplochg %s\n" % (time.time(), msg))


def refresh(con=None, campaign_ids=None):
    t0 = time.time()
    own = con is None
    if own:
        con = pg.connect(app_name="tw-diplochg", autocommit=True,
                         search_path=pg.CORPUS_PATH)
    try:
        if campaign_ids is None:
            campaign_ids = [r[0] for r in con.execute(
                "SELECT campaign_id FROM corpus.campaign ORDER BY campaign_id")]
        n = 0
        for i in range(0, len(campaign_ids), BATCH):
            chunk = campaign_ids[i:i + BATCH]
            n += con.execute(SQL, (chunk,)).rowcount
    finally:
        if own:
            con.close()
    log("refresh exit %.0f ms  %d campaigns  %d intervals"
        % ((time.time() - t0) * 1000, len(campaign_ids), n))
    return n


if __name__ == "__main__":
    refresh()
