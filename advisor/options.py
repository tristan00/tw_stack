from __future__ import annotations

"""Generate the move universe from a recorded decision, then gate it.

THIS IS THE ADVISOR'S JOB, AND IT USED TO BE THE RECORDER'S. The collector fetched game
state and, in the same pass, decided what the agent was allowed to do -- so an action the
generator never emitted was indistinguishable from an action the game refused, and the
only record of either was a row the collector had already made up its mind about.

Now the split is:

    recorder   collect game state  ->  store state
    advisor    generate the move universe FROM THAT STORED STATE
               gate it
               -> surviving options handed back to the recorder to store
               -> survivors passed to the strategies

Everything here is a pure function of a decision record. Nothing in this module touches
the bus, the database, or a file. That is checkable, and check.py checks it.

ONE GATE LAYER. `gate()` is the only thing that decides whether a candidate survives. It
absorbs both halves of what used to be two separate mechanisms: the ~40 inline
availability tests that lived inside the collector's assemblers, and all of
Policy.eligible -- per-turn caps, entity retirement, blacklists, attack/gift pair caps,
FORBIDDEN_KEYS and the end_turn rule. There was never a principled line between them:
`no_points` is `skill_points >= 1` and a per-turn cap is a count, and both decide the same
question. The generators still compute their conditions where the data is, because that is
where the data is, but they report a REASON and gate() is what acts on it.

Gate state is intra-turn -- Policy resets it every turn and updates it after every result
-- so generation runs per decision, not once per turn.
"""

import collections
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)
sys.path.insert(0, common.REFERENCE)

from collect import CollectError, _num


MOVEMENT_STANCES = frozenset((
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DOUBLE_TIME",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_SET_CAMP_RAIDING",
))


def _merc_offers(state, merc_pools):
    offers = []
    at_sea = not state.get("region")
    for atype in sorted(merc_pools or {}):
        for r in merc_pools[atype]:
            if "can" in r:
                ok = bool(r.get("can")) and r["avail"] > 0 and not at_sea
                gate = None if ok else ("at_sea" if at_sea else
                                        "locked" if not r.get("can") else "pool_empty")
            else:
                ok, gate = True, None
            offers.append(_offer(atype, r["key"], ok, gate,
                                 unit=r["key"], cost=r["cost"], pool_avail=r["avail"]))
    return offers


def _offer(atype, key, available, gate=None, **params):
    return {"action_type": atype, "key": key, "available": bool(available),
            "gate": gate, "params": params or {}}


def _skill_offers(skills, has_pts):
    """The skills block, shared by lords and heroes -- it was duplicated verbatim, so a
    fix to one silently left the other behind. Returns (offers, active_skill_keys)."""
    offers, active = [], []
    for s in skills or ():
        key, status = s.get("key"), s.get("status")
        lvl, tot = s.get("level"), s.get("total_levels")
        if status == "active":
            active.append(key)
        ok = (status == "active" and has_pts)
        offers.append(_offer("skills", key, ok,
                             None if ok else ("no_points" if not has_pts else status),
                             level=lvl, total_levels=tot, tier=s.get("tier"),
                             at_max=(None if lvl is None or tot is None else lvl >= tot)))
    return offers, active


def _move_offers(tiles):
    """A validated destination becomes a candidate here, not in the recorder."""
    return [_offer("move", "xy:%s,%s" % (m["x"], m["y"]), True, None,
                   x=m["x"], y=m["y"], sample_index=m.get("sample_index"),
                   reach_rays=m.get("reach_rays"), reach_max=m.get("reach_max"))
            for m in tiles or ()]


def _leave_garrison_offer(state, moves):
    """The inverse of `garrison`, and it was never emitted once in 9,013,360 offers.

    `leave_garrison` is declared in advisor/features.py and mapgraph/schema.py and has a
    working executor in launcher/cm_actions.py -- it was simply never offered, so an army
    that walked into a settlement had no recorded way back out and the action type had 0
    rows. cm:leave_garrison(lookup, x, y) needs a destination, and the only tiles known to
    be legal are the move candidates the game itself validated this decision, so the exit
    is the nearest of those. It is emitted even when unavailable: a gate reason is data,
    and a type that only appears when it is legal cannot teach when it is not.
    """
    lx, ly = state.get("x"), state.get("y")
    tiles = [m["params"] for m in (moves or [])
             if (m.get("params") or {}).get("x") is not None]
    if lx is not None and ly is not None:
        tiles.sort(key=lambda p: (p["x"] - lx) ** 2 + (p["y"] - ly) ** 2)
    if not state.get("garrisoned"):
        return _offer("leave_garrison", "leave_garrison", False, "not_garrisoned")
    if not tiles:
        return _offer("leave_garrison", "leave_garrison", False, "no_exit_tile")
    return _offer("leave_garrison", "leave_garrison", True, None,
                  x=tiles[0]["x"], y=tiles[0]["y"])


