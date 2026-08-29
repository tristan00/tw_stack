from __future__ import annotations


import math
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import guard as G
from advisor.mapgraph import catalogue as C
from advisor import memory as M

_TI = {t: i for i, t in enumerate(S.NODE_TYPES)}
_NREL = S.N_FORWARD_RELATIONS

MOBILE_KINDS = {"army": True, "neutral_army": True, "hero": False, "neutral_hero": False}

_SKILL_REL = {"active": "skill_active", "locked_due_to_rank": "skill_rank_locked",
              "inactive": "skill_inactive"}


class Graph:
    __slots__ = ("_nodes", "_edges",
                 "x", "node_type", "race_idx", "agent_idx", "stance_idx", "subtype_idx",
                 "atype_idx", "term_idx", "cat_idx", "src", "dst", "rel",
                 "id2idx", "node_ids", "action_nodes", "action_keys", "own_mask", "g_ctx",
                 "player_faction", "counts", "provenance")

    def __init__(self):
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
                raise KeyError("mapgraph.build: %r is not a field of node type %r -- the "
                               "numeric budget is fixed in schema.TYPE_FIELDS" % (k, ntype))
            if not isinstance(v, G.Raw):
                raise TypeError("mapgraph.build: %s.%s was written as a plain number. "
                                "Every scalar must come through guard.Reader so its "
                                "provenance can be checked." % (ntype, k))
            row[col] = float(v)
            self.provenance.setdefault("%s.%s" % (ntype, k), set()).add(v.eid.split(":")[0])
        idx = len(self._nodes)
        self.id2idx[nid] = idx
        self._nodes.append((nid, row, _TI[ntype], race, agent, stance, subtype,
                            atype, term, cat, 1.0 if own else 0.0))
        if ntype == "action":
            self.action_nodes.append(idx)
        return idx

    def edge(self, i, j, rel):
        if i is None or j is None:
            return
        r = S.REL_INDEX[rel]
        self._edges.extend((i, j, r, j, i, r + _NREL))

    def finalize(self):
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
        idx = self.id2idx.get(nid)
        if idx is not None:
            return idx
        idx = self.add(nid, kind, _cat_values(kind, key, nid),
                       cat=S.cat_global(kind, key))
        if kind == "building":
            ch = C.chain_of(key)
            if ch:
                self.edge(idx, self.cat_node("chain", ch), "of_chain")
        return idx


def _cat_values(kind, key, nid):
    row = C.attrs().get(kind, {}).get(key)
    if not row:
        return None
    rd = G.Reader(row, nid, "reference.%s" % kind)
    out = {}
    for name in S.TYPE_FIELDS[kind]:
        if name in row:
            out[name] = rd.num(name)
    return out or None


def _pos_ok(x, y):
    return x is not None and y is not None and 0 <= float(x) < 4096 and 0 <= float(y) < 4096


