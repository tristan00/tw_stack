from __future__ import annotations


import hashlib
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

SCHEMA_VERSION = 8

INSTANCE_TYPES = ("faction", "region", "settlement", "province", "slot",
                  "lord", "hero", "action", "cgroup", "screen")
CATALOGUE_TYPES = ("building", "chain", "unit", "tech", "skill", "ritual",
                   "agent_action", "edict", "item", "race", "agent_subtype",
                   "screen_option", "dilemma", "screen_fact", "treaty_term")
NODE_TYPES = INSTANCE_TYPES + CATALOGUE_TYPES
ACTION_TYPE_INDEX = NODE_TYPES.index("action")

TYPE_FIELDS = {
    "faction":      ("is_player", "standing", "treasury", "income", "turn",
                     "campaign_map"),
    "region":       ("x", "y", "public_order", "income",
                     "is_capital", "is_ruin"),
    "settlement":   ("garrison_units", "x", "y"),
    "province":     ("corruption", "growth_per_turn", "free_slots", "max_slots",
                     "settlement_level", "can_set_edict"),
    "slot":         (),
    "lord":         ("x", "y", "units", "hp", "rank", "ap_pct",
                     "acted", "is_leader", "in_own_territory",
                     "skill_points", "pending_recruits", "recruits_this_turn",
                     "queue_age_max", "queue_new_this_turn", "turns_since_moved",
                     "queue_on_hold", "queue_stalled"),
    "hero":         ("x", "y", "rank", "ap_pct",
                     "acted", "is_leader", "in_own_territory",
                     "skill_points"),
    "action":       ("x", "y", "cost", "pool_avail",
                     "pb_choice_loc", "pb_since_loc", "pb_result_loc",
                     "pb_choice_reg", "pb_since_reg", "pb_result_reg",
                     "pb_same_lord", "queue_depth_after", "cancel_turns_left",
                     "cancel_stalled"),
    "cgroup":       (),
    "screen":       ("attitude", "amount_demanded", "amount_offered",
                     "strength_them", "strength_us", "settlements"),
    "building": ("create_cost", "level", "create_time"),
    "unit": ("recruitment_cost", "upkeep_cost", "num_men", "unit_tier", "create_time"),
    "skill": ("unlocked_at_rank", "is_background_skill"),
    "tech": ("tech_tier", "research_points_required"),
    "ritual": (),
    "chain": (), "agent_action": (), "edict": (), "item": (), "race": (),
    "agent_subtype": (),
    "screen_option": (), "dilemma": (), "screen_fact": (), "treaty_term": (),
}
N_SCALARS = sum(len(v) for v in TYPE_FIELDS.values())
MAX_FIELDS = max(max(len(v) for v in TYPE_FIELDS.values()), 1)
FIELD_POS = {t: {k: i for i, k in enumerate(v)} for t, v in TYPE_FIELDS.items()}

WORLD_RELATIONS = (
    "adj",
    "in_prov",
    "sett_of",
    "owns_region", "owns_sett", "owns_char",
    "of_race",
    "at_region",
    "at_sett",
    "besieging",
    "garrisons",
    "in_province",
    "near",
    "can_reach",
)

DIPLO_RELATIONS = ("dip_met", "dip_war", "dip_allied", "dip_trade", "dip_vassal",
                   "dip_nap", "dip_mil_ally", "dip_def_ally", "dip_mil_access")

PROVINCE_RELATIONS = (
    "has_slot",
    "slot_filled",
    "slot_locked",
    "slot_building",
    "of_chain",
    "has_edict",
)

CATALOGUE_RELATIONS = (
    "tech_requires",
    "researching",
    "unlocks",
    "queued",
    "in_army",
    "innate",
    "skill_active",
    "skill_rank_locked",
    "skill_inactive",
    "tech_researched",
    "tech_available",
    "tech_locked",
)

ABILITY_RELATIONS = ("hinder_settlement", "hinder_army", "hinder_agent",
                     "hinder_character", "hinder_province", "assist_army",
                     "assist_province", "command_force", "passive_ability")

ACT_RELATIONS = (
    "act_actor",
    "act_target",
    "act_subject",
    "act_slot",
    "act_on",
    "in_group",
    "of_ego",
    "near_target",
    "near_target_wide",
)

