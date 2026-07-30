r"""cco_actions.py -- v7 EXECUTION core: the confirmed-action engine, the action REGISTRY, and
every CCO-LED executor. Click-based executors live in click_actions.py and register into the
same REGISTRY (cco is always tried first; a click executor exists only where cco failed the
verification protocol -- see verify_cco_commands.py verdicts).

THE RULE THAT MAKES v7 DATA TRUSTWORTHY (the "how do we know a move failed" answer):
every action runs   snapshot(before) -> gates -> execute -> poll confirm(after) -> record
and `taken = executed AND confirmed`. An API "ok"/"clicked" is recorded but trusted for
NOTHING -- commands report success even when the engine refuses (verified: Construct on a
locked slot, stance Activate on an AP-blocked stance). Distinct failure classes are recorded
so the trainer can exclude and the humans can debug:
    forbidden_key | snapshot_failed | pre_check_refused | execute_failed |
    command_silently_refused (no state change by timeout) | executed_unconfirmed (partial)

ENGINE-SAFETY (live-verified 2026-07-29):
  * NEVER pass a Lua cco wrapper as a Call() argument -- HARD-HANGS the engine. All Call
    strings live in the _LUA_* templates below; only str/int keys are ever interpolated.
  * Every per-entry Call goes through the pcall safe-getter g() (zero-return trap).

Spec contract (a dict in REGISTRY, keyed by action_type):
    layer       "cco" | "click"
    snapshot    fn(bus, ctx, pick) -> dict            # before-state; doubles as gate input
    gates       [fn(bus, ctx, pick, before) -> (ok, reason)]
    execute     fn(bus, ctx, pick, before) -> bool    # issued (never trusted)
    confirm     fn(bus, ctx, pick, before) -> (bool, after_dict)
    timeout_s / poll_s                                 # confirm polling
    retryable   bool                                   # False for irreversible (battles, gifts)
    spends_gold bool                                   # engages the treasury-floor gate
    max_per_entity_turn int

ctx  = {"context_kind": "lord"|"settlement"|"campaign", "entity_id": str, ...}
pick = {"action_type": str, "key": str, "params": {...}, "policy": str}
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

TREASURY_FLOOR = 500        # never spend below this (tunable; config.py may override)
try:
    import config as _cfg
    TREASURY_FLOOR = getattr(_cfg, "TREASURY_FLOOR", TREASURY_FLOOR)
except Exception:
    pass

# battle-UI entries are NEVER selectable/executable -- hardcoded at advisor AND executor layer
FORBIDDEN_KEYS = frozenset({"button_attack", "button_spectate"})

# ---------------------------------------------------------------------------- lua templates
# ALL cco Call strings live here (grep-able wrapper-ban invariant: no Call( ever receives a
# Lua local; arguments are inline expression text composed from str/int only).
_G = ("local function g(c,p) local ok,v=pcall(function() return c:Call(p) end);"
      "if ok and v~=nil then return v end return nil end "
      "local function ts(v) return tostring(v) end ")

_LUA_TREASURY = "return cm:get_faction(cm:get_local_faction_name(true)):treasury()"

_LUA_STANCE_STATE = (_G +
    "local mf=cco('CcoCampaignCharacter','%(cqi)s'):Call('MilitaryForceContext');"
    "if not mf then return 'NO-FORCE' end local l=mf:Call('StanceList');"
    "if type(l)~='table' then return 'NO-LIST' end local o={} "
    "for i,v in ipairs(l) do o[#o+1]=ts(g(v,'Key'))..'~'..ts(g(v,'IsActive'))"
    "..'~'..ts(g(v,'CanBeActivated'))..'~'..ts(g(v,'CanAfford')) end "
    "return table.concat(o,'|')")

_LUA_STANCE_ACTIVATE = (_G +
    "local mf=cco('CcoCampaignCharacter','%(cqi)s'):Call('MilitaryForceContext');"
    "if not mf then return 'NO-FORCE' end local l=mf:Call('StanceList');"
    "for i,v in ipairs(l) do if ts(g(v,'Key'))=='%(key)s' then "
    "pcall(function() v:Call('Activate') end); return 'called' end end return 'NOT-IN-LIST'")

_LUA_SLOT_STATE = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s');"
    "if not s then return 'NO-CTX' end local slots=s:Call('BuildingSlotList');"
    "if type(slots)~='table' then return 'NO-SLOTLIST' end "
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "return ts(g(x,'IsBuildingNew')) end end return 'NO-SLOT'")

_LUA_CONSTRUCT = (_G +
    "local s=cco('CcoCampaignSettlement','settlement:%(region)s');"
    "if not s then return 'NO-CTX' end local slots=s:Call('BuildingSlotList');"
    "for i,x in ipairs(slots) do if g(x,'Index')==%(slot)d then "
    "  local p=g(x,'PossibleUpgradeWithoutConversionsList');"
    "  if type(p)~='table' then return 'NO-POSSIBLES' end "
    "  for j=0,#p-1 do "
    "    if ts(g(x,'PossibleUpgradeWithoutConversionsList['..j..'].Key'))=='%(key)s' then "
    "      if g(x,'BuildingRequirementsMet(PossibleUpgradeWithoutConversionsList['..j..'])')~=true then "
    "        return 'REQ-NOT-MET' end "
    "      pcall(function() x:Call('Construct(PossibleUpgradeWithoutConversionsList['..j..'])') end);"
    "      return 'called' end end return 'KEY-NOT-IN-SLOT' end end return 'NO-SLOT'")


def _ev(bus, lua, timeout=15.0):
    """One eval; returns result or None (loud stderr, never an exception -- the engine turns
    None into an explicit refusal so a bus miss can't masquerade as anything else)."""
    try:
        r = bus.send("eval", lua, timeout=timeout) or {}
    except Exception as e:
        sys.stderr.write("cco_actions: bus eval failed -> %s\n" % repr(e)[:120])
        return None
    if r.get("error"):
        sys.stderr.write("cco_actions: lua error -> %s\n" % str(r["error"])[:160])
        return None
    return r.get("result")


def _treasury(bus):
    return _ev(bus, _LUA_TREASURY, timeout=8.0)


# ---------------------------------------------------------------------------- the engine
REGISTRY = {}


def register(action_type, spec):
    spec.setdefault("timeout_s", 6.0)
    spec.setdefault("poll_s", 1.2)
    spec.setdefault("retryable", True)
    spec.setdefault("spends_gold", False)
    spec.setdefault("max_per_entity_turn", 3)
    spec.setdefault("gates", [])
    REGISTRY[action_type] = spec
    return spec


def execute_confirmed(bus, ctx, pick):
    """Run one action through snapshot -> gates -> execute -> poll-confirm. Returns the
    ActionRecord dict (see module docstring). `counted` is the ONLY field that means taken."""
    atype = pick.get("action_type")
    rec = {"ts": time.time(), "context_kind": ctx.get("context_kind"),
           "entity_id": str(ctx.get("entity_id")), "action_type": atype,
           "key": pick.get("key"), "params": pick.get("params") or {},
           "policy": pick.get("policy"), "layer": None, "attempt": pick.get("attempt", 1),
           "executed": False, "confirmed": False, "counted": False, "refusal": None,
           "confirm": None, "gates": {"passed": True, "failed_gate": None}}
    spec = REGISTRY.get(atype)
    if spec is None:
        rec["refusal"] = "unknown_action_type"
        return rec
    rec["layer"] = spec["layer"]
    if pick.get("key") in FORBIDDEN_KEYS:
        rec["refusal"] = "forbidden_key"
        return rec
    if atype == "noop":
        rec.update(executed=True, confirmed=True, counted=True,
                   confirm={"signal": "none", "before": {}, "after": {}, "latency_ms": 0})
        return rec
    try:
        before = spec["snapshot"](bus, ctx, pick)
    except Exception as e:
        rec["refusal"] = "snapshot_failed"
        rec["confirm"] = {"signal": None, "error": repr(e)[:160]}
        return rec
    if before is None:
        rec["refusal"] = "snapshot_failed"
        return rec
    if spec["spends_gold"]:
        cost = (pick.get("params") or {}).get("cost") or 0
        t = before.get("treasury")
        if t is not None and t - cost < TREASURY_FLOOR:
            rec["gates"] = {"passed": False, "failed_gate": "treasury_floor"}
            rec["refusal"] = "pre_check_refused"
            return rec
    for gate in spec["gates"]:
        ok, reason = gate(bus, ctx, pick, before)
        if not ok:
            rec["gates"] = {"passed": False, "failed_gate": reason}
            rec["refusal"] = "pre_check_refused"
            return rec
    t0 = time.time()
    try:
        rec["executed"] = bool(spec["execute"](bus, ctx, pick, before))
    except Exception as e:
        rec["refusal"] = "execute_failed"
        rec["confirm"] = {"signal": None, "error": repr(e)[:160]}
        return rec
    if not rec["executed"]:
        rec["refusal"] = "execute_failed"
        return rec
    deadline = t0 + spec["timeout_s"]
    confirmed, after, polls = False, {}, 0
    while time.time() < deadline:
        time.sleep(spec["poll_s"])
        polls += 1
        try:
            confirmed, after = spec["confirm"](bus, ctx, pick, before)
        except Exception as e:
            after = {"error": repr(e)[:160]}
            confirmed = False
        if confirmed:
            break
    rec["confirmed"] = bool(confirmed)
    rec["counted"] = rec["executed"] and rec["confirmed"]
    rec["confirm"] = {"signal": spec.get("signal"), "before": before, "after": after,
                     "latency_ms": int((time.time() - t0) * 1000), "polls": polls,
                     "timeout_s": spec["timeout_s"]}
    if not rec["counted"]:
        changed = any(after.get(k) != before.get(k) for k in after) if isinstance(after, dict) else False
        rec["refusal"] = "executed_unconfirmed" if changed else "command_silently_refused"
    return rec


# ---------------------------------------------------------------------------- cco: STANCE
def _stances(bus, cqi):
    raw = _ev(bus, _LUA_STANCE_STATE % {"cqi": cqi}, timeout=15.0)
    if raw in (None, "NO-FORCE", "NO-LIST"):
        return None
    out = {}
    for rec in str(raw).split("|"):
        p = rec.split("~")
        if len(p) >= 4:
            out[p[0]] = {"active": p[1] == "true", "can_activate": p[2] == "true",
                         "can_afford": p[3] == "true"}
    return out or None


def _stance_snapshot(bus, ctx, pick):
    st = _stances(bus, ctx["entity_id"])
    if st is None:
        return None
    active = next((k for k, v in st.items() if v["active"]), None)
    tgt = st.get(pick["key"]) or {}
    return {"active_stance": active, "target_in_list": pick["key"] in st,
            "can_activate": tgt.get("can_activate"), "can_afford": tgt.get("can_afford")}


def _stance_gate_whitelist(bus, ctx, pick, before):
    wl = ctx.get("stance_whitelist") or set()
    if pick["key"] not in wl:
        return False, "stance_not_in_legality_whitelist"   # engine sets ILLEGAL stances; never bypass
    return True, None


def _stance_gate_state(bus, ctx, pick, before):
    if not before.get("target_in_list"):
        return False, "stance_not_in_list"
    if before.get("active_stance") == pick["key"]:
        return False, "already_active"
    if not (before.get("can_activate") and before.get("can_afford")):
        return False, "can_activate_or_afford_false"
    return True, None


def _stance_execute(bus, ctx, pick, before):
    return _ev(bus, _LUA_STANCE_ACTIVATE % {"cqi": ctx["entity_id"], "key": pick["key"]}) == "called"


def _stance_confirm(bus, ctx, pick, before):
    st = _stances(bus, ctx["entity_id"]) or {}
    active = next((k for k, v in st.items() if v["active"]), None)
    return active == pick["key"], {"active_stance": active}


register("stance", {
    "layer": "cco", "signal": "is_active_flip",
    "snapshot": _stance_snapshot,
    "gates": [_stance_gate_whitelist, _stance_gate_state],
    "execute": _stance_execute, "confirm": _stance_confirm,
    "timeout_s": 5.0, "poll_s": 1.2, "max_per_entity_turn": 1,
})


# ---------------------------------------------------------------------------- cco: BUILDING
def _building_snapshot(bus, ctx, pick):
    t = _treasury(bus)
    if t is None:
        return None
    slot = (pick.get("params") or {}).get("slot_index")
    if slot is None:
        return None
    is_new = _ev(bus, _LUA_SLOT_STATE % {"region": ctx["entity_id"], "slot": int(slot)})
    return {"treasury": t, "slot_is_building_new": is_new == "true"}


def _building_gate_slot(bus, ctx, pick, before):
    if before.get("slot_is_building_new"):
        return False, "slot_already_building"
    return True, None


def _building_execute(bus, ctx, pick, before):
    slot = int((pick.get("params") or {}).get("slot_index"))
    res = _ev(bus, _LUA_CONSTRUCT % {"region": ctx["entity_id"], "slot": slot,
                                     "key": pick["key"]}, timeout=25.0)
    if res != "called":
        sys.stderr.write("cco_actions: construct %s slot %s %r -> %s\n"
                         % (ctx["entity_id"], slot, pick["key"], res))
        return False
    return True


def _building_confirm(bus, ctx, pick, before):
    slot = int((pick.get("params") or {}).get("slot_index"))
    t1 = _treasury(bus)
    is_new = _ev(bus, _LUA_SLOT_STATE % {"region": ctx["entity_id"], "slot": slot})
    ok = (is_new == "true") and (t1 is not None and before.get("treasury") is not None
                                 and t1 < before["treasury"])
    return ok, {"treasury": t1, "slot_is_building_new": is_new == "true"}


register("building", {
    "layer": "cco", "signal": "treasury_drop_and_is_building_new",
    "snapshot": _building_snapshot,
    "gates": [_building_gate_slot],
    "execute": _building_execute, "confirm": _building_confirm,
    "timeout_s": 6.0, "poll_s": 2.0, "spends_gold": True, "max_per_entity_turn": 2,
})


# ---------------------------------------------------------------------------- noop
register("noop", {"layer": "cco", "signal": "none",
                  "snapshot": lambda bus, ctx, pick: {},
                  "execute": lambda bus, ctx, pick, before: True,
                  "confirm": lambda bus, ctx, pick, before: (True, {})})


if __name__ == "__main__":
    # live smoke: stance flip + revert through the ENGINE (whitelist faked to the known-legal set)
    from bus import Bus
    b = Bus()
    cqi = _ev(b, "local f=cm:get_local_faction(true); return f:faction_leader():command_queue_index()")
    ctx = {"context_kind": "lord", "entity_id": str(int(cqi)),
           "stance_whitelist": {"MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH",
                                "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT"}}
    r1 = execute_confirmed(b, ctx, {"action_type": "stance",
                                    "key": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_MARCH"})
    print("MARCH:", json.dumps({k: r1[k] for k in ("executed", "confirmed", "counted", "refusal")}))
    r2 = execute_confirmed(b, ctx, {"action_type": "stance",
                                    "key": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_DEFAULT"})
    print("DEFAULT:", json.dumps({k: r2[k] for k in ("executed", "confirmed", "counted", "refusal")}))
    r3 = execute_confirmed(b, ctx, {"action_type": "stance",
                                    "key": "MILITARY_FORCE_ACTIVE_STANCE_TYPE_TUNNELING"})
    print("TUNNELING (must refuse):", json.dumps({k: r3[k] for k in ("counted", "refusal")}))