def build_graph(record):
    world = record.get("world") or {}
    campaign = record.get("campaign") or {}
    me = str(campaign.get("faction") or "")
    g = Graph()
    g.player_faction = me

    citizenry = {str(c) for c in (world.get("citizenry") or [])}
    _armies_by_cqi = {str(a.get("cqi")): a for a in (world.get("armies") or [])}
    own_citizenry = {}
    for _c in citizenry:
        _a = _armies_by_cqi.get(_c)
        if _a is not None and _a.get("region"):
            own_citizenry[str(_a.get("region"))] = _a
    relations = {str(r.get("faction")): r for r in (world.get("relations") or [])
                 if r.get("faction")}

    prov_state, char_state, camp_state = {}, {}, {}
    for e in record.get("entities") or []:
        kind = e.get("context_kind")
        st = e.get("state") or {}
        if kind == "province" and st.get("region"):
            prov_state[str(st["region"])] = st
        elif kind in ("lord", "hero"):
            char_state[str(e.get("context_id"))] = (kind, st)
        elif kind == "campaign":
            camp_state = st

    region_rows = {str(r.get("region")): r for r in (world.get("regions") or [])
                   if r.get("region")}
    fac_keys = {me} | set(relations)
    fac_keys |= {str(r.get("owner")) for r in region_rows.values() if r.get("owner")}
    fac_keys |= {str(h.get("faction")) for h in (world.get("hostiles") or [])
                 if h.get("faction")}
    fac_keys.discard("")
    for fk in sorted(fac_keys):
        rel = relations.get(fk) or {}
        rd = G.Reader({"is_player": fk == me, "standing": rel.get("standing")},
                      "faction:" + fk, "faction")
        vals = {"is_player": rd.flag("is_player")}
        if rel.get("standing") is not None:
            vals["standing"] = rd.num("standing")
        if fk == me:
            cd = G.Reader(campaign, "campaign", "campaign")
            vals["treasury"] = cd.num("treasury")
            vals["income"] = cd.num("income")
            vals["turn"] = cd.num("turn")
            _m = str(campaign.get("campaign_map") or "")
            if _m in ("wh3_main_combi", "wh3_main_chaos"):
                md = G.Reader({"map": 1.0 if _m == "wh3_main_combi" else -1.0},
                              "campaign", "campaign")
                vals["campaign_map"] = md.num("map")
        fi = g.add("f:" + fk, "faction", vals,
                   race=S.race_index(fk), cat=S.cat_global("faction", fk),
                   own=(fk == me))
        rn = g.cat_node("race", (fk.split("_")[2] if len(fk.split("_")) > 2 else ""))
        g.edge(fi, rn, "of_race")

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

    for row in (world.get("war_graph") or ()):
        ai = g.id2idx.get("f:" + str(row.get("faction") or ""))
        if ai is None:
            continue
        for peer in row.get("at_war_with") or ():
            bi = g.id2idx.get("f:" + str(peer))
            if bi is not None and bi != ai:
                g.edge(ai, bi, "dip_war")

    for t in camp_state.get("tech") or ():
        tk = str((t or {}).get("key") or "")
        if not tk:
            continue
        g.edge(mi, g.cat_node("tech", tk),
               "tech_researched" if t.get("researched")
               else "tech_available" if t.get("can_research") else "tech_locked")
    cur = camp_state.get("current_research")
    if isinstance(cur, dict):
        cur = cur.get("key")
    if cur:
        g.edge(mi, g.cat_node("tech", str(cur)), "researching")

    prov_of_region = {}
    region_xy = []
    for key, r in sorted(region_rows.items()):
        rd = G.Reader(r, "region:" + key, "world.regions[]")
        vals = {}
        if _pos_ok(r.get("x"), r.get("y")):
            vals["x"] = rd.num("x")
            vals["y"] = rd.num("y")
        vals["is_capital"] = rd.flag("capital")
        vals["is_ruin"] = rd.flag("abandoned")
        st = prov_state.get(key)
        if st is not None:
            pd = G.Reader(st, "region:" + key, "entities.province.state")
            vals["public_order"] = pd.num("public_order")
            vals["income"] = pd.num("income")
        ri = g.add("r:" + key, "region", vals, own=(str(r.get("owner") or "") == me))
        if _pos_ok(r.get("x"), r.get("y")):
            region_xy.append((float(r["x"]), float(r["y"]), ri))
        prov_of_region[key] = str(r.get("province") or "")
        fj = g.id2idx.get("f:" + str(r.get("owner") or ""))
        g.edge(fj, ri, "owns_region")

    for key in list(prov_state) + [str(a.get("region") or "")
                                   for a in (world.get("armies") or [])]:
        if key and ("r:" + key) not in g.id2idx:
            st = prov_state.get(key)
            vals = {}
            if st is not None:
                pd = G.Reader(st, "region:" + key, "entities.province.state")
                vals["public_order"] = pd.num("public_order")
                vals["income"] = pd.num("income")
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
            vals["corruption"] = pd.num("corruption")
            sd = G.Reader(st, "province:" + prov, "entities.province.state")
            if st.get("growth_per_turn") is not None:
                vals["growth_per_turn"] = sd.num("growth_per_turn")
            if st.get("free_slots") is not None:
                vals["free_slots"] = sd.num("free_slots")
            if st.get("max_slots") is not None:
                vals["max_slots"] = sd.num("max_slots")
            if st.get("settlement_level") is not None:
                vals["settlement_level"] = sd.num("settlement_level")
            if st.get("can_set_edict") is not None:
                vals["can_set_edict"] = sd.flag("can_set_edict")
        pi = g.add("p:" + prov, "province", vals)
        g.edge(ri, pi, "in_prov")
        if st is not None:
            for _e in (st.get("active_edict"), st.get("selected_edict")):
                _k = str(_e or "")
                if _k and _k != "none":
                    g.edge(pi, g.cat_node("edict", _k), "has_edict")

    for s in world.get("settlements") or []:
        key = str(s.get("region") or "")
        if not key:
            continue
        gar = own_citizenry.get(key)
        sd = G.Reader(s, "settlement:" + key, "world.settlements[]")
        if gar is not None:
            gd = G.Reader(gar, "char:" + str(gar.get("cqi") or ""), "world.armies[]")
            svals = {"garrison_units": gd.num("units")}
        else:
            svals = {}
        if _pos_ok(s.get("x"), s.get("y")):
            svals["x"] = sd.num("x")
            svals["y"] = sd.num("y")
        si = g.add("s:" + key, "settlement", svals, own=True)
        ri = g.id2idx.get("r:" + key)
        if ri is None:
            ri = g.add("r:" + key, "region", {})
        g.edge(ri, si, "sett_of")
        g.edge(g.id2idx.get("f:" + me), si, "owns_sett")

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
                g.edge(si, g.cat_node("building", str(bkey)), "slot_filled")
            nk = now.get(str(sl)) or now.get(sl)
            if isinstance(nk, dict):
                nk = nk.get("key")
            if nk:
                g.edge(si, g.cat_node("building", str(nk)), "slot_building")

    char_faction = {}
    reach_pending = []
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
        vals = {"rank": rd.num("rank"),
                "ap_pct": rd.num("ap_pct")}
        for _f in ("acted", "is_leader", "in_own_territory"):
            if row.get(_f) is not None:
                vals[_f] = rd.flag(_f)
        if row.get("skill_points") is not None:
            vals["skill_points"] = rd.num("skill_points")
        if _pos_ok(row.get("x"), row.get("y")):
            vals["x"] = rd.num("x")
            vals["y"] = rd.num("y")
        if ntype == "lord":
            vals["units"] = rd.num("units")
            vals["hp"] = rd.num("hp")
            if row.get("pending_recruits") is not None:
                vals["pending_recruits"] = rd.num("pending_recruits")
            mrow = _lord_memory_row(campaign, cqi, row)
            if mrow:
                mr = G.Reader(mrow, "char:" + cqi, "memory.lord")
                for k in mrow:
                    vals[k] = mr.num(k)
        ci = g.add("c:" + cqi, ntype, vals,
                   agent=S.agent_index(a.get("agent_type")),
                   stance=S.stance_index(a.get("stance")),
                   subtype=S.subtype_index(a.get("subtype")), own=True)
        char_faction[cqi] = me
        _wire_char(g, ci, cqi, row, me, prov_of_region, st)
        for tc, hit in ((st or {}).get("reach_chars") or {}).items():
            if hit:
                reach_pending.append((ci, "c:" + str(tc)))
        for tr, hit in ((st or {}).get("reach_setts") or {}).items():
            if hit:
                reach_pending.append((ci, "s:" + str(tr), "r:" + str(tr)))

    for h in world.get("hostiles") or []:
        hk = str(h.get("kind") or "")
        if hk not in MOBILE_KINDS or h.get("visible") is False:
            continue
        cqi = str(h.get("cqi") or "")
        if not cqi or cqi in citizenry or ("c:" + cqi) in g.id2idx:
            continue
        rd = G.Reader(h, "char:" + cqi, "world.hostiles[]")
        ntype = "lord" if MOBILE_KINDS[hk] else "hero"
        vals = {"rank": rd.num("rank")}
        if _pos_ok(h.get("x"), h.get("y")):
            vals["x"] = rd.num("x")
            vals["y"] = rd.num("y")
        if ntype == "lord":
            vals["units"] = rd.num("units")
            vals["hp"] = rd.num("hp")
        ci = g.add("c:" + cqi, ntype, vals,
                   agent=S.agent_index(h.get("agent_type")),
                   stance=S.stance_index(h.get("stance")),
                   subtype=S.subtype_index(h.get("subtype")))
        char_faction[cqi] = str(h.get("faction") or "")
        _wire_char(g, ci, cqi, h, char_faction[cqi], prov_of_region, None)

    for h in world.get("hostiles") or []:
        if str(h.get("kind") or "") != "settlement":
            continue
        rkey = str(h.get("region") or "")
        if not rkey or ("s:" + rkey) in g.id2idx:
            continue
        ri = g.id2idx.get("r:" + rkey)
        if ri is None:
            continue
        hd = G.Reader(h, "settlement:" + rkey, "world.hostiles[kind=settlement]")
        vals = {}
        if _pos_ok(h.get("x"), h.get("y")):
            vals["x"] = hd.num("x")
            vals["y"] = hd.num("y")
        si = g.add("s:" + rkey, "settlement", vals, own=False)
        g.edge(ri, si, "sett_of")
        fj = g.id2idx.get("f:" + str(h.get("faction") or ""))
        if fj is not None:
            g.edge(fj, si, "owns_sett")

    for want in reach_pending:
        ci, ids = want[0], want[1:]
        ti = next((g.id2idx[i] for i in ids if i in g.id2idx), None)
        if ti is not None and ti != ci:
            g.edge(ci, ti, "can_reach")

    _enemy_setts = {(h.get("x"), h.get("y")): str(h.get("region") or "")
                    for h in (world.get("hostiles") or [])
                    if str(h.get("kind") or "") == "settlement" and h.get("region")}
    for h in world.get("hostiles") or []:
        if not h.get("is_armed_citizenry") or h.get("visible") is False:
            continue
        ci = g.id2idx.get("c:" + str(h.get("cqi") or ""))
        rkey = _enemy_setts.get((h.get("x"), h.get("y")))
        if ci is None or not rkey:
            continue
        sj = g.id2idx.get("s:" + rkey)
        if sj is not None:
            g.edge(ci, sj, "garrisons")

    g.finalize()
    _wire_knn(g)
    near_pts = _target_pts(g)
    pend_by_cqi = {cqi: st.get("pending_recruits")
                   for cqi, (kind, st) in char_state.items()
                   if st and st.get("pending_recruits") is not None}

    n_offers = 0
    groups = {}
    for e in record.get("entities") or []:
        ck, cid = e.get("context_kind"), str(e.get("context_id"))
        ego = _ego_of(g, ck, cid, prov_of_region, me)
        for o in e.get("offers") or []:
            n_offers += 1
            _add_action(g, o, ego, ck, cid, groups, prov_of_region, slot_index, me,
                        region_xy, campaign, world, pend_by_cqi, near_pts)

    g.finalize()
    if not g.src:
        return None

    cd = G.Reader(campaign, "campaign", "campaign")
    g.g_ctx = [
        float(cd.num("turn")),
        float(cd.num("treasury")),
        float(cd.num("income")),
        float(cd.num("settlements") / 10.0),
        float(cd.num("armies") / 5.0),
        float(cd.num("allies") / 3.0),
        float(cd.num("vassals") / 3.0),
        float(cd.num("power_rank")),
        float(cd.num("lord_level") / 10.0),
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
        g.edge(ci, pi, "in_province")
    si = g.id2idx.get("s:" + rkey)
    if si is not None:
        if row.get("garrisoned"):
            g.edge(ci, si, "at_sett")
        if row.get("besieging"):
            g.edge(ci, si, "besieging")
    for uk in (st or {}).get("pending_recruit_keys") or ():
        g.edge(ci, g.cat_node("unit", str(uk)), "queued")
    seen_units = set()
    for u in (st or {}).get("unit_cards") or ():
        uk = str((u or {}).get("key") or "")
        if uk and uk not in seen_units:
            seen_units.add(uk)
            g.edge(ci, g.cat_node("unit", uk), "in_army")
    for sk in (st or {}).get("hidden_skills") or ():
        if sk:
            g.edge(ci, g.cat_node("skill", str(sk)), "innate")
    for sk in (st or {}).get("skills") or ():
        skey = str((sk or {}).get("key") or "")
        rel = _SKILL_REL.get(str((sk or {}).get("status") or ""))
        if skey and rel:
            g.edge(ci, g.cat_node("skill", skey), rel)


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


def _lord_memory_row(campaign, cqi, row=None):
    camp = campaign or {}
    out = {}
    counts = camp.get("recruit_counts_turn") or {}
    if cqi in counts:
        out["recruits_this_turn"] = counts[cqi]
    ages = (camp.get("queue_ages") or {}).get(cqi) or ()
    if ages:
        out["queue_age_max"] = max(a for _k, a in ages)
        out["queue_new_this_turn"] = sum(1 for _k, a in ages if a == 0)
    stall = (camp.get("queue_stall") or {}).get(cqi)
    if stall is not None:
        out["queue_stalled"] = float(sum(stall))
    if (row or {}).get("pending_recruit_keys"):
        active = any((c or {}).get("state") == "active"
                     for c in (row or {}).get("recruitable") or ())
        out["queue_on_hold"] = 0.0 if active else 1.0
    mv = (camp.get("last_move_turn") or {}).get(cqi)
    turn = camp.get("turn")
    if mv is not None and turn is not None:
        try:
            out["turns_since_moved"] = max(0.0, float(turn) - float(mv))
        except (TypeError, ValueError):
            pass
    return out


def _target_pts(g):
    out = []
    lxi = S.TYPE_FIELDS["lord"].index("x")
    lyi = S.TYPE_FIELDS["lord"].index("y")
    sxi = S.TYPE_FIELDS["settlement"].index("x")
    syi = S.TYPE_FIELDS["settlement"].index("y")
    for i, t in enumerate(g.node_type):
        tn = S.NODE_TYPES[t]
        if tn in ("lord", "hero"):
            x, y = g.x[i][lxi], g.x[i][lyi]
        elif tn == "settlement":
            x, y = g.x[i][sxi], g.x[i][syi]
        else:
            continue
        if x or y:
            out.append((i, x, y))
    return out


def _ego_of(g, ck, cid, prov_of_region, me):
    if ck in ("lord", "hero"):
        return g.id2idx.get("c:" + cid)
    if ck == "province":
        prov = prov_of_region.get(cid) or ""
        return g.id2idx.get("p:" + prov) or g.id2idx.get("r:" + cid)
    return g.id2idx.get("f:" + me)


_CAT_OF_TYPE = {"recruit_unit": "unit", "recruit_ror": "unit", "recruit_blessed": "unit",
                "recruit_imperial": "unit", "raise_dead": "unit",
                "cancel_recruit": "unit",
                "recruit_lord": "agent_subtype", "recruit_hero": "agent_subtype",
                "research": "tech", "skills": "skill", "rites": "ritual",
                "edict": "edict", "items": "item", "item_unequip": "item",
                "building": "building", "building_repair": "building",
                "building_dismantle": "building", "horde_building": "building"}


def _add_action(g, o, ego, ck, cid, groups, prov_of_region, slot_index, me,
                region_xy=(), campaign=None, world=None, pending=None,
                near_pts=()):
    at = str(o.get("action_type") or "")
    key = str(o.get("key") or "")
    params = o.get("params") or {}
    term = 0
    if at == "diplomacy":
        term = S.term_index(key.split(":", 1)[-1])
    avals = {}
    pd = G.Reader(params, "offer:%s:%s:%s" % (ck, cid, key), "offers[].params")
    if _pos_ok(params.get("x"), params.get("y")):
        avals["x"] = pd.num("x")
        avals["y"] = pd.num("y")
    if params.get("cost") is not None:
        avals["cost"] = pd.num("cost")
    elif params.get("repair_cost") is not None:
        avals["cost"] = pd.num("repair_cost")
    if params.get("pool_avail") is not None:
        avals["pool_avail"] = pd.num("pool_avail")
    eid = "offer:%s:%s:%s" % (ck, cid, key)
    if at in M.PB_ATTACK_TYPES:
        pb = M.prebattle_option_feats(campaign, at, key, params, world, cid)
        mrow = {}
        since = pb["opt_actions_since_prebattle_at_loc"]
        if since is not None:
            mrow["pb_choice_loc"] = M.choice_index(pb["opt_last_prebattle_choice_at_loc"])
            mrow["pb_since_loc"] = min(since, 60.0)
            mrow["pb_result_loc"] = M.result_index(pb["opt_last_prebattle_result_at_loc"])
        since_r = pb["opt_actions_since_prebattle_in_region"]
        if since_r is not None:
            mrow["pb_choice_reg"] = M.choice_index(
                pb["opt_last_prebattle_choice_in_region"])
            mrow["pb_since_reg"] = min(since_r, 60.0)
            mrow["pb_result_reg"] = M.result_index(
                pb["opt_last_prebattle_result_in_region"])
        if pb["opt_last_prebattle_same_lord"] is not None:
            mrow["pb_same_lord"] = pb["opt_last_prebattle_same_lord"]
        if mrow:
            mr = G.Reader(mrow, eid, "memory.prebattle")
            for k in mrow:
                avals[k] = mr.num(k)
    if at in M.RECRUIT_TYPES:
        depth = {"queue_depth_after": float((pending or {}).get(str(cid)) or 0.0) + 1.0}
        avals["queue_depth_after"] = G.Reader(depth, eid,
                                              "memory.queue").num("queue_depth_after")
    if at == "cancel_recruit":
        row = {"queue_depth_after": max(float((pending or {}).get(str(cid)) or 0.0) - 1.0,
                                        0.0)}
        if params.get("turns_left") is not None:
            row["cancel_turns_left"] = params["turns_left"]
        flags = ((campaign or {}).get("queue_stall") or {}).get(str(cid))
        qi = params.get("queue_index")
        if flags is not None and qi is not None and 0 <= int(qi) < len(flags):
            row["cancel_stalled"] = flags[int(qi)]
        qr = G.Reader(row, eid, "memory.queue")
        for k in row:
            avals[k] = qr.num(k)
    ai = g.add("a:%s:%s:%s:%d" % (ck, cid, key, len(g.action_nodes)), "action",
               avals, atype=S.atype_index(at), term=term,
               stance=S.stance_index(key) if at == "stance" else 0)
    g.action_keys.append((str(ck), str(cid), at, key))
    g.edge(ai, ego, "act_actor")

    grp = (ego, at)
    gi = groups.get(grp)
    if gi is None:
        gi = g.add("cg:%s:%s" % (ego, at), "cgroup", {})
        groups[grp] = gi
        g.edge(gi, ego, "of_ego")
    g.edge(ai, gi, "in_group")

    kind = _CAT_OF_TYPE.get(at)
    if kind:
        ck_key = (key.split("@", 1)[0]
                  if at.startswith("recruit") or at == "cancel_recruit" else key)
        if at in ("building_repair", "building_dismantle", "horde_building"):
            ck_key = str(params.get("building_key") or "") or None
        elif at == "building":
            ck_key = key or None
        if at in ("items", "item_unequip"):
            ck_key = str(params.get("item_key") or "") or None
        if ck_key:
            g.edge(ai, g.cat_node(kind, ck_key), "act_on")

    tgt = _target_of(g, at, key, params, region_xy, cid)

    if at == "hero_action":
        akey = str(params.get("action_key") or "")
        if akey:
            g.edge(ai, g.cat_node("agent_action", akey), "act_on")
        ability = str(params.get("ability") or "") or C.ability_of(akey)
        if ability in S.ABILITY_RELATIONS and tgt is not None:
            g.edge(ai, tgt, ability)

    if tgt is not None:
        g.edge(ai, tgt, "act_target")

    if at in M.PB_ATTACK_TYPES and _pos_ok(params.get("x"), params.get("y")):
        tx, ty = float(params["x"]), float(params["y"])
        for j, jx, jy in near_pts:
            if j == tgt or j == ego:
                continue
            d = math.hypot(jx - tx, jy - ty)
            if d <= 10:
                g.edge(ai, j, "near_target")
            elif d <= 25:
                g.edge(ai, j, "near_target_wide")

    subject = params.get("faction") or params.get("target_faction")
    if subject:
        g.edge(ai, g.id2idx.get("f:" + str(subject)), "act_subject")

    if params.get("slot_index") is not None:
        si = None
        try:
            idx = int(float(params["slot_index"]))
        except (TypeError, ValueError):
            idx = None
        if idx is not None:
            if params.get("slot_id"):
                si = g.id2idx.get("hslot:%s:%d" % (cid, idx))
                if si is None:
                    si = g.add("hslot:%s:%d" % (cid, idx), "slot", {})
                    g.edge(ego, si, "has_slot")
            else:
                si = slot_index.get((str(params.get("region") or cid), idx))
        g.edge(ai, si, "act_slot")


def _nearest_region(region_xy, x, y):
    if not region_xy or not _pos_ok(x, y):
        return None
    best, bd = None, None
    for rx, ry, idx in region_xy:
        d = (rx - x) ** 2 + (ry - y) ** 2
        if bd is None or d < bd:
            best, bd = idx, d
    return best


def _target_of(g, at, key, params, region_xy=(), cid=""):
    if at in ("move", "leave_garrison"):
        return _nearest_region(region_xy, params.get("x"), params.get("y"))
    if at in ("building", "building_repair", "building_dismantle"):
        rkey = str(params.get("region") or cid or "")
        return g.id2idx.get("s:" + rkey) or g.id2idx.get("r:" + rkey)
    if params.get("target_cqi") is not None:
        return g.id2idx.get("c:" + str(params["target_cqi"]))
    if at in ("attack_settlement", "colonize", "garrison"):
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
