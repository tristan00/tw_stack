from __future__ import annotations

import hashlib
import zlib

SCHEMA_VERSION = 1

NODE_TYPES = ("region", "character", "faction")

RACES = ("brt", "bst", "chd", "chs", "cst", "cth", "dae", "def", "dwf", "emp", "grn", "hef",
         "kho", "ksl", "lzd", "nor", "nur", "ogr", "skv", "sla", "tmb", "tze", "vmp", "wef")
RACE_VOCAB = len(RACES) + 1
RACE_DIM = 8

STANCE_BUCKETS = 24
STANCE_DIM = 4

AGENT_TYPES = ("general", "champion", "dignitary", "engineer", "runesmith", "spy", "wizard")

ACTION_TYPES = ("stance", "building", "research", "skills", "items", "item_unequip", "rites",
                "recruit_unit", "recruit_lord", "edict", "attack_army", "attack_settlement",
                "colonize", "horde_building", "garrison", "leave_garrison", "end_turn", "noop",
                "move", "diplomacy", "hero_action", "recruit_hero",
                "building_repair", "building_cancel", "building_dismantle",
                "raise_dead", "recruit_ror", "recruit_blessed", "recruit_imperial")
ATYPE_VOCAB = len(ACTION_TYPES) + 1
ATYPE_DIM = 16

KEY_BUCKETS = 256
KEY_DIM = 12

DIST_SCALE = 100.0
COORD_SCALE = 1024.0
COORD_MAX = 4096.0
UNITS_SCALE = 20.0
HP_SCALE = 20.0

GIFT_RANK = {"small": 1, "medium": 2, "large": 3}

NODE_TYPE_FIELDS = tuple("type_" + t for t in NODE_TYPES)
GEO_FIELDS = ("geo_x", "geo_y", "has_pos")
REGION_FIELDS = ("own_owned", "owner_known", "owner_met", "owner_at_war", "owner_allied",
                 "owner_trade", "owner_their_vassal", "owner_standing", "abandoned", "is_ruin",
                 "is_prov_capital", "garrison_known", "garrison_units", "degree",
                 "snap_present", "settlement_level", "buildings", "free_slots", "locked_slots",
                 "public_order", "corr_total", "corr_max", "is_capital", "can_set_edict",
                 "has_active_edict", "building_now_n")
CHARACTER_FIELDS = ("is_own", "alleg_enemy", "alleg_neutral", "has_army", "is_general",
                    "is_leader", "units", "units_known", "hp", "hp_known", "ap_pct", "rank",
                    "garrisoned", "besieging", "acted", "pending_recruits", "skill_points",
                    "in_own_territory", "snap_present", "is_garrison") \
    + tuple("agent_" + t for t in AGENT_TYPES) + ("agent_other",)
FACTION_FIELDS = ("is_player", "met", "excluded", "at_war", "allied", "trade", "their_vassal",
                  "standing", "n_regions_known", "n_chars_visible", "units_visible")

NODE_FIELDS = NODE_TYPE_FIELDS + GEO_FIELDS + REGION_FIELDS + CHARACTER_FIELDS + FACTION_FIELDS
NODE_DIM = len(NODE_FIELDS)

EDGE_TYPES = ("adj", "at", "own_r", "own_c", "dip", "near")
EDGE_FIELDS = tuple("etype_" + t for t in EDGE_TYPES) + (
    "rev", "geo_known", "dist", "dir_sin", "dir_cos", "same_province",
    "dip_at_war", "dip_allied", "dip_trade", "dip_standing")
EDGE_DIM = len(EDGE_FIELDS)

G_CTX_FIELDS = ("turn", "treasury", "income", "settlements", "armies", "characters", "heroes",
                "allies", "vassals", "power_rank", "lord_level", "act_index", "n_offers",
                "is_researching")
G_CTX_DIM = len(G_CTX_FIELDS)

OFFER_FIELDS = ("available", "target_found", "target_miss", "dist", "dir_sin", "dir_cos",
                "ray_0", "ray_1", "ray_2", "ray_3", "ray_4", "ray_5", "ray_6", "ray_7",
                "reach_max", "strength_ratio", "hp_ratio", "n_terms", "gift_rank", "standing",
                "chance", "active", "slot_index", "is_upgrade")
OFFER_DIM = len(OFFER_FIELDS)

MAX_REGIONS = 60
KNN_K = 4

MODEL_DIR = r"D:\twdata\models\gnn"
MIN_ROWS = 40


def race_index(faction_key):
    parts = str(faction_key or "").split("_")
    race = parts[2] if len(parts) > 2 else None
    try:
        return RACES.index(race) + 1
    except ValueError:
        return 0


def stance_index(stance):
    s = str(stance or "").strip()
    if not s or s == "none":
        return 0
    return (zlib.crc32(s.encode()) % (STANCE_BUCKETS - 1)) + 1


def atype_index(action_type):
    try:
        return ACTION_TYPES.index(str(action_type)) + 1
    except ValueError:
        return 0


def key_index(action_key):
    return zlib.crc32(str(action_key or "").encode()) % KEY_BUCKETS


def agent_type_onehot(agent_type):
    out = [0.0] * (len(AGENT_TYPES) + 1)
    t = str(agent_type or "")
    try:
        out[AGENT_TYPES.index(t)] = 1.0
    except ValueError:
        out[-1] = 1.0
    return out


def schema_hash():
    text = "|".join((str(SCHEMA_VERSION),) + NODE_FIELDS + EDGE_FIELDS + G_CTX_FIELDS
                    + OFFER_FIELDS + ACTION_TYPES + RACES
                    + (str(KEY_BUCKETS), str(STANCE_BUCKETS), str(KNN_K), str(MAX_REGIONS)))
    return hashlib.sha1(text.encode()).hexdigest()