def _horde_building_offers(slots):
    """ONE OFFER PER BUILDING, not per (slot, building).

    A horde's slots are interchangeable: an empty force slot is an empty force slot, and
    constructing X in slot 237 or in slot 240 has identical effect. Emitting one candidate
    per slot produced the SAME decision four times -- measured on a Beastmen campaign as
    24 action nodes a message-passing network cannot separate, every one of them
    horde_building, four copies of one building across force_slot_237..240. That is not
    graph blindness, it is the generator asking the same question four times and splitting
    the listwise softmax between identical answers.

    This is the province edict case again: an edict scopes to a province, so the capital
    is the canonical emitter. Here the building is the decision and the slot is the
    execution handle -- kept in params, because cco_actions constructs by slot_id.

    A slot already holding something is NOT interchangeable, so those stay per-slot.
    """
    offers = []
    seen = set()
    for s in slots or []:
        ok = bool(s["available"])
        if s.get("empty"):
            if s["key"] in seen:
                continue
            seen.add(s["key"])
        offers.append(_offer("horde_building", "%s@%s" % (s["slot_id"], s["key"]), ok,
                             None if ok else "requirements_not_met",
                             slot_id=s["slot_id"], slot_index=s["slot_index"],
                             building_key=s["key"], slot_empty=s["empty"]))
    return offers


def _lord_targets(world):
    armies = [h for h in world["hostiles"] if h.get("kind") == "army" and h.get("cqi")]
    esetts = [h for h in world["hostiles"] if h.get("kind") == "settlement" and h.get("region")]
    osetts = [s for s in world["settlements"] if s.get("region")]
    rsetts = [s for s in (world.get("ruins") or []) if s.get("region")]
    return armies, esetts, osetts, rsetts


def _lord_options(cqi, state, world, campaign):
    """Every action a lord could take, with a reason attached to the ones it could not."""
    offers = []
    stationed = (world or {}).get("stationed") or {}
    anc_pool = (campaign or {}).get("anc_pool")
    equipped = state.get("equipped")
    equipped_anywhere = (campaign or {}).get("equipped_all")
    reach_c = state.get("reach_chars") or {}
    reach_s = state.get("reach_setts") or {}
    moves = _move_offers(state.get("move_tiles"))
    for s in state.get("stances") or ():
        key, active = s.get("key"), bool(s.get("active"))
        can_act, afford = bool(s.get("can_activate")), bool(s.get("can_afford"))
        ok = bool(can_act and afford and not active)
        gate = None if ok else ("active" if active else
                                "cannot_activate" if not can_act else "cannot_afford")
        offers.append(_offer("stance", key, ok, gate, active=active))
    for c in state.get("recruitable") or ():
        # ONE OFFER PER UNIT. This used to cross-product every unit with
        # RECRUIT_QUEUES = ("local","global") and mark both rows available=1 -- in all
        # 221,504 offers. The queue was never read from anywhere: no CCO context and no
        # script method distinguishes the local pool from the global one headlessly, and
        # the executor only learns the real pools by reading the recruitment panel. The
        # cost of inventing it is measurable: of 652 'global' picks the agent made, 371
        # (56.9%) died in execute_failed because no global pool was there, against 225 of
        # 1,128 for 'local'. The pool the click lands in is now an execution detail and is
        # recorded as `queue_used` in the confirm diagnostics, where it is observed
        # instead of asserted.
        offers.append(_offer("recruit_unit", c["key"],
                             c.get("state") == "active",
                             None if c.get("state") == "active" else c.get("state"),
                             unit=c["key"], cost=c.get("cost"),
                             recruitment_disabled=c.get("disabled")))
    has_pts = (state.get("skill_points") or 0) >= 1
    offers.extend(_skill_offers(state.get("skills"), has_pts)[0])
    armies, esetts, osetts, rsetts = _lord_targets(world)
    marching = str(state.get("stance") or "") in MOVEMENT_STANCES
    recruiting = (state.get("pending_recruits") or 0) > 0
    for a in armies:
        ok = bool(reach_c.get(str(a["cqi"]))) and not marching
        offers.append(_offer("attack_army", "cqi:%s" % a["cqi"], ok,
                             None if ok else ("movement_stance" if marching else
                                              "recruiting" if recruiting else "cannot_reach"),
                             target_cqi=a["cqi"], target_faction=a.get("faction"),
                             x=a.get("x"), y=a.get("y")))
    for s in esetts:
        ok = bool(reach_s.get(s["region"])) and not marching and not recruiting
        offers.append(_offer("attack_settlement", s["region"], ok,
                             None if ok else ("movement_stance" if marching else
                                              "recruiting" if recruiting else "cannot_reach"),
                             target_faction=s.get("faction"), x=s.get("x"), y=s.get("y")))
    for s in rsetts:
        ok = bool(reach_s.get(s["region"])) and not marching and not recruiting
        offers.append(_offer("colonize", s["region"], ok,
                             None if ok else ("movement_stance" if marching else
                                              "recruiting" if recruiting else "cannot_reach"),
                             x=s.get("x"), y=s.get("y")))
    garrisoned = state.get("garrisoned")
    occ = stationed or {}
    for s in osetts:
        holder = occ.get(str(s["region"]))
        taken = holder is not None and str(holder) != str(cqi)
        ok = bool(reach_s.get(s["region"])) and not garrisoned and not taken
        gate = None if ok else ("already_in_settlement" if garrisoned
                                else "settlement_occupied" if taken else "cannot_reach")
        offers.append(_offer("garrison", "settlement:%s" % s["region"], ok, gate,
                             x=s.get("x"), y=s.get("y")))
    offers.append(_leave_garrison_offer(state, moves))
    offers.extend(_item_offers(anc_pool, equipped, equipped_anywhere))
    offers.extend(_horde_building_offers(state.get("horde_slots")))
    offers.extend(_merc_offers(state, state.get("merc_pools")))
    offers.extend(moves or [])
    offers.append(_offer("noop", "noop", True))
    return offers


