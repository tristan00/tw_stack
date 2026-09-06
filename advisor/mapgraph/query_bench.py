from __future__ import annotations


import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from advisor import memory as M
from decisions import pg


def _walk_plan(node, out):
    out.append({"node": node.get("Node Type"), "relation": node.get("Relation Name"),
                "index": node.get("Index Name"), "rows": node.get("Actual Rows"),
                "loops": node.get("Actual Loops")})
    for child in node.get("Plans", []):
        _walk_plan(child, out)


def _explain(con, name, sql, args=()):
    started = time.time()
    raw = con.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, args).fetchone()[0]
    root = raw[0]
    scans = []
    _walk_plan(root["Plan"], scans)
    plan = root["Plan"]
    return {"name": name, "wall_ms": round((time.time() - started) * 1000, 2),
            "planning_ms": root.get("Planning Time"),
            "execution_ms": root.get("Execution Time"),
            "shared_hit_blocks": plan.get("Shared Hit Blocks", 0),
            "shared_read_blocks": plan.get("Shared Read Blocks", 0),
            "temp_read_blocks": plan.get("Temp Read Blocks", 0),
            "temp_written_blocks": plan.get("Temp Written Blocks", 0),
            "plan": scans}


def run(window=1000, sample=500):
    con = pg.connect(autocommit=True, readonly=True)
    try:
        floor = con.execute(
            "SELECT MIN(first_snapshot_id) FROM (SELECT first_snapshot_id"
            " FROM corpus.campaign WHERE first_snapshot_id IS NOT NULL"
            " ORDER BY first_snapshot_id DESC NULLS LAST LIMIT %s) w",
            (window,)).fetchone()[0]
        ids = [r[0] for r in con.execute(
            "SELECT decision_id FROM corpus.taken WHERE decision_id >= %s"
            " ORDER BY decision_id LIMIT %s", (floor, sample))]
        camps = [r[0] for r in con.execute(
            "SELECT DISTINCT campaign_id FROM corpus.taken WHERE decision_id = ANY(%s)",
            (ids,))]
        skip = list(M._enum_ids(con, "refusal",
                                ["awaiting_execution", "campaign_died"]).values())
        kind = M._enum_ids(con, "interrupt_kind", ["pre_battle"])["pre_battle"]
        plans = []
        plans.append(_explain(con, "window_floor",
            "SELECT MIN(first_snapshot_id) FROM (SELECT first_snapshot_id"
            " FROM corpus.campaign WHERE first_snapshot_id IS NOT NULL"
            " ORDER BY first_snapshot_id DESC NULLS LAST LIMIT %s) w", (window,)))
        plans.append(_explain(con, "decision_heads",
            "SELECT t.decision_id FROM corpus.taken t WHERE t.decision_id >= %s"
            " ORDER BY t.decision_id LIMIT %s", (floor, sample)))
        plans.append(_explain(con, "target_series",
            "SELECT s.campaign_id, s.turn, MIN(s.snapshot_id) FROM corpus.snapshot s"
            " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
            " WHERE s.campaign_id = ANY(%s) GROUP BY s.campaign_id, s.turn", (camps,)))
        plans.append(_explain(con, "replay_stamps", M._REPLAY_SQL, (skip, camps)))
        pb_sql = M._PB_ATTRIB_SQL.replace(
            "__CAMP_FILTER__", "AND s.campaign_id = ANY(%(camps)s)")
        pb_sql += M._PB_ATTRIB_ORDER
        plans.append(_explain(con, "prebattle_attributions", pb_sql,
            {"skip": skip, "kind": kind, "win": M.PB_WINDOW_S, "camps": camps}))
        for table in ("snapshot_campaign", "snapshot_world", "world_army", "world_hostile",
                      "char_state", "char_state_ext", "province_state", "campaign_state"):
            plans.append(_explain(con, "prefetch_" + table,
                                  "SELECT * FROM corpus.%s WHERE snapshot_id = ANY(%%s)"
                                  % table, (ids,)))
        plans.append(_explain(con, "prefetch_entities",
            "SELECT * FROM corpus.snapshot_entity WHERE snapshot_id = ANY(%s)", (ids,)))
        plans.append(_explain(con, "prefetch_offers",
            "SELECT * FROM corpus.offer WHERE decision_id = ANY(%s)", (ids,)))
        settings = dict(con.execute(
            "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
            (["shared_buffers", "effective_cache_size", "work_mem", "maintenance_work_mem",
              "random_page_cost", "effective_io_concurrency", "default_statistics_target",
              "max_parallel_workers_per_gather", "jit"],)).fetchall())
        tables = [dict(zip(("table", "live", "dead", "last_analyze", "size_bytes"), row))
                  for row in con.execute(
            "SELECT relname, n_live_tup, n_dead_tup, COALESCE(last_autoanalyze,last_analyze),"
            " pg_total_relation_size(relid) FROM pg_stat_user_tables"
            " WHERE schemaname='corpus' AND relname = ANY(%s) ORDER BY relname",
            (["campaign", "snapshot", "decision", "taken", "offer", "snapshot_entity",
              "snapshot_campaign", "snapshot_world", "world_army", "world_hostile",
              "char_state", "province_state", "campaign_state", "interrupt"],)).fetchall()]
        indexes = [dict(zip(("table", "index", "scans", "size_bytes"), row))
                   for row in con.execute(
            "SELECT relname, indexrelname, idx_scan, pg_relation_size(indexrelid)"
            " FROM pg_stat_user_indexes WHERE schemaname='corpus'"
            " AND relname = ANY(%s) ORDER BY relname,indexrelname",
            (["campaign", "snapshot", "decision", "taken", "offer", "snapshot_entity",
              "snapshot_campaign", "snapshot_world", "world_army", "world_hostile",
              "char_state", "province_state", "campaign_state", "interrupt"],)).fetchall()]
        cardinality = dict(zip(("decisions", "campaigns", "offers"), con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT t.campaign_id),"
            " (SELECT COUNT(*) FROM corpus.offer o WHERE o.decision_id >= %s)"
            " FROM corpus.taken t WHERE t.decision_id >= %s", (floor, floor)).fetchone()))
        return {"window": window, "sample": sample, "floor": floor,
                "sample_campaigns": len(camps), "cardinality": cardinality,
                "settings": settings, "tables": tables, "indexes": indexes,
                "plans": plans}
    finally:
        con.close()


if __name__ == "__main__":
    window = int(sys.argv[sys.argv.index("--window") + 1]) if "--window" in sys.argv else 1000
    sample = int(sys.argv[sys.argv.index("--sample") + 1]) if "--sample" in sys.argv else 500
    print(json.dumps(run(window, sample), indent=2))
