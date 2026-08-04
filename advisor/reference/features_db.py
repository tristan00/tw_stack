r"""Lookup API over reference.sqlite (offline game data) for the advisor featurizer.
Returns {} / None when the key is absent so callers can featurize uniformly.
"""
import os
import re
import sqlite3
import sys

_EMPTY_SUFFIX_WARNED = set()

DB = r"D:\twdata\reference\reference.sqlite"

_con = None


def _connect():
    global _con
    if _con is None:
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


_AGENT_ACTION_NAME_PREFIX = "agent_actions_localised_action_name_"
_AGENT_ACTION_DESC_PREFIX = "agent_actions_localised_action_description_"
_TR_RE = re.compile(r"^\{\{tr:(.+)\}\}$")
_TR_PREFIX = "ui_text_replacements_localised_text_"


def agent_action_label(action_key):
    """Display name of an agent action, following the loc `{{tr:...}}` indirection (or None).

    agent_actions_localised_action_name_<key> holds `{{tr:agent_action_name_scout_settlement}}`,
    which resolves through ui_text_replacements_localised_text_<...> to "Scout Ruins"."""
    txt = label(_AGENT_ACTION_NAME_PREFIX + str(action_key))
    if not txt:
        return None
    m = _TR_RE.match(txt.strip())
    if not m:
        return txt
    return label(_TR_PREFIX + m.group(1))


def agent_action_keys(name_suffix):
    """Every agent_action key whose loc name row ends in `name_suffix`, e.g.
    'hinder_settlement_scout_settlement' -> the champion/dignitary/.../wizard variants.

    Takes a single suffix or a sequence of them, matching agent_action_rows: HERO_ACTIONS carries
    `loc_suffix` as a LIST, and stringifying that list made every LIKE miss, so no hero action
    could resolve a method name and all 41 died before touching the game."""
    sufs = [name_suffix] if isinstance(name_suffix, str) else list(name_suffix or ())
    out = []
    con = _connect()
    for suf in sufs:
        hit = 0
        for row in con.execute("SELECT key FROM loc WHERE key LIKE ?",
                               (_AGENT_ACTION_NAME_PREFIX + "%" + str(suf),)):
            out.append(row["key"][len(_AGENT_ACTION_NAME_PREFIX):])
            hit += 1
        if not hit and str(suf) not in _EMPTY_SUFFIX_WARNED:
            _EMPTY_SUFFIX_WARNED.add(str(suf))
            sys.stderr.write(
                "features_db: agent_action_keys(%r) matched NOTHING in loc -- every hero action "
                "on this suffix dies at method_name before touching the game. Either the suffix "
                "is wrong or reference.sqlite was built without the agent-action rows.\n" % suf)
    return sorted(set(out))


def verify_hero_action_mappings(hero_actions):
    """{action: label|None} for a HERO_ACTIONS catalogue, logging the unresolved ones loudly.

    One call answers 'can the executor run any of these', instead of learning it one failed
    action at a time in a live campaign."""
    out, missing = {}, []
    for name, spec in sorted((hero_actions or {}).items()):
        label = None
        for k in agent_action_keys((spec or {}).get("loc_suffix")):
            label = agent_action_label(k)
            if label:
                break
        out[name] = label
        if not label:
            missing.append(name)
    if missing:
        sys.stderr.write("features_db: %d/%d hero actions have NO method-name mapping and cannot "
                         "execute: %s\n" % (len(missing), len(out), ", ".join(missing)))
    else:
        sys.stderr.write("features_db: all %d hero actions resolve a method name\n" % len(out))
    return out


_AGENT_ABILITIES = ("hinder_settlement", "hinder_army", "hinder_agent", "hinder_character",
                    "hinder_province", "assist_army", "assist_settlement", "assist_province")
_AGENT_KEY_RE = re.compile(
    r"agent_action_([a-z0-9]+)_(%s)_(.+)$" % "|".join(_AGENT_ABILITIES))
_matrix_cache = {}


def agent_action_rows(name_suffix):
    """[{key, agent, ability, attribute, chance}] for every agent_actions row ending in `suffix`.

    The agent_actions DB table is the authority: 176 rows carrying the real per-agent-type action
    set with its base success chance and attribute. The loc table is a pure existence list."""
    sufs = [name_suffix] if isinstance(name_suffix, str) else list(name_suffix or ())
    out = []
    con = _connect()
    for suf in sufs:
        for row in con.execute(
                "SELECT key,agent,ability,attribute,chance_of_success FROM agent_actions "
                "WHERE key LIKE ?", ("%" + str(suf),)):
            out.append({"key": row["key"], "agent": row["agent"], "ability": row["ability"],
                        "attribute": row["attribute"], "chance": row["chance_of_success"]})
    return out


