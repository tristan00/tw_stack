from __future__ import annotations

import math
import sys

try:
    from mapgraph import schema as S
except ImportError:
    import schema as S

MOBILE_HOSTILE_KINDS = {"army": ("enemy", True), "neutral_army": ("neutral", True),
                        "hero": ("enemy", False), "neutral_hero": ("neutral", False)}

_NF = {name: i for i, name in enumerate(S.NODE_FIELDS)}
_EF = {name: i for i, name in enumerate(S.EDGE_FIELDS)}


def _f(v, default=0.0):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:
        return default
    return x


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def _slog(v):
    x = _f(v)
    return math.copysign(math.log1p(abs(x)), x) / 10.0


def _geo(x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d <= 0.0:
        return 0.0, 0.0, 1.0
    return d / S.DIST_SCALE, dy / d, dx / d


def _ap_pct(v):
    x = _f(v)
    if x > 1.0:
        x = x / 100.0
    return _clip(x, 0.0, 1.0)


def _pos_of(row):
    x, y = row.get("x"), row.get("y")
    if x is None or y is None:
        return None
    fx, fy = _f(x), _f(y)
    if fx >= S.COORD_MAX or fy >= S.COORD_MAX or fx < 0 or fy < 0:
        return None
    return (fx, fy)


class Graph:
    __slots__ = ("x", "race_idx", "stance_idx", "node_ids", "id2idx", "node_meta",
                 "edge_src", "edge_dst", "edge_attr", "g_ctx", "own_mask", "pos",
                 "player_faction", "counts")

    def __init__(self):
        self.x = []
        self.race_idx = []
        self.stance_idx = []
        self.node_ids = []
        self.id2idx = {}
        self.node_meta = []
        self.edge_src = []
        self.edge_dst = []
        self.edge_attr = []
        self.g_ctx = []
        self.own_mask = []
        self.pos = []
        self.player_faction = None
        self.counts = {}

    def add_node(self, nid, ntype, feats, race=0, stance=0, pos=None, own=False, meta=None):
        if nid in self.id2idx:
            raise ValueError("mapgraph: duplicate node id %r -- two sources produced the same "
                             "entity, the graph would silently merge them" % (nid,))
        row = [0.0] * S.NODE_DIM
        row[_NF["type_" + ntype]] = 1.0
        if pos is not None:
            row[_NF["geo_x"]] = pos[0] / S.COORD_SCALE
            row[_NF["geo_y"]] = pos[1] / S.COORD_SCALE
            row[_NF["has_pos"]] = 1.0
        for k, v in feats.items():
            row[_NF[k]] = v
        idx = len(self.x)
        self.id2idx[nid] = idx
        self.node_ids.append(nid)
        self.x.append(row)
        self.race_idx.append(race)
        self.stance_idx.append(stance)
        self.pos.append(pos)
        self.own_mask.append(1.0 if own else 0.0)
        self.node_meta.append(meta or {})
        return idx

    def add_edge(self, i, j, etype, rev=0.0, same_province=0.0, dip=None):
        row = [0.0] * S.EDGE_DIM
        row[_EF["etype_" + etype]] = 1.0
        row[_EF["rev"]] = rev
        pi, pj = self.pos[i], self.pos[j]
        if pi is not None and pj is not None:
            d, sn, cs = _geo(pi[0], pi[1], pj[0], pj[1])
            row[_EF["geo_known"]] = 1.0
            row[_EF["dist"]] = d
            row[_EF["dir_sin"]] = sn
            row[_EF["dir_cos"]] = cs
        row[_EF["same_province"]] = same_province
        if dip:
            row[_EF["dip_at_war"]] = _f(dip.get("at_war"))
            row[_EF["dip_allied"]] = _f(dip.get("allied"))
            row[_EF["dip_trade"]] = _f(dip.get("trade"))
            row[_EF["dip_standing"]] = _clip(_f(dip.get("standing")) / 100.0, -1.0, 1.0)
        self.edge_src.append(i)
        self.edge_dst.append(j)
        self.edge_attr.append(row)


def _cap_regions(regions, world, campaign):
    if len(regions) <= S.MAX_REGIONS:
        return regions
    own_pts = [p for p in (_pos_of(a) for a in (world.get("armies") or [])) if p]
    own_pts += [p for p in (_pos_of(s) for s in (world.get("settlements") or [])) if p]
    if not own_pts:
        return sorted(regions, key=lambda r: str(r.get("region")))[:S.MAX_REGIONS]
    cx = sum(p[0] for p in own_pts) / len(own_pts)
    cy = sum(p[1] for p in own_pts) / len(own_pts)
    me = campaign.get("faction")
    keep_keys = {str(a.get("region")) for a in (world.get("armies") or [])}
    keep_keys |= {str(s.get("region")) for s in (world.get("settlements") or [])}

    def rank(r):
        forced = (str(r.get("owner") or "") == str(me)) or (str(r.get("region")) in keep_keys)
        d = math.hypot(_f(r.get("x")) - cx, _f(r.get("y")) - cy)
        return (0 if forced else 1, d, str(r.get("region")))

    return sorted(regions, key=rank)[:S.MAX_REGIONS]


def build_graph(record):
    world = record.get("world") or {}
    campaign = record.get("campaign") or {}
    regions = world.get("regions") or []
    if not regions:
        return None
    regions = _cap_regions(regions, world, campaign)
    g = Graph()
    me = str(campaign.get("faction") or "")
    g.player_faction = me
    citizenry = {str(c) for c in (world.get("citizenry") or [])}
    relations = {str(r.get("faction")): r for r in (world.get("relations") or [])
                 if r.get("faction")}
    ruin_keys = {str(r.get("region")) for r in (world.get("ruins") or []) if r.get("region")}

    prov_snap = {}
    char_snap = {}
    for e in record.get("entities") or []:
        kind = e.get("context_kind")
        st = e.get("state") or {}
        if kind == "province" and st.get("region"):
            prov_snap[str(st["region"])] = st
        elif kind in ("lord", "hero"):
            char_snap[str(e.get("context_id"))] = (kind, st)

    own_setts = {str(s.get("region")): s for s in (world.get("settlements") or [])
                 if s.get("region")}

    region_rows = {str(r.get("region")): r for r in regions if r.get("region")}
    for key, r in sorted(region_rows.items()):
        owner = str(r.get("owner") or "")
        rel = relations.get(owner)
        st = prov_snap.get(key)
        sett = own_setts.get(key)
        feats = {
            "own_owned": 1.0 if owner and owner == me else 0.0,
            "owner_known": 1.0 if owner else 0.0,
            "owner_met": 1.0 if (owner == me or rel is not None) else 0.0,
            "abandoned": _f(r.get("abandoned")),
            "is_ruin": 1.0 if (key in ruin_keys or _f(r.get("abandoned"))) else 0.0,
            "is_prov_capital": _f(r.get("capital")),
            "degree": len(r.get("adjacent") or ()) / 8.0,
        }
        if rel is not None:
            feats["owner_at_war"] = _f(rel.get("at_war"))
            feats["owner_allied"] = _f(rel.get("allied"))
            feats["owner_trade"] = _f(rel.get("trade"))
            feats["owner_their_vassal"] = _f(rel.get("their_vassal"))
            feats["owner_standing"] = _clip(_f(rel.get("standing")) / 100.0, -1.0, 1.0)
        if sett is not None:
            feats["garrison_known"] = 1.0
            feats["garrison_units"] = _f(sett.get("units")) / S.UNITS_SCALE
        if st is not None:
            locked = st.get("locked_slots")
            feats.update({
                "snap_present": 1.0,
                "settlement_level": _f(st.get("settlement_level")) / 3.0,
                "buildings": _f(st.get("buildings")) / 10.0,
                "free_slots": _f(st.get("free_slots")) / 6.0,
                "locked_slots": (len(locked) if isinstance(locked, (list, tuple))
                                 else _f(locked)) / 6.0,
                "public_order": _clip(_f(st.get("public_order")) / 100.0, -1.0, 1.0),
                "is_capital": _f(st.get("is_capital")),
                "can_set_edict": _f(st.get("can_set_edict")),
                "has_active_edict": 1.0 if str(st.get("active_edict") or "") else 0.0,
                "building_now_n": len(st.get("building_now") or {}) / 4.0,
            })
            corr = st.get("corruption") or {}
            vals = [_f(v) for v in corr.values()]
            feats["corr_total"] = _clip(sum(vals) / 100.0, 0.0, 3.0)
            feats["corr_max"] = _clip((max(vals) if vals else 0.0) / 100.0, 0.0, 1.0)
        g.add_node("r:" + key, "region", feats, race=S.race_index(owner), pos=_pos_of(r),
                   own=(owner == me), meta={"kind": "region", "key": key,
                                            "own_snap": st is not None})

    for r in world.get("ruins") or []:
        key = str(r.get("region") or "")
        if not key or ("r:" + key) in g.id2idx:
            continue
        g.add_node("r:" + key, "region", {"is_ruin": 1.0, "abandoned": 1.0}, pos=_pos_of(r),
                   meta={"kind": "region", "key": key, "own_snap": False})

    def add_char(cqi, feats, faction, stance, pos, own, agent_type, meta):
        base = dict(feats)
        onehot = S.agent_type_onehot(agent_type)
        for i, t in enumerate(S.AGENT_TYPES):
            base["agent_" + t] = onehot[i]
        base["agent_other"] = onehot[-1]
        return g.add_node("c:" + str(cqi), "character", base, race=S.race_index(faction),
                          stance=S.stance_index(stance), pos=pos, own=own, meta=meta)

    skipped = {"citizenry": 0, "no_pos": 0, "invisible": 0}
    char_faction = {}
    for a in world.get("armies") or []:
        cqi = str(a.get("cqi") or "")
        if not cqi or cqi in citizenry:
            skipped["citizenry"] += 1
            continue
        pos = _pos_of(a)
        if pos is None:
            skipped["no_pos"] += 1
        kind, st = char_snap.get(cqi, (None, {}))
        feats = {
            "is_own": 1.0,
            "has_army": _f(a.get("has_army")),
            "is_general": _f(a.get("is_general")),
            "is_leader": _f(a.get("is_leader")),
            "units": _f(a.get("units")) / S.UNITS_SCALE,
            "units_known": 1.0 if a.get("units") is not None else 0.0,
            "hp": _f(a.get("hp")) / S.HP_SCALE,
            "hp_known": 1.0 if a.get("hp") is not None else 0.0,
            "ap_pct": _ap_pct(a.get("ap_pct")),
            "rank": _f(a.get("rank")) / 10.0,
            "in_own_territory": _f(a.get("in_own_territory")),
            "snap_present": 1.0 if kind else 0.0,
        }
        if kind:
            feats.update({"rank": _f(st.get("rank")) / 10.0,
                          "garrisoned": _f(st.get("garrisoned")),
                          "besieging": _f(st.get("besieging")),
                          "acted": _f(st.get("acted")),
                          "pending_recruits": _f(st.get("pending_recruits")) / 5.0,
                          "skill_points": _f(st.get("skill_points")) / 5.0})
        add_char(cqi, feats, me, a.get("stance"), pos, True,
                 a.get("agent_type"),
                 {"kind": "character", "cqi": cqi, "own_kind": kind,
                  "region": str(a.get("region") or "")})
        char_faction[cqi] = me

    for h in world.get("hostiles") or []:
        hk = str(h.get("kind") or "")
        if hk not in MOBILE_HOSTILE_KINDS:
            continue
        if h.get("visible") is False:
            skipped["invisible"] += 1
            continue
        cqi = str(h.get("cqi") or "")
        if not cqi or cqi in citizenry or ("c:" + cqi) in g.id2idx:
            continue
        pos = _pos_of(h)
        if pos is None:
            skipped["no_pos"] += 1
        alleg, has_army = MOBILE_HOSTILE_KINDS[hk]
        faction = str(h.get("faction") or "")
        feats = {
            "alleg_enemy": 1.0 if alleg == "enemy" else 0.0,
            "alleg_neutral": 1.0 if alleg == "neutral" else 0.0,
            "has_army": 1.0 if has_army else 0.0,
            "is_garrison": _f(h.get("is_armed_citizenry")),
            "units": _f(h.get("units")) / S.UNITS_SCALE,
            "units_known": 1.0 if h.get("units") is not None else 0.0,
            "hp": _f(h.get("hp")) / S.HP_SCALE,
            "hp_known": 1.0 if h.get("hp") is not None else 0.0,
            "rank": _f(h.get("rank")) / 10.0,
        }
        add_char(cqi, feats, faction, h.get("stance"), pos,
                 False, h.get("agent_type"),
                 {"kind": "character", "cqi": cqi, "own_kind": None,
                  "region": str(h.get("region") or "")})
        char_faction[cqi] = faction

    faction_keys = {me} | set(relations)
    faction_keys |= {str(r.get("owner")) for r in regions if r.get("owner")}
    faction_keys |= {f for f in char_faction.values() if f}
    faction_keys.discard("")
    for fk in sorted(faction_keys):
        rel = relations.get(fk)
        feats = {"is_player": 1.0 if fk == me else 0.0,
                 "met": 1.0 if (fk == me or rel is not None) else 0.0}
        if rel is not None:
            feats.update({"excluded": _f(rel.get("excluded")),
                          "at_war": _f(rel.get("at_war")),
                          "allied": _f(rel.get("allied")),
                          "trade": _f(rel.get("trade")),
                          "their_vassal": _f(rel.get("their_vassal")),
                          "standing": _clip(_f(rel.get("standing")) / 100.0, -1.0, 1.0)})
        g.add_node("f:" + fk, "faction", feats, race=S.race_index(fk), own=(fk == me),
                   meta={"kind": "faction", "key": fk})

    for fk in sorted(faction_keys):
        fi = g.id2idx["f:" + fk]
        n_regions = n_chars = units = 0.0
        for key, r in region_rows.items():
            if str(r.get("owner") or "") == fk:
                n_regions += 1
        for cqi, cf in char_faction.items():
            if cf == fk:
                n_chars += 1
                units += g.x[g.id2idx["c:" + cqi]][_NF["units"]] * S.UNITS_SCALE
        g.x[fi][_NF["n_regions_known"]] = n_regions / 10.0
        g.x[fi][_NF["n_chars_visible"]] = n_chars / 10.0
        g.x[fi][_NF["units_visible"]] = units / 100.0

    seen_adj = set()
    for key, r in region_rows.items():
        nid = "r:" + key
        i = g.id2idx[nid]
        prov = str(r.get("province") or "")
        for adj in r.get("adjacent") or ():
            j = g.id2idx.get("r:" + str(adj))
            if j is None or (i, j) in seen_adj:
                continue
            seen_adj.add((i, j))
            adj_row = region_rows.get(str(adj)) or {}
            same = 1.0 if (prov and prov == str(adj_row.get("province") or "")) else 0.0
            g.add_edge(i, j, "adj", same_province=same)

    region_pts = [(i, g.pos[i]) for i, m in enumerate(g.node_meta)
                  if m.get("kind") == "region" and g.pos[i] is not None]
    for idx, meta in enumerate(g.node_meta):
        if meta.get("kind") != "character":
            continue
        rj = g.id2idx.get("r:" + meta.get("region", ""))
        if rj is None and g.pos[idx] is not None and region_pts:
            rj = min(region_pts,
                     key=lambda t: (math.hypot(g.pos[idx][0] - t[1][0],
                                               g.pos[idx][1] - t[1][1]), t[0]))[0]
        if rj is not None:
            g.add_edge(idx, rj, "at", rev=0.0)
            g.add_edge(rj, idx, "at", rev=1.0)
        fk = char_faction.get(meta["cqi"]) or ""
        fj = g.id2idx.get("f:" + fk)
        if fj is not None:
            g.add_edge(idx, fj, "own_c", rev=0.0)
            g.add_edge(fj, idx, "own_c", rev=1.0)

    for key, r in region_rows.items():
        owner = str(r.get("owner") or "")
        fj = g.id2idx.get("f:" + owner)
        if fj is not None:
            i = g.id2idx["r:" + key]
            g.add_edge(i, fj, "own_r", rev=0.0)
            g.add_edge(fj, i, "own_r", rev=1.0)

    pi = g.id2idx.get("f:" + me)
    if pi is not None:
        for fk, rel in sorted(relations.items()):
            fj = g.id2idx.get("f:" + fk)
            if fj is None or fj == pi:
                continue
            g.add_edge(pi, fj, "dip", rev=0.0, dip=rel)
            g.add_edge(fj, pi, "dip", rev=1.0, dip=rel)

    chars = [i for i, m in enumerate(g.node_meta)
             if m.get("kind") == "character" and g.pos[i] is not None]
    if len(chars) > 1:
        nearest = {}
        for i in chars:
            ds = sorted(((math.hypot(g.pos[i][0] - g.pos[j][0], g.pos[i][1] - g.pos[j][1]), j)
                         for j in chars if j != i), key=lambda t: (t[0], t[1]))
            nearest[i] = {j for _, j in ds[:S.KNN_K]}
        for i in chars:
            for j in sorted(nearest[i]):
                if j > i and i in nearest.get(j, ()):
                    g.add_edge(i, j, "near")
                    g.add_edge(j, i, "near")

    if not g.edge_src:
        return None

    n_offers = sum(len(e.get("offers") or ()) for e in record.get("entities") or [])
    own_chars = [i for i, m in enumerate(g.node_meta)
                 if m.get("kind") == "character" and g.own_mask[i]]
    heroes = sum(1 for i in own_chars if g.x[i][_NF["has_army"]] == 0.0)
    g.g_ctx = [
        _f(campaign.get("turn")) / 20.0,
        _slog(campaign.get("treasury")),
        _slog(max(_f(campaign.get("income")), 0.0)),
        _f(campaign.get("settlements")) / 10.0,
        _f(campaign.get("armies")) / 5.0,
        len(own_chars) / 10.0,
        heroes / 5.0,
        _f(campaign.get("allies")) / 3.0,
        _f(campaign.get("vassals")) / 3.0,
        _clip(_f(campaign.get("power_rank")) / 300.0, -1.0, 1.0),
        _f(campaign.get("lord_level")) / 10.0,
        _f(campaign.get("act_index")) / 16.0,
        n_offers / 1000.0,
        _f(campaign.get("is_researching")),
    ]
    g.counts = {"nodes": len(g.x), "edges": len(g.edge_src), "regions": len(region_rows),
                "chars": len(chars), "factions": len(faction_keys), **skipped}
    return g


def bind_offer(g, context_kind, context_id, action_type, key, params, available=True):
    params = params or {}
    me_idx = g.id2idx.get("f:" + (g.player_faction or ""))
    if context_kind in ("lord", "hero"):
        ego = g.id2idx.get("c:" + str(context_id))
    elif context_kind == "province":
        ego = g.id2idx.get("r:" + str(context_id))
    else:
        ego = me_idx
    ego_miss = ego is None
    if ego is None:
        ego = me_idx if me_idx is not None else 0

    target = None
    if action_type == "attack_army" and params.get("target_cqi") is not None:
        target = g.id2idx.get("c:" + str(params["target_cqi"]))
    elif action_type in ("attack_settlement", "colonize"):
        target = g.id2idx.get("r:" + str(key))
    elif action_type == "garrison":
        target = g.id2idx.get("r:" + str(key).split(":", 1)[-1])
    elif action_type == "hero_action":
        if params.get("target_cqi") is not None:
            target = g.id2idx.get("c:" + str(params["target_cqi"]))
        if target is None and params.get("region"):
            target = g.id2idx.get("r:" + str(params["region"]))
    elif action_type == "diplomacy" and params.get("faction"):
        target = g.id2idx.get("f:" + str(params["faction"]))
    wants_target = action_type in ("attack_army", "attack_settlement", "colonize", "garrison",
                                   "hero_action", "diplomacy")

    feats = [0.0] * S.OFFER_DIM
    fi = {name: i for i, name in enumerate(S.OFFER_FIELDS)}
    feats[fi["available"]] = 1.0 if available else 0.0
    feats[fi["target_found"]] = 1.0 if target is not None else 0.0
    feats[fi["target_miss"]] = 1.0 if (wants_target and target is None) else 0.0

    ego_pos = g.pos[ego] if ego is not None else None
    tpos = g.pos[target] if target is not None else None
    if tpos is None and params.get("x") is not None and params.get("y") is not None:
        tpos = (_f(params["x"]), _f(params["y"]))
    if ego_pos is not None and tpos is not None:
        d, sn, cs = _geo(ego_pos[0], ego_pos[1], tpos[0], tpos[1])
        feats[fi["dist"]] = d
        feats[fi["dir_sin"]] = sn
        feats[fi["dir_cos"]] = cs

    rays = params.get("reach_rays") or ()
    for k in range(min(8, len(rays))):
        feats[fi["ray_%d" % k]] = _f(rays[k]) / 100.0
    feats[fi["reach_max"]] = _f(params.get("reach_max")) / 100.0

    if target is not None and ego is not None:
        eu = g.x[ego][_NF["units"]] * S.UNITS_SCALE
        tu = g.x[target][_NF["units"]] * S.UNITS_SCALE
        eh = g.x[ego][_NF["hp"]] * S.HP_SCALE
        th = g.x[target][_NF["hp"]] * S.HP_SCALE
        feats[fi["strength_ratio"]] = _clip(eu / (tu + 1.0), 0.0, 5.0) / 5.0
        feats[fi["hp_ratio"]] = _clip(eh / (th + 1.0), 0.0, 5.0) / 5.0

    feats[fi["n_terms"]] = len(params.get("terms") or ()) / 3.0
    feats[fi["gift_rank"]] = S.GIFT_RANK.get(str(params.get("gift")), 0) / 3.0
    feats[fi["standing"]] = _clip(_f(params.get("standing")) / 100.0, -1.0, 1.0)
    feats[fi["chance"]] = _f(params.get("chance")) / 100.0
    feats[fi["active"]] = _f(params.get("active"))
    feats[fi["slot_index"]] = _f(params.get("slot_index")) / 4.0
    feats[fi["is_upgrade"]] = _f(params.get("is_upgrade"))

    return {"ego": ego, "target": target if target is not None else 0,
            "has_target": 0.0 if target is None else 1.0,
            "ego_miss": ego_miss,
            "atype": S.atype_index(action_type), "key_hash": S.key_index(key),
            "feats": feats}
