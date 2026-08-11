from __future__ import annotations

"""The graph schema: as few numbers as the job needs, and everything else is structure.

This is the only graph model in the tree. What it replaced was a 90-field flat node
vector plus a 24-slot per-offer block transcribed from CatBoost's `opt_*` columns;
that code is deleted, and the lessons from it are recorded here rather than in a
parallel package. Adversarial review of the first draft of this one found it was still
~76% CatBoost by feature -- the province block was `features.py:182-209` with the
`prov_` prefix removed.

N_SCALARS counts the raw scalars across the entire ontology and is computed, not written
down, so the number cannot drift from the code. Anything that can be a relation is a
relation, and anything that has a stable game key is a shared catalogue node -- that rule
is what keeps the count small, not a ceiling. What IS capped is MAX_FIELDS, the widest
single node type: every node carries a row that wide, so one greedy type taxes all of them.

    at_war / allied / trade / vassal    ->  distinct edge types
    built{slot: key} / free / locked    ->  slot nodes and building nodes
    pending_recruits (a count)          ->  edges to unit nodes
    hero_action ability / target_kind   ->  the edge type itself
    rite_index / slot_index / pool_index->  deleted (list positions, no semantics)

Catalogue nodes come from D:\\twdata\\reference\\reference.sqlite and every key joins:
357/357 buildings, 3214/3214 skills, 1766/1766 tech, 184/184 units, 97/97 edicts.
CatBoost uses two columns of that database. This is the part of the game the peer model
cannot see.

Nothing here is imported from advisor/features.py.
"""

import hashlib
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

# 4: the blocking screens joined the ontology -- a `screen` node, screen_* action types,
# and the option/dilemma/fact/term catalogues. Every embedding table that indexes those
# changed WIDTH, so a v3 encoder.pt is not loadable against this and rank.py's hash gate
# is what says so instead of a state_dict shape error at inference.
SCHEMA_VERSION = 4

# --------------------------------------------------------------------------
# node types
# --------------------------------------------------------------------------
# instance nodes  -- one per thing in this decision
# catalogue nodes -- one per stable game key, shared across all decisions
INSTANCE_TYPES = ("faction", "region", "settlement", "province", "slot",
                  "lord", "hero", "action", "cgroup", "screen")
CATALOGUE_TYPES = ("building", "chain", "unit", "tech", "skill", "ritual",
                   "agent_action", "edict", "item", "race", "agent_subtype",
                   # blocking screens: the option, the dilemma behind it, a named panel
                   # fact, a treaty term on the table
                   "screen_option", "dilemma", "screen_fact", "treaty_term")
NODE_TYPES = INSTANCE_TYPES + CATALOGUE_TYPES
ACTION_TYPE_INDEX = NODE_TYPES.index("action")

