from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NAME = "game_turn"
FORMULA_VERSION = 1
SOURCE = "decisions"
DEPENDS_ON = ()

TURNS_PER_PASS = 400

TABLES = (
    "game_turn", "game_turn_research", "game_turn_character", "game_turn_skill",
    "game_turn_trait", "game_turn_item", "game_turn_region", "game_turn_building",
    "game_turn_diplomacy", "game_turn_army", "game_turn_unit", "game_turn_mission",
    "game_turn_resource", "game_turn_built")

DDL = """
CREATE TABLE IF NOT EXISTS game_turn_built(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, decision_id BIGINT NOT NULL,
  PRIMARY KEY(campaign_id, turn));

CREATE TABLE IF NOT EXISTS game_turn(
  campaign_id BIGINT NOT NULL, campaign_key TEXT NOT NULL, turn INTEGER NOT NULL,
  decision_id BIGINT NOT NULL, ts DOUBLE PRECISION,
  faction TEXT, campaign_map TEXT, leader TEXT, difficulty DOUBLE PRECISION,
  treasury DOUBLE PRECISION, income DOUBLE PRECISION,
  settlements DOUBLE PRECISION, allies DOUBLE PRECISION, vassals DOUBLE PRECISION,
  lord_level DOUBLE PRECISION, armies DOUBLE PRECISION, power_rank DOUBLE PRECISION,
  techs_done INTEGER, techs_total INTEGER, researching TEXT, research_points DOUBLE PRECISION,
  regions_owned INTEGER, provinces INTEGER, wars INTEGER, known_factions INTEGER,
  characters INTEGER, units INTEGER, items_held INTEGER, missions_open INTEGER,
  defeated SMALLINT,
  PRIMARY KEY(campaign_id, turn));

CREATE TABLE IF NOT EXISTS game_turn_research(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, tech TEXT NOT NULL,
  researched SMALLINT, can_research SMALLINT, cost DOUBLE PRECISION, is_current SMALLINT);

CREATE TABLE IF NOT EXISTS game_turn_character(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, cqi TEXT NOT NULL,
  kind TEXT, subtype TEXT, agent_type TEXT, rank DOUBLE PRECISION, xp DOUBLE PRECISION,
  skill_points DOUBLE PRECISION, region TEXT, stance TEXT, units DOUBLE PRECISION,
  strength DOUBLE PRECISION, upkeep DOUBLE PRECISION, wounded SMALLINT, is_leader SMALLINT,
  garrisoned SMALLINT, x DOUBLE PRECISION, y DOUBLE PRECISION,
  authority DOUBLE PRECISION, loyalty DOUBLE PRECISION,
  PRIMARY KEY(campaign_id, turn, cqi));

CREATE TABLE IF NOT EXISTS game_turn_skill(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, cqi TEXT NOT NULL,
  skill TEXT NOT NULL, level DOUBLE PRECISION, status TEXT, tier DOUBLE PRECISION,
  total_levels DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS game_turn_trait(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, cqi TEXT NOT NULL,
  trait TEXT NOT NULL, points DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS game_turn_item(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, cqi TEXT,
  ancillary TEXT NOT NULL, equipped SMALLINT, source TEXT);

CREATE TABLE IF NOT EXISTS game_turn_region(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, region TEXT NOT NULL,
  province TEXT, owner TEXT, mine SMALLINT, capital SMALLINT, abandoned SMALLINT,
  public_order DOUBLE PRECISION, corruption DOUBLE PRECISION, growth DOUBLE PRECISION,
  income DOUBLE PRECISION, gross_income DOUBLE PRECISION, settlement_level DOUBLE PRECISION,
  buildings DOUBLE PRECISION, free_slots DOUBLE PRECISION, max_slots DOUBLE PRECISION,
  has_port SMALLINT, has_walls SMALLINT, x DOUBLE PRECISION, y DOUBLE PRECISION,
  PRIMARY KEY(campaign_id, turn, region));

CREATE TABLE IF NOT EXISTS game_turn_building(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, region TEXT NOT NULL,
  slot_index INTEGER NOT NULL, building TEXT, queued TEXT, damaged SMALLINT, ruined SMALLINT);

CREATE TABLE IF NOT EXISTS game_turn_diplomacy(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, faction TEXT NOT NULL,
  standing DOUBLE PRECISION, at_war SMALLINT, allied SMALLINT, trade SMALLINT,
  nap SMALLINT, mil_access SMALLINT, mil_ally SMALLINT, def_ally SMALLINT,
  their_vassal SMALLINT, our_master SMALLINT,
  PRIMARY KEY(campaign_id, turn, faction));

CREATE TABLE IF NOT EXISTS game_turn_army(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, cqi TEXT NOT NULL,
  faction TEXT, hostile SMALLINT, subtype TEXT, units DOUBLE PRECISION,
  strength DOUBLE PRECISION, stance TEXT, region TEXT,
  x DOUBLE PRECISION, y DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS game_turn_unit(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, cqi TEXT NOT NULL,
  unit TEXT NOT NULL, category TEXT, strength_pct DOUBLE PRECISION, xp DOUBLE PRECISION);

CREATE TABLE IF NOT EXISTS game_turn_mission(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, mission TEXT NOT NULL,
  status TEXT, turns_remaining DOUBLE PRECISION, is_quest SMALLINT, is_victory SMALLINT,
  pending SMALLINT, issuer TEXT);

CREATE TABLE IF NOT EXISTS game_turn_resource(
  campaign_id BIGINT NOT NULL, turn INTEGER NOT NULL, resource TEXT NOT NULL,
  value DOUBLE PRECISION);

CREATE INDEX IF NOT EXISTS ix_gt_key ON game_turn(campaign_key, turn);
CREATE INDEX IF NOT EXISTS ix_gt_faction ON game_turn(faction);
CREATE INDEX IF NOT EXISTS ix_gtr_ct ON game_turn_research(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gtr_tech ON game_turn_research(tech);
CREATE INDEX IF NOT EXISTS ix_gtc_ct ON game_turn_character(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gts_ct ON game_turn_skill(campaign_id, turn, cqi);
CREATE INDEX IF NOT EXISTS ix_gts_skill ON game_turn_skill(skill);
CREATE INDEX IF NOT EXISTS ix_gtt_ct ON game_turn_trait(campaign_id, turn, cqi);
CREATE INDEX IF NOT EXISTS ix_gti_ct ON game_turn_item(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gti_anc ON game_turn_item(ancillary);
CREATE INDEX IF NOT EXISTS ix_gtreg_ct ON game_turn_region(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gtb_ct ON game_turn_building(campaign_id, turn, region);
CREATE INDEX IF NOT EXISTS ix_gtb_b ON game_turn_building(building);
CREATE INDEX IF NOT EXISTS ix_gtd_ct ON game_turn_diplomacy(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gta_ct ON game_turn_army(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gtu_ct ON game_turn_unit(campaign_id, turn, cqi);
CREATE INDEX IF NOT EXISTS ix_gtm_ct ON game_turn_mission(campaign_id, turn);
CREATE INDEX IF NOT EXISTS ix_gtres_ct ON game_turn_resource(campaign_id, turn);
"""


