r"""decision_instances.py -- the core join: one Decision record per handled menu-open.

Joins the three captured legs (offline; no bus, no game) into the plan's Decision abstraction:

  Decision = {campaign, turn, order, type, panel, t, gt, context,
              options:[{key, id, state, available, source}], chosen, chosen_linked}

Legs + reuse:
  option set + availability  <- ui_components.jsonl menu_open.options[]        (iter_menu_opens)
  turn + context             <- structurer.iter_twstate per-turn player faction record, bridged
                                recorder-t -> game-t by correlation.correlate's offset
  chosen                     <- EVENT-ANCHORED: each player choice event is assigned to the specific
                                menu-open right before it (never turn-level, which over-attributes).
                                building's chosen comes from the click-rect (mouse_down in an option
                                rect, both on the recorder clock).

A menu-open with no choice after it is a BROWSE (chosen=None) -- that is correct, not a gap.

Winning methods wired in (each verified offline on v6 run 20260727_144203):
  building   -> building_chosen.resolve_construction_choices  (in-log UI click-path; 20/20, replaces
                the old mouse-rect click that only linked 2/9 opens with 1 wrong).
  skills     -> SKILL_DROP pseudo-skill filter + WINDOWS['skills'] wide symmetric containment window
                (player cycles chars, so the chosen skill's panel is often not the immediately-prior
                open; containment gates correctness).
  recruit    -> recruit_pool.resolve_recruit_pools  gives chosen_source = which POOL (local/global)
                the SAME unit was recruited from (Y-band; 35/35).
  occupation -> digit-id identity: occupation_decision == the clicked button's numeric option.id.
  captives   -> captive_outcome_key substring of button_captive_option_<x>.
  research   -> ResearchStarted.tech == option key exactly.

TODO(capture-change) -- NOT recoverable offline, requires a recorder change; do NOT fake:
  * option EFFECT features: the effect subtree (CcoEffect leaves) is lazily instantiated on HOVER, so
    at most ONE option per menu_full snapshot ever carries effect text. Offline path-join recovers only
    ~3% of shown options and 1/15 chosen keys. FIX = have the recorder hover-sweep each option (or read
    tooltips per option) so every option's effects are captured. Until then `options[].effect` is absent.
  * research completeness: the Wood Elves technology_panel snapshot enumerates only the base tab
    (25 wh_dlc05_tech_* techs); DLC16 "Ancients" techs live on a separate un-swept tab, so the one
    turn-5 tech_dlc16_wef_ancients_eagles choice cannot link. FIX = sweep all tech tabs on panel open.

    python decision_instances.py <run_dir>        # per-type coverage + a linked sample per type
"""
import bisect
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

_pc = time.perf_counter


def _T(rid, phase, dt):
    """Timing log (per run/thread + phase), gated on env ADVISOR_TIMING=1."""
    if os.environ.get("ADVISOR_TIMING"):
        print("      [%-9s] %-16s %7.1fs" % (rid, phase, dt), flush=True)

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("structurer", "correlation", "runs"):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _p)))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "reference"))
import structurer          # noqa: E402
import correlation         # noqa: E402
from building_chosen import resolve_construction_choices   # noqa: E402  WINNING building method
from recruit_pool import resolve_recruit_pools             # noqa: E402  WINNING recruit-pool method
import item_choices                                        # noqa: E402  ITEM (ancillary) derivation
try:
    import features_db as _FDB   # noqa: E402  DB-backed tech tiers for research tier_gap (Part A/B)
except Exception as e:           # pragma: no cover
    _FDB = None
    sys.stderr.write("decision_instances: features_db import unavailable -> %s\n" % repr(e)[:80])

PANEL_TYPE = {
    "construction": "building", "recruitment": "recruit", "technology": "research",
    "skills": "skills", "occupation": "occupation", "post_battle_captives": "captives",
    "rites": "rites",
    # recorder-added menus (option sets appear only in fresh recordings; older runs have the CHOSEN
    # stance (ForceAdoptsStance events) / active edict (region state) but NO option-set menu_open --
    # those are synthesized below with an empty option set so they still train on chosen+context).
    "army_stances": "stance", "edicts": "edict",
    "rites_great_game": "rites",   # Slaanesh Great-Game rituals -> same decision type as HE rites
    "great_game_rituals": "rites",  # the recorder's ACTUAL panel name for the Great-Game rituals menu
    #                               (event-triggered capture-on-open; survives the poll drop) -> rites
    # ITEM (ancillary / magic-item) pool + equipped scrapes -> the "items" decision type. These opens
    # are NOT anchored via the generic menu_open->choice path (they carry numeric-id options + adds are
    # events + removes fire no event); item_choices.derive owns them end-to-end (real-key resolution,
    # remove detection, option-set assembly). Kept in PANEL_TYPE so runtime.watch live-scores them and
    # iter_menu_opens recognises them; build_decisions SKIPS them in the generic loop (see below).
    "equipment": "items", "equipment_equipped": "items",
    # ---- NEW P1 decision types (forward-looking): the recorder panels below are ADDED in P1; they carry
    # 0 menu_opens on any current recording, so these types produce 0 rows for now and build_rows/
    # train_all skip them gracefully (a type with no rows never gets a key in the rows defaultdict).
    # FLAG (live-verify): these recorder panel NAMES are assumed from FINDINGS.md (the P1 recorder is not
    # yet emitting them); reconcile each key against the panel string the recorder actually writes.
    #   recruit_lord/recruit_hero <- character_panel, split by header tx_recruit "Recruit Lord"/"Recruit
    #     Hero"; options = general_candidate_N (CcoCampaignCharacter). chosen = CharacterRecruited/
    #     CharacterCreated char_subtype (already captured; choice-linking wired in P1, not here).
    #   offices <- offices|main|offices_panel slot occupancy; chosen = slot empty->occupied character.
    #   eternal_dance <- Slaanesh character-panel dance tab (sla_eternal_dance_subpanel); chosen =
    #     btn_dance_<theme> clicked before button_perform.
    #   diplomacy_factions (level-1 WHO) <- diplomacy_dropdown|faction_panel faction rows.
    #   diplomacy_options (level-2 WHAT deal) <- staged clauses (your_offers/their_offers).
    "recruit_lord": "recruit_lord", "recruit_hero": "recruit_hero",
    #   recruit_panel <- the recorder's ACTUAL name for the general/lord recruitment POOL (options are
    #     `general_candidate_N` / `character_details_<cqi>`). The pool-candidate cqi is NOT the recruited
    #     character's cqi (fresh cqi on recruit) and option text is empty, so a candidate cannot be
    #     identity-linked; chosen resolves (unlinked) from the nearest CharacterRecruited/CharacterCreated
    #     char_subtype event within the open window (event source, fallback like research/skills).
    "recruit_panel": "recruit_lord",
    "offices": "offices", "eternal_dance": "eternal_dance",
    # pre_battle <- the pre-battle decision menu (attack / autoresolve / retreat / siege choices). Options
    #   are `button_*` keys; chosen = the pre-battle button the player CLICKED (ComponentLClickUp on one of
    #   those keys), linked by containment (button_ok and other generic OK clicks never match a pre_battle
    #   option key, so they are dropped -- pre_battle takes NO uncontained fallback, see build_decisions).
    "pre_battle": "pre_battle",
    "diplomacy": "diplomatic_target", "diplomacy_options": "diplomatic_deal",   # recorder emits panel="diplomacy" (WHO list) -> target
}

# The pre-battle decision buttons (the game's fixed pre-battle menu universe -- hardcoded like
# STANCE_UNIVERSE). A ComponentLClickUp on one of these, contained in a pre_battle open's option set,
# IS that battle's chosen. Kept as a set so a click is recognised as a pre_battle pick; button_ok is
# included because some pre_battle menus expose it, but containment gating means it only counts when the
# specific open actually showed it (a stray dialog button_ok never lands on a pre_battle open).
PREBATTLE_BUTTONS = frozenset((
    "button_autoresolve", "button_attack", "button_retreat", "button_dismiss",
    "button_maintain_blockade", "button_demand_surrender", "button_sally_forth",
    "button_surround", "button_continue_siege", "button_spectate", "button_ok",
))

# ForceAdoptsStance emits a NUMERIC stance code; this map (derived empirically by pairing every
# ForceAdoptsStance event with the resulting force `stance` STRING across a full run -- each code
# resolves to exactly one string) turns it into the stance KEY the featurizer categorizes on. The
# suffix after MILITARY_FORCE_ACTIVE_STANCE_TYPE_ is used (== the button component's key suffix).
STANCE_CODE = {
    0: "DEFAULT", 1: "MARCH", 2: "AMBUSH", 3: "LAND_RAID", 4: "SET_CAMP", 5: "MUSTER",
    6: "DOUBLE_TIME", 8: "PATROL", 9: "ASSEMBLE_FLEET", 10: "SETTLE", 11: "CHANNELING",
    12: "TUNNELING", 14: "SET_CAMP_RAIDING", 15: "ASTROMANCY", 17: "FIXED_CAMP",
}
CHOICE_WINDOW = 150.0     # sec: a choice may belong to a same-type open up to this long BEFORE it
FWD_TOL = 20.0            # sec: ...or up to this long AFTER it. The option-set snapshot for a panel is
#                          captured a few seconds after the panel actually opens (enumeration lag), and
#                          the recorder->game clock offset carries ~0.1-1s of slop, so the correct open
#                          can read as slightly LATER than the choice event. Measured gaps on v6:
#                          occupation open lands 0.1s AFTER its choice; a research open lands up to 11s
#                          after its tech-slot click. Without this forward tolerance a strict "open
#                          before choice" bisect silently drops those choices.

