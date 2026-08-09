from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor", "reference"))
import features_db as DB

RINGS = (10, 25, 50)
NEAR_K = {"enemy": 3, "enemysett": 3, "friend": 2, "neutral": 1, "ownsett": 2}
NEAR_K_DEFAULT = 2

ACTION_TYPES = ("stance", "building", "research", "skills", "items", "item_unequip", "rites",
                "recruit_unit", "recruit_lord", "edict", "attack_army", "attack_settlement",
                "colonize", "horde_building", "garrison", "leave_garrison", "end_turn", "noop",
                "move", "diplomacy", "hero_action", "recruit_hero",
                "building_repair", "building_cancel", "building_dismantle",
                "raise_dead", "recruit_ror", "recruit_blessed", "recruit_imperial")

UNIT_RECRUIT_TYPES = ("recruit_unit", "raise_dead", "recruit_ror", "recruit_blessed",
                      "recruit_imperial")

PREV_ACTIONS = 5

FACTION_CHANNELS = ("enemy", "neutral", "enemysett")
STANCE_CHANNELS = ("friend", "enemy", "neutral")


def race_of(faction_key):
    if not faction_key:
        return None
    parts = str(faction_key).split("_")
    return parts[2] if len(parts) > 2 else None


def stamp_prev_actions(campaign, history):
    for i in range(PREV_ACTIONS):
        campaign["prev_action_%d" % (i + 1)] = history[-1 - i] if len(history) > i else "none"
    return campaign


def stamp_action_counts(campaign, counts):
    campaign["action_counts"] = dict(counts or {})
    return campaign


def bump_action_counts(counts, action_type):
    if action_type and action_type != "noop":
        counts[action_type] = counts.get(action_type, 0) + 1
    return counts


