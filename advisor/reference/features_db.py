import os
import re
import sqlite3
import sys

_EMPTY_SUFFIX_WARNED = set()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import common

DB = common.REFERENCE_DB

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


UNLOCK_DB = common.UNLOCK_DB
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