# Per-type anchoring window (back_secs, fwd_secs). SKILLS needs a much wider, symmetric window: the
# player cycles lords/heroes, so the immediately-preceding open is usually a DIFFERENT character's
# panel -- naive nearest-before contains the chosen skill only 28% of the time. Containment gates
# correctness (we only accept an open that actually SHOWS the chosen key), so widening only adds true
# links, never false ones. +/-300s is the empirical knee on v6 (90% linked; 77% at +/-180s).
WINDOWS = {"skills": (300.0, 300.0)}
DEFAULT_WINDOW = (CHOICE_WINDOW, FWD_TOL)

# Pseudo-skill families that are NOT real player choices (auto/innate/scaling scaffolding). These keys
# never appear in a skills panel option set, so counting them would wrongly depress skill link%.
SKILL_DROP = ("_agent_action_success_scaling", "_innate_", "_dummy")

# ======================================================================================
# POLL-ELIMINATION SYNTHESIS + RECONCILIATION  (per FINDINGS "POLL ELIMINATION" / "GOVERNING DESIGN")
# The recorder is dropping its blind option-set poll, so the ex-poll decisions (edicts / army_stances /
# construction / rites / diplomacy) are SYNTHESIZED here from purely passive, bus-free streams:
#   semantic EVENTS + per-turn STATE-DIFFS + CLICKS/CLICK-RECT + DB-derived option sets.
# Every decision carries an `_indicators` ledger (one {source, chosen} per witness); `_reconcile` fuses
# them into a confident chosen + `chosen_sources` (which witnesses agreed) + a `conflict` flag, preferring
# agreement across >=2 indicators and degrading gracefully to a single one. All reads are `.get(...)`-
# defaulted, so runs recorded before these fields existed stay inert.

# The complete MILITARY_FORCE_ACTIVE_STANCE_TYPE_* universe (15). Now that the recorder no longer polls
# the per-army stance buttons, this hardcoded enum IS the stance option-set (the per-army AVAILABLE subset
# is poll-only and deferred -- we present the full universe). Suffixes == force-state `stance` suffix ==
# the button component key suffix, so chosen (a suffix) always lands inside it.
STANCE_UNIVERSE = ["DEFAULT", "MARCH", "AMBUSH", "LAND_RAID", "SET_CAMP", "MUSTER", "DOUBLE_TIME",
                   "PATROL", "ASSEMBLE_FLEET", "SETTLE", "CHANNELING", "TUNNELING",
                   "SET_CAMP_RAIDING", "ASTROMANCY", "FIXED_CAMP"]
_STANCE_PREFIX = "MILITARY_FORCE_ACTIVE_STANCE_TYPE_"

# Per-counterparty diplomacy relationship flags dumped in the `diplo` state record (per player/turn).
_DIPLO_FLAGS = ("at_war", "trade", "mil_access", "allied", "mil_ally", "def_ally",
                "non_aggression", "vassal_of")
# diplomatic_option_<clause> component  ->  the diplo-state flag it realizes (for state-diff corroboration).
CLAUSE_FLAG = {"trade_agreement": "trade", "nonaggression_pact": "non_aggression",
               "non_aggression_pact": "non_aggression", "military_alliance": "mil_ally",
               "defensive_alliance": "def_ally", "soft_access": "mil_access",
               "military_access": "mil_access", "vassal": "vassal_of", "declare_war": "at_war"}

# type -> the PRIMARY indicator source for a choice anchored to a menu-open (building overrides per-src).
_CHOICE_SOURCE = {"research": "event", "skills": "event", "occupation": "event", "captives": "event",
                  "rites": "event", "stance": "event", "recruit": "click", "building": "event",
                  # pre_battle / eternal_dance are the button the player CLICKED; recruit_lord/hero are
                  # the CharacterRecruited/CharacterCreated EVENT (subtype) anchored to the pool open.
                  "pre_battle": "click", "eternal_dance": "click",
                  "recruit_lord": "event", "recruit_hero": "event"}


def _add_ind(d, source, chosen):
    """Append one indicator {source, chosen} to a decision's cross-reference ledger."""
    d.setdefault("_indicators", []).append({"source": source, "chosen": chosen})


def _reconcile(d):
    """RECONCILIATION (GOVERNING DESIGN): fuse a decision's `_indicators` into a confident chosen +
    `chosen_sources` + `conflict`. Prefers the majority-voted key across indicators; only FILLS chosen
    when an upstream resolver left it None (never overrides a confident resolver). `chosen_sources` is
    the set of witnesses that agree on the final chosen; `conflict` flags >=2 distinct non-null claims.
    """
    inds = d.get("_indicators") or []
    vals = [i.get("chosen") for i in inds if i.get("chosen") not in (None, "")]
    if d.get("chosen") in (None, "") and vals:
        d["chosen"] = Counter(vals).most_common(1)[0][0]     # majority witness fills an empty chosen
    ch = d.get("chosen")
    d["chosen_sources"] = sorted({i["source"] for i in inds
                                  if i.get("chosen") == ch and ch not in (None, "")})
    d["conflict"] = len(set(vals)) >= 2
    return d


def iter_menu_opens(run_dir):
    """Yield each handled menu_open row (panel in PANEL_TYPE) in capture order, from ui_components.jsonl."""
    p = os.path.join(run_dir, "ui_components.jsonl")
    if not os.path.isfile(p):
        return
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue  # intentional: skip malformed line, hot per-line parse of ui_components.jsonl (no per-line log)
            if r.get("kind") == "menu_open" and r.get("panel") in PANEL_TYPE:
                yield r


# ------------------------------------------------------------------ STATE INDEX (Part A) --------
# Built in the SAME single iter_twstate(log) pass that yields the turn timeline -- NOT a second
# stream. Each per-entity timeline is {key -> (sorted_ts, vals)}; `_at(entry, gt)` returns the value
# with the greatest t <= a decision's game-time (bisect). Region/slot/province/effect indices cover
# ALL factions (occupation targets are enemy regions; province completion needs sibling owners);
# the per-entity char/force/unit/pooled/ritual/tech indices are restricted to the PLAYER faction
# (all the featurizers need, and it bounds memory on the multi-GB logs).
def _finalize(acc):
    """{key -> {t: val}} -> {key -> (sorted_ts_list, vals_list)} for bisect lookup."""
    out = {}
    for k, d in acc.items():
        items = sorted(d.items())
        out[k] = ([t for t, _ in items], [v for _, v in items])
    return out


def _at(entry, gt):
    """Value with greatest t <= gt from a (ts_list, vals_list) entry, else None."""
    if not entry:
        return None
    ts, vals = entry
    i = bisect.bisect_right(ts, gt) - 1
    return vals[i] if i >= 0 else None


def _lookup(idx, name, key, gt):
    if key is None:
        return None
    return _at(idx[name].get(key), gt)


def _turn_index(log, player):
    """(sorted game-times, turn at each, per-turn player faction record, STATE INDEX).

    The state index is a dict of per-entity timelines + a couple of static maps (see module doc):
      region_ts[region] slots[region] provpool[province] regfx[region]         (ALL factions)
      char_ts[cqi] force_ts[mf] units[mf] pooled[fac] ritual_ts[fac] tech_ts[fac]  (PLAYER only)
      completed_techs[fac]=[(t,key)]  taken_skills[cqi]=[(t,key)]  (cumulative event sets)
      prov_regions{province->set(region)}  region_prov{region->province}  player
    """
    gts, turns, ctx = [], [], {}
    region_acc = defaultdict(dict); slot_acc = defaultdict(dict); provpool_acc = defaultdict(dict)
    regfx_acc = defaultdict(dict)
    char_acc = defaultdict(dict); force_acc = defaultdict(dict); units_acc = defaultdict(dict)
    pooled_acc = defaultdict(dict); ritual_acc = defaultdict(dict); tech_acc = defaultdict(dict)
    completed_techs = defaultdict(list); taken_skills = defaultdict(list)
    prov_regions = defaultdict(set); region_prov = {}
    diplo_acc = defaultdict(dict)                     # toward -> {turn: {flag: bool}}  (player row only)
    for r in structurer.iter_twstate(log):
        t = r.get("t", 0.0)
        turn = r.get("turn")
        k = r.get("kind")
        if turn is not None and k != "event":
            gts.append(t); turns.append(turn)
        if k == "faction":
            if r.get("faction") == player:
                ctx.setdefault(turn, r)
            continue
        if k == "region":
            rn = r.get("region")
            if rn is None:
                continue
            region_acc[rn][t] = r
            prov = r.get("province")
            if prov:
                prov_regions[prov].add(rn); region_prov[rn] = prov
        elif k == "slot":
            rn = r.get("region")
            if rn is not None:
                slot_acc[rn].setdefault(t, {})[r.get("slot_index")] = {
                    "has_building": r.get("has_building"), "building": r.get("building")}
        elif k == "province_pooled":
            prov = r.get("province")
            if prov:
                provpool_acc[prov].setdefault(t, {})[r.get("resource")] = (r.get("value"), r.get("max"))
        elif k == "region_effect_bundle":
            rn = r.get("region")
            if rn is not None:
                regfx_acc[rn].setdefault(t, []).append(r.get("effect_bundle"))
        elif k == "char" and r.get("faction") == player and r.get("char_cqi") is not None:
            char_acc[r["char_cqi"]][t] = r
        elif k == "force" and r.get("faction") == player and r.get("mf_cqi") is not None:
            force_acc[r["mf_cqi"]][t] = r
        elif k == "unit" and r.get("faction") == player and r.get("mf_cqi") is not None:
            units_acc[r["mf_cqi"]].setdefault(t, []).append(
                {"category": r.get("category"), "health": r.get("health"),
                 "men": r.get("men"), "max_men": r.get("max_men")})
        elif k == "pooled" and r.get("faction") == player:
            pooled_acc[r["faction"]].setdefault(t, {})[r.get("resource")] = r.get("value")
        elif k == "active_ritual" and r.get("faction") == player:
            ritual_acc[r["faction"]].setdefault(t, []).append(r.get("ritual_key"))
        elif k == "tech_count" and r.get("faction") == player:
            tech_acc[r["faction"]][t] = r.get("completed")
        elif k == "diplo" and r.get("faction") == player and r.get("toward"):
            # per-turn snapshot of the player's relationship with each faction -> diplomacy state-diff
            diplo_acc[r["toward"]][r.get("turn")] = {fl: r.get(fl) for fl in _DIPLO_FLAGS}
        elif k == "event":
            ev = r.get("event")
            if ev == "ResearchCompleted" and r.get("faction") == player and r.get("tech"):
                completed_techs[player].append((t, r["tech"]))
            elif ev == "CharacterSkillPointAllocated" and r.get("char_cqi") is not None and r.get("skill"):
                taken_skills[r["char_cqi"]].append((t, r["skill"]))
    for lst in completed_techs.values():
        lst.sort()
    for lst in taken_skills.values():
        lst.sort()
    order = sorted(range(len(gts)), key=lambda i: gts[i])
    idx = {"region_ts": _finalize(region_acc), "slots": _finalize(slot_acc),
           "provpool": _finalize(provpool_acc), "regfx": _finalize(regfx_acc),
           "char_ts": _finalize(char_acc), "force_ts": _finalize(force_acc),
           "units": _finalize(units_acc), "pooled": _finalize(pooled_acc),
           "ritual_ts": _finalize(ritual_acc), "tech_ts": _finalize(tech_acc),
           "completed_techs": dict(completed_techs), "taken_skills": dict(taken_skills),
           "prov_regions": {p: sorted(rs) for p, rs in prov_regions.items()},
           "region_prov": region_prov, "player": player,
           "diplo": {tw: sorted(d.items()) for tw, d in diplo_acc.items()}}   # toward -> [(turn, flags)]
    return [gts[i] for i in order], [turns[i] for i in order], ctx, idx


