r"""features.py -- the v7 featurizer. Turns STORED DECISION RECORDS into model rows.

Its inputs come from decisions.sqlite (via decisions/journal.read_decision or store.training_rows)
and from the offline reference sqlite. It never opens a bus and never talks to the game -- that is
the recorder's job, and keeping it that way is what guarantees TRAINING AND PREDICTION FEATURIZE
IDENTICALLY: both read the same stored records through this same function.

ONE UNIFORM SCHEMA ACROSS ALL OFFERS -- the same COLUMNS for every action type, so a building and
an attack can be ranked against each other. The VALUES are not uniform, and that is the whole point:
within a single decision point only camp_* is constant. prov_* differs by which province the row is
about, lord_* differs by which lord, near_* differs by where that entity physically stands, and
opt_* differs per action. (Deliberate: noisier at first than per-action-type feature spaces, but
structurally cleaner and the only shape a single global ranking can be trained on.)

    camp_*  the campaign block          faction, turn, income, settlements, treasury, force counts
                                        -- the ONLY block shared by every row of a decision point
    prov_*  the LOCAL province block    the province at the action's locus (joined from the record)
    lord_*  the SUBJECT lord block      only when a lord is the subject; nulls otherwise
    near_*  the LOCAL force picture     friendly/enemy armies + settlements around the locus,
                                        as ring counts + closest distance (computed here, from the
                                        raw positions the recorder stored)
    opt_*   the action                  type, key, availability, its own params, and the game-DB
                                        record for that key (cost, tier, upkeep, ...)

E1 = f(state, action) sees all of it. E2 = g(state) sees everything except opt_*.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor", "reference"))
import features_db as DB                                  # noqa: E402  offline sqlite, no game

# distance rings (logical map units) for the local force picture
RINGS = (10, 25, 50)

# every action type the launcher can execute -- fixed order so the categorical is stable
ACTION_TYPES = ("stance", "building", "research", "skills", "items", "item_unequip", "rites",
                "recruit_unit", "recruit_lord", "edict", "attack_army", "attack_settlement",
                "garrison", "leave_garrison", "end_turn", "noop")


def _f(v):
    """Numeric coercion: bools become 0/1, unparseable becomes None (CatBoost reads it as NaN)."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------ blocks
def campaign_block(campaign, world):
    w = world or {}
    armies = w.get("armies") or []
    hostiles = w.get("hostiles") or []
    return {"camp_faction": campaign.get("faction"),
            "camp_turn": _f(campaign.get("turn")),
            "camp_income": _f(campaign.get("income")),
            "camp_settlements": _f(campaign.get("settlements")),
            "camp_treasury": _f(campaign.get("treasury")),
            "camp_is_researching": _f(campaign.get("is_researching")),
            "camp_armies": float(sum(1 for a in armies if a.get("has_army") and a.get("is_general"))),
            "camp_characters": float(len(armies)),
            "camp_enemy_armies": float(sum(1 for h in hostiles if h.get("kind") == "army")),
            "camp_enemy_settlements": float(sum(1 for h in hostiles if h.get("kind") == "settlement"))}


_EMPTY_LORD = {"lord_rank": None, "lord_skill_points": None, "lord_units": None,
               "lord_pending_recruits": None, "lord_ap_pct": None, "lord_garrisoned": None,
               "lord_besieging": None, "lord_acted": None, "lord_stance": None,
               "lord_present": 0.0}


def lord_block(state):
    if not state:
        return dict(_EMPTY_LORD)
    return {"lord_rank": _f(state.get("rank")), "lord_skill_points": _f(state.get("skill_points")),
            "lord_units": _f(state.get("units")),
            "lord_pending_recruits": _f(state.get("pending_recruits")),
            "lord_ap_pct": _f(state.get("ap_pct")), "lord_garrisoned": _f(state.get("garrisoned")),
            "lord_besieging": _f(state.get("besieging")), "lord_acted": _f(state.get("acted")),
            "lord_stance": state.get("stance"), "lord_present": 1.0}


_EMPTY_PROV = {"prov_province": None, "prov_complete": None, "prov_max_slots": None,
               "prov_free_slots": None, "prov_can_set_edict": None, "prov_selected_edict": None,
               "prov_active_edict": None, "prov_public_order": None, "prov_buildings": None,
               "prov_is_capital": None, "prov_present": 0.0}


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
            "prov_is_capital": _f(state.get("is_capital")), "prov_present": 1.0}


def near_block(world, locus):
    """The LOCAL force picture around a locus (x, y), from the raw positions the recorder stored."""
    w = world or {}
    groups = (("friend", [a for a in (w.get("armies") or [])
                          if a.get("has_army") and a.get("is_general")]),
              ("enemy", [h for h in (w.get("hostiles") or []) if h.get("kind") == "army"]),
              ("enemysett", [h for h in (w.get("hostiles") or []) if h.get("kind") == "settlement"]),
              ("ownsett", list(w.get("settlements") or [])))
    out = {}
    for name, items in groups:
        out["near_%s_closest" % name] = None
        out["near_%s_total" % name] = float(len(items))
        for r in RINGS:
            out["near_%s_r%d" % (name, r)] = None
    if not locus or locus[0] is None or locus[1] is None:
        return out
    x, y = float(locus[0]), float(locus[1])
    for name, items in groups:
        ds = sorted(math.hypot(float(i["x"]) - x, float(i["y"]) - y)
                    for i in items if i.get("x") is not None and i.get("y") is not None)
        if name == "friend" and ds and ds[0] < 1e-6:
            ds = ds[1:]                      # the subject army itself is not "nearby company"
        out["near_%s_closest" % name] = round(ds[0], 2) if ds else None
        for r in RINGS:
            out["near_%s_r%d" % (name, r)] = float(sum(1 for d in ds if d <= r))
    return out


