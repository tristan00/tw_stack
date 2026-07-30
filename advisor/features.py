r"""features.py -- per-decision-type featurizers for the advisor.

Same featurizer is used for training (the chosen option) and scoring (every option). Features are
GENERIC + type-specific so a per-type model trained on chosen options can score UNCHOSEN options it
never saw. Real numeric features (cost / turns / tier / category / unlock) come from the offline game
DB via reference/features_db.py; context comes from the player's per-turn faction state record.

  context_features(fac, faction, subculture, lord) -> generic decision context (numeric + categoricals)
  option_features(dtype, key, source, dilemma, choice) -> type-specific option features (DB-backed)
  featurize(dtype, fac, faction, subculture, lord, key, source, dilemma, choice) -> full row
"""
import collections
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "reference"))
try:
    import features_db as _db      # building_features / tech_features / unit_features / skill_features / ritual_features
except Exception as e:             # pragma: no cover - reference not built yet
    _db = None
    sys.stderr.write("features: features_db import unavailable -> %s\n" % repr(e)[:80])
try:
    import item_choices as _items  # ancillary category-from-key parser (no DB: there is no items table)
except Exception as e:             # pragma: no cover
    _items = None
    sys.stderr.write("features: item_choices import unavailable -> %s\n" % repr(e)[:80])

# ---- context (generic, faction/lord-agnostic quantities + explicit categoricals) --------------
# The vassal_*/num_* diplomatic aggregates are NEW twstate.lua fields (relationship signals every
# decision benefits from). Absent on old runs -> context_features defaults them to 0, so inert until
# fresh recordings carry them; each becomes a ctx_* numeric column shared across ALL per-type models.
STATE_NUM = ["treasury", "income", "net_income", "expenditure", "regions", "num_provinces",
             "forces", "chars", "num_generals", "tax_level", "rank", "turn",
             "num_vassals", "vassal_regions", "vassal_forces", "num_allies",
             "num_military_allies", "num_defensive_allies", "num_at_war",
             "num_non_aggression", "num_trade_partners"]
STATE_BOOL = ["at_war", "is_researching"]
CAT_CONTEXT = ["faction", "subculture", "lord"]


def context_features(fac, faction=None, subculture=None, lord=None):
    fac = fac or {}
    f = {("ctx_" + k): (fac.get(k) if fac.get(k) is not None else 0) for k in STATE_NUM}
    for k in STATE_BOOL:
        f["ctx_" + k] = int(bool(fac.get(k)))
    f["ctx_faction"] = faction or fac.get("faction") or "?"
    f["ctx_subculture"] = subculture or fac.get("subculture") or "?"
    f["ctx_lord"] = lord or "?"
    return f