ABILITY_TARGETS = {
    "hinder_settlement": ("ruins", "enemy_settlements"),
    "hinder_army": ("enemy_armies",),
    "hinder_agent": ("enemy_armies", "enemy_agents"),
    "hinder_character": ("enemy_armies", "enemy_agents"),
    "assist_army": ("own_armies",),
}


ACTION_TARGETS = {
    "scout_settlement": ("ruins",),
    "assault_garrison": ("enemy_settlements",),
    "steal_technology": ("enemy_settlements",),
    "damage_walls": ("enemy_settlements",),
    "assassinate": ("enemy_agents",),
    "wound": ("enemy_agents",),
}


INNATE_ACTIONS = frozenset(("scout_settlement",))

# ONLY THESE ABILITIES ARE IMPLEMENTED IN THE LAUNCHER. cco_actions routes an
# `assist_army` action through the assist path and everything else through a panel path
# that was never finished, so a hinder_* candidate is an action the agent can be offered,
# can pick, and can never perform. Generating it is not "collecting a refusal" -- the game
# never gets asked, so the refusal says nothing about the game. It is gated here, at
# generation, with a reason that names the truth.
SUPPORTED_ABILITIES = frozenset(("assist_army",))


COVERED_ACTIONS = frozenset((
    "assassinate", "assault_garrison", "assault_units", "block_army", "damage_walls",
    "hinder_replenishment", "increase_mobility", "replenish_troops", "scavenge", "scouting",
    "steal_technology", "training", "wound",
    "scout_settlement",
))


NEEDS_SUBPICK = frozenset((
    "damage_building",
    "assault_unit",
))


def _build_hero_actions():
    out = {}
    try:
        sys.path.insert(0, common.REFERENCE)
        import features_db as _DB
        entries = _DB.agent_action_catalogue()
    except Exception as e:
        sys.stderr.write("collect: hero-action catalogue unavailable -> %s\n" % repr(e)[:120])
        return out
    for e in entries:
        act, ability = e["action"], e["ability"]
        if act not in COVERED_ACTIONS or act in NEEDS_SUBPICK or ability not in ABILITY_TARGETS:
            continue
        if ability not in SUPPORTED_ABILITIES:
            continue
        spec = out.setdefault(act, {"loc_suffix": [],
                                    "targets": ACTION_TARGETS.get(act, ABILITY_TARGETS[ability]),
                                    "innate": act in INNATE_ACTIONS})
        spec["loc_suffix"].append("%s_%s" % (ability, act))
    return out


HERO_ACTIONS = _build_hero_actions()


_hero_matrix = {}




_subtype_types = {}


def _hero_subtype_types(faction):
    key = str(faction or "")
    if key not in _subtype_types:
        try:
            sys.path.insert(0, common.REFERENCE)
            import features_db as _DB
            _subtype_types[key] = {sub: agent for agent, sub in _DB.permitted_agent_subtypes(key)}
        except Exception as e:
            sys.stderr.write("collect: permitted subtypes for %s -> %s\n" % (key, repr(e)[:100]))
            _subtype_types[key] = {}
    return _subtype_types[key]


def _hero_action_matrix(action):
    if action not in _hero_matrix:
        spec = HERO_ACTIONS.get(action) or {}
        try:
            sys.path.insert(0, common.REFERENCE)
            import features_db as _DB
            rows = _DB.agent_action_rows(spec.get("loc_suffix") or "")
            ability = rows[0]["ability"] if rows else None
            _hero_matrix[action] = {
                "types": {r["agent"]: r["key"] for r in rows},
                "ability": ability,
                "attribute": {r["agent"]: r["attribute"] for r in rows},
                "chance": {r["agent"]: r["chance"] for r in rows},
                "category": _DB.agent_ability_category(ability) if ability else None,
            }
        except Exception as e:
            sys.stderr.write("collect: hero-action matrix for %s -> %s\n" % (action, repr(e)[:100]))
            _hero_matrix[action] = {"types": {}, "ability": None, "attribute": {}, "chance": {},
                                    "category": None}
    return _hero_matrix[action]


def _anc_id(a):
    """Identity of an ancillary. The record key when we have it, the display name only as
    a fallback -- names are not unique ("Warhorse" is 42 distinct rows among 2,671), so
    counting by name silently merged different items into one pool."""
    return a["key"] or a["name"]


def _free_by_type(pool, equipped_anywhere):
    held = collections.Counter(_anc_id(a) for a in equipped_anywhere or [])
    free = collections.Counter(_anc_id(a) for a in pool or [])
    for ident, n in held.items():
        free[ident] -= n
    return free


def _item_offers(pool, equipped, equipped_anywhere=None):
    offers = []
    if pool and equipped_anywhere is None:
        raise CollectError("_item_offers: item pool with no faction-wide equipped set -- "
                           "assignability cannot be counted")
    free = _free_by_type(pool, equipped_anywhere)
    for a in pool or []:
        name, ident = a["name"], _anc_id(a)
        ok = free.get(ident, 0) > 0
        if ok:
            free[ident] -= 1
        offers.append(_offer("items", a["key"] or name, ok,
                             None if ok else "already_equipped",
                             pool_index=a["index"], item_name=name, item_key=a["key"]))
    for a in equipped or []:
        offers.append(_offer("item_unequip", a["key"] or a["name"], True, None,
                             equipped_index=a["index"], item_name=a["name"], item_key=a["key"]))
    return offers


SETTLEMENT_COLOCATION = 1.5


