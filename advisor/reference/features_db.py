r"""Clean lookup API over reference.sqlite for the advisor featurizer.

The numeric game-data features (decoded offline from db.pack by build_reference.py) are exposed
as one dict per record key.  100% offline -- no game, no bus, no network; just reads the sqlite.

    from reference import features_db as F
    F.building_features("wh2_main_hef_resource_marble_1")  -> {'create_cost': 1000, 'create_time': 1, ...}
    F.tech_features("wh2_main_tech_hef_0_00")              -> {'research_points_required': 200, 'tier': 1, ...}
    F.unit_features("wh2_main_hef_inf_spearmen_0")         -> {'recruitment_cost': 500, 'upkeep_cost': 125, ...}
    F.skill_features(key) / F.ritual_features(key)         -> dicts (bonus tables)
    F.label(key)                                           -> localised name via the loc table

Returns {} when the key is absent so callers can featurize uniformly.
"""
import os
import re
import sqlite3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference.sqlite")

_con = None


def _connect():
    global _con
    if _con is None:
        # check_same_thread=False: this DB is READ-ONLY reference data, and the advisor UI serves scoring
        # from a threaded HTTP server (a different thread per request) -- a single cached connection is
        # safe to share across threads for concurrent reads.
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
    """[(edict_key, label)] of every provincial-initiative EDICT whose key carries one of race_tokens
    (matched via the `_edict_<race>_` segment). This is the non-poll option-set for the edict decision
    now that the recorder's edict poll is dropped -- the game DB is the source of the alternatives.

    APPROXIMATE (slight superset): the DB is not LL/tech-gated, so it can include a few edicts a given
    faction cannot yet enact; the player's faction + observed active edicts disambiguate downstream.
    `race_tokens` is a set of race tokens (e.g. {"sla"} or {"hef"}); pass the player's faction race
    token plus any tokens seen in the player's own active/selected edicts for a data-driven filter."""
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
_BUILD_VARIANT_PREFIX = "building_culture_variants_name_"   # DLC colony/special chains: real name lives here
_BUILD_TIP_PREFIX = "building_chains_chain_tooltip_"
_BUILD_RE = re.compile(r"^wh\d?_[a-z0-9]+_([a-z]+)_")   # race token = key segment[2] (wh2_main_HEF_...)
_building_cache = None


def building_label(chain):
    """Display name of a building CHAIN via loc. Tries the encyclopedia name then the chain tooltip,
    skipping junk rows -- empty, the literal `placeholder` some chains carry as encyclopedia name
    (e.g. wh2_main_hef_resource_marble -> 'placeholder' but tooltip 'Marble Quarry'), and `{{tr:...}}`
    translation-reference rows (variant chains that just point at another chain's loc)."""
    con = _connect()
    for pfx in (_BUILD_NAME_PREFIX, _BUILD_VARIANT_PREFIX, _BUILD_TIP_PREFIX):
        row = con.execute("SELECT text FROM loc WHERE key=?", (pfx + chain,)).fetchone()
        txt = row["text"] if row else None
        if txt and txt != "placeholder" and "{{tr:" not in txt:
            return txt
    return None


def building_options(race_tokens):
    """[(building_key, label)] -- the DB construction option-set for the player's race: ONE representative
    card (the lowest `level`) per building CHAIN, filtered on the key's race segment (segment[2] -- the
    same `_<race>_` mechanism edict_options uses). This is the non-poll option-set for the construction
    decision: the live build browser is transient, poll-only and element-capped (unreliable to scrape),
    so the game DB is the source of the alternatives -- exactly mirroring the edict path.

    APPROXIMATE (superset): not gated by settlement slot / climate / resource / tech / LL, so it lists
    chains a specific settlement may not host; the chosen building (recovered from the click-path) always
    lands in it, and the region's built chains disambiguate downstream. One entry per chain keeps this to
    the 'which building line to invest in' choice rather than every upgrade rung. `race_tokens` is a set
    (e.g. {"hef"}) -- pass the player's faction race token."""
    global _building_cache
    if _building_cache is None:
        by_chain = {}          # chain -> (key, level, race_tok)  -- keep the lowest level per chain
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
    """Real on-screen NAME of a settlement-occupation option, from its numeric card id (or None).

    The occupation card id (e.g. '1063', '1813814333') is the settlement_captured panel's numeric
    button id AND equals the CharacterPerformsSettlementOccupationDecision event's `occupation_
    decision`. It joins directly to loc `culture_settlement_occupation_options_tooltip_<id>`, whose
    text is "<Name>||<description>"; the part before '||' is the displayed name. NON-positional --
    the same id -> the same name for any faction / offer size. Verified: 1063/1813814333 -> 'Occupy',
    1058/1812399685 -> 'Loot & Occupy', 224851311 -> 'Occupy' (a 2-option offer the x-order labeler
    mislabelled 'option0')."""
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
    """Real, faction-correct display NAME of a post-battle captive option, from the event's numeric
    `captive_record_key` (or None).

    The generic captive BUTTON ids (button_captive_option_kill/_enslave/_release) are reskinned per
    faction in the display and carry no text; the game's own CharacterPostBattleCaptureOption event
    supplies the numeric record key of the chosen option, which joins to loc `campaign_post_battle_
    captive_options_onscreen_name_<key>`. Verified (Slaanesh Masque, live): 1362331655 (outcome
    enslave) -> 'Devour Captives'; 1124097752 -> 'Entice Captives'; 1228100539 -> 'Offer as Tribute';
    101527833 -> 'Share the Loot'."""
    row = _connect().execute(
        "SELECT text FROM loc WHERE key=?",
        ("campaign_post_battle_captive_options_onscreen_name_%s" % record_key,)).fetchone()
    return row["text"] if row and row["text"] else None


_CAPTIVE_BUTTONS = ("kill", "enslave", "release")


def captive_options(culture=None, faction=None, subculture=None):
    """Name ALL THREE post-battle captive BUTTONS (kill / enslave / release) for a captor,
    from the game DB alone -- NO hover, NO on-screen scrape, ONE deterministic method.

    Returns {button: {"record_key": <id>, "name": <faction-correct on-screen name>}} for each
    of kill/enslave/release the captor has an option for. The binding is the game's own
    campaign_group ORIGINATOR (captor) criterion, decoded offline into `captive_binding`
    (see build_reference._captive_reference). Resolution per button is faction-specific
    first (LL specials like Rakarth 'Lay Out Carrion' / the Masque 'Entice Captives'), then
    the captor's culture (the base race options, e.g. High Elves Execute/Force Labour/Ransom),
    then subculture -- the FIRST entity that has a binding for that button wins. Pass the
    player's `culture`, `faction`, and/or `subculture` keys (from the is_human faction row);
    any that are None are simply skipped."""
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