def safe_hi(src, an=None) -> int:
    row = src.execute("SELECT MAX(decision_id) m FROM decisions").fetchone()
    return int(row[0] or 0)


def source_stats(src, hi):
    return None


def _n(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _b(v):
    return 1 if v else 0


def _load(src, decision_id):
    row = src.execute(
        "SELECT d.decision_id, d.campaign_id, d.turn, d.ts, c.campaign_key,"
        " bc.z AS camp, bw.z AS world"
        " FROM decisions d"
        " LEFT JOIN campaigns c ON c.campaign_id = d.campaign_id"
        " LEFT JOIN blobs bc ON bc.blob_id = d.campaign_blob"
        " LEFT JOIN blobs bw ON bw.blob_id = d.world_blob"
        " WHERE d.decision_id = %s", (decision_id,)).fetchone()
    if row is None:
        return None
    ents = {}
    for e in src.execute(
            "SELECT e.context_kind, e.context_id, b.z FROM entities e"
            " JOIN blobs b ON b.blob_id = e.features_blob"
            " WHERE e.decision_id = %s", (decision_id,)):
        try:
            ents.setdefault(e["context_kind"], []).append((e["context_id"],
                                                           json.loads(e["z"])))
        except (ValueError, TypeError):
            continue
    d = dict(row)
    for k in ("camp", "world"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except (ValueError, TypeError):
            d[k] = None
    d["entities"] = ents
    return d


def _pending(src, an, lo, hi):
    built = {(int(r["campaign_id"]), int(r["turn"])): int(r["decision_id"])
             for r in an.execute("SELECT campaign_id, turn, decision_id FROM game_turn_built")}
    rows = src.execute(
        "SELECT campaign_id, turn, MAX(decision_id) AS did FROM decisions"
        " WHERE decision_id <= %s AND turn IS NOT NULL"
        " GROUP BY campaign_id, turn ORDER BY MAX(decision_id)", (hi,)).fetchall()
    out = []
    for r in rows:
        key = (int(r["campaign_id"]), int(r["turn"]))
        did = int(r["did"])
        if built.get(key) != did:
            out.append((key[0], key[1], did))
        if len(out) >= TURNS_PER_PASS:
            break
    return out


def _clear(an, cid, turn):
    for t in TABLES:
        if t == "game_turn_built":
            continue
        an.execute("DELETE FROM %s WHERE campaign_id=%%s AND turn=%%s" % t, (cid, turn))


def _character_rows(kind, cqi, st, cid, turn):
    return (cid, turn, str(cqi), kind, st.get("subtype"), st.get("agent_type"),
            _n(st.get("rank")), _n(st.get("xp")), _n(st.get("skill_points")),
            st.get("region"), st.get("stance"), _n(st.get("units")), _n(st.get("hp")),
            _n(st.get("upkeep")), _b(st.get("wounded")), _b(st.get("is_leader")),
            _b(st.get("garrisoned")), _n(st.get("x")), _n(st.get("y")),
            _n(st.get("authority")), _n(st.get("loyalty")))


def _corruption_total(c):
    if not isinstance(c, dict):
        return None
    vals = [v for v in c.values() if isinstance(v, (int, float))]
    return float(sum(vals)) if vals else None


def _emit(an, cid, turn, rec):
    camp = rec["camp"] or {}
    world = rec["world"] or {}
    ents = rec["entities"]
    campaign_state = (ents.get("campaign") or [(None, {})])[0][1]

    tech = campaign_state.get("tech") or []
    cur_research = campaign_state.get("current_research")
    lords = ents.get("lord") or []
    heroes = ents.get("hero") or []
    provinces = ents.get("province") or []
    chars = [("lord", cqi, st) for cqi, st in lords] + [("hero", cqi, st) for cqi, st in heroes]
    relations = world.get("relations") or []
    regions = world.get("regions") or []
    armies = world.get("armies") or []
    hostiles = world.get("hostiles") or []
    missions = campaign_state.get("missions") or []
    equipped_all = campaign_state.get("equipped_all") or []
    anc_pool = campaign_state.get("anc_pool") or []

    n_units = sum(int(_n(st.get("units")) or 0) for _, _, st in chars)
    mine = {r.get("region") for r in regions if r.get("owner") == camp.get("faction")}

    an.execute(
        "INSERT INTO game_turn VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (cid, rec["campaign_key"], turn, rec["decision_id"], rec["ts"],
         camp.get("faction"), camp.get("campaign_map"), camp.get("leader"),
         _n(camp.get("difficulty")), _n(camp.get("treasury")), _n(camp.get("income")),
         _n(camp.get("settlements")), _n(camp.get("allies")), _n(camp.get("vassals")),
         _n(camp.get("lord_level")), _n(camp.get("armies")), _n(camp.get("power_rank")),
         sum(1 for t in tech if t.get("researched")), len(tech), cur_research,
         _n(campaign_state.get("research_points")),
         len(mine), len({r.get("province") for r in regions if r.get("region") in mine}),
         sum(1 for r in relations if r.get("at_war")), len(relations),
         len(chars), n_units, len(equipped_all) + len(anc_pool),
         sum(1 for m in missions if m.get("status") == "active"),
         _b(camp.get("defeated"))))

    if tech:
        an.cursor().executemany(
            "INSERT INTO game_turn_research VALUES(%s,%s,%s,%s,%s,%s,%s)",
            [(cid, turn, t.get("key"), _b(t.get("researched")), _b(t.get("can_research")),
              _n(t.get("cost")), _b(t.get("key") == cur_research)) for t in tech
             if t.get("key")])

    if chars:
        an.cursor().executemany(
            "INSERT INTO game_turn_character VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [_character_rows(k, cqi, st, cid, turn) for k, cqi, st in chars])

    sk, tr, it, un = [], [], [], []
    for _, cqi, st in chars:
        for s in (st.get("skills") or ()):
            if s.get("key"):
                sk.append((cid, turn, str(cqi), s["key"], _n(s.get("level")),
                           s.get("status"), _n(s.get("tier")), _n(s.get("total_levels"))))
        for t_, pts in (st.get("trait_progress") or {}).items():
            tr.append((cid, turn, str(cqi), t_, _n(pts)))
        for t_ in (st.get("traits") or ()):
            key = t_.get("key") if isinstance(t_, dict) else t_
            if key and key not in (st.get("trait_progress") or {}):
                tr.append((cid, turn, str(cqi), key, None))
        for a in (st.get("equipped") or ()):
            if a.get("key"):
                it.append((cid, turn, str(cqi), a["key"], 1, "equipped"))
        for a in (st.get("armory") or ()):
            key = a.get("key") if isinstance(a, dict) else a
            if key:
                it.append((cid, turn, str(cqi), key, 1, "armory"))
        for u in (st.get("unit_cards") or ()):
            if u.get("key"):
                un.append((cid, turn, str(cqi), u["key"], u.get("category"),
                           _n(u.get("strength_pct")), _n(u.get("xp"))))
    for a in anc_pool:
        if a.get("key"):
            it.append((cid, turn, None, a["key"], 0, "pool"))
    for rows, sql in ((sk, "INSERT INTO game_turn_skill VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"),
                      (tr, "INSERT INTO game_turn_trait VALUES(%s,%s,%s,%s,%s)"),
                      (it, "INSERT INTO game_turn_item VALUES(%s,%s,%s,%s,%s,%s)"),
                      (un, "INSERT INTO game_turn_unit VALUES(%s,%s,%s,%s,%s,%s,%s)")):
        if rows:
            an.cursor().executemany(sql, rows)

    pos = {r.get("region"): r for r in regions}
    prov_rows, build_rows = [], []
    seen_regions = set()
    for reg, st in provinces:
        r = pos.get(reg) or {}
        if reg in seen_regions:
            continue
        seen_regions.add(reg)
        prov_rows.append((
            cid, turn, reg, st.get("province") or r.get("province"),
            r.get("owner") or camp.get("faction"), 1, _b(r.get("capital")),
            _b(r.get("abandoned")), _n(st.get("public_order")),
            _corruption_total(st.get("corruption")), _n(st.get("growth_per_turn")),
            _n(st.get("income")), _n(st.get("gross_income")), _n(st.get("settlement_level")),
            _n(st.get("buildings")), _n(st.get("free_slots")), _n(st.get("max_slots")),
            _b(st.get("has_port")), _b(st.get("has_walls")), _n(r.get("x")), _n(r.get("y"))))
        for slot in (st.get("slot_states") or ()):
            idx = _i(slot.get("index"))
            if idx is None:
                continue
            build_rows.append((cid, turn, reg, idx, slot.get("key"),
                               slot.get("queued_key"), _b(slot.get("damaged")),
                               _b(slot.get("ruined"))))
    for r in regions:
        reg = r.get("region")
        if not reg or reg in seen_regions:
            continue
        seen_regions.add(reg)
        prov_rows.append((cid, turn, reg, r.get("province"), r.get("owner"),
                          _b(reg in mine), _b(r.get("capital")), _b(r.get("abandoned")),
                          None, None, None, None, None, None, None, None, None,
                          None, None, _n(r.get("x")), _n(r.get("y"))))
    if prov_rows:
        an.cursor().executemany(
            "INSERT INTO game_turn_region VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s)", prov_rows)
    if build_rows:
        an.cursor().executemany(
            "INSERT INTO game_turn_building VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", build_rows)

    if relations:
        seen = set()
        rows = []
        for r in relations:
            f = r.get("faction")
            if not f or f in seen:
                continue
            seen.add(f)
            rows.append((cid, turn, f, _n(r.get("standing")), _b(r.get("at_war")),
                         _b(r.get("allied")), _b(r.get("trade")), _b(r.get("nap")),
                         _b(r.get("mil_access")), _b(r.get("mil_ally")),
                         _b(r.get("def_ally")), _b(r.get("their_vassal")),
                         _b(r.get("our_master"))))
        an.cursor().executemany(
            "INSERT INTO game_turn_diplomacy VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            rows)

    army_rows = [(cid, turn, str(a.get("cqi")), camp.get("faction"), 0, a.get("subtype"),
                  _n(a.get("units")), _n(a.get("hp")), a.get("stance"), a.get("region"),
                  _n(a.get("x")), _n(a.get("y"))) for a in armies if a.get("cqi") is not None]
    army_rows += [(cid, turn, str(h.get("cqi")), h.get("faction"), 1, None,
                   _n(h.get("units")), _n(h.get("hp")), h.get("stance"), h.get("region"),
                   _n(h.get("x")), _n(h.get("y"))) for h in hostiles
                  if h.get("cqi") is not None and h.get("kind") == "army"]
    if army_rows:
        an.cursor().executemany(
            "INSERT INTO game_turn_army VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            army_rows)

    if missions:
        an.cursor().executemany(
            "INSERT INTO game_turn_mission VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(cid, turn, m.get("mission"), m.get("status"), _n(m.get("turns_remaining")),
              _b(m.get("is_quest")), _b(m.get("is_victory")), _b(m.get("pending")),
              m.get("issuer")) for m in missions if m.get("mission")])

    res = camp.get("resources") or {}
    if isinstance(res, dict) and res:
        an.cursor().executemany(
            "INSERT INTO game_turn_resource VALUES(%s,%s,%s,%s)",
            [(cid, turn, k, _n(v)) for k, v in res.items()])


def step(src, an, lo, hi):
    todo = _pending(src, an, lo, hi)
    written = 0
    watermark = lo
    for cid, turn, did in todo:
        rec = _load(src, did)
        if rec is None:
            continue
        _clear(an, cid, turn)
        try:
            _emit(an, cid, turn, rec)
        except Exception:
            _clear(an, cid, turn)
            raise
        an.execute("INSERT INTO game_turn_built VALUES(%s,%s,%s)"
                   " ON CONFLICT(campaign_id, turn) DO UPDATE SET decision_id=excluded.decision_id",
                   (cid, turn, did))
        written += 1
        watermark = max(watermark, did)
    if not todo:
        watermark = hi
    return watermark, written


TENANTS = (sys.modules[__name__],)
