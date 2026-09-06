from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import store as _store
from decisions import dicts as dicts_mod

ACQ_STEP = 5000

NAME = "acquisition"
FORMULA_VERSION = 4
DEPENDS_ON = ()
TABLES = ("acquisition",)

_INSERT = (
    "INSERT INTO acquisition(campaign_id, family, key_id, ctx, sub_id, kind_id,"
    " first_seen_snapshot, first_seen_turn, acquired_snapshot, acquired_turn, ranks)"
    " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    " ON CONFLICT(campaign_id, family, key_id, ctx) DO UPDATE SET"
    " sub_id = COALESCE(acquisition.sub_id, excluded.sub_id),"
    " kind_id = COALESCE(acquisition.kind_id, excluded.kind_id),"
    " acquired_snapshot = COALESCE(acquisition.acquired_snapshot,"
    "                              excluded.acquired_snapshot),"
    " acquired_turn = COALESCE(acquisition.acquired_turn, excluded.acquired_turn),"
    " ranks = GREATEST(COALESCE(acquisition.ranks, 0),"
    "                  COALESCE(excluded.ranks, 0))")


def log(msg):
    sys.stderr.write("%.3f  acq %s\n" % (time.time(), msg))


class _SetCache:

    def __init__(self, src, sql, cap=200000):
        self.src = src
        self.sql = sql
        self.cache: dict = {}
        self.cap = cap

    def get_many(self, set_ids):
        want = sorted({s for s in set_ids if s is not None
                       and s not in self.cache})
        if want:
            if len(self.cache) > self.cap:
                self.cache.clear()
            for s in want:
                self.cache[s] = []
            for row in self.src.execute(self.sql, (want,)):
                self.cache[row[0]].append(tuple(row)[1:])
        return {s: self.cache.get(s) or [] for s in set_ids if s is not None}