def _settlement_points(world):
    w = world or {}
    pts = []
    for s in (w.get("settlements") or []):
        pts.append((s.get("x"), s.get("y")))
    for h in (w.get("hostiles") or []):
        if h.get("kind") == "settlement":
            pts.append((h.get("x"), h.get("y")))
    for r in (w.get("ruins") or []):
        pts.append((r.get("x"), r.get("y")))
    return [(float(x), float(y)) for x, y in pts if x is not None and y is not None]


def _on_settlement(pts, x, y):
    if x is None or y is None:
        return False
    fx, fy = float(x), float(y)
    for sx, sy in pts:
        if math.hypot(fx - sx, fy - sy) <= SETTLEMENT_COLOCATION:
            return True
    return False


def _is_citizenry(hostile):
    if "is_armed_citizenry" not in hostile:
        raise CollectError("hostiles row %s carries no is_armed_citizenry -- the installed mod "
                           "pack is older than the handler that emits it; rebuild bus/dist/tw.pack"
                           % hostile.get("cqi"))
    return hostile["is_armed_citizenry"] is True


def _hero_action_targets(world, kind, self_cqi):
    w = world or {}
    if kind == "ruins":
        return [{"target_kind": "settlement", "region": t["region"], "x": t.get("x"),
                 "y": t.get("y")} for t in (w.get("ruins") or []) if t.get("region")]
    if kind == "enemy_settlements":
        return [{"target_kind": "settlement", "region": h["region"], "x": h.get("x"),
                 "y": h.get("y"), "target_faction": h.get("faction")}
                for h in (w.get("hostiles") or [])
                if h.get("kind") == "settlement" and h.get("region")]
    if kind == "enemy_armies":
        return [{"target_kind": "character", "target_cqi": h["cqi"], "x": h.get("x"),
                 "y": h.get("y"), "target_faction": h.get("faction")}
                for h in (w.get("hostiles") or [])
                if h.get("kind") == "army" and h.get("cqi") and not _is_citizenry(h)]
    if kind == "own_armies":
        cz = set(str(x) for x in w["citizenry"])
        return [{"target_kind": "character", "target_cqi": a["cqi"], "x": a.get("x"),
                 "y": a.get("y"), "target_own": True}
                for a in (w.get("armies") or [])
                if a.get("has_army") and a.get("cqi") and str(a["cqi"]) != str(self_cqi)
                and str(a["cqi"]) not in cz]
    if kind == "enemy_agents":
        return [{"target_kind": "character", "target_cqi": a["cqi"], "x": a.get("x"),
                 "y": a.get("y"), "target_faction": a.get("faction"), "target_agent": True}
                for a in (w.get("enemy_agents") or [])
                if a.get("cqi") and str(a["cqi"]) != str(self_cqi)]
    return []


def _same_tile(state, x, y):
    hx, hy = (state or {}).get("x"), (state or {}).get("y")
    if hx is None or hy is None or x is None or y is None:
        return False
    return abs(float(hx) - float(x)) < 1e-6 and abs(float(hy) - float(y)) < 1e-6


def _granted_actions(skill_keys):
    try:
        sys.path.insert(0, common.REFERENCE)
        import features_db as _DB
        return _DB.actions_for_skills(skill_keys)
    except Exception as e:
        raise CollectError("agent-action unlock map unreadable (%s) -- refusing to offer hero "
                           "actions the hero may not have" % repr(e)[:90])


def _hero_action_offers(cqi, state, world, reach_c, reach_s, is_agent, type_key, skills=(),
                        granted=None, can_embed=True):
    offers = []
    active = [k for k in (skills or ()) if k]
    granted = set(granted or ())
    sett_pts = _settlement_points(world)
    embedded = any(_same_tile(state, a.get("x"), a.get("y"))
                   for a in (world.get("armies") or [])
                   if a.get("has_army") and str(a.get("cqi")) != str(cqi))
    for action, spec in HERO_ACTIONS.items():
        mat = _hero_action_matrix(action)
        action_key = (mat["types"] or {}).get(type_key)
        if action_key is None:
            continue
        sufs = spec["loc_suffix"]
        sufs = [sufs] if isinstance(sufs, str) else list(sufs)
        has_skill = any(any(s in k for s in sufs) for k in active)
        unlocked = action_key in granted
        cands = []
        for tk in spec["targets"]:
            cands.extend(_hero_action_targets(world, tk, cqi))
        for t in cands:
            is_char = t["target_kind"] == "character"
            tid = t.get("target_cqi") if is_char else t.get("region")
            reachable = bool((reach_c if is_char else reach_s or {}).get(str(tid)))
            in_sett = is_char and _on_settlement(sett_pts, t.get("x"), t.get("y"))
            embeds = mat.get("category") == "embedded"
            ok = bool(is_agent and action_key and unlocked and reachable
                      and not (embeds and embedded))
            gate = (None if ok else
                    "not_agent" if not is_agent else
                    "agent_type_cannot_%s" % (type_key or "unknown") if not action_key else
                    ("no_granted_actions" if not granted else "action_not_granted")
                    if not unlocked else
                    "cannot_reach" if not reachable else
                    "already_embedded")
            offers.append(_offer(
                "hero_action", "%s@%s" % (action, ("cqi:%s" % tid) if is_char else tid), ok, gate,
                action=action, action_key=action_key, ability=mat["ability"],
                ability_category=mat.get("category"),
                attribute=(mat.get("attribute") or {}).get(type_key),
                chance=(mat.get("chance") or {}).get(type_key),
                agent_type=type_key, skill_unlocked=has_skill, innate=bool(spec.get("innate")),
                target_kind=t["target_kind"],
                target_cqi=t.get("target_cqi"), region=t.get("region"),
                target_faction=t.get("target_faction"), target_own=bool(t.get("target_own")),
                target_on_settlement=bool(in_sett), target_is_agent=bool(t.get("target_agent")),
                x=t.get("x"), y=t.get("y")))
    return offers