def _diplo_became_true(idx, toward, turn, flag):
    """True if the player's `flag` relationship toward `toward` flipped False->True at/around `turn`
    (compared to the last snapshot strictly before it) -- the state-diff that CORROBORATES a sent deal
    clause actually landing. Tolerant of the send resolving on the same or the following turn."""
    if not flag or turn is None:
        return False
    tl = (idx.get("diplo") or {}).get(toward)
    if not tl:
        return False
    turns = [t for t, _ in tl]
    hi = bisect.bisect_right(turns, (turn or 0) + 1) - 1     # newest snapshot up to turn+1
    if hi < 0:
        return False
    after = tl[hi][1].get(flag)
    # last snapshot strictly before this turn (the "before" side of the diff)
    lo = bisect.bisect_left(turns, turn) - 1
    before = tl[lo][1].get(flag) if lo >= 0 else None
    return bool(after) and not bool(before)


# ------------------------------------------------------------------ per-decision context JOIN ----
# Each helper reads the sub-records the featurizer needs at the decision's game-time and returns a
# COMPACT nested dict attached under a named context key (context["region"], context["army"], ...).
# split_columns only featurizes ctx_*/opt_* keys, so these nested dicts are inert until features.py
# reads them -- keeping the join (here) and the featurize (features.py) cleanly separated.
_REGION_KEEP = ("region", "owner", "owner_is_human", "public_order", "growth_per_turn", "gdp",
                "num_buildings", "province", "is_capital", "is_abandoned", "climate", "garrison_str",
                "active_edict", "selected_edict")
_FORCE_KEEP = ("mf_cqi", "stance", "region", "region_garrison", "is_army", "is_navy", "is_horde",
               "units", "strength", "morale", "upkeep", "mp_pct")
_CHAR_KEEP = ("char_cqi", "subtype", "type", "rank", "wounded", "loyalty", "is_governor",
              "faction_leader", "region", "in_settlement")


def _trim(row, keep):
    return {k: row.get(k) for k in keep} if row else None


def _corruption(idx, province, gt):
    """{resource: value} for a province at gt + derived max/dominant. CORRUPTION lives in
    province_pooled (region.corruption is null on this patch); JOIN is region.province==province."""
    d = _lookup(idx, "provpool", province, gt)
    if not d:
        return None
    vals = {res: (v if v is not None else 0) for res, (v, _mx) in d.items()}
    corr = {res: v for res, v in vals.items() if "corruption" in (res or "")}
    if not corr:
        return {"values": vals, "max": 0, "dominant": None}
    dom = max(corr, key=lambda r: corr[r])
    return {"values": corr, "max": max(corr.values()),
            "dominant": (dom.split("corruption_")[-1] if corr[dom] > 0 else None)}


def _prov_regions_owned(idx, province, gt):
    """(owned_fraction, is_complete, owned, total) -- of every sibling region in the province, how many
    the player owns right now (from the FULL turn-start scrape carried in region_ts, all factions)."""
    regs = idx["prov_regions"].get(province) or []
    if not regs:
        return (None, None, 0, 0)
    player = idx["player"]; owned = total = 0
    for rn in regs:
        rr = _lookup(idx, "region_ts", rn, gt)
        if not rr:
            continue
        total += 1
        if rr.get("owner") == player:
            owned += 1
    if not total:
        return (None, None, 0, 0)
    return (owned / total, owned == total, owned, total)


def _region_buildings(idx, region, gt):
    """List of building-level keys present in a region's slots at gt (for chain-level / free-slot)."""
    slots = _lookup(idx, "slots", region, gt) or {}
    return [s.get("building") for s in slots.values() if s.get("has_building") and s.get("building")]


def _free_slots(idx, region, gt):
    slots = _lookup(idx, "slots", region, gt)
    if not slots:
        return None
    return sum(1 for s in slots.values() if s.get("has_building") is False)


def _climate_suit(idx, region, gt):
    """+1 if a *_climate_suitable effect bundle is active on the region, -1 if *_unsuitable, else 0."""
    bundles = _lookup(idx, "regfx", region, gt) or []
    suit = any("climate_suitable" in (b or "") for b in bundles)
    unsuit = any("climate_unsuitable" in (b or "") for b in bundles)
    return 1 if suit else -1 if unsuit else 0


def _force_at_region(idx, region, gt):
    """The player force garrisoned/standing at `region` at gt (prefer a real army with most units)."""
    best = None
    for mf, entry in idx["force_ts"].items():
        fr = _at(entry, gt)
        if not fr:
            continue
        if fr.get("region") == region or fr.get("region_garrison") == region:
            score = (0 if fr.get("is_army") else 1, -(fr.get("units") or 0))
            if best is None or score < best[0]:
                best = (score, fr)
    return best[1] if best else None


def _recent_region(sel, sel_t, gt, idx):
    """Nearest SettlementSelected/CharacterSelected BEFORE gt that yields a region (best-effort)."""
    i = bisect.bisect_right(sel_t, gt) - 1
    while i >= 0:
        _t, reg, cq = sel[i]
        if reg:
            return reg
        if cq is not None:
            cr = _lookup(idx, "char_ts", cq, gt)
            if cr and cr.get("region"):
                return cr["region"]
        i -= 1
    return None


def _completed_tiers(idx, faction, gt):
    """Tiers of every tech the player has completed at/before gt (for research tier_gap)."""
    lst = idx["completed_techs"].get(faction) or []
    cut = bisect.bisect_right([t for t, _ in lst], gt)
    tiers = []
    for _t, tk in lst[:cut]:
        tf = _FDB.tech_features(tk) if _FDB else {}
        if tf and tf.get("tier") is not None:
            tiers.append(tf["tier"])
    return tiers


def _taken_skill_count(idx, char_cqi, gt):
    lst = idx["taken_skills"].get(char_cqi) or []
    return bisect.bisect_right([t for t, _ in lst], gt)


def _build_selection_events(events):
    """Sorted (game-time, region_or_None, char_cqi_or_None) from SettlementSelected/CharacterSelected
    -- the recorder's witnesses of WHICH settlement/character the player was on (recruit/build region).
    Event `t` is already game-time (same clock as a decision's gt)."""
    sel = []
    for e in events:
        ev = e.get("event")
        if ev == "SettlementSelected" and e.get("garrison"):
            sel.append((e.get("t", 0.0), e["garrison"], None))
        elif ev == "CharacterSelected" and e.get("char_cqi") is not None:
            sel.append((e.get("t", 0.0), None, e["char_cqi"]))
    sel.sort()
    return sel, [x[0] for x in sel]


