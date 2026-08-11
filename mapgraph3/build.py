from __future__ import annotations

"""Decision record -> graph.

Every scalar goes through guard.Reader, so it arrives as a guard.Raw tagged with the one
entity and record path it came from. Combining two entities raises. That is the mechanism
that makes `strength_ratio`, ego-to-target distance, and reach envelopes unwritable rather
than merely discouraged.

The numeric budget is 22 values for the whole ontology (schema.TYPE_FIELDS). Everything
else is an edge or an identity embedding.
"""

import math
import sys

try:
    from mapgraph3 import schema as S
    from mapgraph3 import guard as G
    from mapgraph3 import catalogue as C
except ImportError:
    import schema as S
    import guard as G
    import catalogue as C

_TI = {t: i for i, t in enumerate(S.NODE_TYPES)}
_NREL = S.N_FORWARD_RELATIONS

MOBILE_KINDS = {"army": True, "neutral_army": True, "hero": False, "neutral_hero": False}


class Graph:
    __slots__ = ("_nodes", "_edges",
                 "x", "node_type", "race_idx", "agent_idx", "stance_idx", "subtype_idx",
                 "atype_idx", "term_idx", "cat_idx", "src", "dst", "rel",
                 "id2idx", "node_ids", "action_nodes", "action_keys", "own_mask", "g_ctx",
                 "player_faction", "counts", "provenance")

    def __init__(self):
        # Nodes accumulate as one tuple each and edges as a flat run of 6 ints, then
        # finalize() transposes them into the per-attribute lists everything downstream
        # reads. Appending to eleven parallel lists per node and six per edge was 3.5M
        # python-level append calls over a corpus walk, which is most of what building a
        # graph costs -- the work itself is a few dict lookups.
        self._nodes = []
        self._edges = []
        self.x = []
        self.node_type = []
        self.race_idx = []
        self.agent_idx = []
        self.stance_idx = []
        self.subtype_idx = []
        self.atype_idx = []
        self.term_idx = []
        self.cat_idx = []
        self.src, self.dst, self.rel = [], [], []
        self.id2idx = {}
        self.node_ids = []
        self.action_nodes = []
        # (context_kind, context_id, action_type, key) per action node, in order --
        # how the trainer locates the offer that was actually taken
        self.action_keys = []
        self.own_mask = []
        self.g_ctx = []
        self.player_faction = None
        self.counts = {}
        self.provenance = {}

    def add(self, nid, ntype, values=None, race=0, agent=0, stance=0, subtype=0,
            atype=0, term=0, cat=0, own=False):
        idx = self.id2idx.get(nid)
        if idx is not None:
            return idx
        pos = S.FIELD_POS[ntype]
        row = [0.0] * S.MAX_FIELDS
        for k, v in (values or {}).items():
            col = pos.get(k)
            if col is None:
                raise KeyError("mapgraph3.build: %r is not a field of node type %r -- the "
                               "numeric budget is fixed in schema.TYPE_FIELDS" % (k, ntype))
            if not isinstance(v, G.Raw):
                raise TypeError("mapgraph3.build: %s.%s was written as a plain number. "
                                "Every scalar must come through guard.Reader so its "
                                "provenance can be checked." % (ntype, k))
            row[col] = float(v)
            # Provenance is keyed by ENTITY, not by path: the same semantic field is
            # legitimately read from two collections (own armies vs enemy hostiles).
            # Cross-entity combination is prevented by guard.Raw raising, not here.
            self.provenance.setdefault("%s.%s" % (ntype, k), set()).add(v.eid.split(":")[0])
        idx = len(self._nodes)
        self.id2idx[nid] = idx
        self._nodes.append((nid, row, _TI[ntype], race, agent, stance, subtype,
                            atype, term, cat, 1.0 if own else 0.0))
        if ntype == "action":
            self.action_nodes.append(idx)
        return idx

    def edge(self, i, j, rel):
        """Both directions, as distinct relations: 'A owns B' is not 'B owns A'."""
        if i is None or j is None:
            return
        # One dict lookup and one extend. The reverse relation is the forward index plus
        # the forward count, which is exactly what rel_index(rel, True) returns.
        r = S.REL_INDEX[rel]
        self._edges.extend((i, j, r, j, i, r + _NREL))

    def finalize(self):
        """Transpose the buffers into the per-attribute lists the rest of the code reads.

        One zip and three strided slices, all at C level, instead of 17k python appends
        per graph. Idempotent, so calling it twice is harmless.
        """
        if self._nodes:
            cols = list(zip(*self._nodes))
            (self.node_ids, self.x, self.node_type, self.race_idx, self.agent_idx,
             self.stance_idx, self.subtype_idx, self.atype_idx, self.term_idx,
             self.cat_idx, self.own_mask) = [list(c) for c in cols]
        e = self._edges
        self.src, self.dst, self.rel = e[0::3], e[1::3], e[2::3]
        return self

    def cat_node(self, kind, key):
        if not key:
            return None
        nid = "%s:%s" % (kind, key)
        return self.add(nid, kind, cat=S.cat_global(kind, key))