def _hero_options(cqi, state, world, campaign):
    offers = []
    is_agent = bool(state.get("is_agent"))
    can_embed = bool(state.get("can_embed"))
    type_key = state.get("agent_type") or ""
    anc_pool = (campaign or {}).get("anc_pool")
    equipped = state.get("equipped")
    equipped_anywhere = (campaign or {}).get("equipped_all")
    reach_c = state.get("reach_chars") or {}
    reach_s = state.get("reach_setts") or {}
    moves = _move_offers(state.get("move_tiles"))
    has_pts = (state.get("skill_points") or 0) >= 1
    sk_offers, active_skills = _skill_offers(state.get("skills"), has_pts)
    offers.extend(sk_offers)
    granted = _granted_actions(active_skills + (state.get("hidden_skills") or []))
    offers.extend(_hero_action_offers(cqi, state, world or {}, reach_c or {}, reach_s or {},
                                      is_agent, type_key, active_skills, granted, can_embed))
    offers.extend(_item_offers(anc_pool, equipped, equipped_anywhere))
    offers.extend(moves or [])
    offers.append(_offer("noop", "noop", True))
    return offers


def _slot_action_offers(region, slots):
    offers = []
    for s in slots or []:
        idx = s.get("index")
        if idx is None:
            continue
        key = "%s@%s" % (region, int(idx))
        common_p = dict(region=region, slot_index=idx, building_key=s["key"],
                        queued_key=s["queued_key"], health=s["health"],
                        max_health=s["max_health"], ruined=s["ruined"],
                        upgrading=s["upgrading"], dismantling=s["dismantling"])
        ok = s["can_repair"] and s["damaged"] and not s["repairing"]
        offers.append(_offer("building_repair", key, ok,
                             None if ok else ("not_damaged" if not s["damaged"] else
                                              "already_repairing" if s["repairing"] else
                                              "cannot_repair"),
                             damaged=s["damaged"], repairing=s["repairing"],
                             repair_cost=s["repair_cost"], **common_p))
        ok = s["can_dismantle"] and not s["empty"]
        offers.append(_offer("building_dismantle", key, ok,
                             None if ok else ("slot_empty" if s["empty"] else "cannot_dismantle"),
                             refund=s["refund"], **common_p))
    return offers


def _province_options(region, state, campaign):
    offers = []
    lord_pools = (campaign or {}).get("lord_pools") or {}
    slot_states = state.get("slot_states")
    edicts = state.get("edicts") or []
    # ONE OFFER PER (slot, building). This loop used to hold `seen` on the building key
    # alone, so a building constructible in two slots produced a single offer carrying
    # whichever slot happened to come first -- and the other slot, a genuinely different
    # action with a different cost and a different consequence, was dropped. The slot is
    # already in params and the executor already builds from params.slot_index, so the
    # only thing the dedupe ever did was delete work.
    seen = set()
    for b in state.get("buildable") or ():
        slot_i, key = b.get("slot_index"), b.get("key")
        if (slot_i, key) in seen:
            continue
        seen.add((slot_i, key))
        ok = bool(b.get("active"))
        gate = None if ok else ("not_buildable_now" if b.get("empty")
                                else "not_upgradeable_now")
        offers.append(_offer("building", key, ok, gate,
                             slot_index=slot_i, is_upgrade=(not b.get("empty")),
                             can_upgrade=b.get("can_upgrade"), cost=b.get("cost"),
                             upkeep=b.get("upkeep"), level=b.get("level"),
                             can_afford=b.get("can_afford")))
    # An edict applies to the whole PROVINCE, but this assemble runs per REGION, so a
    # province with two owned regions offered every one of its edicts twice -- 748 of
    # 3,310 (22.6%) were literal repeats, splitting the listwise softmax between two
    # candidates that do the identical thing. The capital is the canonical emitter, and
    # the province is now in params so the offer says what it actually scopes to.
    complete = bool(state.get("complete_owner"))
    sel = state.get("selected_edict")
    if state.get("is_capital"):
        for key in edicts:
            ok = complete and key != sel
            offers.append(_offer("edict", key, ok,
                                 None if ok else ("province_not_complete" if not complete
                                                  else "already_selected"),
                                 province=state.get("province"), region=region,
                                 can_set=bool(state.get("can_set_edict")),
                                 is_selected=(key == sel)))
    for sub, pool in (lord_pools or {}).items():
        n = pool["n"]
        if not n:
            continue
        # ONE OFFER PER CANDIDATE. This loop used to pick a single index --
        # `i = oks.index(True) if any(oks) else 0` -- and emit one offer for the whole
        # pool. Candidate 0 is almost always recruitable, which is why candidate_index
        # was 0 in all 349,934 rows of the previous corpus while n_candidates ranged
        # 1..6. The other candidates were fetched across the bus and dropped in python:
        # 671,186 of them. They are different game actions with different traits, ranks
        # and mounts, and the agent could never choose between them.
        agent_type = (_hero_subtype_types(campaign.get("faction")) or {}).get(sub)
        fielded = (campaign.get("hero_type_counts") or {}).get(agent_type or "", 0)

        def _at(field, i, default=None):
            col = pool[field]
            return col[i] if i < len(col) else default

        for i in range(n):
            tr = _at("traits", i, [])
            is_agent = bool(_at("agents", i) or False)
            if is_agent:
                ok = bool(_at("can", i) and agent_type)
                gate = (None if ok else
                        "agent_type_unknown" if not agent_type else
                        "cannot_recruit_character")
            else:
                ok = bool(_at("can", i))
                gate = None if ok else "cannot_recruit_character"
            # `is_agent` and `cand_rank` are RECORDED, not argued away. Both were
            # dropped on the reasoning that is_agent restates action_type and Rank reads
            # a constant 0.0 for a pool entry that is not yet a character. Both readings
            # may well be right -- and neither is a reason to delete the field. The
            # standing rule is that a field the game answered gets collected and coverage
            # decides whether it carries information; a value argued away in a comment is
            # a value nobody can check. If they are constant, coverage will say so, and
            # that is a finding rather than an assumption.
            # `region` matters and was never recorded: _lord_execute_inner opens the
            # settlement panel for the entity it was offered on, so raising this candidate
            # at region A and at region B put the character in different places. The pool
            # is faction-wide, so the same candidate was offered once per owned region --
            # 159,336 rows over 103,606 distinct (decision, key) pairs, a 1.54x split --
            # and the two rows were byte-identical in action_key and params. They are
            # different actions; now they say so.
            offers.append(_offer("recruit_hero" if is_agent else "recruit_lord",
                                 "%s@%d" % (sub, i), ok, gate,
                                 region=region, candidate_index=i, n_candidates=n,
                                 traits=tr, trait=(tr[0] if tr else None),
                                 n_traits=(None if tr is None else len(tr)),
                                 bg_skill=_at("bg_skills", i), cqi=_at("cqis", i),
                                 unit_key=_at("units", i), is_agent=is_agent,
                                 cand_rank=_at("ranks", i),
                                 cand_subtype=_at("subtypes", i),
                                 agent_type=agent_type, type_fielded=fielded))
    offers.extend(_slot_action_offers(region, slot_states))
    offers.append(_offer("noop", "noop", True))
    return offers


