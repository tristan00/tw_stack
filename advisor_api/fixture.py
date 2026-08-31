from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decisions import pg, pg_schema

SCHEMA = "test_fixture"
ANALYTICS_SCHEMA = "test_fixture_analytics"
VERSION = "4"
FACTIONS = 3
CAMPAIGNS_PER_FACTION = 8

_QUALIFYING = """
SELECT c.campaign_id, c.faction FROM campaigns c
WHERE c.outcome IS NOT NULL
  AND c.picked_ts IS NOT NULL AND c.selector LIKE 'ucb%%'
  AND (SELECT COUNT(*) FROM decisions d WHERE d.campaign_id = c.campaign_id) >= 30
  AND EXISTS (SELECT 1 FROM taken t JOIN actions a ON a.action_id = t.action_id
              WHERE t.campaign_id = c.campaign_id AND t.counted = 1
              AND a.action_type = 'items')
  AND EXISTS (SELECT 1 FROM taken t JOIN actions a ON a.action_id = t.action_id
              WHERE t.campaign_id = c.campaign_id AND t.counted = 1
              AND a.action_type = 'research')
  AND EXISTS (SELECT 1 FROM taken t JOIN actions a ON a.action_id = t.action_id
              WHERE t.campaign_id = c.campaign_id AND t.counted = 1
              AND a.action_type = 'building')
  AND EXISTS (SELECT 1 FROM taken t JOIN actions a ON a.action_id = t.action_id
              WHERE t.campaign_id = c.campaign_id AND t.counted = 1
              AND a.action_type = 'skills')
ORDER BY c.campaign_id
"""


def _select_campaigns(con) -> list:
    per_faction: dict = {}
    for r in con.execute(_QUALIFYING):
        fac = r[1]
        if fac not in per_faction:
            if len(per_faction) >= FACTIONS:
                continue
            per_faction[fac] = []
        if len(per_faction[fac]) < CAMPAIGNS_PER_FACTION:
            per_faction[fac].append(r[0])
        if (len(per_faction) == FACTIONS
                and all(len(v) == CAMPAIGNS_PER_FACTION
                        for v in per_faction.values())):
            break
    chosen = []
    for got in per_faction.values():
        chosen.extend(got)
    return sorted(chosen)


def _current_version(con) -> str | None:
    row = con.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
        (SCHEMA,)).fetchone()
    if not row:
        return None
    row = con.execute(
        "SELECT v FROM %s.meta WHERE k = 'fixture_version'" % SCHEMA).fetchone()
    return row[0] if row else None


