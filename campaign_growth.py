from __future__ import annotations


MEASURED = "measured"
SINGLE_TURN = "single_turn"
NO_TURN_ROWS = "no_turn_rows"

_TRAJECTORY = """
SELECT c.campaign_key AS ckey,
       agg.turn_rows AS turn_rows,
       agg.first_turn AS first_turn,
       agg.last_measured_turn AS last_measured_turn,
       fo.settlements AS first_settlements,
       lo.settlements AS final_settlements,
       agg.peak_settlements AS peak_settlements,
       fo.lord_level AS first_lord_level,
       lo.lord_level AS final_lord_level,
       agg.peak_lord_level AS peak_lord_level,
       agg.peak_power_rank AS peak_power_rank,
       lo.power_rank AS final_power_rank,
       lo.income AS final_income
  FROM (SELECT campaign_id, COUNT(*) AS turn_rows,
               MIN(turn) AS first_turn, MAX(turn) AS last_measured_turn,
               MIN(decision_id) AS first_id, MAX(decision_id) AS last_id,
               MAX(settlements) AS peak_settlements,
               MAX(lord_level) AS peak_lord_level,
               MAX(power_rank) AS peak_power_rank
          FROM decisions %s GROUP BY campaign_id) agg
  JOIN decisions fo ON fo.decision_id = agg.first_id
  JOIN decisions lo ON lo.decision_id = agg.last_id
  LEFT JOIN campaigns c ON c.campaign_id = agg.campaign_id
"""

TRAJECTORY_SQL = _TRAJECTORY % ""
TRAJECTORY_SQL_ONE = _TRAJECTORY % ("WHERE campaign_id IN (SELECT campaign_id"
                                    " FROM campaigns WHERE campaign_key = ?)")


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