def _pos_ok(x, y):
    return x is not None and y is not None and 0 <= float(x) < 4096 and 0 <= float(y) < 4096


def build_graph(record):
    world = record.get("world") or {}
    campaign = record.get("campaign") or {}
    me = str(campaign.get("faction") or "")
    g = Graph()
    g.player_faction = me

    citizenry = {str(c) for c in (world.get("citizenry") or [])}
    relations = {str(r.get("faction")): r for r in (world.get("relations") or [])
                 if r.get("faction")}

    prov_state, char_state = {}, {}
    for e in record.get("entities") or []:
        kind = e.get("context_kind")
        st = e.get("state") or {}
        if kind == "province" and st.get("region"):
            prov_state[str(st["region"])] = st
        elif kind in ("lord", "hero"):
            char_state[str(e.get("context_id"))] = (kind, st)

    # ---------------- factions ------------------------------------------
    region_rows = {str(r.get("region")): r for r in (world.get("regions") or [])
                   if r.get("region")}
    fac_keys = {me} | set(relations)
    fac_keys |= {str(r.get("owner")) for r in region_rows.values() if r.get("owner")}
    fac_keys |= {str(h.get("faction")) for h in (world.get("hostiles") or [])
                 if h.get("faction")}
    fac_keys.discard("")
    for fk in sorted(fac_keys):
        rd = G.Reader({"is_player": fk == me}, "faction:" + fk, "faction")
        fi = g.add("f:" + fk, "faction", {"is_player": rd.flag("is_player")},
                   race=S.race_index(fk), own=(fk == me))
        rn = g.cat_node("race", (fk.split("_")[2] if len(fk.split("_")) > 2 else ""))
        g.edge(fi, rn, "of_race")

    # diplomacy: one relation per treaty state, not five columns on a node
    mi = g.id2idx.get("f:" + me)
    for fk, rel in sorted(relations.items()):
        fj = g.id2idx.get("f:" + fk)
        if fj is None or fj == mi:
            continue
        g.edge(mi, fj, "dip_met")
        for flag, name in (("at_war", "dip_war"), ("allied", "dip_allied"),
                           ("trade", "dip_trade"), ("their_vassal", "dip_vassal"),
                           ("nap", "dip_nap"), ("mil_ally", "dip_mil_ally"),
                           ("def_ally", "dip_def_ally"), ("mil_access", "dip_mil_access")):
            if rel.get(flag):
                g.edge(mi, fj, name)

    # ---------------- regions, provinces, settlements ---------------------
    # public_order is read off the REGION interface; corruption off the PROVINCE
    # pooled-resource manager. Different owners, so different nodes.
    prov_of_region = {}
    for key, r in sorted(region_rows.items()):
        rd = G.Reader(r, "region:" + key, "world.regions[]")
        vals = {}
        if _pos_ok(r.get("x"), r.get("y")):
            vals["x"] = rd.num("x") / S.COORD_SCALE
            vals["y"] = rd.num("y") / S.COORD_SCALE
        st = prov_state.get(key)
        if st is not None:
            pd = G.Reader(st, "region:" + key, "entities.province.state")
            vals["public_order"] = (pd.num("public_order") / S.ORDER_SCALE).clip(-1.0, 1.0)
        ri = g.add("r:" + key, "region", vals, own=(str(r.get("owner") or "") == me))
        prov_of_region[key] = str(r.get("province") or "")
        fj = g.id2idx.get("f:" + str(r.get("owner") or ""))
        g.edge(fj, ri, "owns_region")

    # regions the record did not enumerate: referenced by a character or a province
    for key in list(prov_state) + [str(a.get("region") or "")
                                   for a in (world.get("armies") or [])]:
        if key and ("r:" + key) not in g.id2idx:
            st = prov_state.get(key)
            vals = {}
            if st is not None:
                pd = G.Reader(st, "region:" + key, "entities.province.state")
                vals["public_order"] = (pd.num("public_order") /
                                        S.ORDER_SCALE).clip(-1.0, 1.0)
            g.add("r:" + key, "region", vals)
            prov_of_region.setdefault(key, str((st or {}).get("province") or ""))

    for key, r in region_rows.items():
        i = g.id2idx["r:" + key]
        for adj in r.get("adjacent") or ():
            j = g.id2idx.get("r:" + str(adj))
            if j is not None and j > i:
                g.edge(i, j, "adj")

    for key, prov in sorted(prov_of_region.items()):
        if not prov:
            continue
        ri = g.id2idx["r:" + key]
        st = prov_state.get(key)
        vals = {}
        if st is not None:
            corr = st.get("corruption") or {}
            pd = G.Reader({"corruption": max([float(v) for v in corr.values()] or [0.0])},
                          "province:" + prov, "entities.province.state.corruption")
            vals["corruption"] = (pd.num("corruption") / S.CORRUPT_SCALE).clip(0.0, 1.0)
        pi = g.add("p:" + prov, "province", vals)
        g.edge(ri, pi, "in_prov")

    for s in world.get("settlements") or []:
        key = str(s.get("region") or "")
        if not key:
            continue
        sd = G.Reader(s, "settlement:" + key, "world.settlements[]")
        svals = {"garrison_units": sd.num("units") / S.UNITS_SCALE}
        if _pos_ok(s.get("x"), s.get("y")):
            svals["x"] = sd.num("x") / S.COORD_SCALE
            svals["y"] = sd.num("y") / S.COORD_SCALE
        si = g.add("s:" + key, "settlement", svals, own=True)
        ri = g.id2idx.get("r:" + key)
        if ri is None:
            ri = g.add("r:" + key, "region", {})
        g.edge(ri, si, "sett_of")
        g.edge(g.id2idx.get("f:" + me), si, "owns_sett")

    # ---------------- slots and buildings ---------------------------------
    # `buildings`, `built`, `free_slots`, `max_slots`, `locked_slots`, `building_now`
    # all become structure here: seven province columns replaced by nodes and edges.
    # A slot belongs to a REGION. This loop runs per region but used to name the node
    # "slot:<province>:<n>", so every region of a province wrote into the same slot
    # nodes: of 558 shared (province, slot) pairs, 341 (61.1%) ended up holding two
    # different built buildings. That is not a collapse, it is corruption -- the second
    # region's building overwrote the first one's on a node the model reads as one place.
    # The region node already exists and is one hop from the province via in_prov.
    slot_index = {}
    for key, st in sorted(prov_state.items()):
        ri = g.id2idx.get("r:" + key)
        if ri is None:
            continue
        try:
            n_slots = int(float(st.get("max_slots") or 0))
        except (TypeError, ValueError):
            n_slots = 0
        built = st.get("built") or {}
        locked = {str(v) for v in (st.get("locked_slots") or [])}
        now = st.get("building_now") or {}
        for sl in range(max(n_slots, len(built))):
            si = g.add("slot:%s:%d" % (key, sl), "slot", {})
            slot_index[(key, sl)] = si
            g.edge(ri, si, "has_slot")
            if str(sl) in locked:
                g.edge(si, si, "slot_locked")
            bkey = built.get(str(sl)) or built.get(sl)
            if bkey:
                bi = g.cat_node("building", str(bkey))
                g.edge(si, bi, "slot_filled")
                ch = C.chain_of(str(bkey))
                if ch:
                    g.edge(bi, g.cat_node("chain", ch), "of_chain")
            nk = now.get(str(sl)) or now.get(sl)
            if isinstance(nk, dict):
                nk = nk.get("key")
            if nk:
                g.edge(si, g.cat_node("building", str(nk)), "slot_building")

    # ---------------- characters ------------------------------------------
    char_faction = {}
    for a in world.get("armies") or []:
        cqi = str(a.get("cqi") or "")
        if not cqi or cqi in citizenry:
            continue
        kind, st = char_state.get(cqi, (None, {}))
        row = dict(a)
        row.update({k: v for k, v in (st or {}).items() if v is not None})
        rd = G.Reader(row, "char:" + cqi, "world.armies[]")
        is_hero = (kind == "hero") or not a.get("has_army")
        ntype = "hero" if is_hero else "lord"
        vals = {"rank": rd.num("rank") / S.RANK_SCALE,
                "ap_pct": rd.num("ap_pct").clip(0.0, 1.0)}
        if _pos_ok(row.get("x"), row.get("y")):
            vals["x"] = rd.num("x") / S.COORD_SCALE
            vals["y"] = rd.num("y") / S.COORD_SCALE
        if ntype == "lord":
            vals["units"] = rd.num("units") / S.UNITS_SCALE
            vals["hp"] = rd.num("hp") / S.HP_SCALE
        ci = g.add("c:" + cqi, ntype, vals,
                   agent=S.agent_index(a.get("agent_type")),
                   stance=S.stance_index(a.get("stance")),
                   subtype=S.subtype_index(a.get("subtype")), own=True)
        char_faction[cqi] = me
        _wire_char(g, ci, cqi, row, me, prov_of_region, st)

    for h in world.get("hostiles") or []:
        hk = str(h.get("kind") or "")
        if hk not in MOBILE_KINDS or h.get("visible") is False:
            continue
        cqi = str(h.get("cqi") or "")
        if not cqi or cqi in citizenry or ("c:" + cqi) in g.id2idx:
            continue
        rd = G.Reader(h, "char:" + cqi, "world.hostiles[]")
        ntype = "lord" if MOBILE_KINDS[hk] else "hero"
        # enemy characters carry no ap_pct in the record; leave it absent rather than
        # inventing a 0.0 and tagging it as if it had been read
        vals = {"rank": rd.num("rank") / S.RANK_SCALE}
        if _pos_ok(h.get("x"), h.get("y")):
            vals["x"] = rd.num("x") / S.COORD_SCALE
            vals["y"] = rd.num("y") / S.COORD_SCALE
        if ntype == "lord":
            vals["units"] = rd.num("units") / S.UNITS_SCALE
            vals["hp"] = rd.num("hp") / S.HP_SCALE
        ci = g.add("c:" + cqi, ntype, vals,
                   agent=S.agent_index(h.get("agent_type")),
                   stance=S.stance_index(h.get("stance")),
                   subtype=S.subtype_index(h.get("subtype")))
        char_faction[cqi] = str(h.get("faction") or "")
        _wire_char(g, ci, cqi, h, char_faction[cqi], prov_of_region, None)

    # ---------------- enemy settlements -----------------------------------
    # MOBILE_KINDS dropped every hostile of kind "settlement", so an enemy settlement had
    # no node at all. The target was never missing from the snapshot -- 0 of 2,571
    # attack_settlement targets were, 44.6% of them arriving through hostiles rather than
    # world.regions -- it simply had nowhere to point. One node type fixes three things at
    # once: attack_settlement gets a target, `besieging` gets somewhere to attach (it had
    # 0 edges in 653 graphs), and garrison targets resolve.
    for h in world.get("hostiles") or []:
        if str(h.get("kind") or "") != "settlement":
            continue
        rkey = str(h.get("region") or "")
        if not rkey or ("s:" + rkey) in g.id2idx:
            continue
        hd = G.Reader(h, "settlement:" + rkey, "world.hostiles[kind=settlement]")
        vals = {"garrison_units": hd.num("units") / S.UNITS_SCALE}
        if _pos_ok(h.get("x"), h.get("y")):
            vals["x"] = hd.num("x") / S.COORD_SCALE
            vals["y"] = hd.num("y") / S.COORD_SCALE
        si = g.add("s:" + rkey, "settlement", vals, own=False)
        ri = g.id2idx.get("r:" + rkey)
        if ri is None:
            ri = g.add("r:" + rkey, "region", {}, own=False)
        g.edge(ri, si, "sett_of")
        fj = g.id2idx.get("f:" + str(h.get("faction") or ""))
        if fj is not None:
            g.edge(fj, si, "owns_sett")

    # _wire_knn reads node_type and x to find characters, so the node buffers have to be
    # materialised before it runs. It only ADDS edges, so the final finalize() below
    # picks those up; finalize is idempotent.
    g.finalize()
    _wire_knn(g)

    # ---------------- actions ---------------------------------------------
    n_offers = 0
    groups = {}
    for e in record.get("entities") or []:
        ck, cid = e.get("context_kind"), str(e.get("context_id"))
        ego = _ego_of(g, ck, cid, prov_of_region, me)
        for o in e.get("offers") or []:
            n_offers += 1
            _add_action(g, o, ego, ck, cid, groups, prov_of_region, slot_index, me)

    g.finalize()
    if not g.src:
        return None

    cd = G.Reader(campaign, "campaign", "campaign")
    g.g_ctx = [
        float(cd.num("turn") / 20.0),
        float((cd.num("treasury") / S.TREASURY_SCALE).clip(-5.0, 5.0)),
        float((cd.num("income") / S.TREASURY_SCALE).clip(-5.0, 5.0)),
        float(cd.num("settlements") / 10.0),
        float(cd.num("armies") / 5.0),
        float(cd.num("allies") / 3.0),
        float(cd.num("vassals") / 3.0),
        float((cd.num("power_rank") / 300.0).clip(-1.0, 1.0)),
        float(cd.num("lord_level") / 10.0),
        math.log1p(n_offers) / 7.0,
    ]
    g.finalize()
    g.counts = {"nodes": len(g.x), "edges": len(g.src), "actions": len(g.action_nodes),
                "regions": len(region_rows), "offers": n_offers}
    return g


