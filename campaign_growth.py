from __future__ import annotations


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
       MAX(power_rank)                                AS peak_power_rank,
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
    if not turn_rows:
        return NO_TURN_ROWS
    return MEASURED if int(turn_rows) >= 2 else SINGLE_TURN


def delta(first, last, turn_rows):
    if state_of(turn_rows) != MEASURED or first is None or last is None:
        return None
    return float(last) - float(first)


def span_turns(first_turn, last_measured_turn, turn_rows):
    if state_of(turn_rows) != MEASURED or first_turn is None or last_measured_turn is None:
        return None
    n = int(last_measured_turn) - int(first_turn)
    return n if n > 0 else None


def per_turn(delta_value, span):
    if delta_value is None or not span:
        return None
    return float(delta_value) / float(span)


def enrich(row: dict) -> dict:
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
    return {r["ckey"]: dict(r) for r in con.execute(TRAJECTORY_SQL)}