# ---- the raw scalars; N_SCALARS below is the computed total, MAX_FIELDS the widest ----
# public_order is read off the REGION interface (r:public_order()); corruption comes
# from the PROVINCE pooled-resource manager. They are not the same owner.
TYPE_FIELDS = {
    # standing is a RECORDED per-faction fact (world.relations[].standing), and it is here
    # because identity alone is not enough. Two met factions of the same race that own no
    # visible settlement and share their treaty flags were the same node to WL, so every
    # diplomacy action aimed at them was the same action -- measured at 6.9% of action
    # nodes on fresh data, all diplomacy. A catalogue id separates them unless two keys
    # hash together; standing means even then they differ.
    "faction":      ("is_player", "standing"),                         # 2
    # income is the region's own earnings. The only economy signals on a region were
    # public_order and, one hop away, a province-pooled corruption -- so a region
    # embedding could not tell the rich capital from a frontier holding earning 40, while
    # every action node hangs off a region through at_region / has_slot / sett_of.
    "region":       ("x", "y", "public_order", "income"),              # 4
    # x,y are free here too (MAX_FIELDS is 6) and both own and enemy settlement rows
    # carry them. Distance to a settlement is most of what an attack decision is about.
    "settlement":   ("garrison_units", "x", "y"),                      # 3
    "province":     ("corruption",),                                   # 1
    "slot":         (),
    "lord":         ("x", "y", "units", "hp", "rank", "ap_pct"),       # 6
    "hero":         ("x", "y", "rank", "ap_pct"),                      # 4
    # x,y are free: MAX_FIELDS is 6, set by `lord`, so an action row already carried five
    # unused zero columns and the encoder input width does not move. `move` recorded a
    # destination that nothing downstream could see -- the only thing distinguishing one
    # move candidate from another was its sample_index.
    # `available` was here while the store held gated rows. Every stored action is a
    # candidate now, so the field was a constant 1.0 -- an input column carrying no
    # information, which the network still had to learn to ignore.
    "action":       ("x", "y"),                                        # 2
    "cgroup":       (),
    # One per INTERRUPT decision -- the blocking screen itself. These six are the panel's
    # own numbers, read off the open panel as a single entity, and they are the same six
    # the CatBoost interrupt model already reads as isc_dip_* columns
    # (interrupt_model.py:57-77). They are numbers, so they are scalars; the panel's
    # STRING facts (attitude_label, result, casualties, outcome, reliability, race) are
    # screen_fact catalogue nodes instead.
    # Exactly 6, which is MAX_FIELDS as set by `lord` -- so this type is free: it widens
    # nothing. A 7th would tax every node of every type in every graph.
    "screen":       ("attitude", "amount_demanded", "amount_offered",
                     "strength_them", "strength_us", "settlements"),   # 6
    # catalogue nodes are pure identity -- their content is their embedding
    "building": (), "chain": (), "unit": (), "tech": (), "skill": (),
    "ritual": (), "agent_action": (), "edict": (), "item": (), "race": (),
    "agent_subtype": (),
    "screen_option": (), "dilemma": (), "screen_fact": (), "treaty_term": (),
}
# The file said 22 in its docstring, 19 in this comment, and computed 20 -- three numbers
# for one quantity, none of them checked. So it is computed, and it is REPORTED (meta.json
# and the models card), which is the part that was actually load-bearing.
#
# There was a SCALAR_BUDGET = 22 ceiling here that raised on the 23rd field. It is gone.
# It had reached exactly 22 of 22 -- `region.income` spent the last slot -- so the next
# honest field anywhere in the ontology was an ImportError, and the thing it was
# protecting was never the count. Adding a 7th field to any ONE type is what costs:
# `x` is [N, MAX_FIELDS] for every node and TypeEncoders projects _NT * MAX_FIELDS, so a
# single wide type widens the input row of every node of every type in every graph.
# MAX_FIELDS is the real budget, it is asserted in test_build.py, and it is unchanged.
N_SCALARS = sum(len(v) for v in TYPE_FIELDS.values())
MAX_FIELDS = max(max(len(v) for v in TYPE_FIELDS.values()), 1)
# {node type: {field name: column}} -- the same information as TYPE_FIELDS, indexed the
# way build.Graph.add needs it. Building a graph writes ~1000 field values, and
# TYPE_FIELDS[t].index(k) is a linear scan for every one of them.
FIELD_POS = {t: {k: i for i, k in enumerate(v)} for t, v in TYPE_FIELDS.items()}

# --------------------------------------------------------------------------
# relations
# --------------------------------------------------------------------------
WORLD_RELATIONS = (
    "adj",              # region <-> region -- runtime only, and shroud-clipped:
                        # no adjacency table exists in db.pack (all 1521 scanned).
                        # mean degree 3.11, 7% of regions isolated.
    "in_prov",          # region <-> province
    "sett_of",          # region <-> settlement
    "owns_region", "owns_sett", "owns_char",
    "of_race",          # faction <-> race
    "at_region",        # char <-> region
    "at_sett",          # char <-> settlement   (was the `garrisoned` flag)
    "besieging",        # char <-> settlement   (was the `besieging` flag)
    "garrisons",        # char <-> settlement -- an enemy armed-citizenry stack and the
                        # settlement it defends. MOBILE_KINDS admits it as an ordinary
                        # `lord` node and the citizenry skip filters only OUR own, so the
                        # defenders were built floating free of their walls; coincident
                        # x,y was the only thing tying them together.
    "in_province",      # char <-> province     (province-wide lord modifiers)
    "near",             # char <-> char
)

DIPLO_RELATIONS = ("dip_met", "dip_war", "dip_allied", "dip_trade", "dip_vassal",
                   "dip_nap", "dip_mil_ally", "dip_def_ally", "dip_mil_access")

PROVINCE_RELATIONS = (
    "has_slot",         # province <-> slot
    "slot_filled",      # slot <-> building
    "slot_locked",      # slot (locked_slots list becomes a relation)
    "slot_building",    # slot <-> building under construction
    "of_chain",         # building <-> chain
)