SCREEN_RELATIONS = (
    "on_screen",
    "of_dilemma",
    "screen_fact",
    "demanded",
    "offered",
    "in_treaty",
)

RELATIONS = (WORLD_RELATIONS + DIPLO_RELATIONS + PROVINCE_RELATIONS
             + CATALOGUE_RELATIONS + ABILITY_RELATIONS + ACT_RELATIONS
             + SCREEN_RELATIONS)
REL_INDEX = {r: i for i, r in enumerate(RELATIONS)}
N_RELATIONS = len(RELATIONS) * 2
REL_DIM = 24


N_FORWARD_RELATIONS = len(RELATIONS)


RACES = ("brt", "bst", "chd", "chs", "cst", "cth", "dae", "def", "dwf", "emp", "grn", "hef",
         "kho", "ksl", "lzd", "nor", "nur", "ogr", "skv", "sla", "tmb", "tze", "vmp", "wef")
RACE_VOCAB = len(RACES) + 1
RACE_DIM = 12

AGENT_TYPES = ("general", "colonel", "champion", "dignitary", "engineer", "minister",
               "runesmith", "spy", "wizard")
AGENT_VOCAB = len(AGENT_TYPES) + 1
AGENT_DIM = 8

STANCES = (
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_AMBUSH",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_ASSEMBLE_FLEET",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_ASTROMANCY",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_CHANNELING",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DISEMBARK",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DOUBLE_TIME",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_FIXED_CAMP",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_LAND_RAID",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MUSTER",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_PATROL",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_SEA_RAID",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_SETTLE",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_SET_CAMP",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_SET_CAMP_RAIDING",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_STALKING",
    "MILITARY_FORCE_ACTIVE_STANCE_TYPE_TUNNELING",
)
STANCE_BUCKETS = 32
STANCE_DIM = 8

SUBTYPE_BUCKETS = 2048
SUBTYPE_DIM = 16

SCREEN_TYPES = ("ally_attacked", "battle_results", "declare_war_cancel", "dilemma",
                "diplomacy", "diplomacy_notice", "diplomacy_proposal", "event_ack",
                "occupation", "pre_battle", "war_declared")

SCREEN_ACTION_TYPES = tuple("screen_%s" % s for s in SCREEN_TYPES)

ACTION_TYPES = ("stance", "building", "research", "skills", "items", "item_unequip", "rites",
                "recruit_unit", "recruit_lord", "edict", "attack_army", "attack_settlement",
                "colonize", "horde_building", "garrison", "leave_garrison", "end_turn", "noop",
                "move", "diplomacy", "hero_action", "recruit_hero",
                "building_repair", "building_dismantle",
                "raise_dead", "recruit_ror", "recruit_blessed",
                "recruit_imperial", "cancel_recruit") + SCREEN_ACTION_TYPES
ATYPE_VOCAB = len(ACTION_TYPES) + 1
ATYPE_DIM = 24


def screen_action_type(screen):
    s = str(screen or "")
    if s not in SCREEN_TYPES:
        raise ValueError(
            "mapgraph.schema: %r is not a known interrupt screen. Add it to SCREEN_TYPES "
            "-- an unknown screen would be encoded as atype 0 and the model could not tell "
            "which panel it was answering. Known: %s" % (s, ", ".join(SCREEN_TYPES)))
    return "screen_%s" % s

DIPLO_TERMS = ("declare_war", "peace", "trade_agreement", "nonaggression_pact",
               "soft_access", "defensive_alliance", "military_alliance", "vassal",
               "confederation", "gift_small", "gift_medium", "gift_large")
TERM_VOCAB = len(DIPLO_TERMS) + 1
TERM_DIM = 12

CAT_BUCKETS = {"building": 8192, "chain": 4096, "unit": 4096, "tech": 4096,
               "skill": 8192, "ritual": 2048, "agent_action": 256, "edict": 256,
               "item": 4096, "race": 32,
               "agent_subtype": 2048,
               "faction": 16384,
               "screen_option": 4096,
               "dilemma": 4096,
               "screen_fact": 2048,
               "treaty_term": 64}
CAT_DIM = 32

G_CTX_FIELDS = ("turn", "treasury", "income", "settlements", "armies",
                "allies", "vassals", "power_rank", "lord_level")
G_CTX_DIM = len(G_CTX_FIELDS)