def _enrich_context(d, idx, sel, sel_t):
    """Attach the relevant sub-records into d['context'] (a fresh copy of the faction row, so the
    per-turn record shared across a turn's decisions is never mutated). Which records are relevant is
    per decision TYPE (Part A). Missing sources leave the nested key absent -> featurizer defaults."""
    gt = d.get("gt")
    base = dict(d.get("context") or {})
    dtype = d["type"]
    if dtype in ("building", "occupation", "edict"):
        region = d.get("chosen_region")
        if not region and dtype == "building":
            region = _recent_region(sel, sel_t, gt, idx)
        if region:
            rr = _lookup(idx, "region_ts", region, gt)
            base["region"] = _trim(rr, _REGION_KEEP)
            prov = (rr or {}).get("province") or idx["region_prov"].get(region)
            if prov:
                base["prov_corruption"] = _corruption(idx, prov, gt)
                frac, complete, owned, total = _prov_regions_owned(idx, prov, gt)
                base["prov_owned_fraction"] = frac
                base["prov_is_complete"] = complete
                base["prov_owned_count"] = owned
                base["prov_region_count"] = total
            base["climate_suit"] = _climate_suit(idx, region, gt)
            if dtype == "building":
                base["region_buildings"] = _region_buildings(idx, region, gt)
                base["region_free_slots"] = _free_slots(idx, region, gt)
    elif dtype == "recruit":
        region = _recent_region(sel, sel_t, gt, idx)
        if region:
            fr = _force_at_region(idx, region, gt)
            if fr:
                base["army"] = _trim(fr, _FORCE_KEEP)
                base["army_units"] = _lookup(idx, "units", fr.get("mf_cqi"), gt) or []
    elif dtype == "stance":
        mf = d.get("_mf_cqi")
        fr = _lookup(idx, "force_ts", mf, gt)
        if fr:
            base["army"] = _trim(fr, _FORCE_KEEP)
            base["army_units"] = _lookup(idx, "units", mf, gt) or []
            reg = fr.get("region") or fr.get("region_garrison")
            if reg:
                rr = _lookup(idx, "region_ts", reg, gt)
                base["region"] = _trim(rr, _REGION_KEEP)
    elif dtype == "skills":
        cq = d.get("_char_cqi")
        cr = _lookup(idx, "char_ts", cq, gt)
        if cr:
            base["char"] = _trim(cr, _CHAR_KEEP)
        base["char_taken_skills"] = _taken_skill_count(idx, cq, gt) if cq is not None else 0
    elif dtype == "items":
        # the character the item is equipped on/removed from (skills-style char block) + the faction
        # pool (influence etc., rites-style) -- the state a value model reads to judge an item choice.
        cq = d.get("_char_cqi")
        cr = _lookup(idx, "char_ts", cq, gt) if cq is not None else None
        if cr:
            base["char"] = _trim(cr, _CHAR_KEEP)
        base["pooled"] = _lookup(idx, "pooled", idx["player"], gt) or {}
        base["equipped_count"] = sum(1 for o in (d.get("options") or []) if o.get("source") == "equipped")
        base["available_count"] = sum(1 for o in (d.get("options") or []) if o.get("source") == "available")
    elif dtype in ("diplomatic_target", "diplomatic_deal"):
        # the counterparty + the player's treasury (deals cost money) + the CURRENT relationship snapshot
        # toward the counterparty (the state a value model reads to judge a diplomatic move).
        base["pooled"] = _lookup(idx, "pooled", idx["player"], gt) or {}
        cp = d.get("_counterparty")
        base["counterparty"] = cp
        tl = (idx.get("diplo") or {}).get(cp) if cp else None
        if tl:
            turn = d.get("turn")
            hi = bisect.bisect_right([t for t, _ in tl], (turn if turn is not None else 0)) - 1
            if hi >= 0:
                base["diplo_state"] = tl[hi][1]
    elif dtype in ("research", "rites", "captives"):
        player = idx["player"]
        base["pooled"] = _lookup(idx, "pooled", player, gt) or {}
        if dtype == "research":
            base["completed_count"] = _lookup(idx, "tech_ts", player, gt) or 0
            base["completed_tech_tiers"] = _completed_tiers(idx, player, gt)
        elif dtype == "rites":
            base["active_rituals"] = _lookup(idx, "ritual_ts", player, gt) or []
        elif dtype == "captives":
            cq = d.get("_char_cqi")
            cr = _lookup(idx, "char_ts", cq, gt) if cq is not None else None
            reg = (cr or {}).get("region")
            if reg:
                rr = _lookup(idx, "region_ts", reg, gt)
                base["region"] = _trim(rr, _REGION_KEEP)
                prov = (rr or {}).get("province")
                if prov:
                    base["prov_corruption"] = _corruption(idx, prov, gt)
    d["context"] = base


def _collect_choices(events):
    """The player's actual choices, each as (dtype, key, gt, meta). meta is None except where a winner
    method carries extra fields (building region/slot). Filters that separate a real player pick from
    engine auto-fire:
      skills   -- drop a skill point in the same game-second as a CharacterRankUp for that char (auto),
                  and drop the pseudo-skill families in SKILL_DROP (never a panel option).
      recruit  -- collapse the duplicate log lines emitted for ONE physical `_recruitable` click
                  (same normalized unit within 1.2s) so the count is real distinct recruits, not
                  raw log spam. Two genuine recruits of the same unit (e.g. from both pools) are
                  >1.2s apart and are kept.
    BUILDING is intentionally NOT collected here: its chosen building comes from the winning in-log
    click-path resolver (resolve_construction_choices), injected in build_decisions."""
    rankups = {(e.get("char_cqi"), round(e.get("t", 0), 1))
               for e in events if e.get("event") == "CharacterRankUp"}
    out = []
    last_recruit = None                              # (norm_unit, t) for dedup of one physical click
    stance_seen = set()                              # (mf_cqi, turn, code) -- one stance pick per force/turn
    char_seen = set()                                # (char_cqi, t~) -- Recruited+Created fire together
    for e in events:
        ev, t = e.get("event"), e.get("t")
        if ev == "ResearchStarted" and e.get("tech"):
            out.append(("research", e["tech"], t, None))
        elif ev == "CharacterSkillPointAllocated" and e.get("skill"):
            sk = e["skill"]
            if any(d in sk for d in SKILL_DROP):
                continue
            if (e.get("char_cqi"), round(t, 1)) in rankups:
                continue
            out.append(("skills", sk, t, {"char_cqi": e.get("char_cqi")}))
        elif ev == "CharacterPerformsSettlementOccupationDecision" and e.get("occupation_decision") is not None:
            # `garrison` carries the target region KEY (settlement is enemy-owned pre-decision)
            out.append(("occupation", str(e["occupation_decision"]), t, {"region": e.get("garrison")}))
        elif ev == "ComponentLClickUp" and (e.get("component") or "").endswith("_recruitable"):
            key = structurer._strip_unit(e["component"])
            nu = _norm_unit(key)
            if last_recruit and last_recruit[0] == nu and t is not None and t - last_recruit[1] < 1.2:
                last_recruit = (nu, t)               # extend the burst; still one physical click
                continue
            last_recruit = (nu, t)
            out.append(("recruit", key, t, None))
        elif ev == "CharacterPostBattleCaptureOption" and e.get("captive_outcome_key"):
            # captive_outcome_key (kill/enslave/release) is the universal MODEL key; captive_record_key
            # is the numeric id that joins to the faction-correct DISPLAY name (e.g. Slaanesh 'Devour
            # Captives') via campaign_post_battle_captive_options_onscreen_name_<id>.
            out.append(("captives", e["captive_outcome_key"], t,
                        {"char_cqi": e.get("char_cqi"),
                         "captive_record_key": e.get("captive_record_key")}))
        elif ev == "RitualStartedEvent" and e.get("ritual_rkey"):
            out.append(("rites", e["ritual_rkey"], t, {"char_cqi": e.get("char_cqi")}))
        elif ev == "ForceAdoptsStance" and e.get("mf_cqi") is not None and e.get("stance") is not None:
            code = e.get("stance")
            dk = (e.get("mf_cqi"), e.get("turn"), code)
            if dk in stance_seen:
                continue
            stance_seen.add(dk)
            out.append(("stance", STANCE_CODE.get(code, str(code)), t,
                        {"mf_cqi": e.get("mf_cqi"), "code": code}))
        elif ev == "ComponentLClickUp" and (e.get("component") or "") in PREBATTLE_BUTTONS:
            # pre_battle: the clicked pre-battle button IS the chosen; anchored (containment-gated) to
            # the pre_battle open. component == the option key exactly (button_autoresolve, ...).
            out.append(("pre_battle", e["component"], t, None))
        elif ev == "ComponentLClickUp" and (e.get("component") or "").startswith("btn_dance_"):
            # eternal_dance (Slaanesh): btn_dance_<theme> clicked before button_perform is the chosen.
            out.append(("eternal_dance", e["component"], t, None))
        elif ev in ("CharacterRecruited", "CharacterCreated") and e.get("char_subtype"):
            # recruit_lord/hero: the recruited character's subtype IS the chosen (the pool candidate's
            # cqi does not survive to the recruited cqi, so this is unlinked -- resolved from the event).
            # Recruited+Created fire together for one physical recruit -> dedup by (cqi, ~second).
            cq = e.get("char_cqi")
            dk = (cq, round(t or 0.0, 1))
            if dk in char_seen:
                continue
            char_seen.add(dk)
            out.append(("recruit_lord", e["char_subtype"], t, {"char_cqi": cq}))
    return out


def _norm_unit(k):
    return re.sub(r"_\d+$", "", k or "")


def _digits(s):
    """Trailing run of digits, e.g. 'option 1063'->'1063', '1063'->'1063'. None if none."""
    m = re.findall(r"\d+", str(s) if s is not None else "")
    return m[-1] if m else None


def _link(dtype, key, opts):
    """Link a chosen key to a shown option. Returns (option-identity-shown, linked_bool).

    occupation: the event's `occupation_decision` IS the numeric component-id of the button the
    player clicked; it equals exactly one option's `id`. Compared on digits so a stray 'option 1063'
    rendering (id) still matches the raw '1063' (key)."""
    if dtype == "occupation":
        dk = _digits(key)
        if dk is not None:
            for op in opts:
                if _digits(op.get("id")) == dk:
                    return op.get("id"), True
        return key, False
    for op in opts:
        k = op.get("key")
        if dtype == "recruit" and _norm_unit(k) == _norm_unit(key):
            return k, True
        if dtype == "captives" and key and (key in (k or "")):
            return k, True
        if dtype in ("research", "skills", "rites", "building", "stance", "edict", "items",
                     "pre_battle", "eternal_dance") and k == key:
            return k, True
    return key, False


