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


_lookup_cache = {}


def _lookup(table, key_col, key):
    ck = (table, key_col, key)
    hit = _lookup_cache.get(ck)
    if hit is None:
        row = _connect().execute(
            "SELECT * FROM %s WHERE %s=?" % (table, key_col), (key,)
        ).fetchone()
        hit = dict(row) if row is not None else {}
        _lookup_cache[ck] = hit
    return dict(hit)


def building_features(key):
    return _lookup("buildings", "key", key)


def building_chain_features(key):
    return _lookup("building_chains", "key", key)


def tech_features(key):
    return _lookup("tech", "key", key)


def unit_features(key):
    return _lookup("units", "key", key)


def skill_features(key):
    return _lookup("skills", "key", key)


def ritual_features(key):
    return _lookup("rituals", "key", key)


def label(key):
    row = _connect().execute("SELECT text FROM loc WHERE key=?", (key,)).fetchone()
    return row["text"] if row else None


_AGENT_ACTION_NAME_PREFIX = "agent_actions_localised_action_name_"
_AGENT_ACTION_DESC_PREFIX = "agent_actions_localised_action_description_"
_TR_RE = re.compile(r"^\{\{tr:(.+)\}\}$")
_TR_PREFIX = "ui_text_replacements_localised_text_"


def agent_action_label(action_key):
    txt = label(_AGENT_ACTION_NAME_PREFIX + str(action_key))
    if not txt:
        return None
    m = _TR_RE.match(txt.strip())
    if not m:
        return txt
    return label(_TR_PREFIX + m.group(1))


_performable_cache = None


def performable_action_keys():
    global _performable_cache
    if _performable_cache is None:
        con = _connect()
        cats = {r["key"]: r["category"] for r in con.execute(
            "SELECT key,category FROM agent_abilities")}
        out = set()
        for r in con.execute("SELECT key,ability,show_in_ui FROM agent_actions"):
            if cats.get(r["ability"]) == "ambient":
                continue
            if str(r["show_in_ui"]).strip().lower() in ("0", "false", "none", ""):
                continue
            out.add(r["key"])
        _performable_cache = out
    return _performable_cache


def agent_action_keys(name_suffix):
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


def agent_action_payload(name_suffix):
    con = _connect()
    for key in agent_action_keys(name_suffix):
        row = con.execute(
            "SELECT r.key AS result, r.actor_bundle, r.target_bundle, r.actor_bundle_turns, "
            "r.target_bundle_turns FROM agent_actions a JOIN action_results r "
            "ON r.key=a.cannot_fail_result WHERE a.key=?", (key,)).fetchone()
        if row is None:
            continue
        effects = [(o["effect"], o["effect_scope"], o["value"]) for o in con.execute(
            "SELECT effect, effect_scope, value FROM action_result_outcomes "
            "WHERE action_result_key=? AND outcome='generic_bonus_value' AND affects_target=1 "
            "AND effect IS NOT NULL AND effect_scope IS NOT NULL ORDER BY key", (row["result"],))]
        if not effects:
            continue
        return {"action_key": key, "result_key": row["result"],
                "target_bundle": row["target_bundle"],
                "target_turns": row["target_bundle_turns"],
                "actor_bundle": row["actor_bundle"],
                "actor_turns": row["actor_bundle_turns"],
                "effects": effects}
    sys.stderr.write("features_db: agent_action_payload(%r) resolved no result bundle -- the "
                     "assist action cannot apply its effect\n" % (name_suffix,))
    return None


def verify_hero_action_mappings(hero_actions):
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
    row = _connect().execute("SELECT category FROM agent_abilities WHERE key=?",
                             (str(ability),)).fetchone()
    return row["category"] if row else None


def permitted_agent_subtypes(faction):
    return [(r["agent"], r["subtype"]) for r in _connect().execute(
        "SELECT agent,subtype FROM agent_permitted_subtypes WHERE faction=? ORDER BY agent,subtype",
        (str(faction),))]


def agent_action_matrix(name_suffix):
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
    con = _connect()
    for pfx in (_BUILD_NAME_PREFIX, _BUILD_VARIANT_PREFIX, _BUILD_TIP_PREFIX):
        row = con.execute("SELECT text FROM loc WHERE key=?", (pfx + chain,)).fetchone()
        txt = row["text"] if row else None
        if txt and txt != "placeholder" and "{{tr:" not in txt:
            return txt
    return None


def building_options(race_tokens):
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
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("culture_settlement_occupation_options_tooltip_%s" % card_id,)).fetchone()
    if not row or not row["text"]:
        return None
    return row["text"].split("||", 1)[0] or None


def occupation_desc(card_id):
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("culture_settlement_occupation_options_tooltip_%s" % card_id,)).fetchone()
    if not row or not row["text"] or "||" not in row["text"]:
        return None
    return row["text"].split("||", 1)[1] or None


def captive_label(record_key):
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("campaign_post_battle_captive_options_onscreen_name_%s" % record_key,)).fetchone()
    return row["text"] if row and row["text"] else None


_CAPTIVE_BUTTONS = ("kill", "enslave", "release")


def captive_options(culture=None, faction=None, subculture=None):
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


UNLOCK_DB = r"D:\twdata\reference\agent_action_unlocks.sqlite"
_unlock_con = None
_unlock_cache = {}


def _unlock_connect():
    global _unlock_con
    if _unlock_con is None:
        _unlock_con = sqlite3.connect("file:%s?mode=ro" % UNLOCK_DB.replace("\\", "/"),
                                      uri=True, check_same_thread=False)
        _unlock_con.row_factory = sqlite3.Row
    return _unlock_con


def actions_for_skills(skill_keys):
    out = set()
    con = _unlock_connect()
    for k in skill_keys or ():
        k = str(k)
        if k not in _unlock_cache:
            _unlock_cache[k] = {r["agent_action"] for r in con.execute(
                "SELECT agent_action FROM skill_actions WHERE skill=?", (k,))}
        out |= _unlock_cache[k]
    return out