# ---- per-type CONTEXT features (Part B): read the sub-records decision_instances joined onto the
# decision's context (context["region"], ["prov_corruption"], ["army"], ["char"], ...). Numeric keys
# are ctx_*, categoricals ctx_c_* (split_columns routes them). Every key is emitted with a default so
# the column set is stable per type whether or not the source record was recoverable.
def context_type_features(dtype, fac):
    fac = fac or {}
    f = {}
    region = fac.get("region") or {}
    corr = fac.get("prov_corruption") or {}
    # region block -- present for building/occupation/edict (chosen settlement) + captives (battle site)
    if region:
        f["ctx_region_public_order"] = _num(region, "public_order")
        f["ctx_region_gdp"] = _num(region, "gdp")
        f["ctx_region_num_buildings"] = _num(region, "num_buildings")
        f["ctx_region_growth"] = _num(region, "growth_per_turn")
        f["ctx_is_capital"] = int(bool(region.get("is_capital")))
        f["ctx_climate_suit"] = _num(fac, "climate_suit")
        f["ctx_c_climate"] = region.get("climate") or "?"
        f["ctx_in_own_territory"] = int(region.get("owner_is_human") is True)
    if corr:
        f["ctx_prov_corruption_max"] = _num(corr, "max")
        f["ctx_c_corruption_dominant"] = corr.get("dominant") or "none"
    if fac.get("prov_owned_fraction") is not None:
        f["ctx_prov_owned_fraction"] = _num(fac, "prov_owned_fraction")
        f["ctx_prov_is_complete"] = int(bool(fac.get("prov_is_complete")))
    if dtype == "building":
        f["ctx_region_free_slots"] = _num(fac, "region_free_slots", -1)
    elif dtype == "edict":
        f["ctx_c_current_edict"] = region.get("active_edict") or "none"
    elif dtype in ("recruit", "stance"):
        army = fac.get("army") or {}
        units = fac.get("army_units") or []
        f["ctx_army_unit_count"] = _num(army, "units")
        f["ctx_army_strength"] = _num(army, "strength")
        f["ctx_army_morale"] = _num(army, "morale")
        f["ctx_army_upkeep"] = _num(army, "upkeep")
        f["ctx_army_mp_pct"] = _num(army, "mp_pct")
        hs = [u.get("health") for u in units if isinstance(u.get("health"), (int, float))]
        f["ctx_army_avg_health"] = (sum(hs) / len(hs)) if hs else 0
        if dtype == "recruit":
            for cat, n in collections.Counter(
                    u.get("category") for u in units if u.get("category")).items():
                f["ctx_mix_" + cat] = n
        else:  # stance
            f["ctx_is_army"] = int(bool(army.get("is_army")))
            f["ctx_is_navy"] = int(bool(army.get("is_navy")))
            f["ctx_is_horde"] = int(bool(army.get("is_horde")))
            f["ctx_c_stance_current"] = (army.get("stance") or "?").replace(
                "MILITARY_FORCE_ACTIVE_STANCE_TYPE_", "")
    elif dtype == "skills":
        char = fac.get("char") or {}
        f["ctx_char_rank"] = _num(char, "rank", -1)
        f["ctx_char_loyalty"] = _num(char, "loyalty")
        f["ctx_char_taken_skills"] = _num(fac, "char_taken_skills")
        f["ctx_is_governor"] = int(bool(char.get("is_governor")))
        f["ctx_faction_leader"] = int(bool(char.get("faction_leader")))
        f["ctx_c_char_type"] = char.get("type") or "?"
        f["ctx_c_char_subtype"] = char.get("subtype") or "?"
    elif dtype == "research":
        f["ctx_completed_count"] = _num(fac, "completed_count")
        tiers = fac.get("completed_tech_tiers") or []
        f["ctx_max_completed_tier"] = max(tiers) if tiers else 0
        f["ctx_research_queue_idle"] = int(bool(fac.get("research_queue_idle")))
    elif dtype == "items":
        # the character the item goes on/off (rank/type gate item value) + how full the item slots are.
        char = fac.get("char") or {}
        f["ctx_char_rank"] = _num(char, "rank", -1)
        f["ctx_is_governor"] = int(bool(char.get("is_governor")))
        f["ctx_faction_leader"] = int(bool(char.get("faction_leader")))
        f["ctx_c_char_type"] = char.get("type") or "?"
        f["ctx_c_char_subtype"] = char.get("subtype") or "?"
        f["ctx_equipped_count"] = _num(fac, "equipped_count")
        f["ctx_available_count"] = _num(fac, "available_count")
    elif dtype in ("recruit_lord", "recruit_hero"):
        # LORD/HERO roster-composition context (P1). Per user spec these are PROVINCE-level decisions:
        # the MODERATE province features come from the shared `region` block above (populated once the
        # decision_instances join attaches the settlement's region for these types -- FLAG: join not yet
        # wired, so region defaults empty on any current data). Here we add the roster counts: current
        # #lords / #heroes overall + #of the candidate's OWN type (composition signal). Sourced from the
        # per-turn faction record -> defaults to 0 until the mod/join carries them (FLAG: live-verify the
        # field names num_lords/num_heroes/num_this_type).
        f["ctx_num_lords"] = _num(fac, "num_lords")
        f["ctx_num_heroes"] = _num(fac, "num_heroes")
        f["ctx_num_this_type"] = _num(fac, "num_this_type")
    if dtype in ("rites", "captives"):
        pooled = fac.get("pooled") or {}
        vals = [v for v in pooled.values() if isinstance(v, (int, float))]
        f["ctx_pooled_max"] = max(vals) if vals else 0
        if dtype == "rites":
            f["ctx_active_ritual_count"] = len(fac.get("active_rituals") or [])
    return f