def _pool_map(run_dir):
    """(campaign_faction, turn, norm_unit) -> [(click_t_recorder, pool_source), ...]. From the WINNING
    recruit-pool method (Y-band): the panel shows global on the upper card row, local on the lower, and
    the recruit click's screen-Y says which pool the SAME unit was recruited from. click_t is on the
    recorder clock; each entry is matched (and consumed) to the recruit choice nearest it in time, so a
    unit recruited from BOTH pools in one turn labels each instance correctly even if one instance's
    recruitment panel was never scraped."""
    m = defaultdict(list)
    for pr in resolve_recruit_pools(run_dir):
        if pr.get("source"):
            m[(pr["campaign"], pr["turn"], pr["unit"])].append((pr.get("click_t") or 0.0, pr["source"]))
    for k in m:
        m[k].sort()
    return m


def _take_pool(pool_map, key, want_rt):
    """Nearest-in-time (recorder clock) pool source for a recruit instance; consumed so it is used once."""
    lst = pool_map.get(key)
    if not lst:
        return None
    i = min(range(len(lst)), key=lambda j: abs(lst[j][0] - want_rt))
    return lst.pop(i)[1]


# ---- passive secondary indicators for the reconciliation layer (bus-free: state-diff + clicks) ----
def _stance_state_diffs(idx):
    """{(mf_cqi, turn): stance_suffix} for every point a PLAYER force's `stance` state CHANGES vs its
    previous per-turn scrape -- the STATE-DIFF witness of an army-stance decision (independent of the
    ForceAdoptsStance event)."""
    out = {}
    for mf, (ts, rows) in idx["force_ts"].items():
        prev = None
        for row in rows:
            st = (row.get("stance") or "")
            suf = st.replace(_STANCE_PREFIX, "") if st else None
            if suf and suf != prev:
                out[(mf, row.get("turn"))] = suf
                prev = suf
            elif suf:
                prev = suf
    return out


def _stance_clicks(events):
    """Sorted [(gt, stance_suffix)] from every button_MILITARY_FORCE_ACTIVE_STANCE_TYPE_* click
    (the CLICK witness of a stance pick)."""
    out = []
    tag = "button_" + _STANCE_PREFIX
    for e in events:
        if e.get("event") == "ComponentLClickUp":
            c = e.get("component") or ""
            if c.startswith(tag):
                out.append((e.get("t") or 0.0, c[len(tag):]))
    out.sort()
    return out


def _rites_clicks(events):
    """Sorted [(gt, ritual_rkey)] from ritual card clicks (CcoCampaignRitual<n><rkey>) -- the CLICK
    witness of a rite pick. The rkey is the trailing `wh..._ritual_...` token embedded in the id."""
    out = []
    rx = re.compile(r"(wh[0-9a-z_]*ritual[0-9a-z_]*)")
    for e in events:
        if e.get("event") == "ComponentLClickUp":
            c = e.get("component") or ""
            if c.startswith("CcoCampaignRitual"):
                m = rx.search(c)
                if m:
                    out.append((e.get("t") or 0.0, m.group(1)))
    out.sort()
    return out


def _nearest_within(pairs, gt, tol):
    """The value of the (sorted-by-gt) (gt, value) pair nearest `gt` within +/- tol, else None."""
    if not pairs or gt is None:
        return None
    best = min(pairs, key=lambda p: abs(p[0] - gt))
    return best[1] if abs(best[0] - gt) <= tol else None


def _player_edict_tokens(idx):
    """Race token(s) selecting the player's edict option-set: the faction-key race token PLUS any token
    seen in the player's OWN active/selected edicts (data-driven, covers factions whose edict token
    differs from the faction token). Edict keys embed the token as `_edict_<race>_`."""
    player = idx["player"]
    toks = set()
    parts = (player or "").split("_")
    if len(parts) >= 3:
        toks.add(parts[2])                       # e.g. wh2_main_HEF_nagarythe / wh3_dlc27_SLA_masque
    er = re.compile(r"_edict_([a-z]+)_")
    for rn, (ts, rows) in idx["region_ts"].items():
        for row in rows:
            if row.get("owner") != player:
                continue
            for fld in ("active_edict", "selected_edict"):
                m = er.search(row.get(fld) or "")
                if m:
                    toks.add(m.group(1))
    return toks


def _player_building_tokens(idx):
    """Race token selecting the player's construction option-set: the faction-key race token (segment[2],
    the same `_<race>_` segment building keys carry -- wh2_main_HEF_nagarythe -> 'hef'). Building keys have
    no player-state field to broaden from (unlike edicts' active_edict), so the faction token alone."""
    parts = (idx.get("player") or "").split("_")
    return {parts[2]} if len(parts) >= 3 else set()


DIPLO_CLICK_TOL = 30.0        # sec: match a diplomacy state-flip/click to its send within this window
STANCE_CLICK_TOL = 8.0        # sec: a stance button-click sits within a few s of the ForceAdoptsStance
RITE_CLICK_TOL = 12.0         # sec: a ritual card-click precedes RitualStartedEvent by a few s