CATALOGUE_RELATIONS = (
    "tech_requires",    # tech <-> tech -- the prerequisite DAG, 2056 nodes
    "researching",      # faction <-> tech
    "unlocks",          # skill <-> agent_action
    "queued",           # lord <-> unit  (pending_recruit_keys; was a count)
    # An army's actual composition, not what it has ordered. `units` was a bare COUNT for
    # the whole life of the corpus, so two 10-stacks were identical to the model whether one
    # was ten spearmen and the other ten dragons. As an EDGE per unit the roster is
    # structure the encoder can read, and it joins reference.sqlite for caste/tier/cost --
    # no new scalar, so the budget is untouched.
    "in_army",          # lord|hero <-> unit
    # Background/innate skills, which live in HiddenSkillList and are what a character IS
    # rather than what it has spent points on. SkillList (the assignable tree) reaches the
    # graph only as the target of a `skills` offer; nothing described the character itself.
    "innate",           # lord|hero <-> skill
)

# what the action DOES, as the edge type. agent_abilities is a 9-value closed
# vocabulary, so a hero action's semantics are structural, not a param.
ABILITY_RELATIONS = ("hinder_settlement", "hinder_army", "hinder_agent",
                     "hinder_character", "hinder_province", "assist_army",
                     "assist_province", "command_force", "passive_ability")

ACT_RELATIONS = (
    "act_actor",        # action <-> the entity performing it
    "act_target",       # action <-> the entity it is aimed at
    "act_subject",      # action <-> the faction it concerns
    "act_slot",         # action <-> the building slot it operates on
    "act_on",           # action <-> the catalogue node it instantiates
    "in_group",         # action <-> its candidate group
    "of_ego",           # candidate group <-> the actor it belongs to
)

# Blocking screens: dilemmas, pre-battle, battle results, occupation, diplomacy. The
# option is an action node like any other -- what is new is the screen it sits on.
SCREEN_RELATIONS = (
    "on_screen",        # action <-> the screen it is an option of
    "of_dilemma",       # action <-> the dilemma catalogue node (shared across occurrences,
                        # which is the whole point: the same dilemma recurs across
                        # campaigns and its options should mean the same thing each time)
    "screen_fact",      # screen <-> a string-valued panel fact (forecast, outcome,
                        # attitude label, reliability) as a catalogue node
    "demanded",         # screen <-> a treaty term THEY want from us
    "offered",          # screen <-> a treaty term they are giving
    "in_treaty",        # screen <-> a treaty already standing between us
)

RELATIONS = (WORLD_RELATIONS + DIPLO_RELATIONS + PROVINCE_RELATIONS
             + CATALOGUE_RELATIONS + ABILITY_RELATIONS + ACT_RELATIONS
             + SCREEN_RELATIONS)
REL_INDEX = {r: i for i, r in enumerate(RELATIONS)}
N_RELATIONS = len(RELATIONS) * 2        # forward and reverse are different relations
REL_DIM = 24


N_FORWARD_RELATIONS = len(RELATIONS)




# --------------------------------------------------------------------------
# identity vocabularies
# --------------------------------------------------------------------------
RACES = ("brt", "bst", "chd", "chs", "cst", "cth", "dae", "def", "dwf", "emp", "grn", "hef",
         "kho", "ksl", "lzd", "nor", "nur", "ogr", "skv", "sla", "tmb", "tze", "vmp", "wef")
RACE_VOCAB = len(RACES) + 1
RACE_DIM = 12

# 9 values, not the 7 carried before -- `colonel` and `minister` were silently missing
AGENT_TYPES = ("general", "colonel", "champion", "dignitary", "engineer", "minister",
               "runesmith", "spy", "wizard")
AGENT_VOCAB = len(AGENT_TYPES) + 1
AGENT_DIM = 8

# A closed enum, so it is written down rather than hashed. crc32 % 32 put 7 of the 19
# observed values (36.8%) on a shared row -- LAND_RAID and CHANNELING were both 19, which
# is why two stance offers stayed indistinguishable to WL even after the key was encoded.
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
STANCE_BUCKETS = 32             # 18 dense + room for a stance a future pack adds
STANCE_DIM = 8

# 565 subtypes in reference.agent_permitted_subtypes, which covers lords as well as
# agents. Dense from there; hashing put 50 of the 249 observed (20.1%) on a shared row.
SUBTYPE_BUCKETS = 2048
SUBTYPE_DIM = 16