def _cross_features(dtype, fac, opt):
    """Context x OPTION interactions -- need BOTH the option's DB row and the faction/region context,
    so they live here (after option_features). Prefixed ctx_ (numeric) since they are per-decision-state
    signals; they vary per option (affordability, chain level) which is exactly the discriminator."""
    fac = fac or {}
    f = {}
    treasury = _num(fac, "treasury"); net = _num(fac, "net_income")
    if dtype in ("building", "recruit"):
        cost = _num(opt, "opt_n_cost"); upkeep = _num(opt, "opt_n_upkeep")
        f["ctx_can_afford"] = int(treasury >= cost) if cost else 1
        f["ctx_cost_vs_treasury"] = min(9.99, cost / treasury) if treasury > 0 else (0.0 if not cost else 9.99)
        f["ctx_upkeep_vs_income"] = min(9.99, upkeep / net) if net > 0 else (0.0 if not upkeep else 9.99)
    if dtype == "building":
        chain = opt.get("opt_c_chain")
        lvls = []
        for bk in (fac.get("region_buildings") or []):
            bf = _db.building_features(bk) if _db else {}
            if bf and bf.get("building_chain") == chain and bf.get("level") is not None:
                lvls.append(bf["level"])
        f["ctx_chain_current_level"] = max(lvls) if lvls else -1
        f["ctx_chain_already_built"] = int(bool(lvls))
    elif dtype == "occupation":
        owned = _num(fac, "prov_owned_count"); total = _num(fac, "prov_region_count")
        f["ctx_completes_province"] = int(total > 0 and owned + 1 >= total)
    elif dtype == "research":
        tiers = fac.get("completed_tech_tiers") or []
        maxc = max(tiers) if tiers else 0
        tier = _num(opt, "opt_n_tier", -1)
        f["ctx_tier_gap"] = (tier - maxc) if tier >= 0 else 0
    elif dtype == "skills":
        crank = _num(fac.get("char") or {}, "rank", 0)
        orank = _num(opt, "opt_n_rank", -1)
        f["ctx_skill_rank_ok"] = int(crank >= orank) if orank >= 0 else 1
        f["ctx_skill_rank_gap"] = (crank - orank) if orank >= 0 else 0
    elif dtype == "rites":
        pooled = fac.get("pooled") or {}
        have = max([v for v in pooled.values() if isinstance(v, (int, float))] or [0])
        need = _num(opt, "opt_n_influence")  # influence is the primary rite cost axis (slaves secondary)
        f["ctx_currency_vs_cost"] = min(9.99, have / need) if need > 0 else (0.0 if not have else 9.99)
        f["ctx_rite_already_active"] = int((opt.get("opt_c_ritual_key") or "___") in
                                           (fac.get("active_rituals") or []))
    return f


def _num(d, k, default=0):
    v = (d or {}).get(k)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default  # intentional: non-numeric -> default, hot per-value coercion helper (no log)


# ---- small key-parsing helpers (categorical derivations from an option key, no DB) --------------
# dilemma ordinal TOKEN -> 0-based index (mirrors dilemmas._token_ordinal; kept local to avoid a
# circular import). Used only as a fallback when the option's own numeric `ordinal` is absent.
_DIL_WORD_ORD = {w: i for i, w in enumerate(
    ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH",
     "ELEVENTH", "TWELFTH"])}