def build_decisions(run_dir):
    """Every handled Decision in a run, across campaigns. Chosen is EVENT-ANCHORED (each real player
    choice assigned to the specific menu-open whose option set contains it). Winners wired in:
      building  -- chosen comes from the in-log UI click-path (resolve_construction_choices), injected
                   as choices; replaces the flimsy mouse-rect (2/9 opens, 1 wrong) with 20/20.
      skills    -- SKILL_DROP pseudo-skill filter + a wide symmetric containment window (WINDOWS).
      recruit   -- chosen_source = the pool (local/global) recovered by the Y-band method.
      occupation/captives -- digit-id / substring identity link (already in _link)."""
    logs = sorted(glob.glob(os.path.join(run_dir, "logs", "script_log_*.tail")),
                  key=os.path.getsize, reverse=True)
    rid = os.path.basename(run_dir.rstrip("/\\"))[-6:]
    _t = _pc(); pool_map = _pool_map(run_dir); _T(rid, "recruit_pool", _pc() - _t)
    dip_opens = _diplomacy_menu_opens(run_dir)             # run-level WHO option-set (read once)
    decisions = []
    for log in logs:
        player = structurer.player_faction(log)
        if not player:
            continue
        _t = _pc(); gts, turns, ctx, idx = _turn_index(log, player); _T(rid, "turn_index", _pc() - _t)
        if not gts:
            continue
        _t = _pc(); off = correlation.correlate(run_dir, only_files=[log]).get("offset"); _T(rid, "correlate", _pc() - _t)
        if off is None:
            continue
        gmin, gmax = gts[0], gts[-1]
        cid = "%s.%s" % (rid, player.split("_", 2)[-1][:12])
        # opens belonging to THIS campaign (by game-time span), each a decision (chosen=None for now)
        _t = _pc()
        opens = []
        order_ctr = defaultdict(int)
        for mo in iter_menu_opens(run_dir):
            rt = mo.get("t")
            if rt is None:
                continue
            gt = rt + off
            if not (gmin - 5 <= gt <= gmax + 5):
                continue
            i = bisect.bisect_right(gts, gt) - 1
            turn = turns[i] if i >= 0 else None
            if turn is None:
                continue
            dtype = PANEL_TYPE[mo["panel"]]
            # Types OWNED by a dedicated synthesis/derivation path (NOT the generic menu-open anchoring),
            # or with no usable offline signal -- skipped here so they never emit an unresolvable/leaky
            # browse decision. Each still stays in PANEL_TYPE for LIVE recognition (runtime.watch).
            #   items             -> item_choices.derive (numeric-id options, event adds, no-event removes)
            #   diplomatic_*      -> _diplomacy_decisions (click + diplo state-diff; WHO + WHAT layers); a
            #                        generic diplomacy open would only add an unresolvable browse duplicate
            #   recruit_lord/hero -> synthesized from CharacterRecruited/Created below: the pool candidates
            #                        (general_candidate_N) carry empty text -> unnameable & never link, so
            #                        the recruited char_subtype EVENT is the choice (like stance/rites)
            #   offices           -> no usable chosen (panel scrape = UI holders title/portrait/kick, key
            #                        null; no assignment event anywhere in the corpus). Documented zero-data;
            #                        recognized live, but emits no offline decision (avoids a raw-key table)
            if dtype in ("items", "diplomatic_target", "diplomatic_deal",
                         "recruit_lord", "recruit_hero", "offices"):
                continue
            opts = [{"key": o.get("key"), "id": o.get("id"), "state": o.get("state"),
                     "available": (True if o.get("clickable") is True else
                                   False if o.get("clickable") is False else None),
                     "source": o.get("source"),
                     # the recorder's single captured on-screen label (JOB 1) so the OFFLINE replay path
                     # renders verbatim game text too; None on keyed options / pre-recorder-change data.
                     "onscreen": o.get("onscreen")} for o in (mo.get("options") or [])]
            order_ctr[turn] += 1
            opens.append({"campaign": cid, "turn": turn, "order": order_ctr[turn], "type": dtype,
                          "panel": mo["panel"], "t": rt, "gt": gt, "context": ctx.get(turn),
                          "options": opts, "chosen": None, "chosen_linked": False,
                          "chosen_source": None})
        _T(rid, "menu_opens", _pc() - _t)
        # actual player choices (event-anchored) + the winning building click-path choices injected
        _t = _pc()
        events = structurer.parse_events(log, turn=None, player_only=True)
        choices = _collect_choices(events)
        for bc in resolve_construction_choices(log):
            if bc.get("chosen") and bc.get("gt") is not None:
                choices.append(("building", bc["chosen"], bc["gt"],
                                {"region": bc.get("region"), "slot": bc.get("slot"),
                                 "src": bc.get("source")}))
        _T(rid, "events+constr", _pc() - _t)
        # passive secondary indicators (bus-free) for the reconciliation layer
        stance_sd = _stance_state_diffs(idx)               # (mf_cqi, turn) -> stance suffix (state-diff)
        stance_clk = _stance_clicks(events)                # [(gt, suffix)] stance-button clicks
        rites_clk = _rites_clicks(events)                  # [(gt, rkey)] ritual-card clicks
        edict_opts = _FDB.edict_options(_player_edict_tokens(idx)) if _FDB else []   # DB option-set
        building_opts = _FDB.building_options(_player_building_tokens(idx)) if _FDB else []  # DB option-set
        _t = _pc()
        by_type_opens = defaultdict(list)
        for d in opens:
            by_type_opens[d["type"]].append(d)
        stance_linked = set()                              # id(meta) of stance choices linked to an open
        rites_linked = set()                               # id(meta) of rite choices linked to an open
        building_linked = set()                            # id(meta) of build choices linked to an open
        for dtype, dopens in by_type_opens.items():
            dopens.sort(key=lambda d: d["gt"])
            ogts = [d["gt"] for d in dopens]
            back, fwd = WINDOWS.get(dtype, DEFAULT_WINDOW)
            for cdt, key, cgt, meta in choices:
                if cdt != dtype or cgt is None:
                    continue
                # candidate opens in [cgt-back, cgt+fwd]; choose the one whose option set actually
                # CONTAINS this choice (identity), tie-broken by nearest in game-time. Robust to the
                # open being captured a little before OR after the choice (enumeration lag + clock slop).
                lo = bisect.bisect_left(ogts, cgt - back)
                hi = bisect.bisect_right(ogts, cgt + fwd)
                best = None
                for j in range(lo, hi):
                    shown, linked = _link(dtype, key, dopens[j]["options"])
                    score = (0 if linked else 1, abs(dopens[j]["gt"] - cgt))
                    if best is None or score < best[0]:
                        best = (score, j, shown, linked)
                if best is None:
                    continue                               # no open near this choice -> not a menu pick
                _, j, shown, linked = best
                d = dopens[j]
                # recruit pool (local/global) for THIS instance: match the pool click nearest this
                # choice in time (choice recorder-time = cgt - off), consumed so each is used once.
                pool = None
                if dtype == "recruit":
                    pool = _take_pool(pool_map, (player, d["turn"], _norm_unit(key)), cgt - off)
                d.setdefault("_choices", []).append((key, shown, linked, pool))   # per-choice ledger
                if dtype == "stance" and linked:
                    stance_linked.add(id(meta))
                if dtype == "rites" and linked:
                    rites_linked.add(id(meta))
                if dtype == "building" and linked:
                    building_linked.add(id(meta))
                if d["chosen_linked"]:                     # decision point already resolved
                    continue
                # An uncontained anchor (linked=False) is only trusted as a chosen when the option-set
                # is known to be INCOMPLETE rather than the WRONG panel. Construction option sets are the
                # settlement's full building tree captured on open, and the click-path proves the chosen
                # card was present -- so an uncontained building anchor means a DIFFERENT settlement's
                # open grabbed by time (misattribution): keep that open a browse. Research/skills panels
                # can be genuinely partial (an un-swept tech tab), so their nearest-open fallback stands.
                # pre_battle joins building here: its button option-set is complete (all battle buttons are
                # captured on open), so an uncontained click (e.g. a dialog button_ok that is not one of THIS
                # battle's buttons) is the WRONG panel -> containment-gated only, no nearest-open fallback.
                if linked or (d["chosen"] is None and dtype not in ("building", "pre_battle")):
                    d["chosen"], d["chosen_linked"] = shown, linked
                    if dtype == "recruit":
                        d["chosen_source"] = pool
                    if meta:                               # carry the entity keys the JOIN (Part A) needs
                        if meta.get("region") is not None:
                            d["chosen_region"] = meta["region"]
                        if meta.get("slot") is not None:
                            d["chosen_slot"] = meta["slot"]
                        if meta.get("char_cqi") is not None:
                            d["_char_cqi"] = meta["char_cqi"]
                        if meta.get("mf_cqi") is not None:
                            d["_mf_cqi"] = meta["mf_cqi"]
                        if meta.get("captive_record_key") is not None:
                            d["_captive_record_key"] = meta["captive_record_key"]
                    # DISPLAY name of the chosen option (faction-correct, non-positional): captives
                    # from the numeric record key, occupation from the chosen card id -- both via the
                    # offline loc join. The model keys (chosen) are untouched; this is UI-only.
                    if _FDB is not None:
                        if dtype == "captives" and meta and meta.get("captive_record_key"):
                            d["chosen_name"] = _FDB.captive_label(meta["captive_record_key"])
                        elif dtype == "occupation" and shown is not None:
                            d["chosen_name"] = _FDB.occupation_label(_digits(shown) or shown)
                    # RECONCILIATION indicators for this anchored choice: the semantic/click witness that
                    # produced it, the menu_open option-set (chosen iff contained), + passive secondaries.
                    src = _CHOICE_SOURCE.get(dtype, "event")
                    if dtype == "building":
                        bsrc = (meta or {}).get("src")
                        src = ("click" if bsrc == "clickpath" else
                               "state_diff" if bsrc == "completed" else "event")
                        _add_ind(d, "event", shown)        # BuildingConstructionIssuedByPlayer attributes it
                    _add_ind(d, src, shown)
                    _add_ind(d, "menu_open", shown if linked else None)
                    if dtype == "stance":
                        mf = (meta or {}).get("mf_cqi")
                        if stance_sd.get((mf, d["turn"])) == shown:
                            _add_ind(d, "state_diff", shown)
                        if _nearest_within(stance_clk, cgt, STANCE_CLICK_TOL) == shown:
                            _add_ind(d, "click", shown)
                    elif dtype == "rites":
                        if _nearest_within(rites_clk, cgt, RITE_CLICK_TOL) == shown:
                            _add_ind(d, "click", shown)
        # PART C: SYNTHESIZE the ex-poll decisions that have no captured menu_open (the recorder's poll
        # is gone). Each carries chosen (from event/state/click) + reconciliation indicators; the option
        # set comes from the hardcoded enum (stances) / DB (edicts) / clicks (diplomacy) -- never a poll.
        # -- army_stances: chosen = ForceAdoptsStance event, corroborated by the force-state stance diff +
        #    the stance-button click; option-set = the full MILITARY_FORCE_ACTIVE_STANCE_TYPE_* universe.
        stance_opts = [{"key": s, "id": _STANCE_PREFIX + s, "state": "candidate", "available": None,
                        "source": "enum"} for s in STANCE_UNIVERSE]
        for cdt, key, cgt, meta in choices:
            if cdt != "stance" or cgt is None or id(meta) in stance_linked:
                continue
            turn = turns[bisect.bisect_right(gts, cgt) - 1] if gts else None
            mf = meta.get("mf_cqi")
            d = {"campaign": cid, "turn": turn, "order": 0, "type": "stance",
                 "panel": "army_stances", "t": cgt, "gt": cgt, "context": ctx.get(turn),
                 "options": list(stance_opts), "chosen": key, "chosen_linked": True,
                 "chosen_source": None, "_mf_cqi": mf}
            _add_ind(d, "event", key)
            if stance_sd.get((mf, turn)) == key:
                _add_ind(d, "state_diff", key)
            if _nearest_within(stance_clk, cgt, STANCE_CLICK_TOL) == key:
                _add_ind(d, "click", key)
            opens.append(d)
        # -- rites: chosen = RitualStartedEvent.ritual_rkey (performing_faction == player here; the
        #    in_player_turn superset caveat noted in FINDINGS), corroborated by the ritual-card click +
        #    active_ritual state. Option set stays empty unless a great_game_rituals menu_open linked it.
        for cdt, key, cgt, meta in choices:
            if cdt != "rites" or cgt is None or id(meta) in rites_linked:
                continue
            turn = turns[bisect.bisect_right(gts, cgt) - 1] if gts else None
            d = {"campaign": cid, "turn": turn, "order": 0, "type": "rites",
                 "panel": "rites", "t": cgt, "gt": cgt, "context": ctx.get(turn),
                 "options": [], "chosen": key, "chosen_linked": True, "chosen_source": None,
                 "_char_cqi": meta.get("char_cqi")}
            _add_ind(d, "event", key)
            if _nearest_within(rites_clk, cgt, RITE_CLICK_TOL) == key:
                _add_ind(d, "click", key)
            opens.append(d)
        # -- recruit_lord: chosen = CharacterRecruited/Created char_subtype (the recruited character's
        #    subtype IS the choice -- a semantic EVENT ground truth, exactly like stance/rites). The pool
        #    candidates (general_candidate_N) carry no readable identity and never link, so the option set
        #    is the chosen subtype itself; it resolves to a readable agent name (agent_subtypes loc) at
        #    display. One decision per recruit event -> recruit_lord finally trains (was 0 linked rows: the
        #    subtype never lands in a general_candidate_N option set, so anchoring alone can never link it).
        for cdt, key, cgt, meta in choices:
            if cdt != "recruit_lord" or cgt is None:
                continue
            turn = turns[bisect.bisect_right(gts, cgt) - 1] if gts else None
            d = {"campaign": cid, "turn": turn, "order": 0, "type": "recruit_lord",
                 "panel": "recruit_panel", "t": cgt, "gt": cgt, "context": ctx.get(turn),
                 "options": [{"key": key, "id": None, "state": "recruited", "available": True,
                              "source": "event", "type": key}],
                 "chosen": key, "chosen_linked": True, "chosen_source": None,
                 "_char_cqi": (meta or {}).get("char_cqi")}
            _add_ind(d, "event", key)
            opens.append(d)
        # -- eternal_dance (Slaanesh): chosen = btn_dance_<theme> click (the CLICK witness = the performed
        #    dance). No dance menu_open is captured, so the option set is the chosen theme itself; it
        #    resolves to a readable "Dance of <Theme>" at display.
        for cdt, key, cgt, meta in choices:
            if cdt != "eternal_dance" or cgt is None:
                continue
            turn = turns[bisect.bisect_right(gts, cgt) - 1] if gts else None
            d = {"campaign": cid, "turn": turn, "order": 0, "type": "eternal_dance",
                 "panel": "eternal_dance", "t": cgt, "gt": cgt, "context": ctx.get(turn),
                 "options": [{"key": key, "id": None, "state": "danced", "available": True,
                              "source": "click"}],
                 "chosen": key, "chosen_linked": True, "chosen_source": None}
            _add_ind(d, "click", key)
            opens.append(d)
        # -- construction: chosen = the build.construct click-path / BuildingCompleted (from
        #    resolve_construction_choices) + the BuildingConstructionIssuedByPlayer event. Synthesize the
        #    ones that did NOT link to a construction menu_open (the live build browser is transient,
        #    poll-only, visible-only-descent + 500-node capped -> unreliable to scrape). Option-set =
        #    the DB building universe (race-token filtered), MIRRORING edicts: one representative card per
        #    building CHAIN, the chosen (from the click-path) always forced present. Combined streams:
        #    event/click chosen + DB option-set + region context. A poll-captured construction open (when
        #    the browser IS visible) still wins via the anchor loop above -> this is the reliable fallback.
        b_opt_keys = {k for k, _lab in building_opts}
        b_base_opts = [{"key": k, "id": k, "state": "candidate", "available": None, "source": "db",
                        "label": lab} for k, lab in building_opts]
        for cdt, key, cgt, meta in choices:
            if cdt != "building" or cgt is None or id(meta) in building_linked:
                continue
            turn = turns[bisect.bisect_right(gts, cgt) - 1] if gts else None
            bsrc = (meta or {}).get("src")
            opts = list(b_base_opts)
            if key and key not in b_opt_keys:                  # force the chosen card in (upgrade rung /
                opts.append({"key": key, "id": key, "state": "candidate", "available": None,   # off-universe)
                             "source": "db", "label": (_FDB.label(key) if _FDB else None) or key})
            d = {"campaign": cid, "turn": turn, "order": 0, "type": "building",
                 "panel": "construction", "t": cgt, "gt": cgt, "context": ctx.get(turn),
                 "options": opts, "chosen": key, "chosen_linked": bool(key), "chosen_source": None,
                 "chosen_region": meta.get("region"), "chosen_slot": meta.get("slot")}
            if _FDB and key:
                nm = _FDB.building_label((_FDB.building_features(key) or {}).get("building_chain") or "")
                if nm:
                    d["chosen_name"] = nm
            _add_ind(d, "event", key)                       # BuildingConstructionIssuedByPlayer / Completed
            _add_ind(d, "click" if bsrc == "clickpath" else "state_diff" if bsrc == "completed" else "event", key)
            if key in b_opt_keys:
                _add_ind(d, "db", key)                       # DB option-set contains chosen
            opens.append(d)
        # -- edicts: chosen = region-state active_edict diff; option-set from the DB (race-token filtered).
        opens.extend(_edict_decisions(idx, cid, ctx, edict_opts))
        # -- diplomacy: the two layers (WHO target + WHAT deal) from clicks + diplo state-diff.
        opens.extend(_diplomacy_decisions(events, idx, cid, ctx, gts, turns, off, dip_opens))
        # ITEM (ancillary) decisions -- derived end-to-end: adds from CharacterAncillaryGained (real
        # key), removes from the equipped scrape-diff + the repeat-gain inference, option sets from the
        # nearest pool/equipped snapshot (numeric ids resolved to real keys). Owns its own resolution;
        # not routed through the anchoring above (which is why "items" is skipped in the menu-open loop).
        item_opens = item_choices.derive(run_dir, events, off, gts, turns, player, cid, ctx,
                                          gmin=gmin, gmax=gmax)
        for d in item_opens:                               # tag item witnesses for the reconciliation layer
            ch = d.get("chosen")
            act = d.get("action")
            if act in ("add", "swap"):
                _add_ind(d, "event", ch)                    # add = CharacterAncillaryGained (event key)
            if act in ("remove", "swap"):
                _add_ind(d, "state_diff", ch)               # remove = equipped-scrape diff (no event)
            if d.get("chosen_linked"):
                _add_ind(d, "menu_open", ch)                # the pool/equipped snapshot contained it
        opens.extend(item_opens)
        # PART A: per-decision context JOIN (attach the relevant sub-records into each context) +
        # RECONCILIATION: fuse each decision's indicators -> chosen_sources + conflict (degrades to the
        # single available witness, so a decision with only one indicator still resolves).
        sel, sel_t = _build_selection_events(events)
        for d in opens:
            _enrich_context(d, idx, sel, sel_t)
            _reconcile(d)
        _T(rid, "anchor", _pc() - _t)
        decisions.extend(opens)
    return decisions