# ------------------------------------------------------------------------------ action block
def _db_features(action_type, key):
    """The game's own record for the action -- costs, tiers, upkeep. Offline sqlite lookup."""
    if action_type == "building":
        d = dict(DB.building_features(key))
        d.update({("chain_" + k): v for k, v in
                  DB.building_chain_features(d.get("building_chain") or "").items()})
        return d
    if action_type == "research":
        return DB.tech_features(key)
    if action_type == "recruit_unit":
        return DB.unit_features(key)
    if action_type == "skills":
        return DB.skill_features(key)
    if action_type == "rites":
        return DB.ritual_features(key)
    return {}


_COST_FIELDS = ("create_cost", "recruitment_cost", "cost_per_round", "influence_cost")


def action_block(offer, locus, treasury):
    """opt_* for one offer: what it is, whether it was on, its params, and its DB record."""
    atype, key = offer.get("action_type"), str(offer.get("key"))
    params = offer.get("params") or {}
    out = {"opt_type": atype, "opt_key": key,
           "opt_available": 1.0 if offer.get("available") else 0.0,
           "opt_gate": offer.get("gate") or "none"}
    # target geometry: the recorder stored the target's raw position, the distance is ours to make
    tx, ty = params.get("x"), params.get("y")
    if locus and tx is not None and ty is not None and locus[0] is not None:
        out["opt_target_dist"] = round(math.hypot(float(tx) - float(locus[0]),
                                                  float(ty) - float(locus[1])), 2)
    else:
        out["opt_target_dist"] = None
    out["opt_target_faction"] = params.get("target_faction")
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
    return out


# ------------------------------------------------------------------------------ assembly
def _locus(context_kind, state, world, provinces):
    if context_kind == "lord":
        return (state.get("x"), state.get("y"))
    if context_kind == "province":
        s = next((s for s in (world.get("settlements") or [])
                  if s.get("region") == state.get("region")), None)
        return (s.get("x"), s.get("y")) if s else (None, None)
    cap = (next((s for s in (world.get("settlements") or []) if s.get("capital")), None)
           or (world.get("settlements") or [None])[0])
    return (cap.get("x"), cap.get("y")) if cap else (None, None)


def state_row(record, entity):
    """E2's input: everything about the situation, nothing about the action."""
    world = record.get("world") or {}
    provinces = {e["context_id"]: e.get("state") or {} for e in record.get("entities") or []
                 if e.get("context_kind") == "province"}
    ck, st = entity.get("context_kind"), entity.get("state") or {}
    row = campaign_block(record.get("campaign") or {}, world)
    row["ctx_kind"] = ck
    if ck == "lord":
        row.update(lord_block(st))
        row.update(province_block(provinces.get(st.get("region"))))   # the province he stands in
    elif ck == "province":
        row.update(dict(_EMPTY_LORD))
        row.update(province_block(st))
    else:
        cap = next((p for p in provinces.values() if p.get("is_capital")), None)
        row.update(dict(_EMPTY_LORD))
        row.update(province_block(cap))
    row.update(near_block(world, _locus(ck, st, world, provinces)))
    return row


def offer_rows(record, entity):
    """[(offer, E1 row)] for one entity: its state row plus each offer's action block."""
    world = record.get("world") or {}
    provinces = {e["context_id"]: e.get("state") or {} for e in record.get("entities") or []
                 if e.get("context_kind") == "province"}
    st = entity.get("state") or {}
    locus = _locus(entity.get("context_kind"), st, world, provinces)
    treasury = _f((record.get("campaign") or {}).get("treasury"))
    base = state_row(record, entity)
    out = []
    for o in entity.get("offers") or []:
        row = dict(base)
        row.update(action_block(o, locus, treasury))
        out.append((o, row))
    return out


def decision_rows(record):
    """[(entity, offer, E1 row)] for a whole faction-wide decision point -- the ranking's input."""
    out = []
    for e in record.get("entities") or []:
        for offer, row in offer_rows(record, e):
            out.append((e, offer, row))
    return out


# ------------------------------------------------------------------------------ column typing
def split_columns(rows):
    """(numeric_cols, categorical_cols) over a list of feature dicts. A column is numeric only if
    every value present is a number; anything else is categorical (CatBoost handles them natively)."""
    cols = {}
    for r in rows:
        for k, v in r.items():
            if v is None:
                cols.setdefault(k, True)
                continue
            cols[k] = cols.get(k, True) and isinstance(v, (int, float)) and not isinstance(v, bool)
    num = sorted(k for k, isnum in cols.items() if isnum)
    cat = sorted(k for k, isnum in cols.items() if not isnum)
    return num, cat


def matrix(rows, num, cat):
    """CatBoost-ready matrix: numerics as floats (None -> nan), categoricals as strings."""
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
