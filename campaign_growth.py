from __future__ import annotations

"""The one definition of campaign growth.

Growth is FIRST -> PEAK, per metric, over the sequential decision snapshots in
`decision_points.campaign`. `turn_rows` counts snapshots, not turns.

Four definitions used to coexist, and they disagreed on the source, the window, the sign
convention, and whether loss could exist at all:

  the kill gate   advisor/loop.py:_growth_verdict -- decision snapshots over a variable 3-4
                  turn window, recorded ONLY when it fired, so it could only read flat or
                  down. This is what the dashboard was showing. 147 of 152 filled cells
                  read `N -> N` and the "grew" branch was unreachable code.
  the ledger      advisor/session.py:_campaign_growth -- a snapshot start against a
                  target_rows peak, clamped by max() so a campaign that lost settlements
                  recorded a gain of zero.
  the model       advisor/base_model.py -- a snapshot baseline against a future max.
  the dashboard   advisor_api/queries.py -- whatever the postmortem happened to freeze.

The Campaigns tab's number was structurally non-positive while the Training tab's was
structurally non-negative: two numbers called growth that could never agree. This module is
the single definition they now share. It lives at the repo root because
`advisor_api/queries.py` does not import `advisor` and `advisor/session.py` does not import
`advisor_api`, so neither package could own it -- the same reason `metrics_db.py` is here.

EXCLUDED, with the measurement rather than the assertion:

  power_rank -- a rank among every faction in the world, so it moves when OTHER factions
  move. Turn to turn over 884 consecutive pairs: median |change| 25, p90 91, max 236; and
  median 23 when restricted to turns where settlements did not change at all. Not a
  differenceable quantity at this sampling rate.

  income -- a per-turn flow rather than a stock, dominated by the faction's economic
  template. Its mode is exactly 3000.0 across 83 rows, 71 of which have no settlements:
  that is what a horde looks like, not what growth looks like.

Both settlements and lord level ship, because neither alone is honest. Settlements is
structurally zero for the 32 horde campaigns that never own a region -- `decisions/collect.py`
already says "a horde legitimately owns nothing" -- while lord level moves on 100 of 240
campaigns and never falls.

THE JOIN TRAP. `target_rows.campaign_id` holds the campaign KEY STRING; `campaigns.campaign_id`
is an integer surrogate. The natural-looking join returns zero rows and raises nothing
(measured: 0 the wrong way, 1139 the right way). This query does not join `campaigns` at
all -- it groups by the key it already has -- which is the cheapest possible immunity.
"""

MEASURED = "measured"
SINGLE_TURN = "single_turn"
NO_TURN_ROWS = "no_turn_rows"

_TRAJECTORY = """
SELECT ckey,
       COUNT(*)                                       AS turn_rows,
       MIN(turn)                                      AS first_turn,
       MAX(turn)                                      AS last_measured_turn,
       MAX(CASE WHEN rn_lo = 1 THEN settlements END)  AS first_settlements,
       MAX(CASE WHEN rn_hi = 1 THEN settlements END)  AS final_settlements,
       MAX(settlements)                               AS peak_settlements,
       MAX(CASE WHEN rn_lo = 1 THEN lord_level END)   AS first_lord_level,
       MAX(CASE WHEN rn_hi = 1 THEN lord_level END)   AS final_lord_level,
       MAX(lord_level)                                AS peak_lord_level,
       MIN(power_rank)                                AS peak_power_rank,
       MAX(CASE WHEN rn_hi = 1 THEN power_rank END)   AS final_power_rank,
       MAX(CASE WHEN rn_hi = 1 THEN income END)       AS final_income
  FROM (SELECT campaign_id AS ckey,
               CAST(json_extract(campaign, '$.turn')        AS REAL) AS turn,
               CAST(json_extract(campaign, '$.settlements') AS REAL) AS settlements,
               CAST(json_extract(campaign, '$.lord_level')  AS REAL) AS lord_level,
               CAST(json_extract(campaign, '$.power_rank')  AS REAL) AS power_rank,
               CAST(json_extract(campaign, '$.income')      AS REAL) AS income,
               ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY decision_id ASC)  AS rn_lo,
               ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY decision_id DESC) AS rn_hi
          FROM decision_points %s)
 GROUP BY ckey
"""

TRAJECTORY_SQL = _TRAJECTORY % ""
TRAJECTORY_SQL_ONE = _TRAJECTORY % "WHERE campaign_id = ?"


def state_of(turn_rows) -> str:
    """Whether a growth span exists at all, as a named state."""
    if not turn_rows:
        return NO_TURN_ROWS
    return MEASURED if int(turn_rows) >= 2 else SINGLE_TURN


def delta(first, last, turn_rows):
    """last - first, or None when there is no span. Never 0.0 as a stand-in for no data."""
    if state_of(turn_rows) != MEASURED or first is None or last is None:
        return None
    return float(last) - float(first)


def span_turns(first_turn, last_measured_turn, turn_rows):
    """Turns between the first and last measurement. None when there is no span."""
    if state_of(turn_rows) != MEASURED or first_turn is None or last_measured_turn is None:
        return None
    n = int(last_measured_turn) - int(first_turn)
    return n if n > 0 else None


def per_turn(delta_value, span):
    """Growth per turn played."""
    if delta_value is None or not span:
        return None
    return float(delta_value) / float(span)


def enrich(row: dict) -> dict:
    """Add the derived fields to a stored trajectory row."""
    out = dict(row)
    tr = out.get("turn_rows")
    out["growth_state"] = state_of(tr)
    span = span_turns(out.get("first_turn"), out.get("last_measured_turn"), tr)
    out["growth_span_turns"] = span
    s = delta(out.get("first_settlements"), out.get("peak_settlements"), tr)
    l = delta(out.get("first_lord_level"), out.get("peak_lord_level"), tr)
    out["settlements_growth"] = s
    out["lord_growth"] = l
    out["settlements_per_turn"] = per_turn(s, span)
    out["lord_per_turn"] = per_turn(l, span)
    return out


def trajectories(con) -> dict:
    """{campaign_key: row}. One pass over target_rows."""
    return {r["ckey"]: dict(r) for r in con.execute(TRAJECTORY_SQL)}