def _f(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _resource_feats(campaign):
    res = (campaign or {}).get("resources") or {}
    out = {"camp_n_resources": float(len(res))}
    for k, v in res.items():
        out["res_%s" % k] = _f(v)
    return out


HERO_AGENT_TYPES = ("champion", "dignitary", "engineer", "runesmith", "spy", "wizard")


def _hero_roster_feats(armies):
    heroes = [a for a in (armies or []) if not a.get("has_army")]
    counts = {}
    for a in heroes:
        t = str(a.get("agent_type") or "unknown")
        counts[t] = counts.get(t, 0) + 1
    out = {"camp_heroes_%s" % t: float(counts.get(t, 0)) for t in HERO_AGENT_TYPES}
    known = sum(counts.get(t, 0) for t in HERO_AGENT_TYPES)
    out["camp_heroes_unknown_type"] = float(len(heroes) - known)
    out["camp_hero_types_distinct"] = float(len([t for t in HERO_AGENT_TYPES if counts.get(t)]))
    return out


def _agent_feats(armies, world):
    w = world or {}
    own = [a for a in (armies or []) if not a.get("has_army")]
    enemy = w.get("enemy_agents") or []
    at_war = [a for a in enemy if a.get("at_war")]
    out = {
        "camp_own_agents": float(len(own)),
        "camp_enemy_agents_known": float(len(enemy)),
        "camp_enemy_agents_at_war": float(len(at_war)),
        "camp_enemy_agent_factions": float(len({a.get("faction") for a in enemy if a.get("faction")})),
    }
    for t in HERO_AGENT_TYPES:
        out["camp_agents_%s" % t] = float(sum(1 for a in own if a.get("agent_type") == t))
    return out


def _action_history_feats(campaign):
    hist = campaign.get("action_counts") or {}
    out = {"camp_taken_total": float(sum(hist.values()) if hist else 0)}
    for a in ACTION_TYPES:
        out["camp_taken_%s" % a] = float(hist.get(a, 0))
    return out


def campaign_block(campaign, world):
    w = world or {}
    armies = w.get("armies") or []
    hostiles = w.get("hostiles") or []
    prev = {"camp_prev_action_%d" % (i + 1): str(campaign.get("prev_action_%d" % (i + 1)) or "none")
            for i in range(PREV_ACTIONS)}
    return dict(_resource_feats(campaign), **prev, **_hero_roster_feats(armies),
            **_agent_feats(armies, w), **_action_history_feats(campaign),
            **{"camp_faction": campaign.get("faction"),
            "camp_race": race_of(campaign.get("faction")),
            "camp_game_version": campaign.get("game_version"),
            "camp_turn": _f(campaign.get("turn")),
            "camp_act_index": _f(campaign.get("act_index")),
            "camp_move_index": _f(campaign.get("move_index")),
            "camp_income": _f(campaign.get("income")),
            "camp_settlements": _f(campaign.get("settlements")),
            "camp_lord_level": _f(campaign.get("lord_level")),
            "camp_allies": _f(campaign.get("allies")),
            "camp_vassals": _f(campaign.get("vassals")),
            "camp_power_rank": _f(campaign.get("power_rank")),
            "camp_treasury": _f(campaign.get("treasury")),
            "camp_is_researching": _f(campaign.get("is_researching")),
            "camp_armies": float(sum(1 for a in armies if a.get("has_army"))),
            "camp_army_units": float(sum((a.get("units") or 0) for a in armies if a.get("has_army"))),
            "camp_enemy_units_known": float(sum((h.get("units") or 0) for h in hostiles
                                                if h.get("kind") == "army")),
            "camp_characters": float(len(armies)),
            "camp_lords": float(sum(1 for a in armies if a.get("has_army"))),
            "camp_heroes": float(sum(1 for a in armies if not a.get("has_army"))),
            "camp_enemy_armies": float(sum(1 for h in hostiles if h.get("kind") == "army")),
            "camp_enemy_settlements": float(sum(1 for h in hostiles
                                                if h.get("kind") == "settlement"))})


_EMPTY_LORD = {"lord_rank": None, "lord_skill_points": None, "lord_units": None,
               "lord_pending_recruits": None, "lord_ap_pct": None, "lord_garrisoned": None,
               "lord_besieging": None, "lord_acted": None, "lord_stance": None,
               "lord_subtype": None, "lord_is_leader": None,
               "lord_x": None, "lord_y": None, "lord_has_army": None,
               "lord_hp": None, "lord_present": 0.0}


def lord_block(state):
    if not state:
        return dict(_EMPTY_LORD)
    return {"lord_rank": _f(state.get("rank")), "lord_skill_points": _f(state.get("skill_points")),
            "lord_units": _f(state.get("units")),
            "lord_pending_recruits": _f(state.get("pending_recruits")),
            "lord_ap_pct": _f(state.get("ap_pct")), "lord_garrisoned": _f(state.get("garrisoned")),
            "lord_besieging": _f(state.get("besieging")), "lord_acted": _f(state.get("acted")),
            "lord_stance": state.get("stance"),
            "lord_subtype": state.get("subtype"), "lord_is_leader": _f(state.get("is_leader")),
            "lord_x": _f(state.get("x")), "lord_y": _f(state.get("y")),
            "lord_has_army": (1.0 if state.get("units") is not None else 0.0),
            "lord_hp": _f(state.get("hp")), "lord_present": 1.0}


def lord_territory_block(state, world):
    cqi = str((state or {}).get("cqi") or "")
    for a in ((world or {}).get("armies") or []):
        if str(a.get("cqi")) == cqi:
            return {"lord_in_own_territory": _f(a.get("in_own_territory"))}
    return {"lord_in_own_territory": None}


_EMPTY_PROV = {"prov_province": None, "prov_complete": None, "prov_max_slots": None,
               "prov_free_slots": None, "prov_can_set_edict": None, "prov_selected_edict": None,
               "prov_active_edict": None, "prov_public_order": None, "prov_buildings": None,
               "prov_is_capital": None, "prov_settlement_level": None, "prov_present": 0.0}


def province_block(state):
    if not state or not state.get("settlement_present"):
        return dict(_EMPTY_PROV)
    return {"prov_province": state.get("province"), "prov_complete": _f(state.get("complete_owner")),
            "prov_max_slots": _f(state.get("max_slots")), "prov_free_slots": _f(state.get("free_slots")),
            "prov_can_set_edict": _f(state.get("can_set_edict")),
            "prov_selected_edict": state.get("selected_edict"),
            "prov_active_edict": state.get("active_edict"),
            "prov_public_order": _f(state.get("public_order")),
            "prov_buildings": _f(state.get("buildings")),
            "prov_is_capital": _f(state.get("is_capital")),
            "prov_settlement_level": _f(state.get("settlement_level")), "prov_present": 1.0,
            **_corruption_feats(state.get("corruption"))}


CORRUPTION_KEYS = ("wh3_main_corruption_chaos", "wh3_main_corruption_khorne",
                   "wh3_main_corruption_nurgle", "wh3_main_corruption_skaven",
                   "wh3_main_corruption_slaanesh", "wh3_main_corruption_tzeentch",
                   "wh3_main_corruption_vampiric")


def _corruption_feats(corr):
    c = corr or {}
    out = {"corr_%s" % k.rsplit("_", 1)[-1]: _f(c.get(k)) for k in CORRUPTION_KEYS}
    known = [v for v in out.values() if v is not None]
    out["corr_total"] = float(sum(known)) if known else None
    out["corr_max"] = float(max(known)) if known else None
    return out


def positioning_block(near, world, prov_key, locus=None):
    out = {}
    fe, en = near.get("near_friend_1_dist"), near.get("near_enemy_1_dist")
    out["pos_enemy_minus_friend"] = (en - fe) if (fe is not None and en is not None) else None
    out["pos_exposed"] = (1.0 if en < fe else 0.0) if (fe is not None and en is not None) else None
    w0 = world or {}
    lx = float(locus[0]) if (locus and locus[0] is not None) else None
    ly = float(locus[1]) if (locus and locus[1] is not None) else None
    for nm, items in (("friend", [a for a in (w0.get("armies") or []) if a.get("has_army")]),
                      ("enemy", [h for h in (w0.get("hostiles") or [])
                                 if h.get("kind") == "army"])):
        pts = [(float(i["x"]), float(i["y"])) for i in items
               if i.get("x") is not None and i.get("y") is not None]
        out["agg_%s_n" % nm] = float(len(pts))
        known = [u for u in (i.get("units") for i in items) if u is not None]
        out["agg_%s_units" % nm] = float(sum(known)) if known else None
        if pts and lx is not None and ly is not None:
            ds = [math.hypot(px - lx, py - ly) for px, py in pts]
            out["agg_%s_mean_dist" % nm] = round(sum(ds) / len(ds), 2)
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            out["agg_%s_centroid_dist" % nm] = round(math.hypot(cx - lx, cy - ly), 2)
            s, c = bearing(cx - lx, cy - ly)
            out["agg_%s_centroid_sin" % nm], out["agg_%s_centroid_cos" % nm] = s, c
        else:
            for suf in ("mean_dist", "centroid_dist", "centroid_sin", "centroid_cos"):
                out["agg_%s_%s" % (nm, suf)] = None
    r0 = RINGS[0] if RINGS else None
    f0 = near.get("near_friend_r%d" % r0) if r0 else None
    e0 = near.get("near_enemy_r%d" % r0) if r0 else None
    out["pos_support_ratio"] = (((f0 or 0.0) + 1.0) / ((e0 or 0.0) + 1.0)
                               if (f0 is not None or e0 is not None) else None)
    w = world or {}
    if prov_key:
        arm = [a for a in (w.get("armies") or []) if a.get("province") == prov_key]
        hos = [h for h in (w.get("hostiles") or [])
               if h.get("province") == prov_key and h.get("kind") in ("army", "neutral_army")]
        out["prov_own_lords"] = float(sum(1 for a in arm if a.get("has_army")))
        out["prov_own_heroes"] = float(sum(1 for a in arm if not a.get("has_army")))
        out["prov_enemy_known"] = float(sum(1 for h in hos if h.get("kind") == "army"))
        out["prov_neutral_known"] = float(sum(1 for h in hos if h.get("kind") == "neutral_army"))
    else:
        out["prov_own_lords"] = out["prov_own_heroes"] = None
        out["prov_enemy_known"] = out["prov_neutral_known"] = None
    return out


def province_buildings_block(provinces, prov_state):
    out = {"pbld_count": None, "pbld_inprogress": None}
    if not prov_state or not prov_state.get("settlement_present"):
        return out
    pk = prov_state.get("province")
    same = [s for s in (provinces or {}).values()
            if s.get("settlement_present") and s.get("province") == pk] or [prov_state]
    keys, inprog = set(), 0
    for s in same:
        for k in (s.get("built") or {}).values():
            if k:
                keys.add(str(k))
        for v in (s.get("building_now") or {}).values():
            inprog += 1
            if v.get("key"):
                out["pbldnow_%s" % v["key"]] = 1.0
    for k in keys:
        out["pbld_%s" % k] = 1.0
    out["pbld_count"] = float(len(keys))
    out["pbld_inprogress"] = float(inprog)
    return out


def lord_recruit_block(state):
    ks = (state or {}).get("pending_recruit_keys") or []
    out = {"lrec_inprogress": float(len(ks))}
    for k in ks:
        out["lrec_%s" % k] = 1.0
    return out


def carried_province_block(provinces, here):
    owned = [s for s in (provinces or {}).values() if s.get("settlement_present")]
    n = len(owned)
    out = {"own_provinces": float(len({s.get("province") for s in owned})) if n else 0.0,
           "own_settlements": float(n),
           "own_buildings_total": float(sum(len(s.get("built") or {}) for s in owned)),
           "own_building_now": float(sum(len(s.get("building_now") or {}) for s in owned)),
           "own_free_slots": float(sum((_f(s.get("free_slots")) or 0.0) for s in owned)),
           "here_is_ours": 1.0 if here else 0.0}
    return out


def bearing(dx, dy):
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return None, None
    return round(dy / d, 4), round(dx / d, 4)


def _units_of(item):
    u = item.get("units")
    return None if u is None else _f(u)


def near_block(world, locus):
    w = world or {}
    groups = (("friend", [a for a in (w.get("armies") or []) if a.get("has_army")]),
              ("enemy", [h for h in (w.get("hostiles") or []) if h.get("kind") == "army"]),
              ("neutral", [h for h in (w.get("hostiles") or []) if h.get("kind") == "neutral_army"]),
              ("enemysett", [h for h in (w.get("hostiles") or []) if h.get("kind") == "settlement"]),
              ("ownsett", list(w.get("settlements") or [])))
    out = {}
    for name, items in groups:
        out["near_%s_closest" % name] = None
        out["near_%s_total" % name] = float(len(items))
        out["near_%s_strength" % name] = None
        out["near_%s_strength_r25" % name] = None
        out["near_%s_dir_sin" % name] = None
        out["near_%s_dir_cos" % name] = None
        for k in range(NEAR_K.get(name, NEAR_K_DEFAULT)):
            pre = "near_%s_%d" % (name, k + 1)
            out[pre + "_dist"] = out[pre + "_strength"] = None
            out[pre + "_dir_sin"] = out[pre + "_dir_cos"] = None
            if name in FACTION_CHANNELS:
                out[pre + "_faction"] = out[pre + "_race"] = None
            if name in STANCE_CHANNELS:
                out[pre + "_stance"] = out[pre + "_hp"] = None
        for r in RINGS:
            out["near_%s_r%d" % (name, r)] = None
    if not locus or locus[0] is None or locus[1] is None:
        return out
    x, y = float(locus[0]), float(locus[1])
    for name, items in groups:
        pairs = sorted(((math.hypot(float(i["x"]) - x, float(i["y"]) - y), i) for i in items
                        if i.get("x") is not None and i.get("y") is not None),
                       key=lambda p: p[0])
        if name == "friend" and pairs and pairs[0][0] < 1e-6:
            pairs = pairs[1:]
        ds = [d for d, _ in pairs]
        out["near_%s_closest" % name] = round(ds[0], 2) if ds else None
        for r in RINGS:
            out["near_%s_r%d" % (name, r)] = float(sum(1 for d in ds if d <= r))
        for k in range(NEAR_K.get(name, NEAR_K_DEFAULT)):
            pre = "near_%s_%d" % (name, k + 1)
            if k < len(pairs):
                d, it = pairs[k]
                out[pre + "_dist"] = round(d, 2)
                out[pre + "_strength"] = _units_of(it)
                s, c = bearing(float(it["x"]) - x, float(it["y"]) - y)
                out[pre + "_dir_sin"], out[pre + "_dir_cos"] = s, c
                if name in FACTION_CHANNELS:
                    fk = it.get("faction")
                    out[pre + "_faction"] = str(fk) if fk else None
                    out[pre + "_race"] = race_of(fk)
                if name in STANCE_CHANNELS:
                    st = it.get("stance")
                    out[pre + "_stance"] = str(st) if st else None
                    out[pre + "_hp"] = _f(it.get("hp"))
            else:
                out[pre + "_dist"] = out[pre + "_strength"] = None
                out[pre + "_dir_sin"] = out[pre + "_dir_cos"] = None
                if name in FACTION_CHANNELS:
                    out[pre + "_faction"] = out[pre + "_race"] = None
                if name in STANCE_CHANNELS:
                    out[pre + "_stance"] = out[pre + "_hp"] = None
        if pairs:
            d0, nearest = pairs[0]
            out["near_%s_strength" % name] = _units_of(nearest)
            s, c = bearing(float(nearest["x"]) - x, float(nearest["y"]) - y)
            out["near_%s_dir_sin" % name], out["near_%s_dir_cos" % name] = s, c
            known = [_units_of(i) for d, i in pairs if d <= 25]
            known = [u for u in known if u is not None]
            out["near_%s_strength_r25" % name] = float(sum(known)) if known else None
    return out


def _db_features(action_type, key):
    if action_type == "building":
        d = dict(DB.building_features(key))
        d.update({("chain_" + k): v for k, v in
                  DB.building_chain_features(d.get("building_chain") or "").items()})
        return d
    if action_type == "research":
        return DB.tech_features(key)
    if action_type in UNIT_RECRUIT_TYPES:
        return DB.unit_features(key.partition("@")[0])
    if action_type == "skills":
        return DB.skill_features(key)
    if action_type == "rites":
        return DB.ritual_features(key)
    return {}


_COST_FIELDS = ("create_cost", "recruitment_cost", "cost_per_round", "influence_cost")


def _target_units(atype, params, key, world):
    w = world or {}
    if atype == "attack_army":
        cqi = (params or {}).get("target_cqi")
        if cqi is None:
            return None
        for h in (w.get("hostiles") or []):
            if h.get("kind") == "army" and str(h.get("cqi")) == str(cqi):
                return _units_of(h)
        return None
    if atype == "attack_settlement":
        for h in (w.get("hostiles") or []):
            if h.get("kind") == "settlement" and str(h.get("region")) == str(key):
                return _units_of(h)
        return None
    return None


DIPLO_TERM_FEATS = ("nonaggression_pact", "trade_agreement", "defensive_alliance", "soft_access",
                    "military_alliance", "vassal", "confederation", "declare_war", "peace")
DIPLO_GIFT_RANK = {"small": 1, "medium": 2, "large": 3}


DIPLO_ARMY_KINDS = ("army", "neutral_army")


def _target_army_dist(world, faction):
    if not faction:
        return None
    best = None
    for h in ((world or {}).get("hostiles") or []):
        if h.get("faction") != faction or h.get("kind") not in DIPLO_ARMY_KINDS:
            continue
        if h.get("is_armed_citizenry") is True:
            continue
        d = _f(h.get("dist"))
        if d is not None and (best is None or d < best):
            best = d
    return best


def _diplomacy_feats(atype, params, world=None):
    if atype != "diplomacy":
        return {}
    terms = list(params.get("terms") or [])
    out = {
        "dip_target": params.get("faction"),
        "dip_target_race": race_of(params.get("faction")),
        "dip_standing": _f(params.get("standing")),
        "dip_standing_abs": (abs(_f(params.get("standing")))
                             if _f(params.get("standing")) is not None else None),
        "dip_hostile_attitude": (1.0 if (_f(params.get("standing")) or 0.0) < 0 else 0.0),
        "dip_at_war": _f(params.get("at_war")),
        "dip_allied": _f(params.get("allied")),
        "dip_trade": _f(params.get("trade")),
        "dip_their_vassal": _f(params.get("their_vassal")),
        "dip_has_any_tie": 1.0 if any(_f(params.get(k)) for k in
                                      ("allied", "trade", "their_vassal")) else 0.0,
        "dip_n_terms": float(len(terms)),
        "dip_is_pair": 1.0 if len(terms) > 1 else 0.0,
        "dip_target_army_dist": _target_army_dist(world, params.get("faction")),
    }
    for t in DIPLO_TERM_FEATS:
        out["dip_term_%s" % t] = 1.0 if t in terms else 0.0
    gift = params.get("gift")
    gift = (gift.get("tier") if isinstance(gift, dict) else gift) or None
    out["dip_gift_tier"] = str(gift) if gift else "none"
    out["dip_is_gift"] = 1.0 if gift else 0.0
    out["dip_gift_rank"] = float(DIPLO_GIFT_RANK.get(str(gift), 0))
    return out


HERO_ABILITIES = ("assist_army", "hinder_agent", "hinder_army", "hinder_character",
                  "hinder_settlement")
HERO_AGENT_TYPES = ("champion", "dignitary", "engineer", "runesmith", "spy", "wizard")


def _hero_action_feats(atype, params, locus, world):
    if atype != "hero_action":
        return {}
    ability = params.get("ability")
    agent_type = params.get("agent_type")
    target_kind = params.get("target_kind")
    is_own = 1.0 if params.get("target_own") else 0.0
    out = {
        "ha_action": params.get("action") or "none",
        "ha_action_key": params.get("action_key") or "none",
        "ha_ability": ability or "none",
        "ha_agent_type": agent_type or "none",
        "ha_target_kind": target_kind or "none",
        "ha_target_is_own": is_own,
        "ha_is_hostile": 1.0 if str(ability or "").startswith("hinder") else 0.0,
        "ha_is_assist": 1.0 if str(ability or "").startswith("assist") else 0.0,
        "ha_vs_settlement": 1.0 if target_kind == "settlement" else 0.0,
        "ha_vs_character": 1.0 if target_kind == "character" else 0.0,
        "ha_target_faction": params.get("target_faction") or "none",
        "ha_target_race": race_of(params.get("target_faction")),
        "ha_attribute": params.get("attribute") or "none",
        "ha_chance": _f(params.get("chance")),
        "ha_ability_category": params.get("ability_category") or "none",
        "ha_skill_unlocked": 1.0 if params.get("skill_unlocked") else 0.0,
        "ha_innate": 1.0 if params.get("innate") else 0.0,
        "ha_target_on_settlement": 1.0 if params.get("target_on_settlement") else 0.0,
        "ha_target_is_agent": 1.0 if params.get("target_is_agent") else 0.0,
    }
    for a in HERO_ABILITIES:
        out["ha_ability_%s" % a] = 1.0 if ability == a else 0.0
    for t in HERO_AGENT_TYPES:
        out["ha_agent_%s" % t] = 1.0 if agent_type == t else 0.0
    tgt = None
    cqi = params.get("target_cqi")
    if cqi is not None:
        for h in ((world or {}).get("hostiles") or []) + ((world or {}).get("armies") or []):
            if str(h.get("cqi")) == str(cqi):
                tgt = h
                break
    out["ha_target_units"] = _f(_units_of(tgt)) if tgt else None
    out["ha_target_hp"] = _f(tgt.get("hp")) if tgt else None
    return out


def _recruit_hero_feats(atype, params):
    if atype != "recruit_hero":
        return {}
    t = params.get("agent_type")
    out = {"rh_agent_type": t or "none",
           "rh_type_fielded": _f(params.get("type_fielded")),
           "rh_n_candidates": _f(params.get("n_candidates")),
           "rh_cand_rank": _f(params.get("cand_rank"))}
    for a in HERO_AGENT_TYPES:
        out["rh_is_%s" % a] = 1.0 if t == a else 0.0
    return out


def _target_hp(atype, params, world):
    if atype != "attack_army":
        return None
    cqi = (params or {}).get("target_cqi")
    if cqi is None:
        return None
    for h in ((world or {}).get("hostiles") or []):
        if h.get("kind") == "army" and str(h.get("cqi")) == str(cqi):
            return _f(h.get("hp"))
    return None


DELTA_CHANNELS = ("friend", "enemy", "enemysett", "ownsett", "neutral")


def _channel_items(world, name, self_cqi=None):
    w = world or {}
    if name == "friend":
        items = [a for a in (w.get("armies") or []) if a.get("has_army")]
        if self_cqi is not None:
            items = [a for a in items if str(a.get("cqi")) != str(self_cqi)]
        return items
    if name == "enemy":
        return [h for h in (w.get("hostiles") or []) if h.get("kind") == "army"]
    if name == "neutral":
        return [h for h in (w.get("hostiles") or []) if h.get("kind") == "neutral_army"]
    if name == "enemysett":
        return [h for h in (w.get("hostiles") or []) if h.get("kind") == "settlement"]
    return list(w.get("settlements") or [])


def _nearest_dist(items, x, y):
    best = None
    for i in items:
        if i.get("x") is None or i.get("y") is None:
            continue
        d = math.hypot(float(i["x"]) - x, float(i["y"]) - y)
        if best is None or d < best:
            best = d
    return round(best, 2) if best is not None else None


def action_block(offer, locus, treasury, world=None, self_units=None, self_hp=None,
                 near_before=None, self_cqi=None):
    atype, key = offer.get("action_type"), str(offer.get("key"))
    params = offer.get("params") or {}
    out = {"opt_type": atype, "opt_key": key,
           "opt_available": 1.0 if offer.get("available") else 0.0,
           "opt_gate": offer.get("gate") or "none"}
    out.update(_diplomacy_feats(atype, params, world))
    out.update(_hero_action_feats(atype, params, locus, world))
    out.update(_recruit_hero_feats(atype, params))
    out["opt_recruit_queue"] = (str(params.get("queue") or key.partition("@")[2] or "none")
                                if atype == "recruit_unit" else "none")
    tx, ty = params.get("x"), params.get("y")
    if locus and tx is not None and ty is not None and locus[0] is not None:
        dx, dy = float(tx) - float(locus[0]), float(ty) - float(locus[1])
        out["opt_target_dist"] = round(math.hypot(dx, dy), 2)
        out["opt_target_dir_sin"], out["opt_target_dir_cos"] = bearing(dx, dy)
    else:
        out["opt_target_dist"] = None
        out["opt_target_dir_sin"] = out["opt_target_dir_cos"] = None
    for ch in DELTA_CHANNELS:
        out["opt_delta_%s_dist" % ch] = None
    if tx is not None and ty is not None and near_before:
        ax, ay = float(tx), float(ty)
        for ch in DELTA_CHANNELS:
            before = _f(near_before.get("near_%s_1_dist" % ch))
            after = _nearest_dist(_channel_items(world, ch, self_cqi), ax, ay)
            out["opt_delta_%s_dist" % ch] = (round(after - before, 2)
                                             if after is not None and before is not None
                                             else None)
    tgt_units = _target_units(atype, params, key, world)
    out["opt_self_units"] = _f(self_units)
    out["opt_target_units"] = tgt_units
    if out["opt_self_units"] is not None and tgt_units is not None:
        out["opt_strength_diff"] = out["opt_self_units"] - tgt_units
        out["opt_strength_ratio"] = out["opt_self_units"] / max(tgt_units, 1.0)
    else:
        out["opt_strength_diff"] = out["opt_strength_ratio"] = None
    out["opt_self_hp"] = _f(self_hp)
    out["opt_target_hp"] = _target_hp(atype, params, world)
    if out["opt_self_hp"] is not None and out["opt_target_hp"] is not None:
        out["opt_hp_diff"] = out["opt_self_hp"] - out["opt_target_hp"]
        out["opt_hp_ratio"] = out["opt_self_hp"] / max(out["opt_target_hp"], 1.0)
    else:
        out["opt_hp_diff"] = out["opt_hp_ratio"] = None
    out["opt_target_faction"] = params.get("target_faction")
    out["opt_target_race"] = race_of(params.get("target_faction"))
    out["opt_trait"] = params.get("trait")
    out["opt_n_traits"] = _f(params.get("n_traits"))
    out["opt_cand_rank"] = _f(params.get("cand_rank"))
    out["opt_slot_index"] = _f(params.get("slot_index"))
    out["opt_candidate_index"] = _f(params.get("candidate_index"))
    out["opt_rite_index"] = _f(params.get("rite_index"))
    out["opt_is_active"] = _f(params.get("active"))
    db = _db_features(atype, key) or {}
    cost = None
    for k, v in db.items():
        if k in ("key", "id"):
            continue
        n = _f(v)
        out["opt_db_" + k] = n if n is not None else (str(v) if v is not None else None)
        if k in _COST_FIELDS and n is not None and cost is None:
            cost = n
    out["opt_cost"] = cost
    out["opt_cost_vs_treasury"] = (cost / treasury) if (cost and treasury) else None
    if atype == "research":
        rc, rp = _f(params.get("cost")), _f(params.get("points_available"))
        out["opt_research_turns"] = (rc / max(rp, 1.0)) if rc is not None else None
    else:
        out["opt_research_turns"] = None
    if atype == "recruit_unit":
        ct = _f(out.get("opt_db_create_time"))
        out["opt_recruit_turns_effective"] = ((ct * 2.0 if key.partition("@")[2] == "global"
                                               else ct) if ct is not None else None)
    elif atype in UNIT_RECRUIT_TYPES:
        out["opt_recruit_turns_effective"] = 0.0
    else:
        out["opt_recruit_turns_effective"] = None
    return out


def _locus(context_kind, state, world, provinces):
    if context_kind in ("lord", "hero"):
        return (state.get("x"), state.get("y"))
    if context_kind == "province":
        s = next((s for s in (world.get("settlements") or [])
                  if s.get("region") == state.get("region")), None)
        return (s.get("x"), s.get("y")) if s else (None, None)
    cap = (next((s for s in (world.get("settlements") or []) if s.get("capital")), None)
           or (world.get("settlements") or [None])[0])
    return (cap.get("x"), cap.get("y")) if cap else (None, None)


def state_row(record, entity):
    world = record.get("world") or {}
    provinces = {e["context_id"]: e.get("state") or {} for e in record.get("entities") or []
                 if e.get("context_kind") == "province"}
    ck, st = entity.get("context_kind"), entity.get("state") or {}
    row = campaign_block(record.get("campaign") or {}, world)
    row["ctx_kind"] = ck
    if ck in ("lord", "hero"):
        here = provinces.get(st.get("region"))
        row.update(lord_block(st))
        row.update(lord_territory_block(st, world))
        row.update(lord_recruit_block(st))
        row.update(province_block(here))
        row.update(province_buildings_block(provinces, here))
        row.update(carried_province_block(provinces, here))
    elif ck == "province":
        row.update(dict(_EMPTY_LORD))
        row.update(province_block(st))
        row.update(province_buildings_block(provinces, st))
        row.update(carried_province_block(provinces, st))
    else:
        cap = next((p for p in provinces.values() if p.get("is_capital")), None)
        row.update(dict(_EMPTY_LORD))
        row.update(province_block(cap))
        row.update(province_buildings_block(provinces, cap))
        row.update(carried_province_block(provinces, cap))
    loc = _locus(ck, st, world, provinces)
    near = near_block(world, loc)
    row.update(near)
    row.update(positioning_block(near, world, st.get("province"), loc))
    return row


def offer_rows(record, entity, base_sink=None):
    world = record.get("world") or {}
    provinces = {e["context_id"]: e.get("state") or {} for e in record.get("entities") or []
                 if e.get("context_kind") == "province"}
    st = entity.get("state") or {}
    locus = _locus(entity.get("context_kind"), st, world, provinces)
    treasury = _f((record.get("campaign") or {}).get("treasury"))
    self_units = _f(st.get("units"))
    self_hp = _f(st.get("hp"))
    base = state_row(record, entity)
    near_before = {k: v for k, v in base.items() if k.startswith("near_")}
    out = []
    for o in entity.get("offers") or []:
        row = dict(base)
        row.update(action_block(o, locus, treasury, world=world, self_units=self_units,
                                self_hp=self_hp, near_before=near_before,
                                self_cqi=st.get("cqi")))
        out.append((o, row))
    if base_sink is not None:
        base_sink[str(entity.get("context_id"))] = base
    return out


def decision_rows(record, base_sink=None):
    out = []
    for e in record.get("entities") or []:
        for offer, row in offer_rows(record, e, base_sink=base_sink):
            out.append((e, offer, row))
    return out


MODEL_COLUMNS = frozenset((
    "opt_type", "opt_key", "opt_available", "opt_gate", "opt_cost", "opt_cost_vs_treasury",
    "opt_hp_ratio", "opt_hp_diff", "opt_self_hp", "opt_self_units",
    "opt_strength_ratio", "opt_strength_diff",
    "opt_target_faction", "opt_target_race", "opt_target_dist", "opt_is_active", "ctx_kind",
    "opt_research_turns", "opt_recruit_turns_effective",
    "opt_db_chain_chain_category", "opt_db_upkeep_cost",
    "opt_delta_friend_dist", "opt_delta_enemy_dist", "opt_delta_enemysett_dist",
    "opt_delta_ownsett_dist", "opt_delta_neutral_dist",
    "lord_subtype", "lord_hp", "lord_stance", "lord_units", "lord_rank", "lord_skill_points",
    "lord_ap_pct", "lord_acted", "lord_has_army",
    "lord_pending_recruits", "lord_garrisoned", "lord_besieging", "lord_is_leader",
    "lord_in_own_territory",
    "near_friend_1_dist",
    "near_enemy_1_dist", "near_enemy_1_stance", "near_enemy_1_faction",
    "near_enemy_1_strength",
    "near_enemysett_1_dist", "near_enemysett_1_faction",
    "near_enemysett_1_strength", "near_ownsett_1_dist",
    "near_neutral_1_faction", "near_neutral_1_race",
    "near_enemy_total", "near_enemysett_total",
    "prov_province", "prov_active_edict", "prov_is_capital", "prov_buildings", "prov_free_slots",
    "prov_public_order", "prov_settlement_level",
    "camp_faction", "camp_race", "camp_turn", "camp_lord_level", "camp_power_rank",
    "camp_settlements", "camp_enemy_settlements", "camp_income", "camp_treasury", "camp_armies",
    "camp_is_researching",
    "camp_taken_total", "camp_taken_diplomacy", "camp_act_index",
    "camp_prev_action_1", "camp_prev_action_2", "camp_prev_action_3",
    "camp_prev_action_4", "camp_prev_action_5",
    "dip_target", "dip_target_race", "dip_at_war", "dip_allied", "dip_has_any_tie",
    "dip_gift_tier", "dip_gift_rank", "dip_standing", "dip_trade", "dip_target_army_dist",
    "pos_exposed", "pos_support_ratio", "agg_enemy_n", "agg_enemy_mean_dist",
    "isc_screen", "isc_n_options", "isc_option", "isc_fc_result", "isc_fc_casualties",
    "isc_dilemma_id", "isc_option_id", "isc_option_label", "isc_payload", "isc_n_payload",
))

MODEL_COLUMNS_ENABLED = True


def split_columns(rows):
    cols = {}
    for r in rows:
        for k, v in r.items():
            if MODEL_COLUMNS_ENABLED and k not in MODEL_COLUMNS:
                continue
            if v is None:
                cols.setdefault(k, True)
                continue
            cols[k] = cols.get(k, True) and isinstance(v, (int, float)) and not isinstance(v, bool)
    num = sorted(k for k, isnum in cols.items() if isnum)
    cat = sorted(k for k, isnum in cols.items() if not isnum)
    if MODEL_COLUMNS_ENABLED and rows and not (num or cat):
        raise RuntimeError(
            "MODEL_COLUMNS matched nothing against %d built columns -- the feature names have "
            "moved and every model would train on an empty matrix. Built sample: %s"
            % (len(rows[0]), sorted(list(rows[0])[:12])))
    return num, cat


def matrix(rows, num, cat):
    out = []
    for r in rows:
        vals = []
        for c in num:
            v = _f(r.get(c))
            vals.append(float("nan") if v is None else v)
        for c in cat:
            v = r.get(c)
            vals.append("?" if v is None else str(v))
        out.append(vals)
    return out