# Every blocking screen the launcher can answer, taken from the `_choose` / `_sticky_choice`
# / `_drive_decision` call sites in launcher/interrupts.py. Closed set -- it is OUR code
# that decides a screen is answerable at all, so an unknown one here means the two files
# have drifted, which mapgraph.invariants cross-checks by AST rather than trusting this
# comment. The builder RAISES on a screen it does not know: a screen silently landing on
# atype 0 would be answered by a model that cannot tell which panel it is looking at.
SCREEN_TYPES = ("ally_attacked", "battle_results", "declare_war_cancel", "dilemma",
                "diplomacy", "diplomacy_notice", "diplomacy_proposal", "event_ack",
                "occupation", "pre_battle", "war_declared")

# An interrupt option is an action node like any other. The screen it belongs to is its
# action TYPE, prefixed -- `diplomacy` alone is already a map action type (a proposal we
# send), and the incoming panel is a different thing entirely.
SCREEN_ACTION_TYPES = tuple("screen_%s" % s for s in SCREEN_TYPES)

ACTION_TYPES = ("stance", "building", "research", "skills", "items", "item_unequip", "rites",
                "recruit_unit", "recruit_lord", "edict", "attack_army", "attack_settlement",
                "colonize", "horde_building", "garrison", "leave_garrison", "end_turn", "noop",
                "move", "diplomacy", "hero_action", "recruit_hero",
                "building_repair", "building_dismantle",
                "raise_dead", "recruit_ror", "recruit_blessed",
                "recruit_imperial") + SCREEN_ACTION_TYPES
ATYPE_VOCAB = len(ACTION_TYPES) + 1
ATYPE_DIM = 24


def screen_action_type(screen):
    """`screen_<name>`, raising on a screen this schema does not know."""
    s = str(screen or "")
    if s not in SCREEN_TYPES:
        raise ValueError(
            "mapgraph.schema: %r is not a known interrupt screen. Add it to SCREEN_TYPES "
            "-- an unknown screen would be encoded as atype 0 and the model could not tell "
            "which panel it was answering. Known: %s" % (s, ", ".join(SCREEN_TYPES)))
    return "screen_%s" % s

# 12 diplomacy terms observed in the corpus; replaces 3,624 hashed key buckets
DIPLO_TERMS = ("declare_war", "peace", "trade_agreement", "nonaggression_pact",
               "soft_access", "defensive_alliance", "military_alliance", "vassal",
               "confederation", "gift_small", "gift_medium", "gift_large")
TERM_VOCAB = len(DIPLO_TERMS) + 1
TERM_DIM = 12

# Catalogue nodes get real id-indexed embeddings, not a shared crc32. The predecessor hashed
# 136,369 distinct action keys into 256 buckets -- and 118,802 of those keys are `move`
# destinations, pure noise, which then collided with the ~17.5k semantic keys.
# `move` gets no key at all: its destination is x,y.
# Sized to hold every key in reference.sqlite as a DENSE id, plus room above it for keys
# the reference does not know. Reference counts at time of writing: buildings 5,259,
# skills 5,944, chains 1,943, units 2,609, tech 2,056, rituals 1,326, agent_actions 176.
# `chain` was 2,048 against 1,943 keys -- 105 spare, which is not headroom.
CAT_BUCKETS = {"building": 8192, "chain": 4096, "unit": 4096, "tech": 4096,
               "skill": 8192, "ritual": 2048, "agent_action": 256, "edict": 256,
               "item": 4096, "race": 32,
               # recruit_lord and recruit_hero linked to no catalogue node at all: their
               # key is a character SUBTYPE, and `unit` is the wrong vocabulary for it.
               # 565 distinct in reference.agent_permitted_subtypes.
               "agent_subtype": 2048,
               # A faction key is a stable game key, which by this file's own rule makes
               # it a catalogue node -- and it was the one instance type with no identity
               # at all. reference.sqlite has no faction table, so these hash instead of
               # being dense: 16384 rows against roughly 600 factions in Immortal Empires.
               "faction": 16384,
               # ---- interrupt screens ----
               # An option's identity, "<screen>|<dilemma_id>|<option_id>". 48 distinct in
               # the corpus at the time of writing (21 occupation, 15 dilemma, 6
               # battle_results, 4 pre_battle, 2 diplomacy) and it grows with every new
               # dilemma the game shows us, so it is sized for the tail, not for today.
               "screen_option": 4096,
               # The dilemma itself, shared by its own options and across occurrences --
               # the same dilemma recurs in campaign after campaign.
               "dilemma": 4096,
               # A string-valued panel fact: "pre_battle.result=<state>",
               # "diplomacy.attitude_label=<label>", "battle_results.outcome=<...>".
               "screen_fact": 2048,
               # DEAL_ITEMS and whatever else a diplomacy panel lists.
               "treaty_term": 64}