def _campaign_options(campaign, world):
    offers = []
    current = (campaign or {}).get("current_research")
    points = (campaign or {}).get("research_points")
    for row in (campaign or {}).get("tech") or ():
        key, done, can = row.get("key"), bool(row.get("researched")), bool(row.get("can_research"))
        gate = None if can else ("researched" if done else
                                 "in_progress" if key == current else "prerequisites_not_met")
        if current and can:
            can, gate = False, "already_researching"
        offers.append(_offer("research", key, can, gate, in_progress=(key == current),
                             cost=row.get("cost"), points_available=points,
                             current_research=current))
    for r in (campaign or {}).get("rites") or ():
        # The real ritual key is the identity; the index is only the execution handle.
        # An empty key means the CCO read failed -- recorded as None so a coverage check
        # can see it, never silently backfilled with the ordinal.
        key = r.get("key")
        reason = r.get("invalid_reason")
        ok = bool(r.get("can_perform"))
        idx = r.get("index")
        offers.append(_offer("rites", key or ("rite_index_%s" % idx), ok,
                             None if ok else (reason or "cannot_perform"),
                             rite_index=idx, ritual_key=key, invalid_reason=reason))
    offers.extend(_diplo_options((world or {}).get("relations")))
    offers.append(_offer("end_turn", "end_turn", True))
    offers.append(_offer("noop", "noop", True))
    return offers


DIPLO_TERMS = ("nonaggression_pact", "trade_agreement", "defensive_alliance", "soft_access",
               "military_alliance", "vassal", "confederation")


DIPLO_DECLARE_WAR = "declare_war"


DIPLO_PEACE = "peace"


DIPLO_GIFT_TIERS = ("small", "medium", "large")


def _diplo_options(targets):
    """Diplomacy candidates from the recorded relations.

    `None` means the read FAILED and `[]` means the world really is empty -- the two are
    different facts and the distinction survives the database as a JSON null.
    """
    if targets is None:
        sys.stderr.write("collect: DIPLOMACY TARGET READ FAILED -- no diplomacy will be offered this "
                         "snapshot. This is a broken read, NOT an empty world.\n")
        return []
    if not targets:
        sys.stderr.write("collect: diplomacy targets EMPTY from factions_met -- 0 diplomacy offers "
                         "this snapshot.\n")
        return []
    targets = [t for t in targets if not t.get("excluded")]
    if not targets:
        return []
    targets.sort(key=lambda t: -abs(t["standing"]))
    offers = []
    for t in targets:
        f = t["faction"]
        rel = {"standing": t["standing"], "at_war": t["at_war"], "allied": t["allied"],
               "trade": t["trade"], "their_vassal": t["their_vassal"]}
        at_war = bool(t["at_war"])
        offers.append(_offer("diplomacy", "%s:%s" % (f, DIPLO_DECLARE_WAR), not at_war,
                             "already_at_war" if at_war else None,
                             faction=f, terms=[DIPLO_DECLARE_WAR], **rel))
        offers.append(_offer("diplomacy", "%s:%s" % (f, DIPLO_PEACE), at_war,
                             None if at_war else "not_at_war",
                             faction=f, terms=[DIPLO_PEACE], **rel))
        # A treaty you already hold cannot be proposed again. The launcher refused these
        # at click time as already_allied / already_trading / already_in_vassalage, using
        # world.relations -- which is in the snapshot, so the refusal belongs here.
        # our_master is the direction that was missing: `their_vassal` says they serve
        # us, and proposing vassalage while WE serve THEM is equally impossible.
        vassalage = t.get("their_vassal") or t.get("our_master")
        HELD = {"military_alliance": t.get("allied"), "trade_agreement": t.get("trade"),
                "vassal": vassalage, "confederation": vassalage}
        for a in DIPLO_TERMS:
            held = bool(HELD.get(a))
            ok = (not at_war) and not held
            gate = ("at_war_offers_only_peace" if at_war else
                    "already_holds_%s" % a if held else None)
            offers.append(_offer("diplomacy", "%s:%s" % (f, a), ok, gate,
                                 faction=f, terms=[a], **rel))
        for tier in DIPLO_GIFT_TIERS:
            offers.append(_offer("diplomacy", "%s:gift_%s" % (f, tier), not at_war,
                                 "at_war_offers_only_peace" if at_war else None,
                                 faction=f, terms=[], gift=tier, **rel))
    return offers