def _wire_char(g, ci, cqi, row, faction, prov_of_region, st):
    g.edge(g.id2idx.get("f:" + str(faction or "")), ci, "owns_char")
    rkey = str(row.get("region") or "")
    ri = g.id2idx.get("r:" + rkey)
    if ri is not None:
        g.edge(ci, ri, "at_region")
    prov = str(row.get("province") or "") or prov_of_region.get(rkey, "")
    pi = g.id2idx.get("p:" + prov)
    if pi is not None:
        g.edge(ci, pi, "in_province")          # province-wide lord modifiers
    si = g.id2idx.get("s:" + rkey)
    if si is not None:
        if row.get("garrisoned"):
            g.edge(ci, si, "at_sett")
        if row.get("besieging"):
            g.edge(ci, si, "besieging")
    for uk in (st or {}).get("pending_recruit_keys") or ():
        g.edge(ci, g.cat_node("unit", str(uk)), "queued")


def _wire_knn(g):
    pts = []
    xi = S.TYPE_FIELDS["lord"].index("x")
    yi = S.TYPE_FIELDS["lord"].index("y")
    for i, t in enumerate(g.node_type):
        if S.NODE_TYPES[t] in ("lord", "hero"):
            pts.append((i, g.x[i][xi], g.x[i][yi]))
    if len(pts) < 2:
        return
    for i, x0, y0 in pts:
        d = sorted(((math.hypot(x0 - x1, y0 - y1), j) for j, x1, y1 in pts if j != i))
        for _, j in d[:S.KNN_K]:
            if j > i:
                g.edge(i, j, "near")