def _dil_token_ord(token):
    """A dilemma option token (FIRST.. / SCRIPTED_<n>) -> 0-based ordinal, else -1."""
    t = str(token or "")
    if t in _DIL_WORD_ORD:
        return _DIL_WORD_ORD[t]
    if t.startswith("SCRIPTED_") and t[len("SCRIPTED_"):].isdigit():
        return int(t[len("SCRIPTED_"):]) - 1
    return -1


def _faction_race(key):
    """RACE/subculture token parsed from a faction key (wh*_{main|dlcNN|twaNN}_{race}_{name}) -- e.g.
    wh3_main_sla_subtle_torture -> 'sla', wh2_twa03_def_rakarth -> 'def'. A LOW-cardinality categorical
    that generalizes across factions of the same race (vs the raw, mostly-unseen faction key). '?' if it
    doesn't parse."""
    parts = str(key or "").split("_")
    return parts[2] if len(parts) >= 3 and parts[0].startswith("wh") else "?"


def option_features(dtype, key, source=None, dilemma=None, choice=None, option=None):
    """Type-specific option features. Numeric keys start opt_n_*, categoricals opt_c_*. DB-backed.
    `dilemma`/`choice` are used only by the dilemma path (the dilemma key + the option's choice
    label/token). `option` is the FULL per-option record (recorder-captured extra fields such as a
    candidate's trait/level/cost or a faction's diplo stats); it is read only by the newer per-option
    types and every field is `.get(...)`-defaulted, so old data (which lacks those fields) stays inert."""
    key = key or ""
    o = option or {}
    f = {"opt_c_source": source or "none"}
    if dtype == "building":
        b = _db.building_features(key) if _db else {}
        f["opt_n_cost"] = _num(b, "create_cost")
        f["opt_n_time"] = _num(b, "create_time")
        f["opt_n_upkeep"] = _num(b, "upkeep_cost")
        f["opt_n_tier"] = _num(b, "level", -1)
        f["opt_c_chain"] = (b or {}).get("building_chain") or "?"
        # DEMOLISH/REPAIR building-model EXTENSION (per user: NOT a standalone type): the option's action
        # (build / demolish / repair) so the SAME building model scores raze + repair options alongside
        # construction. Defaults to "build" -> every existing construction option is unchanged/inert;
        # demolish/repair options (fresh recordings: button_raze / RegionBuildingCancelled / repair-
        # BuildingCompleted) carry option["action"] to discriminate.
        f["opt_c_action"] = o.get("action") or "build"
    elif dtype == "recruit":
        u = _db.unit_features(key) if _db else {}
        f["opt_n_cost"] = _num(u, "recruitment_cost")
        f["opt_n_upkeep"] = _num(u, "upkeep_cost")
        f["opt_n_time"] = _num(u, "create_time")
        f["opt_n_tier"] = _num(u, "tier", -1)
        f["opt_n_men"] = _num(u, "num_men")
        f["opt_c_category"] = (u or {}).get("category") or "?"
        f["opt_c_caste"] = (u or {}).get("caste") or "?"
    elif dtype == "research":
        t = _db.tech_features(key) if _db else {}
        f["opt_n_cost"] = _num(t, "research_points_required")
        f["opt_n_perround"] = _num(t, "cost_per_round")
        f["opt_n_tier"] = _num(t, "tier", -1)
        f["opt_n_parents"] = _num(t, "required_parents")
        f["opt_c_branch"] = ("military" if (t or {}).get("is_military") else
                             "civil" if (t or {}).get("is_civil") else
                             "engineering" if (t or {}).get("is_engineering") else "?")
        f["opt_c_unlocks"] = "yes" if (t or {}).get("building_level") else "no"
    elif dtype == "skills":
        s = _db.skill_features(key) if _db else {}
        f["opt_n_rank"] = _num(s, "unlocked_at_rank", -1)
        f["opt_n_influence"] = _num(s, "influence_cost")
        f["opt_c_group"] = ("lord" if "_lord_" in key else "hero" if "_hero_" in key
                            else "all" if "_all_" in key else "magic" if "_magic_" in key else "?")
    elif dtype == "rites":
        r = _db.ritual_features(key) if _db else {}
        f["opt_n_cooldown"] = _num(r, "cooldown_time")
        f["opt_n_influence"] = _num(r, "influence_cost")
        f["opt_n_slaves"] = _num(r, "slave_cost")
        f["opt_c_category"] = (r or {}).get("category") or "?"
        f["opt_c_ritual_key"] = key or "?"
    elif dtype == "captives":
        f["opt_c_outcome"] = (key or "").replace("button_captive_option_", "") or "?"
    elif dtype == "occupation":
        f["opt_c_decision"] = str(key)
    elif dtype == "pre_battle":
        # PRE-BATTLE menu (attack / autoresolve / retreat / continue-siege / demand-surrender / ...).
        # No DB table -- the option IS the clicked button. Previously pre_battle had NO case, so every
        # option got only opt_c_source == byte-identical (100% of decisions indistinguishable). The
        # ACTION categorical (button key, LOW cardinality + well covered) lets the model value each
        # action; a coarse group generalizes across siege/field variants. Campaign stats
        # (treasury/regions/income/rank/num_generals) already arrive via context_features (shared across
        # options -- context, not discriminator). The deeper BOTH-SIDES battle-odds features
        # (deployment strength ratio) await a recorder deployment-odds grab -- out of scope here.
        k = key or ""
        _FIGHT = {"button_attack", "button_sally_forth", "button_surround"}
        _AVOID = {"button_retreat", "button_dismiss", "button_spectate"}
        _SIEGE = {"button_continue_siege", "button_maintain_blockade", "button_demand_surrender"}
        f["opt_c_action"] = k or "?"
        f["opt_c_action_group"] = ("fight" if k in _FIGHT else "resolve" if k == "button_autoresolve"
                                   else "avoid" if k in _AVOID else "siege" if k in _SIEGE else "other")
    elif dtype == "stance":
        # stance KEY suffix (DEFAULT/MARCH/AMBUSH/...) + a coarse family grouping. No stance DB table.
        k = (key or "").replace("MILITARY_FORCE_ACTIVE_STANCE_TYPE_", "")
        _MOVE = {"MARCH", "DOUBLE_TIME", "PATROL"}
        _RAID = {"LAND_RAID", "SET_CAMP_RAIDING", "AMBUSH"}
        _CAMP = {"SET_CAMP", "FIXED_CAMP", "SETTLE", "MUSTER", "ASSEMBLE_FLEET"}
        _MAGIC = {"CHANNELING", "ASTROMANCY", "TUNNELING"}
        f["opt_c_stance"] = k or "?"
        f["opt_c_stance_group"] = ("move" if k in _MOVE else "raid" if k in _RAID else
                                   "camp" if k in _CAMP else "magic" if k in _MAGIC else
                                   "default" if k == "DEFAULT" else "other")
    elif dtype == "edict":
        # GAP: no edicts table exists in reference.sqlite (no create cost / numeric effect data for an
        # edict). Categorize by key substring only -- flagged; add an edicts DB table to enrich further.
        kk = (key or "").lower()
        f["opt_c_edict"] = key or "?"
        f["opt_c_edict_group"] = ("order" if "order" in kk else "growth" if "growth" in kk else
                                  "corruption" if "corrupt" in kk else
                                  "military" if ("military" in kk or "recruit" in kk or "war" in kk) else
                                  "economy" if ("gdp" in kk or "wealth" in kk or "trade" in kk or
                                                "income" in kk or "splendour" in kk) else "other")
    elif dtype == "items":
        # ITEM (ancillary) option, featurized from the KEY: the CATEGORY token (weapon/armour/talisman/
        # enchanted_item/...) + the item key itself (reference.sqlite has no ancillary features table).
        # The category is the generalizable per-option signal -- the model values "equip a talisman" vs
        # "equip a weapon" and DISTINGUISHES options that differ by category (verified: a resolved-key
        # option-set scores 4 distinct (exploit,explore) tuples of 4). WHY the current data is still
        # degenerate is NOT a missing feature: every CHOSEN key trained on is a resolved real key
        # (CharacterAncillaryGained.ancillary) but every OPTION-SET key is an UNRESOLVED numeric ancillary
        # CQI ('780') -- the two distributions are DISJOINT (measured 128/128 chosen real vs 81/81 options
        # numeric, 0 overlap), so no feature computable from a numeric option key exists in the training
        # space and both models collapse it. The real fix is id->key RESOLUTION via the recorder's
        # equipment-panel scrape TEXT (a capture follow-on, out of scope here -- like pre_battle's battle
        # odds); once options carry real keys this featurizer distinguishes them. item_category on a
        # numeric id -> "unknown" (a clean categorical, not a junk numeric string). `source` =
        # available(add) vs equipped(remove) -- carried in opt_c_source above.
        f["opt_c_item"] = key or "?"
        cat = _items.item_category(key) if _items else "?"
        f["opt_c_category"] = "unknown" if str(cat).isdigit() else cat
    elif dtype in ("recruit_lord", "recruit_hero"):
        # LORD/HERO recruit candidate (P1; 0 rows until fresh recordings). Per user spec: candidate
        # trait(s) + starting level + TYPE (the class/subtype tab) + campaign+province context (context
        # side) + current #lords/#heroes per type (context side). `type` IS the chosen char_subtype =
        # key; trait/level/cost are per-candidate (CcoCampaignCharacter-resolved by the recorder) and
        # arrive on `option` -> default until captured (FLAG: live-verify the option field names).
        f["opt_c_type"] = o.get("type") or key or "?"          # lord/hero class/subtype (the tab)
        f["opt_n_level"] = _num(o, "level", -1)                # candidate starting level
        f["opt_n_cost"] = _num(o, "cost")                      # recruit cost (influence/gold)
        f["opt_c_trait"] = str(o.get("trait") or o.get("traits") or "?")   # candidate trait(s)
        f["opt_c_lore"] = o.get("lore") or "?"                 # sorcerer-lord lore sub-pick, if any
    elif dtype == "offices":
        # OFFICE assignment candidate (P1; 0 rows until fresh recordings). Per user spec: candidate
        # trait/level/type + office bonus + campaign ctx. type = candidate char subtype (key); office +
        # bonus + trait/level arrive on `option` (offices-panel scrape / char-record getter) -> default
        # until captured (FLAG: live-verify the option field names).
        f["opt_c_type"] = o.get("type") or key or "?"          # candidate character subtype/class
        f["opt_n_level"] = _num(o, "level", -1)                # candidate rank/level
        f["opt_c_trait"] = str(o.get("trait") or o.get("traits") or "?")
        f["opt_c_office"] = o.get("office") or "?"             # which office slot this option assigns to
        f["opt_n_office_bonus"] = _num(o, "office_bonus")      # magnitude of the office's effect bonus
    elif dtype == "eternal_dance":
        # ETERNAL DANCE (Slaanesh; P1). "others = shared base context + option features": the dance
        # theme/move is the whole option. key = the dance theme (wh3_dlc27_eternal_dance_* / btn_dance_*).
        f["opt_c_dance"] = key or "?"
        f["opt_n_tempo"] = _num(o, "tempo", -1)                # tempo level, if the recorder carries it
    elif dtype == "diplomatic_target":
        # DIPLOMACY level-1 (WHO to deal with; P1). Option = a known faction; per FINDINGS its row
        # carries strength rank / attitude / settlements / treaties / ally-options / race. key = faction.
        # opt_c_faction (the raw key) is high-cardinality + mostly unseen -> it distinguishes options but
        # generalizes poorly; opt_c_target_race (parsed from the key) is a LOW-cardinality signal shared
        # across factions of the same race, so the model can value "deal with a Dark Elf faction" even
        # for an unseen one. The strength/settlement/treaty scalars remain recorder-capture-gated (they
        # default inert until the WHO-list scrape carries them -- a follow-on, not this pass).
        f["opt_c_faction"] = key or "?"
        f["opt_c_target_race"] = _faction_race(key)
        f["opt_n_strength_rank"] = _num(o, "strength_rank", -1)
        f["opt_n_settlements"] = _num(o, "settlements")
        f["opt_n_treaties"] = _num(o, "treaties")
        f["opt_n_ally_options"] = _num(o, "ally_options")
        f["opt_c_attitude"] = str(o.get("attitude") or "?")
        f["opt_c_race"] = o.get("race") or o.get("subculture") or "?"
    elif dtype == "diplomatic_deal":
        # DIPLOMACY level-2 (WHAT deal; P1). Option = a staged clause; deal scalars (success_chance,
        # treasury delta) + the counterparty resolve when a deal is active. key = clause type/component.
        f["opt_c_clause"] = key or "?"
        f["opt_c_counterparty"] = o.get("counterparty") or "?"
        f["opt_n_success_chance"] = _num(o, "success_chance", -1)
        f["opt_n_value"] = _num(o, "value")                    # clause magnitude (gold/settlement value)
    elif dtype == "dilemma":
        # opt_c_dilemma (which dilemma) + opt_c_choice (this option's label/token) + opt_n_ordinal.
        # BUG FIXED: opt_n_ordinal used str(key).isdigit(), true only in TRAINING (key = chosen ordinal
        # int) -- at SCORING key is the option TOKEN ("FIRST"/"SCRIPTED_1"), so every option collapsed to
        # -1 (train/score mismatch), leaving only the per-dilemma-UNIQUE label -> CatBoost saw one novel
        # categorical per option and returned an identical value for all (100% exploit-identical). Now the
        # ordinal is read from the option's own numeric `ordinal` (scoring path) or the digit key
        # (training path) or the token, so options carry a consistent, DIFFERING numeric the model can use.
        ordv = o.get("ordinal")
        if ordv is None:
            ordv = int(key) if str(key).isdigit() else _dil_token_ord(key)
        f["opt_n_ordinal"] = float(ordv) if isinstance(ordv, (int, float)) else -1.0
        f["opt_c_dilemma"] = dilemma or "?"
        f["opt_c_choice"] = choice or (str(key) if key not in (None, "") else "?")
    return f