# ---------------------------------------------------------------------------- generate

def generate(record):
    """The whole move universe for one recorded decision, ungated.

    Pure: `record` is what the recorder stored, and nothing here reads the bus, the
    database or a file. Emission order is entity order then within-entity order, so the
    stored option sequence is a function of the snapshot rather than of whatever the
    generator happened to do first.
    """
    world = record.get("world") or {}
    campaign = record.get("campaign") or {}
    for e in record.get("entities") or ():
        ck, cid = e.get("context_kind"), str(e.get("context_id"))
        st = e.get("state") or {}
        if ck == "lord":
            offers = _lord_options(cid, st, world, campaign)
        elif ck == "hero":
            offers = _hero_options(cid, st, world, campaign)
        elif ck == "province":
            offers = _province_options(cid, st, campaign)
        elif ck == "campaign":
            offers = _campaign_options(st, world)
        else:
            raise CollectError("options.generate: unknown context_kind %r -- refusing to "
                               "silently skip an entity the recorder stored" % ck)
        for o in offers:
            # The gate needs the acting entity's state -- action points, for one -- and
            # only generate() knows which entity an offer came from. Carried under a
            # private key and dropped before the option is handed to the recorder.
            o["_state"] = st
            yield ck, cid, o


# -------------------------------------------------------------------------------- gate

FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})
MAX_ACTIONS_PER_ENTITY = 6
END_TURN_AFTER = 5

FACTION_WIDE_CAPS = frozenset(("recruit_lord", "recruit_hero", "research", "rites",
                               "building_dismantle"))
# A cap of 0 is how a type is DELIBERATELY DISABLED: the gate refuses at `count >= cap`
# and the count starts at 0, so the type is generated, gated every time, and never
# stored. noop and building_dismantle are both switched off this way on purpose. It is
# stated here rather than by deleting the type, so the generator still enumerates it and
# turning it back on is one number.
PER_TURN_CAPS = {"recruit_lord": 1, "recruit_hero": 1, "recruit_unit": 4, "edict": 1,
                 "research": 1, "rites": 1, "diplomacy": 3, "noop": 0, "stance": 1,
                 "hero_action": 3, "building_dismantle": 0, "raise_dead": 4,
                 "recruit_ror": 1, "recruit_blessed": 4, "recruit_imperial": 1}
# Everything that moves the character across the map. The executor was refusing these
# with `no_action_points` -- a gate the ADVISOR should have applied, because the snapshot
# already carries ap_remaining, ap_per_turn and ap_pct on every lord and hero. Offering a
# move to a lord with no movement left is not collecting a refusal, it is spending a
# decision to be told something the state already said.
COSTS_MOVEMENT = frozenset(("move", "attack_army", "attack_settlement", "colonize",
                            "garrison", "leave_garrison", "hero_action"))

# A force holds 20 units. The launcher refused a 21st with `army_full`, which is a fact
# about the game the snapshot already carries as state.units -- so it is decided here.
# The value matches launcher/click_actions.MAX_FORCE_UNITS; it is game data, not policy.
MAX_FORCE_UNITS = 20
FILLS_THE_ARMY = frozenset(("recruit_unit", "raise_dead", "recruit_ror",
                            "recruit_blessed", "recruit_imperial"))

ATTACK_PAIR_TYPES = frozenset(("attack_army", "attack_settlement"))
ATTACK_PAIR_CAP = 1
DIPLO_TARGET_CAP = 1
DIPLO_GIFT_CAP = 0


def _is_gift(key):
    return str(key).split(":", 1)[-1].startswith("gift_")


def _pair_key(context_kind, context_id, action_type, key):
    if action_type == "diplomacy":
        return ("diplomacy", str(key).split(":", 1)[0])
    return (context_kind, str(context_id), action_type, str(key))


def _cap_key(context_kind, context_id, action_type):
    if action_type in FACTION_WIDE_CAPS:
        return ("faction", "*", action_type)
    return (context_kind, str(context_id), action_type)