_catalogue_cache = None


def agent_action_catalogue():
    """[{action, ability, category, types:{agent: key}, attribute:{}, chance:{}}] for every
    non-ambient agent action the game defines.

    Generated from agent_actions rather than hand-listed: the table is the authority on which
    agent types may perform what, and hand lists go stale against DLC."""
    global _catalogue_cache
    if _catalogue_cache is not None:
        return _catalogue_cache
    con = _connect()
    cats = {r["key"]: r["category"] for r in con.execute("SELECT key,category FROM agent_abilities")}
    out = {}
    for r in con.execute("SELECT key,agent,ability,attribute,chance_of_success FROM agent_actions"):
        ability = r["ability"]
        if cats.get(ability) == "ambient":
            continue
        pre = "agent_action_%s_%s_" % (r["agent"], ability)
        i = r["key"].find(pre)
        if i < 0:
            continue
        action = r["key"][i + len(pre):]
        e = out.setdefault((ability, action), {"action": action, "ability": ability,
                                               "category": cats.get(ability), "types": {},
                                               "attribute": {}, "chance": {}})
        e["types"][r["agent"]] = r["key"]
        e["attribute"][r["agent"]] = r["attribute"]
        e["chance"][r["agent"]] = r["chance_of_success"]
    _catalogue_cache = sorted(out.values(), key=lambda e: (e["ability"], e["action"]))
    return _catalogue_cache


def agent_ability_category(ability):
    """actions | embedded | ambient -- 'ambient' abilities are never right-click methods."""
    row = _connect().execute("SELECT category FROM agent_abilities WHERE key=?",
                             (str(ability),)).fetchone()
    return row["category"] if row else None


def permitted_agent_subtypes(faction):
    """[(agent_type, subtype)] the faction may field -- the recruit-hero universe."""
    return [(r["agent"], r["subtype"]) for r in _connect().execute(
        "SELECT agent,subtype FROM agent_permitted_subtypes WHERE faction=? ORDER BY agent,subtype",
        (str(faction),))]


def agent_action_matrix(name_suffix):
    """{agent_type_key: full_action_key} -- which agent classes may perform this action.

    The engine's character_type_key() ('engineer', 'wizard', ...) is the same token the action
    key embeds, so this map IS the per-hero gate: a type absent here cannot perform the action."""
    suffixes = [name_suffix] if isinstance(name_suffix, str) else list(name_suffix or ())
    ck = tuple(suffixes)
    if ck not in _matrix_cache:
        out = {}
        for suffix in suffixes:
            for key in agent_action_keys(suffix):
                m = _AGENT_KEY_RE.search(key)
                if m and key.endswith(suffix):
                    out.setdefault(m.group(1), key)
        _matrix_cache[ck] = out
    return dict(_matrix_cache[ck])


def agent_action_ability(name_suffix):
    """The ability class of an action suffix, e.g. hinder_settlement | hinder_agent | assist_army."""
    suffixes = [name_suffix] if isinstance(name_suffix, str) else list(name_suffix or ())
    for suffix in suffixes:
        for key in agent_action_keys(suffix):
            m = _AGENT_KEY_RE.search(key)
            if m and key.endswith(suffix):
                return m.group(2)
    return None


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
_BUILD_RE = re.compile(r"^wh\d?_[a-z0-9]+_([a-z]+)_")
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
        by_chain = {}
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


_SUBTYPE_PREFIX = "agent_subtypes_onscreen_name_override_"
_SUBTYPE_RE = re.compile(r"^wh\d?_[a-z0-9]+_([a-z]+)_")
_subtype_cache = None


def agent_subtypes(race_tokens):
    """[(subtype_key, label)] for these race tokens -- every lore variant the game ships.

    A SUPERSET: the caller must confirm each against the engine's recruitment pool. The live
    world only shows subtypes some faction already fielded, which is why an unplayed lore
    (every Wood Elf Spellweaver, 8 of 9 High Elf Archmages) never reached the advisor."""
    global _subtype_cache
    if _subtype_cache is None:
        _subtype_cache = []
        for row in _connect().execute("SELECT key,text FROM loc WHERE key LIKE ?",
                                      (_SUBTYPE_PREFIX + "%",)):
            sub = row["key"][len(_SUBTYPE_PREFIX):]
            m = _SUBTYPE_RE.match(sub)
            _subtype_cache.append((m.group(1) if m else None, sub, row["text"]))
    toks = set(race_tokens or ())
    return [(sub, txt) for tok, sub, txt in _subtype_cache if tok in toks]