class _Acquisition:
    NAME = NAME
    FORMULA_VERSION = FORMULA_VERSION
    DEPENDS_ON = DEPENDS_ON
    TABLES = TABLES

    def __init__(self):
        self._dicts = None
        self._enums = None
        self._caches = None

    def safe_hi(self, src, an=None):
        row = src.execute("SELECT MAX(decision_id) m FROM corpus.decision").fetchone()
        return int(row[0] or 0)

    def _init(self, src, an):
        if self._dicts is None:
            self._dicts = dicts_mod.Dicts(an)
            self._enums = {
                "active": self._dicts.resolve_enum("skill_status",
                                                   ["active"])["active"],
                "lord": self._dicts.resolve_enum("entity_kind", ["lord"])["lord"],
                "hero": self._dicts.resolve_enum("entity_kind", ["hero"])["hero"],
            }
            self._caches = {
                "tech": _SetCache(src, "SELECT set_id, tech_node_id, researched,"
                                       " can_research FROM corpus.tech_set_member"
                                       " WHERE set_id = ANY(%s)"
                                       " ORDER BY set_id, ord"),
                "items": _SetCache(src, "SELECT set_id, ancillary_id"
                                        " FROM corpus.item_slot_set_member"
                                        " WHERE set_id = ANY(%s)"
                                        " AND ancillary_id IS NOT NULL"
                                        " ORDER BY set_id, ord"),
                "skills": _SetCache(src, "SELECT set_id, skill_id, status_id,"
                                         " level FROM corpus.skill_set_member"
                                         " WHERE set_id = ANY(%s)"
                                         " ORDER BY set_id, ord"),
                "traits": _SetCache(src, "SELECT set_id, trait_id, level"
                                         " FROM corpus.trait_set_member"
                                         " WHERE set_id = ANY(%s)"
                                         " ORDER BY set_id, ord"),
                "trait_progress": _SetCache(src, "SELECT set_id, trait_id, points"
                                                 " FROM corpus.trait_progress_set_member"
                                                 " WHERE set_id = ANY(%s)"
                                                 " ORDER BY set_id, ord"),
                "built": _SetCache(src, "SELECT set_id, building_id"
                                        " FROM corpus.built_set_member"
                                        " WHERE set_id = ANY(%s)"
                                        " ORDER BY set_id, ord"),
                "building_now": _SetCache(src, "SELECT set_id, building_id"
                                               " FROM corpus.building_now_set_member"
                                               " WHERE set_id = ANY(%s)"
                                               " ORDER BY set_id, ord"),
                "buildable": _SetCache(src, "SELECT set_id, building_id, active"
                                            " FROM corpus.buildable_set_member"
                                            " WHERE set_id = ANY(%s)"
                                            " ORDER BY set_id, ord"),
                "settlements": _SetCache(src, "SELECT set_id, region_id"
                                              " FROM corpus.settlement_set_member"
                                              " WHERE set_id = ANY(%s)"
                                              " AND region_id IS NOT NULL"
                                              " ORDER BY set_id, ord"),
            }

    def _sub_id(self, subtype_id, agent_type_key):
        if subtype_id is not None:
            return subtype_id
        if not agent_type_key:
            return None
        return self._dicts.resolve("agent_subtype",
                                   [agent_type_key])[agent_type_key]

    def step(self, src, an, lo, hi):
        watermark = lo
        written = 0
        while watermark < hi:
            watermark, n = self._step_chunk(src, an, watermark, hi)
            written += n
        return watermark, written

    def _step_chunk(self, src, an, lo, hi):
        t0 = time.time()
        self._init(src, an)
        hi2 = min(hi, lo + ACQ_STEP)
        snaps = {int(r[0]): (int(r[1]), int(r[2])) for r in src.execute(
            "SELECT s.snapshot_id, s.campaign_id, s.turn FROM corpus.snapshot s"
            " JOIN corpus.decision d ON d.decision_id = s.snapshot_id"
            " WHERE s.snapshot_id > %s AND s.snapshot_id <= %s", (lo, hi2))}
        if not snaps:
            return hi2, 0
        active = self._enums["active"]
        lord, hero = self._enums["lord"], self._enums["hero"]
        facts = []

        cs_rows = src.execute(
            "SELECT snapshot_id, tech_set_id, anc_pool_set_id, equipped_all_set_id"
            " FROM corpus.campaign_state"
            " WHERE snapshot_id > %s AND snapshot_id <= %s"
            " ORDER BY snapshot_id", (lo, hi2)).fetchall()
        tech = self._caches["tech"].get_many([r["tech_set_id"] for r in cs_rows])
        items = self._caches["items"].get_many(
            [r["anc_pool_set_id"] for r in cs_rows]
            + [r["equipped_all_set_id"] for r in cs_rows])
        for r in cs_rows:
            did = r["snapshot_id"]
            for node_id, researched, can_research in tech[r["tech_set_id"]]:
                if researched or can_research:
                    facts.append((did, "research", node_id, "", None, None,
                                  researched, None))
            for (anc_id,) in items[r["anc_pool_set_id"]]:
                facts.append((did, "items", anc_id, "", None, None, False, None))
            for (anc_id,) in items[r["equipped_all_set_id"]]:
                facts.append((did, "items", anc_id, "", None, None, True, None))

        ch_rows = src.execute(
            "SELECT cs.snapshot_id, ch.cqi, cs.is_hero, cs.subtype_id,"
            " at.key AS agent_type, cs.skill_set_id, cs.equipped_set_id,"
            " ce.trait_set_id, ce.trait_progress_set_id"
            " FROM corpus.char_state cs"
            " JOIN corpus.character ch USING (character_id)"
            " LEFT JOIN dict.agent_type at ON at.id = cs.agent_type_id"
            " LEFT JOIN corpus.char_state_ext ce"
            "  ON ce.snapshot_id = cs.snapshot_id"
            "  AND ce.character_id = cs.character_id"
            " WHERE cs.snapshot_id > %s AND cs.snapshot_id <= %s"
            " ORDER BY cs.snapshot_id", (lo, hi2)).fetchall()
        skills = self._caches["skills"].get_many(
            [r["skill_set_id"] for r in ch_rows])
        equipped = self._caches["items"].get_many(
            [r["equipped_set_id"] for r in ch_rows])
        traits = self._caches["traits"].get_many(
            [r["trait_set_id"] for r in ch_rows])
        progress = self._caches["trait_progress"].get_many(
            [r["trait_progress_set_id"] for r in ch_rows])
        for r in ch_rows:
            did = r["snapshot_id"]
            ctx = str(r["cqi"])
            sub = self._sub_id(r["subtype_id"], r["agent_type"])
            kind = hero if r["is_hero"] else lord
            for skill_id, status_id, level in skills[r["skill_set_id"]]:
                acquired = level > 0
                if acquired or status_id == active:
                    facts.append((did, "skills", skill_id, ctx, sub, kind,
                                  acquired, level if acquired else None))
            for (anc_id,) in equipped[r["equipped_set_id"]]:
                facts.append((did, "items", anc_id, "", None, kind, True, None))
            materialized = set()
            for trait_id, level in traits.get(r["trait_set_id"]) or []:
                materialized.add(trait_id)
                facts.append((did, "traits", trait_id, ctx, sub, kind,
                              level > 0, level if level > 0 else None))
            for trait_id, points in progress.get(r["trait_progress_set_id"]) or []:
                if trait_id not in materialized and points > 0:
                    facts.append((did, "traits", trait_id, ctx, sub, kind,
                                  False, None))

        pv_rows = src.execute(
            "SELECT ps.snapshot_id, dr.key AS region, ps.built_set_id,"
            " ps.building_now_set_id, ps.buildable_set_id"
            " FROM corpus.province_state ps"
            " JOIN dict.region dr ON dr.id = ps.region_id"
            " WHERE ps.snapshot_id > %s AND ps.snapshot_id <= %s"
            " ORDER BY ps.snapshot_id", (lo, hi2)).fetchall()
        built = self._caches["built"].get_many(
            [r["built_set_id"] for r in pv_rows])
        now = self._caches["building_now"].get_many(
            [r["building_now_set_id"] for r in pv_rows])
        buildable = self._caches["buildable"].get_many(
            [r["buildable_set_id"] for r in pv_rows])
        for r in pv_rows:
            did = r["snapshot_id"]
            region = str(r["region"] or "")
            for (building_id,) in built.get(r["built_set_id"]) or []:
                facts.append((did, "building", building_id, region, None, None,
                              True, None))
            for (building_id,) in now.get(r["building_now_set_id"]) or []:
                facts.append((did, "building", building_id, region, None, None,
                              True, None))
            for building_id, is_active in buildable.get(r["buildable_set_id"]) or []:
                if is_active:
                    facts.append((did, "building", building_id, region, None,
                                  None, False, None))

        wr_rows = src.execute(
            "SELECT sw.snapshot_id, sw.settlement_set_id"
            " FROM corpus.snapshot_world sw"
            " JOIN corpus.decision d ON d.decision_id = sw.snapshot_id"
            " WHERE sw.snapshot_id > %s AND sw.snapshot_id <= %s"
            " ORDER BY sw.snapshot_id", (lo, hi2)).fetchall()
        setts = self._caches["settlements"].get_many(
            [r["settlement_set_id"] for r in wr_rows])
        for r in wr_rows:
            did = r["snapshot_id"]
            for (region_id,) in setts.get(r["settlement_set_id"]) or []:
                facts.append((did, "settlement", region_id, "", None, None,
                              True, None))

        agg: dict = {}
        facts.sort(key=lambda f: f[0])
        for did, family, key_id, ctx, sub, kind, acquired, ranks in facts:
            cid, turn = snaps[did]
            k = (cid, family, key_id, ctx)
            e = agg.get(k)
            if e is None:
                e = agg[k] = {"sub": sub, "kind": kind, "st": turn, "sd": did,
                              "at": None, "ad": None, "rk": None}
            if e["sub"] is None and sub is not None:
                e["sub"] = sub
            if e["kind"] is None and kind is not None:
                e["kind"] = kind
            if acquired:
                if e["at"] is None or (did < (e["ad"] or did + 1)):
                    e["at"], e["ad"] = turn, did
                if ranks is not None and (e["rk"] is None or ranks > e["rk"]):
                    e["rk"] = ranks
        batch = [(cid, family, key_id, ctx, e["sub"], e["kind"], e["sd"], e["st"],
                  e["ad"], e["at"], e["rk"])
                 for (cid, family, key_id, ctx), e in agg.items()]
        if batch:
            _store.executemany(an, _INSERT, batch)
        log("step %d..%d exit %.0f ms facts=%d rows=%d"
            % (lo, hi2, (time.time() - t0) * 1000, len(facts), len(batch)))
        return hi2, len(batch)


ACQUISITION = _Acquisition()

TENANTS = (ACQUISITION,)
