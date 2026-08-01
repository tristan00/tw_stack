r"""Lookup API over reference.sqlite (offline game data) for the advisor featurizer.
Returns {} / None when the key is absent so callers can featurize uniformly.
"""
import os
import re
import sqlite3

DB = r"D:\twdata\reference\reference.sqlite"

_con = None


def _connect():
    global _con
    if _con is None:
        # check_same_thread=False: read-only reference DB, shared across the UI server's threads
        _con = sqlite3.connect(DB, check_same_thread=False)
        _con.row_factory = sqlite3.Row
    return _con


def _lookup(table, key_col, key):
    row = _connect().execute(
        "SELECT * FROM %s WHERE %s=?" % (table, key_col), (key,)
    ).fetchone()
    return dict(row) if row is not None else {}


def building_features(key):
    """building level key -> create_cost, create_time, upkeep_cost, food_cost, dev_point_cost,
    building_chain, level, building_instance_key."""
    return _lookup("buildings", "key", key)


def building_chain_features(key):
    """building chain key -> superchain, chain_category, sort_order (the chain graph)."""
    return _lookup("building_chains", "key", key)


def tech_features(key):
    """tech key -> research_points_required, cost_per_round, tier, required_parents, food_cost,
    node_set, building_level (unlock link), is_civil/is_engineering/is_military/is_hidden."""
    return _lookup("tech", "key", key)


def unit_features(key):
    """unit key (main_units.unit) -> recruitment_cost, upkeep_cost, create_time, food_cost,
    multiplayer_cost, caste, category, class, tier, num_men, is_naval, ui_unit_group_land."""
    return _lookup("units", "key", key)


def skill_features(key):
    """skill key -> unlocked_at_rank, influence_cost, is_background_skill, background_weighting."""
    return _lookup("skills", "key", key)


def ritual_features(key):
    """ritual key -> category, cast_time, cooldown_time, slave_cost, influence_cost,
    required_resources, expended_resources."""
    return _lookup("rituals", "key", key)


def label(key):
    """Localised name for a record key via the loc table (or None)."""
    row = _connect().execute("SELECT text FROM loc WHERE key=?", (key,)).fetchone()
    return row["text"] if row else None


_EDICT_PREFIX = "provincial_initiative_records_localised_name_"
_EDICT_RE = re.compile(r"_edict_([a-z]+)_")
_edict_cache = None


def edict_options(race_tokens):
    """[(edict_key, label)] of every provincial-initiative EDICT matching a race token; a slight
    superset, since the DB rows are not LL/tech-gated."""
    global _edict_cache
    if _edict_cache is None:
        _edict_cache = []
        for row in _connect().execute("SELECT key,text FROM loc WHERE key LIKE ?",
                                      (_EDICT_PREFIX + "%",)):
            ek = row["key"][len(_EDICT_PREFIX):]
            m = _EDICT_RE.search(ek)
            _edict_cache.append((m.group(1) if m else None, ek, row["text"]))
    toks = set(race_tokens or ())
    return [(ek, txt) for tok, ek, txt in _edict_cache if tok in toks]


_BUILD_NAME_PREFIX = "building_chains_encyclopedia_name_"
_BUILD_VARIANT_PREFIX = "building_culture_variants_name_"
_BUILD_TIP_PREFIX = "building_chains_chain_tooltip_"
_BUILD_RE = re.compile(r"^wh\d?_[a-z0-9]+_([a-z]+)_")   # race token = key segment[2] (wh2_main_HEF_...)
_building_cache = None


def building_label(chain):
    """Display name of a building CHAIN via loc: encyclopedia name, then variant, then chain tooltip,
    skipping empty / `placeholder` / `{{tr:...}}` rows."""
    con = _connect()
    for pfx in (_BUILD_NAME_PREFIX, _BUILD_VARIANT_PREFIX, _BUILD_TIP_PREFIX):
        row = con.execute("SELECT text FROM loc WHERE key=?", (pfx + chain,)).fetchone()
        txt = row["text"] if row else None
        if txt and txt != "placeholder" and "{{tr:" not in txt:
            return txt
    return None


def building_options(race_tokens):
    """[(building_key, label)]: one card (the lowest `level`) per building CHAIN matching a race token;
    a superset, since the DB rows are not slot/climate/resource/tech/LL-gated."""
    global _building_cache
    if _building_cache is None:
        by_chain = {}          # chain -> (key, level, race_tok)
        for row in _connect().execute("SELECT key,building_chain,level FROM buildings"):
            key, chain = row["key"], row["building_chain"]
            if not key or not chain:
                continue
            lvl = row["level"] if row["level"] is not None else 0
            m = _BUILD_RE.match(key)
            tok = m.group(1) if m else None
            cur = by_chain.get(chain)
            if cur is None or lvl < cur[1]:
                by_chain[chain] = (key, lvl, tok)
        _building_cache = [(key, tok, chain) for chain, (key, lvl, tok) in by_chain.items()]
    toks = set(race_tokens or ())
    return [(key, building_label(chain) or chain) for key, tok, chain in _building_cache if tok in toks]


def occupation_label(card_id):
    """On-screen NAME of a settlement-occupation option, from its numeric card id (or None)."""
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("culture_settlement_occupation_options_tooltip_%s" % card_id,)).fetchone()
    if not row or not row["text"]:
        return None
    return row["text"].split("||", 1)[0] or None


def occupation_desc(card_id):
    """Description of an occupation option (the part AFTER '||' in the tooltip loc), or None."""
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("culture_settlement_occupation_options_tooltip_%s" % card_id,)).fetchone()
    if not row or not row["text"] or "||" not in row["text"]:
        return None
    return row["text"].split("||", 1)[1] or None


def captive_label(record_key):
    """Faction-correct display NAME of a post-battle captive option, from its record key (or None)."""
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("campaign_post_battle_captive_options_onscreen_name_%s" % record_key,)).fetchone()
    return row["text"] if row and row["text"] else None


_CAPTIVE_BUTTONS = ("kill", "enslave", "release")


def captive_options(culture=None, faction=None, subculture=None):
    """{button: {"record_key", "name"}} for kill/enslave/release, resolved faction, then culture,
    then subculture -- the first entity with a binding for that button wins."""
    con = _connect()
    out = {}
    for button in _CAPTIVE_BUTTONS:
        for etype, ekey in (("faction", faction), ("culture", culture), ("subculture", subculture)):
            if not ekey:
                continue
            row = con.execute(
                "SELECT b.record_key, o.onscreen_name FROM captive_binding b "
                "JOIN captive_options o ON o.record_key = b.record_key "
                "WHERE b.entity_type=? AND b.entity_key=? AND b.button=?",
                (etype, ekey, button)).fetchone()
            if row and row["onscreen_name"]:
                out[button] = {"record_key": row["record_key"], "name": row["onscreen_name"]}
                break
    return out


if __name__ == "__main__":
    print("marble_1 :", building_features("wh2_main_hef_resource_marble_1"))
    print("tech_hef  :", tech_features("wh2_main_tech_hef_0_00"))
    print("spearmen :", unit_features("wh2_main_hef_inf_spearmen_0"))
    print("skill    :", skill_features("wh2_dlc04_skill_vmp_lord_unique_helman_ghorst_battle_1"))
    print("ritual   :", ritual_features("wh2_dlc09_ritual_crafting_tmb_arcane_item_blue_khepra"))