CAT_DIM = 32

# The VALUE head's context. These are raw recorded campaign facts, not derived ones, and
# the value head predicts a campaign outcome -- so it is allowed to see campaign state.
# They are counted separately from the node budget and REPORTED, because being uncounted
# is how ten extra scalars went unnoticed.
#
# log_n_offers is gone. It was math.log1p(n_offers)/7.0 -- a derived composite, and worse,
# a fact about OUR GENERATOR rather than about the game. A model that sees how many
# options it was handed can learn the option generator instead of the world, which is the
# specific failure this schema was rebuilt to end.
G_CTX_FIELDS = ("turn", "treasury", "income", "settlements", "armies",
                "allies", "vassals", "power_rank", "lord_level")
G_CTX_DIM = len(G_CTX_FIELDS)

# normalisation constants -- scaling by a constant is not feature engineering, but it
# has to be written down, because this schema feeds raw x,y (0..1024) next to ap and corruption
COORD_SCALE = 1024.0
UNITS_SCALE = 20.0
HP_SCALE = 20.0
RANK_SCALE = 10.0
ORDER_SCALE = 100.0
CORRUPT_SCALE = 100.0
TREASURY_SCALE = 10000.0
# per-region income. Observed 0..736 across the corpus; 500 puts the common range inside
# [0,1.5] and the clip catches the rest.
INCOME_SCALE = 500.0

# ---- interrupt panel numbers ----
# Diplomatic attitude runs roughly -100..100 on the panel's dy_value node.
ATTITUDE_SCALE = 100.0
# Gold on the table. Deals in the corpus run to a few thousand; 5000 puts the common
# range inside [0,1] and the clip catches a ransom.
DEAL_GOLD_SCALE = 5000.0
# "Strength Rank: 12" -- a rank, not a strength, so smaller is stronger. Same scale for
# both sides so the two are comparable AS NUMBERS on one node; the model is free to
# subtract them, which it may do because they are two facts of one panel, not two
# entities (guard.Raw is what would stop a cross-entity subtraction, and does not apply).
STRENGTH_RANK_SCALE = 50.0
# Their settlement count, off `opponent_settlement_number`.
SCREEN_SETTLEMENTS_SCALE = 10.0

KNN_K = 4
MODEL_DIR = common.MODEL_MAPGRAPH
REFERENCE_DB = common.REFERENCE_DB
MIN_ROWS = 40


# Vocabulary lookups as dicts. These are called once per node per graph and were
# tuple.index() linear scans -- 210k of them per 200 graphs. Same values, O(1).
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
    """Hash a key the enumeration does not know, into the range ABOVE the dense ids so it
    can never alias one that is known."""
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
    """Lazy, because catalogue imports this module. Empty dict if reference.sqlite is
    absent, which degrades to the old hashing and says so."""
    if not _DENSE_CACHE:
        try:
            from advisor.mapgraph import catalogue as _cat
        except ImportError:
            import catalogue as _cat
        _DENSE_CACHE.update(_cat.dense_ids())
        _DENSE_CACHE.setdefault("__loaded__", True)
    return _DENSE_CACHE.get(kind) or {}


def cat_index(kind, key):
    """Catalogue id. Namespaced per kind so a unit key cannot collide with a skill.

    Dense from reference.sqlite where the kind has a table there, because crc32 % buckets
    is NOT injective and never was: 9,393 of 19,313 reference keys (48.6%) shared a row
    with a different key -- 60.6% of chains, 51.1% of skills. Two different buildings
    landing on one embedding is not a hash detail, it is the model being told they are the
    same building.

    A key the reference does not know still hashes, but into the range ABOVE the dense
    ids, so an unknown key can never alias a known one. That range is small and shrinking;
    it is reported by mapgraph.invariants rather than left to be discovered.
    """
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
    """Single embedding table across all catalogue kinds, disjoint id ranges."""
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