class Gate:
    """The only thing that decides whether a candidate survives.

    It holds the intra-turn state -- what has been taken, what failed, which entities are
    spent -- so it is reset every turn and updated after every result. That is why
    generation runs per decision and not once per turn: the answer changes as the turn
    goes on.
    """

    def __init__(self, max_actions_per_entity=MAX_ACTIONS_PER_ENTITY):
        self.max_actions_per_entity = max_actions_per_entity
        self.retired = set()
        self.blacklist = set()
        self.failed_types = set()
        self.entity_actions = {}
        self.type_actions = {}
        self.pair_actions = {}
        self.gift_actions = 0
        self.last_drops = []

    def new_turn(self):
        self.retired.clear()
        self.blacklist.clear()
        self.failed_types.clear()
        self.entity_actions.clear()
        self.type_actions.clear()
        self.pair_actions.clear()
        self.gift_actions = 0

    def retire(self, context_kind, context_id):
        self.retired.add((context_kind, str(context_id)))

    def note_result(self, pick, counted):
        k = (pick["context_kind"], str(pick["context_id"]))
        tk = _cap_key(k[0], k[1], pick["action_type"])
        self.type_actions[tk] = self.type_actions.get(tk, 0) + 1
        if pick["action_type"] in ATTACK_PAIR_TYPES or pick["action_type"] == "diplomacy":
            pk = _pair_key(k[0], k[1], pick["action_type"], pick["key"])
            self.pair_actions[pk] = self.pair_actions.get(pk, 0) + 1
        if pick["action_type"] == "diplomacy" and _is_gift(pick["key"]):
            self.gift_actions += 1
        if counted:
            n = self.entity_actions.get(k, 0) + 1
            self.entity_actions[k] = n
            if n >= self.max_actions_per_entity:
                self.retired.add(k)
        elif pick["action_type"] != "end_turn":
            self.blacklist.add((k[0], k[1], pick["action_type"], str(pick["key"])))
            self.failed_types.add(pick["action_type"])

    @staticmethod
    def _no_movement_left(state, at):
        """True when this action needs movement and the entity has none left."""
        if at not in COSTS_MOVEMENT:
            return False
        rem = (state or {}).get("ap_remaining")
        if rem is None:
            return False          # unread is not "zero" -- say nothing rather than guess
        try:
            return float(rem) <= 0.0
        except (TypeError, ValueError):
            return False

    def reason(self, ck, cid, o, actions_taken):
        """Why this candidate cannot be taken right now, or None if it can.

        The first branch is the generator's own verdict -- cannot_reach, no_points,
        already_equipped. The rest are the turn-level rules. They used to live in two
        different components, and there was never a principled line between them.
        """
        at, key = o.get("action_type"), str(o.get("key"))
        k = (ck, str(cid))
        if not o.get("available"):
            return o.get("gate") or "no_gate_recorded"
        if self._no_movement_left(o.get("_state"), at):
            return "no_action_points"
        # Walking away breaks the siege, so the launcher refused it. That was the one
        # launcher check with no advisor equivalent, which is why it moves here rather
        # than simply being deleted -- `besieging` is on every lord snapshot.
        if at == "move" and (o.get("_state") or {}).get("besieging"):
            return "besieging"
        if at in FILLS_THE_ARMY:
            units = (o.get("_state") or {}).get("units")
            # unread is not "full" -- an absent count says nothing, so it says nothing
            if units is not None and float(units) >= MAX_FORCE_UNITS:
                return "army_full"
        if at == "end_turn" and actions_taken < END_TURN_AFTER:
            return "end_turn_before_6th_decision"
        if key in FORBIDDEN_KEYS:
            return "forbidden_key"
        if k in self.retired and at != "end_turn":
            return "entity_retired"
        if at in self.failed_types:
            return "type_failed_this_turn"
        if (k[0], k[1], at, key) in self.blacklist:
            return "blacklisted_this_turn"
        if at in ATTACK_PAIR_TYPES and self.pair_actions.get(
                _pair_key(k[0], k[1], at, key), 0) >= ATTACK_PAIR_CAP:
            return "attack_pair_cap:%d" % ATTACK_PAIR_CAP
        if at == "diplomacy" and _is_gift(key) and self.gift_actions >= DIPLO_GIFT_CAP:
            return "diplo_gift_cap:%d" % DIPLO_GIFT_CAP
        if at == "diplomacy" and self.pair_actions.get(
                _pair_key(k[0], k[1], at, key), 0) >= DIPLO_TARGET_CAP:
            return "diplo_target_cap:%d" % DIPLO_TARGET_CAP
        cap = PER_TURN_CAPS.get(at)
        if cap is not None and self.type_actions.get(_cap_key(k[0], k[1], at), 0) >= cap:
            return "per_turn_cap:%d" % cap
        return None

    def apply(self, record, actions_taken=0):
        """Generate and gate in one pass. Returns the survivors, in emission order.

        A gated candidate is counted and then dropped. It is never returned, never
        stored, never scored and never a graph node -- an option the agent could not have
        taken is not part of the decision it faced.
        """
        self.last_drops = []
        out = []
        for ck, cid, o in generate(record):
            row = {"context_kind": ck, "context_id": cid,
                   "action_type": o.get("action_type"), "key": o.get("key"),
                   "params": o.get("params") or {}}          # note: no _state
            why = self.reason(ck, cid, o, actions_taken)
            if why is None:
                out.append(row)
            else:
                self.last_drops.append(dict(row, reason=why))
        return out


def attach(record, options):
    """Put the surviving options back on the record, so everything downstream -- features,
    the graph builder, the strategies -- reads the same list the store holds."""
    by_ent = {}
    for o in options:
        by_ent.setdefault((o["context_kind"], str(o["context_id"])), []).append(o)
    for e in record.get("entities") or ():
        e["offers"] = [{"action_type": o["action_type"], "key": o["key"],
                        "params": o.get("params") or {}}
                       for o in by_ent.get((e.get("context_kind"),
                                            str(e.get("context_id"))), ())]
    return record
