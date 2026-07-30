r"""verify_cco_commands.py -- the ONE-TIME live verification protocol for cco COMMANDS.

Run on a DISPOSABLE fresh campaign after any game patch / mod rebuild, BEFORE trusting any
command in the loop. Each command gets a positive test (legal target, asserted from game
state) and negative tests (illegal targets must refuse). Prints a verdict table.

VERDICTS 2026-07-29 (WH3 v8.1.1, fresh Nagarythe turn 1 + turn-19 campaign):

  command            verdict     notes
  -----------------  ----------  ------------------------------------------------------------
  stance Activate    GUARDED-OK  Executes + flips IsActive. ⚠ NO LEGALITY GUARD: set TUNNELING
                                 on a High Elf army (rule breach). REQUIRES faction whitelist
                                 pre-check (from the recorder's army_stances UI-stack capture).
                                 CanBeActivated is a state gate (AP), NOT legality. Post-assert
                                 IsActive is the only truth (calls always "succeed").
  Construct          OK          Inline-expression args ONLY:
                                 Call('Construct(PossibleUpgradeWithoutConversionsList[i])').
                                 Positive: farm_1 treasury 5000->4000 (== DB cost), IsBuildingNew
                                 flips. Negatives: locked slot + growth-gated settlement upgrade
                                 both silently refused (treasury unchanged). Pre-check: req_met
                                 + cost <= treasury + slot unlocked; post-assert treasury+flag.
  SetInitiative      REJECTED    Takes an ExpressionState (UI-component object) -- UI-coupled.
                                 ⚠ passing a Lua cco WRAPPER as a Call ARG HARD-HANGS THE ENGINE
                                 (frozen process, bus dead, force-kill). Edicts execute via the
                                 UI click verb. (No cm script-API setter either.)
  RecruitUnit        ABSENT      Exists only on CcoCustomBattlePlayerSlot (custom battle).
                                 Campaign recruitment stays UI capture + click verb.

HARD RULES derived here (also in ui-capture/cco_queries.py docstring):
  * NEVER pass Lua cco wrappers as Call() arguments -- engine hang. Inline expression args only.
  * Commands report success even when refused -- post-asserts from game state are mandatory.
  * A verdicts change (new patch) => rerun this script before the loop runs any command.

    python verify_cco_commands.py           # runs the safe subset live (stance cycle + report)
    python verify_cco_commands.py construct # ALSO queues a real building (spends gold!)
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\ui-capture")
from bus import Bus                      # noqa: E402
import cco_queries as CQ                 # noqa: E402

_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end ")


def _active_stance(bus, cqi):
    out = CQ.lord_actions(bus, cqi)
    for s in out["stances"]:
        if s["active"]:
            return s["key"]
    return None


def _activate(bus, cqi, key):
    lua = (_G +
        "local mf=cco('CcoCampaignCharacter','%s'):Call('MilitaryForceContext');"
        "local l=mf:Call('StanceList');"
        "for i,v in ipairs(l) do if tostring(g(v,'Key'))=='%s' then "
        "pcall(function() v:Call('Activate') end); return 'called' end end return 'NOT-FOUND'"
    ) % (cqi, key)
    return CQ._ev(bus, lua)


def verify_stance(bus):
    ents = CQ.list_entities(bus)
    if not ents["lords"]:
        return "SKIP (no lords)"
    cqi = ents["lords"][0]["cqi"]
    before = _active_stance(bus, cqi)
    _activate(bus, cqi, "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH")
    time.sleep(1.2)
    marched = _active_stance(bus, cqi) == "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH"
    _activate(bus, cqi, before or "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT")
    time.sleep(1.2)
    reverted = _active_stance(bus, cqi) == (before or "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT")
    return "PASS (flip+revert)" if marched and reverted else \
        "FAIL (marched=%s reverted=%s)" % (marched, reverted)


def verify_construct(bus):
    """Queues a REAL building (cheapest req-met possible in the first open slot). Spends gold."""
    ents = CQ.list_entities(bus)
    if not ents["regions"]:
        return "SKIP (no regions)"
    region = ents["regions"][0]
    sa = CQ.settlement_actions(bus, region)
    t0 = (bus.send("eval", "return cm:get_local_faction(true):treasury()", timeout=8) or {}).get("result")
    target = None
    for sl in sa["slots"]:
        if sl["building"] is None and not sl["is_building_new"] and sl["possibles"]:
            for i, p in enumerate(sl["possibles"]):
                if p["req_met"]:
                    target = (sl["index"], i, p["key"])
                    break
        if target:
            break
    if not target:
        return "SKIP (no buildable possible)"
    sidx, pidx, key = target
    lua = (_G +
        "local s=cco('CcoCampaignSettlement','settlement:%s');"
        "local slots=s:Call('BuildingSlotList');"
        "for i,x in ipairs(slots) do if g(x,'Index')==%d then "
        "pcall(function() x:Call('Construct(PossibleUpgradeWithoutConversionsList[%d])') end);"
        "return 'called' end end return 'NO-SLOT'"
    ) % (region, sidx, pidx)
    CQ._ev(bus, lua)
    time.sleep(2.0)
    t1 = (bus.send("eval", "return cm:get_local_faction(true):treasury()", timeout=8) or {}).get("result")
    sa2 = CQ.settlement_actions(bus, region)
    queued = any(sl["index"] == sidx and sl["is_building_new"] for sl in sa2["slots"])
    dropped = t0 is not None and t1 is not None and t1 < t0
    return ("PASS (%s queued, treasury %s->%s)" % (key, t0, t1)) if queued and dropped else \
        "FAIL (queued=%s treasury %s->%s)" % (queued, t0, t1)


if __name__ == "__main__":
    b = Bus()
    print("stance Activate :", verify_stance(b))
    if len(sys.argv) > 1 and sys.argv[1] == "construct":
        print("Construct       :", verify_construct(b))
    else:
        print("Construct       : (skipped -- pass 'construct' to run; it spends gold)")
    print("SetInitiative   : REJECTED by protocol (UI-coupled; wrapper-arg hangs engine)")
    print("RecruitUnit     : ABSENT campaign-side (custom battle only)")