def featurize(dtype, fac, faction, subculture, lord, key, source=None, dilemma=None, choice=None,
              option=None):
    row = context_features(fac, faction, subculture, lord)
    row.update(context_type_features(dtype, fac))
    opt = option_features(dtype, key, source, dilemma=dilemma, choice=choice, option=option)
    row.update(opt)
    row.update(_cross_features(dtype, fac, opt))
    return row


_CTX_CAT = ("ctx_faction", "ctx_subculture", "ctx_lord")


def split_columns(rows):
    """Given feature-dict rows, return (numeric_cols, categorical_cols) by name convention.
    Numeric = ctx_* (NOT ctx_c_*, NOT the 3 named categoricals) + opt_n_*. Categorical = the 3 named
    + ctx_c_* (per-type context categoricals) + opt_c_*. The ctx_c_* routing is REQUIRED: without it a
    string-valued ctx_c_* would land in the numeric matrix and crash float()/CatBoost."""
    cols = set()
    for r in rows:
        cols.update(r.keys())
    num = sorted(c for c in cols if c.startswith("ctx_") and not c.startswith("ctx_c_")
                 and c not in _CTX_CAT)
    num += sorted(c for c in cols if c.startswith("opt_n_"))
    cat = list(_CTX_CAT) + sorted(c for c in cols if c.startswith("ctx_c_"))
    cat += sorted(c for c in cols if c.startswith("opt_c_"))
    cat = [c for c in cat if c in cols]
    return num, cat