KNN_K = 4
MODEL_DIR = common.MODEL_MAPGRAPH
MIN_ROWS = 40


_RACE_IX = {r: i + 1 for i, r in enumerate(RACES)}
_AGENT_IX = {t: i + 1 for i, t in enumerate(AGENT_TYPES)}
_ATYPE_IX = {t: i + 1 for i, t in enumerate(ACTION_TYPES)}
_TERM_IX = {t: i + 1 for i, t in enumerate(DIPLO_TERMS)}


def race_index(faction_key):
    parts = str(faction_key or "").split("_")
    race = parts[2] if len(parts) > 2 else None
    return _RACE_IX.get(race, 0)


def agent_index(agent_type):
    return _AGENT_IX.get(str(agent_type), 0)


_STANCE_IX = {s: i + 1 for i, s in enumerate(STANCES)}


def _above(s, lo, buckets):
    span = buckets - lo
    if span <= 0:
        raise ValueError("no room above the dense ids (lo=%d, buckets=%d)" % (lo, buckets))
    return lo + (zlib.crc32(s.encode()) % span)


def stance_index(stance):
    s = str(stance or "").strip()
    if not s or s == "none":
        return 0
    hit = _STANCE_IX.get(s)
    return hit if hit is not None else _above(s, len(STANCES) + 1, STANCE_BUCKETS)


def subtype_index(subtype):
    s = str(subtype or "").strip()
    if not s:
        return 0
    d = _dense("agent_subtype")
    hit = d.get(s)
    if hit is not None:
        if hit >= SUBTYPE_BUCKETS:
            raise ValueError("subtype_index: %d subtypes but SUBTYPE_BUCKETS is %d"
                             % (len(d), SUBTYPE_BUCKETS))
        return hit
    return _above(s, len(d) + 1, SUBTYPE_BUCKETS)


def atype_index(action_type):
    return _ATYPE_IX.get(str(action_type), 0)


def term_index(term):
    return _TERM_IX.get(str(term), 0)


_DENSE_CACHE = {}


def _dense(kind):
    if not _DENSE_CACHE:
        from advisor.mapgraph import catalogue as _cat
        _DENSE_CACHE.update(_cat.dense_ids())
        _DENSE_CACHE.setdefault("__loaded__", True)
    return _DENSE_CACHE.get(kind) or {}


def cat_index(kind, key):
    if not key:
        return 0
    n = CAT_BUCKETS[kind]
    d = _dense(kind)
    hit = d.get(key)
    if hit is not None:
        if hit >= n:
            raise ValueError(
                "cat_index: %s has %d keys but CAT_BUCKETS[%r] is %d -- raise the bucket "
                "count; silently wrapping would reintroduce collisions"
                % (kind, len(d), kind, n))
        return hit
    lo = len(d) + 1
    span = n - lo
    if span <= 0:
        raise ValueError("cat_index: no room above the dense ids for %r" % kind)
    return lo + (zlib.crc32(("%s\x00%s" % (kind, key)).encode()) % span)


CAT_OFFSET = {}
_off = 0
for _k in sorted(CAT_BUCKETS):
    CAT_OFFSET[_k] = _off
    _off += CAT_BUCKETS[_k]
CAT_VOCAB = _off


def cat_global(kind, key):
    return CAT_OFFSET[kind] + cat_index(kind, key)


def schema_hash():
    parts = [str(SCHEMA_VERSION)]
    for t in NODE_TYPES:
        parts.append(t + ":" + ",".join(TYPE_FIELDS[t]))
    parts += list(RELATIONS) + list(ACTION_TYPES) + list(RACES) + list(AGENT_TYPES)
    parts += list(DIPLO_TERMS) + list(G_CTX_FIELDS)
    parts += ["%s=%d" % (k, v) for k, v in sorted(CAT_BUCKETS.items())]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


if __name__ == "__main__":
    print("scalars: %d" % N_SCALARS)
    for t in NODE_TYPES:
        if TYPE_FIELDS[t]:
            print("   %-13s %s" % (t, ", ".join(TYPE_FIELDS[t])))
    print("node types: %d   relations: %d (x2 = %d)   catalogue vocab: %d"
          % (len(NODE_TYPES), len(RELATIONS), N_RELATIONS, CAT_VOCAB))
    print("schema_hash: %s" % schema_hash())