def ensure(force=False) -> str:
    con = pg.connect(autocommit=True, search_path="public")
    try:
        if not force and _current_version(con) == VERSION:
            return "fixture %s at version %s" % (SCHEMA, VERSION)
        cids = _select_campaigns(con)
        if len(cids) < FACTIONS * CAMPAIGNS_PER_FACTION:
            raise RuntimeError(
                "the corpus holds only %d qualifying campaigns; the fixture needs "
                "%d -- point TW_PG_* at a populated corpus once to build it"
                % (len(cids), FACTIONS * CAMPAIGNS_PER_FACTION))
        con.execute("DROP SCHEMA IF EXISTS %s CASCADE" % SCHEMA)
        con.execute("CREATE SCHEMA %s" % SCHEMA)
        build = pg.connect(autocommit=False, search_path=SCHEMA + ", public")
        try:
            cur = build.cursor()
            cur.execute("SET LOCAL search_path = %s" % SCHEMA)
            cur.execute(pg_schema.DDL)
            ids = "(%s)" % ", ".join(str(int(c)) for c in cids)
            copies = [
                ("collector_versions", "SELECT * FROM public.collector_versions"),
                ("campaigns",
                 "SELECT * FROM public.campaigns WHERE campaign_id IN %s" % ids),
                ("actions",
                 "SELECT * FROM public.actions WHERE action_id IN ("
                 "  SELECT o.action_id FROM public.offers o"
                 "  JOIN public.decisions d ON d.decision_id = o.decision_id"
                 "  WHERE d.campaign_id IN %s"
                 "  UNION SELECT t.action_id FROM public.taken t"
                 "  WHERE t.campaign_id IN %s AND t.action_id IS NOT NULL)"
                 % (ids, ids)),
                ("blobs",
                 "SELECT * FROM public.blobs WHERE blob_id IN ("
                 "  SELECT campaign_blob FROM public.decisions"
                 "  WHERE campaign_id IN %s AND campaign_blob IS NOT NULL"
                 "  UNION SELECT world_blob FROM public.decisions"
                 "  WHERE campaign_id IN %s AND world_blob IS NOT NULL"
                 "  UNION SELECT e.features_blob FROM public.entities e"
                 "  JOIN public.decisions d ON d.decision_id = e.decision_id"
                 "  WHERE d.campaign_id IN %s"
                 "  UNION SELECT campaign_blob FROM public.interrupts"
                 "  WHERE campaign_id IN %s AND campaign_blob IS NOT NULL"
                 "  UNION SELECT world_blob FROM public.interrupts"
                 "  WHERE campaign_id IN %s AND world_blob IS NOT NULL"
                 "  UNION SELECT panel_blob FROM public.interrupts"
                 "  WHERE campaign_id IN %s AND panel_blob IS NOT NULL)"
                 % (ids, ids, ids, ids, ids, ids)),
                ("decisions",
                 "SELECT * FROM public.decisions WHERE campaign_id IN %s" % ids),
                ("entities",
                 "SELECT e.* FROM public.entities e JOIN public.decisions d"
                 " ON d.decision_id = e.decision_id"
                 " WHERE d.campaign_id IN %s" % ids),
                ("offers",
                 "SELECT o.* FROM public.offers o JOIN public.decisions d"
                 " ON d.decision_id = o.decision_id"
                 " WHERE d.campaign_id IN %s" % ids),
                ("taken",
                 "SELECT * FROM public.taken WHERE campaign_id IN %s" % ids),
                ("offer_scores",
                 "SELECT s.* FROM public.offer_scores s JOIN public.decisions d"
                 " ON d.decision_id = s.decision_id"
                 " WHERE d.campaign_id IN %s" % ids),
                ("offer_model_scores",
                 "SELECT s.* FROM public.offer_model_scores s"
                 " JOIN public.decisions d ON d.decision_id = s.decision_id"
                 " WHERE d.campaign_id IN %s" % ids),
                ("interrupts",
                 "SELECT * FROM public.interrupts WHERE campaign_id IN %s" % ids),
                ("postmortems",
                 "SELECT p.* FROM public.postmortems p JOIN public.campaigns c"
                 " ON c.campaign_key = p.campaign_key"
                 " WHERE c.campaign_id IN %s" % ids),
                ("diplomacy_events",
                 "SELECT ev.* FROM public.diplomacy_events ev"
                 " JOIN public.campaigns c ON c.campaign_key = ev.campaign_key"
                 " WHERE c.campaign_id IN %s" % ids),
                ("ucb_picks",
                 "SELECT DISTINCT p.* FROM public.ucb_picks p"
                 " JOIN public.campaigns c ON c.campaign_id IN %s"
                 " AND c.campaign_map = p.campaign_map"
                 " AND c.faction = p.faction"
                 " AND c.picked_ts >= p.ts - 1 AND c.picked_ts <= p.ts + 900"
                 % ids),
                ("ucb_pick_rows",
                 "SELECT r.* FROM public.ucb_pick_rows r WHERE r.pick_id IN ("
                 "  SELECT DISTINCT p.pick_id FROM public.ucb_picks p"
                 "  JOIN public.campaigns c ON c.campaign_id IN %s"
                 "  AND c.campaign_map = p.campaign_map"
                 "  AND c.faction = p.faction"
                 "  AND c.picked_ts >= p.ts - 1 AND c.picked_ts <= p.ts + 900)"
                 % ids),
            ]
            def common_cols(table):
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = %s"
                    " ORDER BY ordinal_position", (table,))
                pub = [r[0] for r in cur.fetchall()]
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = %s AND table_name = %s",
                    (SCHEMA, table))
                fix = {r[0] for r in cur.fetchall()}
                return [c for c in pub if c in fix]

            for table, select in copies:
                cols = ", ".join(common_cols(table))
                cur.execute(
                    "INSERT INTO %s.%s (%s) SELECT %s FROM (%s) src"
                    % (SCHEMA, table, cols, cols, select))
            cur.execute(pg_schema.VIEWS)
            cur.execute(
                "INSERT INTO %s.meta(k, v) VALUES ('fixture_version', %%s)"
                " ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v" % SCHEMA,
                (VERSION,))
            build.commit()
        except Exception:
            build.rollback()
            raise
        finally:
            build.close()
        _build_analytics(con)
        return "fixture %s rebuilt at version %s from campaigns %s" % (
            SCHEMA, VERSION, cids)
    finally:
        con.close()


def _build_analytics(admin_con):
    from analytics import store as astore
    from analytics.runner import one_pass
    from analytics.tenants import TENANTS
    admin_con.execute("DROP SCHEMA IF EXISTS %s CASCADE" % ANALYTICS_SCHEMA)
    src = pg.connect(autocommit=True, readonly=True, row_factory=pg.row_factory,
                     search_path=SCHEMA + ", public")
    an = astore.connect(schema=ANALYTICS_SCHEMA)
    try:
        prev = None
        for _ in range(500):
            res = one_pass(src, an, TENANTS, log=lambda _m: None)
            if res["failed"]:
                raise RuntimeError("fixture analytics pass failed: %s"
                                   % res["failed"])
            marks = tuple(sorted((r["tenant"], r["watermark"])
                                 for r in res["done"]))
            if marks == prev:
                break
            prev = marks
    finally:
        src.close()
        an.close()


if __name__ == "__main__":
    print(ensure(force="--force" in sys.argv[1:]))