def _edict_decisions(idx, cid, ctx, edict_options):
    """Edict decisions SYNTHESIZED from passive streams (the recorder's edict poll is gone):
      chosen     = region STATE `active_edict` -- the point it first appears / changes to a new value
                   (edicts are province-wide, so dedup by (province, edict)).  STATE-DIFF witness.
      corroboration = the region's `selected_edict` when it agrees with active_edict (a 2nd state field).
      option-set = the DB provincial-initiative edicts filtered to the player's race token(s); the chosen
                   key lands in it -> a `db` reconciliation witness (option-set contains chosen).
    """
    player = idx["player"]
    opt_keys = {k for k, _lab in edict_options}
    base_opts = [{"key": k, "id": k, "state": "candidate", "available": None, "source": "db",
                  "label": lab} for k, lab in edict_options]
    out, seen = [], set()
    for rn, entry in idx["region_ts"].items():
        ts, rows = entry
        prev = None
        for t, row in zip(ts, rows):
            ae = row.get("active_edict")
            if ae and ae != prev and row.get("owner") == player:
                prov = row.get("province")
                sk = (prov, ae)
                if sk not in seen:
                    seen.add(sk)
                    turn = row.get("turn")
                    d = {"campaign": cid, "turn": turn, "order": 0, "type": "edict",
                         "panel": "edicts", "t": t, "gt": t, "context": ctx.get(turn),
                         "options": list(base_opts), "chosen": ae, "chosen_linked": ae in opt_keys,
                         "chosen_source": None, "chosen_region": rn}
                    _add_ind(d, "state_diff", ae)                       # active_edict flip
                    if (row.get("selected_edict") or None) == ae:
                        _add_ind(d, "state_selected", ae)              # 2nd state field agrees
                    if ae in opt_keys:
                        _add_ind(d, "db", ae)                          # option-set contains chosen
                    lab = next((l for k, l in edict_options if k == ae), None)
                    if lab:
                        d["chosen_name"] = lab
                    out.append(d)
            prev = ae
    return out


def _diplomacy_menu_opens(run_dir):
    """[(recorder_t, [faction_key,...])] from every `diplomacy` menu_open (the diplomacy_dropdown
    capture-on-open) -- the level-1 WHO option-set (known factions). Read directly because `diplomacy`
    is not a PANEL_TYPE key (its two decision layers are synthesized, not generic-anchored)."""
    p = os.path.join(run_dir, "ui_components.jsonl")
    out = []
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"menu_open"' not in line or '"diplomacy"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue  # intentional: skip malformed ui_components line (hot per-line parse)
            if r.get("kind") == "menu_open" and r.get("panel") == "diplomacy":
                facs = [o.get("key") for o in (r.get("options") or []) if o.get("key")]
                out.append((r.get("t"), facs))
    return out