def _ego_of(g, ck, cid, prov_of_region, me):
    if ck in ("lord", "hero"):
        return g.id2idx.get("c:" + cid)
    if ck == "province":
        prov = prov_of_region.get(cid) or ""
        return g.id2idx.get("p:" + prov) or g.id2idx.get("r:" + cid)
    return g.id2idx.get("f:" + me)


_CAT_OF_TYPE = {"recruit_unit": "unit", "recruit_ror": "unit", "recruit_blessed": "unit",
                "recruit_imperial": "unit", "raise_dead": "unit",
                # recruit_lord/_hero key a character SUBTYPE, which is not a unit key and
                # was in no catalogue, so 560 of the offers in a 40-graph sample linked to
                # nothing at all. 565 subtypes in reference.agent_permitted_subtypes.
                "recruit_lord": "agent_subtype", "recruit_hero": "agent_subtype",
                "research": "tech", "skills": "skill", "rites": "ritual",
                "edict": "edict", "items": "item", "item_unequip": "item",
                "building": "building", "building_repair": "building",
                "building_dismantle": "building", "building_cancel": "building",
                "horde_building": "building"}


def _add_action(g, o, ego, ck, cid, groups, prov_of_region, slot_index, me):
    at = str(o.get("action_type") or "")
    key = str(o.get("key") or "")
    params = o.get("params") or {}
    rd = G.Reader({"available": o.get("available")}, "offer:%s:%s:%s" % (ck, cid, key),
                  "offers[]")
    term = 0
    if at == "diplomacy":
        term = S.term_index(key.split(":", 1)[-1])
    avals = {"available": rd.flag("available")}
    if _pos_ok(params.get("x"), params.get("y")):
        pd = G.Reader(params, "offer:%s:%s:%s" % (ck, cid, key), "offers[].params")
        avals["x"] = pd.num("x") / S.COORD_SCALE
        avals["y"] = pd.num("y") / S.COORD_SCALE
    ai = g.add("a:%s:%s:%s:%d" % (ck, cid, key, len(g.action_nodes)), "action",
               avals, atype=S.atype_index(at), term=term)
    g.action_keys.append((str(ck), str(cid), at, key))
    g.edge(ai, ego, "act_actor")

    grp = (ego, at)
    gi = groups.get(grp)
    if gi is None:
        gi = g.add("cg:%s:%s" % (ego, at), "cgroup", {})
        groups[grp] = gi
        g.edge(gi, ego, "of_ego")
    g.edge(ai, gi, "in_group")

    # what it instantiates -- the shared catalogue node
    kind = _CAT_OF_TYPE.get(at)
    if kind:
        ck_key = key.split("@", 1)[0] if at.startswith("recruit") else key
        if at in ("building_repair", "building_dismantle", "building_cancel",
                  "horde_building"):
            # These are keyed by the slot they act on, so the building is in params.
            ck_key = str(params.get("building_key") or "") or None
        elif at == "building":
            # `building` has no params.building_key -- its action_key IS the building key,
            # and it joins the catalogue 912/912. Reading a field that does not exist for
            # this type meant every construction offer built no act_on edge at all.
            ck_key = key or None
        if at in ("items", "item_unequip"):
            ck_key = str(params.get("item_key") or "") or None
        if ck_key:
            g.edge(ai, g.cat_node(kind, ck_key), "act_on")

    # Computed once. _target_of only reads id2idx entries under the c:/s:/r:/f: prefixes,
    # and the only node the hero_action branch can add is an agent_action catalogue node,
    # so hoisting it above that branch cannot change what it returns. Edge emission order
    # is unchanged.
    tgt = _target_of(g, at, key, params)

    if at == "hero_action":
        akey = str(params.get("action_key") or "")
        if akey:
            g.edge(ai, g.cat_node("agent_action", akey), "act_on")
        ability = str(params.get("ability") or "") or C.ability_of(akey)
        if ability in S.ABILITY_RELATIONS and tgt is not None:
            g.edge(ai, tgt, ability)

    if tgt is not None:
        g.edge(ai, tgt, "act_target")

    subject = params.get("faction") or params.get("target_faction")
    if subject:
        g.edge(ai, g.id2idx.get("f:" + str(subject)), "act_subject")

    if params.get("slot_index") is not None:
        rkey = str(params.get("region") or cid)
        try:
            si = slot_index.get((rkey, int(float(params["slot_index"]))))
        except (TypeError, ValueError):
            si = None
        g.edge(ai, si, "act_slot")


def _target_of(g, at, key, params):
    if params.get("target_cqi") is not None:
        return g.id2idx.get("c:" + str(params["target_cqi"]))
    if at in ("attack_settlement", "colonize", "garrison"):
        # `garrison` is keyed "settlement:<region>" while the nodes are "s:<region>", so
        # the lookup missed on all 1,973 garrison offers -- every one of them was an
        # action with no target edge. Nothing else in the corpus carries the prefix, so
        # stripping it is safe for the other two types.
        rkey = str(key)
        if rkey.startswith("settlement:"):
            rkey = rkey[len("settlement:"):]
        return g.id2idx.get("s:" + rkey) or g.id2idx.get("r:" + rkey)
    if at == "diplomacy":
        return g.id2idx.get("f:" + str(key).split(":", 1)[0])
    if params.get("region"):
        return (g.id2idx.get("s:" + str(params["region"]))
                or g.id2idx.get("r:" + str(params["region"])))
    return None