def _diplomacy_decisions(events, idx, cid, ctx, gts, turns, off, dip_opens):
    """SYNTHESIZE the two diplomacy decision layers from passive CLICKS + diplo STATE-DIFF (no poll):
      diplomatic_deal   (WHAT) -- one per `button_send`: chosen = the staged `diplomatic_option_*`
                          clauses (since the last counterparty select), toward the selected counterparty.
                          CLICK witness; corroborated by a diplo state flip (`_diplo_became_true`).
      diplomatic_target (WHO)  -- the counterparty each deal opens with. CLICK witness; option-set from
                          the nearest `diplomacy` menu_open (the known-factions list); state-diff same.
    `dip_opens` = [(recorder_t, [faction_keys])] for the WHO option-set.
    """
    dip_gt = sorted(((t + off, facs) for t, facs in dip_opens if t is not None), key=lambda x: x[0])

    def _turn_at(gt):
        i = bisect.bisect_right(gts, gt) - 1
        return turns[i] if i >= 0 else None

    def _who_options(gt, counterparty):
        if not dip_gt:
            return [{"key": counterparty, "id": None, "state": "selected", "available": True,
                     "source": "target"}]
        facs = min(dip_gt, key=lambda x: abs(x[0] - gt))[1]
        opts = [{"key": fk, "id": "faction_row_entry_" + fk, "state": "candidate", "available": True,
                 "source": "diplomacy_dropdown"} for fk in facs]
        if counterparty not in facs:
            opts.append({"key": counterparty, "id": None, "state": "selected", "available": True,
                         "source": "target"})
        return opts

    out = []
    counterparty = None
    staged = []
    for e in events:
        if e.get("event") != "ComponentLClickUp":
            continue
        comp = e.get("component") or ""
        gt = e.get("t")
        if comp.startswith("faction_row_entry_"):
            counterparty = comp[len("faction_row_entry_"):]
            staged = []                                  # new target -> restart the clause tally
        elif comp.startswith("diplomatic_option_"):
            staged.append(comp[len("diplomatic_option_"):])
        elif comp == "button_send" and counterparty and staged:
            turn = _turn_at(gt)
            clauses = sorted(set(staged))
            deal_key = "+".join(clauses)
            realized = any(_diplo_became_true(idx, counterparty, turn, CLAUSE_FLAG.get(c))
                           for c in clauses)
            deal = {"campaign": cid, "turn": turn, "order": 0, "type": "diplomatic_deal",
                    "panel": "diplomacy_options", "t": gt, "gt": gt, "context": ctx.get(turn),
                    "options": [{"key": c, "id": "diplomatic_option_" + c, "state": "sent",
                                 "available": True, "source": "sent", "counterparty": counterparty}
                                for c in clauses],
                    "chosen": deal_key, "chosen_linked": True, "chosen_source": None,
                    "_counterparty": counterparty}
            _add_ind(deal, "click", deal_key)
            if realized:
                _add_ind(deal, "state_diff", deal_key)
            out.append(deal)
            tgt = {"campaign": cid, "turn": turn, "order": 0, "type": "diplomatic_target",
                   "panel": "diplomacy_factions", "t": gt, "gt": gt, "context": ctx.get(turn),
                   "options": _who_options(gt, counterparty),
                   "chosen": counterparty, "chosen_linked": True, "chosen_source": None,
                   "_counterparty": counterparty}
            _add_ind(tgt, "click", counterparty)
            if any(o.get("key") == counterparty and o.get("source") == "diplomacy_dropdown"
                   for o in tgt["options"]):
                _add_ind(tgt, "menu_open", counterparty)
            if realized:
                _add_ind(tgt, "state_diff", counterparty)
            out.append(tgt)
            staged = []
    return out


def _print_enriched(d):
    """Dump the joined per-decision context sub-records (Part A) for a sample decision."""
    ctx = d.get("context") or {}
    reg = ctx.get("region"); corr = ctx.get("prov_corruption"); army = ctx.get("army")
    ch = ctx.get("char")
    print("    context: treasury=%s net_income=%s at_war=%s"
          % (ctx.get("treasury"), ctx.get("net_income"), ctx.get("at_war")))
    if reg:
        print("    region : %s  public_order=%s gdp=%s num_buildings=%s free_slots=%s is_capital=%s climate=%s owner_is_human=%s"
              % (reg.get("region"), reg.get("public_order"), reg.get("gdp"), reg.get("num_buildings"),
                 ctx.get("region_free_slots"), reg.get("is_capital"), reg.get("climate"),
                 reg.get("owner_is_human")))
    if ctx.get("prov_owned_fraction") is not None:
        print("    province: owned_fraction=%.2f is_complete=%s (owned %s/%s)  climate_suit=%s"
              % (ctx.get("prov_owned_fraction"), ctx.get("prov_is_complete"),
                 ctx.get("prov_owned_count"), ctx.get("prov_region_count"), ctx.get("climate_suit")))
    if corr:
        print("    corruption: dominant=%s max=%s  values=%s"
              % (corr.get("dominant"), corr.get("max"),
                 {k.split("corruption_")[-1]: v for k, v in (corr.get("values") or {}).items()}))
    if army:
        units = ctx.get("army_units") or []
        mix = {}
        for u in units:
            mix[u.get("category")] = mix.get(u.get("category"), 0) + 1
        hs = [u.get("health") for u in units if isinstance(u.get("health"), (int, float))]
        print("    army   : mf_cqi=%s unit_count=%s strength=%s morale=%s stance=%s"
              % (army.get("mf_cqi"), army.get("units"), army.get("strength"), army.get("morale"),
                 army.get("stance")))
        print("    army_units(%d): category_mix=%s  avg_health=%.0f"
              % (len(units), mix, (sum(hs) / len(hs)) if hs else 0))
    if ch:
        print("    char   : subtype=%s type=%s rank=%s is_governor=%s faction_leader=%s taken_skills=%s"
              % (ch.get("subtype"), ch.get("type"), ch.get("rank"), ch.get("is_governor"),
                 ch.get("faction_leader"), ctx.get("char_taken_skills")))
    if ctx.get("completed_count") is not None:
        print("    research: completed_count=%s completed_tiers=%s queue_idle=%s"
              % (ctx.get("completed_count"), ctx.get("completed_tech_tiers"), ctx.get("research_queue_idle")))
    if ctx.get("pooled") is not None:
        print("    pooled : %s  active_rituals=%s" % (ctx.get("pooled"), ctx.get("active_rituals")))
    if d["type"] == "building":
        rb = ctx.get("region_buildings") or []
        chosen = d.get("chosen")
        chain = (_FDB.building_features(chosen) or {}).get("building_chain") if _FDB and chosen else None
        lvls = [(_FDB.building_features(bk) or {}).get("level") for bk in rb] if _FDB else []
        same = [(bk, (_FDB.building_features(bk) or {}).get("level")) for bk in rb
                if _FDB and (_FDB.building_features(bk) or {}).get("building_chain") == chain]
        cur = max([l for _b, l in same if l is not None], default=-1)
        print("    building: chosen_chain=%s  chain_current_level=%s chain_already_built=%s  region_buildings=%s"
              % (chain, cur, bool(same), rb))


def main():
    run_dir = sys.argv[1]
    ds = build_decisions(run_dir)
    by_type = defaultdict(list)
    for d in ds:
        by_type[d["type"]].append(d)
    print("run: %s   ->  %d decisions" % (run_dir, len(ds)))
    # per-OPEN view (a decision point) + RECONCILIATION view (avg # agreeing indicators, #conflicts).
    print("%-17s %6s %8s | %8s %8s | %8s %8s %8s | %8s %5s"
          % ("type", "opens", "avg_opt", "chosen", "linked", "1ind", ">=2ind", "avg_src",
             "conflict", "src"))
    for t in sorted(by_type):
        dt = by_type[t]
        avg = sum(len(d["options"]) for d in dt) / len(dt)
        ch = sum(1 for d in dt if d["chosen"] is not None)
        lk = sum(1 for d in dt if d["chosen_linked"])
        ns = [len(d.get("chosen_sources") or []) for d in dt if d["chosen"] is not None]
        one = sum(1 for n in ns if n == 1)
        multi = sum(1 for n in ns if n >= 2)
        avgsrc = (sum(ns) / len(ns)) if ns else 0.0
        confl = sum(1 for d in dt if d.get("conflict"))
        srcset = sorted({s for d in dt for s in (d.get("chosen_sources") or [])})
        print("%-17s %6d %8.1f | %8d %8d | %8d %8d %8.2f | %8d %5s"
              % (t, len(dt), avg, ch, lk, one, multi, avgsrc, confl, ",".join(srcset)[:40]))
    tch = sum(1 for d in ds if d["chosen"] is not None)
    tlk = sum(1 for d in ds if d["chosen_linked"])
    tpick = [pl for d in ds for pl in d.get("_choices", [])]
    tnlk = sum(1 for pl in tpick if pl[2])
    print("TOTAL: %d opens, %d opens-with-choice, %d open-linked | %d actual choices anchored, "
          "%d linked (%.0f%%)"
          % (len(ds), tch, tlk, len(tpick), tnlk, 100.0 * tnlk / max(1, len(tpick))))
    for t in sorted(by_type):
        d = next((x for x in by_type[t] if x.get("chosen_sources")), None) \
            or next((x for x in by_type[t] if x["chosen"] is not None), None)
        if not d:
            print("\n--- %s: NO chosen sample (opens: %d) ---" % (t, len(by_type[t])))
            continue
        extra = ""
        if t == "recruit" and d.get("chosen_source"):
            extra = "  pool=%s" % d["chosen_source"]
        elif t == "building" and d.get("chosen_region"):
            extra = "  region=%s slot=%s" % (d["chosen_region"], d.get("chosen_slot"))
        elif t in ("diplomatic_target", "diplomatic_deal"):
            extra = "  counterparty=%s" % d.get("_counterparty")
        print("\n--- %s  campaign %s turn %s  chosen=%r%s ---"
              % (t, d["campaign"], d["turn"], d["chosen"], extra))
        print("    chosen_sources=%s  conflict=%s  indicators=%s"
              % (d.get("chosen_sources"), d.get("conflict"),
                 [(i["source"], i["chosen"]) for i in d.get("_indicators") or []]))
        if d.get("chosen_name"):
            print("    chosen_name=%r" % d["chosen_name"])
        _print_enriched(d)
        for o in d["options"][:6]:
            hit = (o.get("key") == d["chosen"] or str(o.get("id")) == str(d["chosen"]))
            # for recruit the pool disambiguates which of the two same-key rows is the real one
            if hit and t == "recruit" and d.get("chosen_source") and o.get("source") != d["chosen_source"]:
                hit = False
            mark = " <== CHOSEN" if hit else ""
            print("    %-40s state=%-9s avail=%s src=%s%s"
                  % (str(o["key"])[:40], o["state"], o["available"], o["source"], mark))


if __name__ == "__main__":
    main()
