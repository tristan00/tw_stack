r"""Build the CHOICE-VALUE dataset from historical campaigns (for the CatBoost value model).

One row = one player choice (semantic action). Columns:
  identifiers : campaign, turn
  categorical : faction, subculture, lord (legendary lord)        <- faction/lord-SPECIFIC, kept
  choice      : action_type (generic verb), action_key (detail key)
  state       : the player's START-OF-TURN generic state (faction/lord-AGNOSTIC quantities)
  targets     : value_now, value_ahead, target (= normalized value delta over the next H turns)

The "value" is a normalized gamestate metric = mean of min-max-normalized {income, settlements,
power_score, allies} across ALL campaign-turns (a universal 0..1 scale). target = value(t+H) - value(t)
so the model learns which choices, in which context, IMPROVE the normalized state.

    python value_dataset.py [H] [out.csv]        # H defaults to 3
"""
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structurer                                          # noqa: E402

RUNS_ROOT = "D:/twdata/runs/human"

# generic (faction/lord-agnostic) numeric state features from the player faction record.
# The vassal_*/num_* diplomatic aggregates are NEW twstate.lua fields: absent on old runs, so every
# reader defaults them (.get(...) or 0) -> inert until fresh recordings carry them.
STATE_NUM = ["treasury", "income", "net_income", "expenditure", "regions", "num_provinces",
             "num_complete_provinces", "forces", "chars", "num_generals", "num_allies", "tax_level",
             "trade_value", "trade_value_pct", "rank",
             "num_vassals", "vassal_regions", "vassal_forces", "num_military_allies",
             "num_defensive_allies", "num_at_war", "num_non_aggression", "num_trade_partners"]
STATE_BOOL = ["at_war", "is_researching", "has_rituals"]
VALUE_PARTS = ("income", "settlements", "power_score", "allies", "vassals")


def choice_key(a):
    """A generalized-ish key for the choice detail (kept raw for now; unit->category etc. is a TODO)."""
    at, d = a["action"], (a.get("detail") or "")
    if at == "Recruit unit":
        return d.split(" ")[0]
    if at == "Start research":
        return d
    if at == "Allocate skill point":
        return d.split(" ")[0]
    if at == "Adopt army stance":
        return d.split(",")[-1].strip()
    if at == "Post-battle captive decision":
        return d
    if at == "Settlement occupation decision":
        return d.split("(")[-1].rstrip(")").strip()
    return {"Construct building": "building", "Besiege settlement": "besiege",
            "Battle (autoresolved)": "battle", "Battle": "battle",
            "Move character": "move", "Character targeted action": "agent_action",
            "Character action (friendly target)": "agent_action", "End turn": ""}.get(at, "")


def extract_campaign(log):
    player = structurer.player_faction(log)
    fac_first, power = {}, defaultdict(float)
    ev = defaultdict(list)
    char_sub = {}
    for r in structurer.iter_twstate(log):
        t = r.get("turn")
        if t is None:
            continue
        k = r.get("kind")
        if k == "faction" and r.get("faction") == player:
            fac_first.setdefault(t, r)
        elif k == "force" and r.get("faction") == player:
            s = r.get("strength")
            if s is not None:
                power[t] += s
        elif k == "char" and r.get("faction") == player:
            if r.get("char_cqi") is not None and r.get("subtype"):
                char_sub[r["char_cqi"]] = r["subtype"]
        elif k == "event":
            facs = [v for kk, v in r.items() if "faction" in kk and v]
            if (facs and player in facs) or (not facs and r.get("in_player_turn")):
                ev[t].append(r)
    if not fac_first:
        return None
    ref = next(iter(fac_first.values()))
    lord = char_sub.get(ref.get("leader_cqi"))
    sub = ref.get("subculture")
    per_turn = {}
    for t, rec in fac_first.items():
        per_turn[t] = {"state": rec, "power": power.get(t, 0.0),
                       "actions": structurer._classify_turn(ev.get(t, []))}
    return {"player": player, "lord": lord, "subculture": sub, "turns": per_turn}


def main():
    H = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "exports", "choice_value_H%d.csv" % H)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    # ---- gather every campaign ----
    camps = []
    for name in sorted(os.listdir(RUNS_ROOT)):
        d = os.path.join(RUNS_ROOT, name)
        mp = os.path.join(d, "meta.json")
        if not os.path.isfile(mp):
            continue
        try:
            import json
            v = json.load(open(mp)).get("recorder_version")
        except Exception as e:
            sys.stderr.write("value_dataset: skip %s, meta.json unreadable -> %s\n" % (name, repr(e)[:60]))
            continue
        if v not in ("v5", "v6"):
            continue
        for c in structurer.list_campaigns(d):
            ex = extract_campaign(c["log"])
            if ex:
                ex["id"] = "%s.%s.t%s" % (name[-6:], c["faction"].split("_", 2)[-1][:12], c["max_turn"])
                camps.append(ex)
                print("  extracted %-24s turns=%d" % (ex["id"], len(ex["turns"])), flush=True)

    # ---- normalized value per (campaign,turn): min-max each part globally ----
    def parts(ct):
        s = ct["state"]
        return {"income": s.get("income") or 0, "settlements": s.get("regions") or 0,
                "power_score": ct["power"], "allies": s.get("num_allies") or 0,
                "vassals": s.get("num_vassals") or 0}
    allpts = [parts(ct) for c in camps for ct in c["turns"].values()]
    rng = {p: (min(x[p] for x in allpts), max(x[p] for x in allpts)) for p in VALUE_PARTS}

    def value(ct):
        pv = parts(ct)
        vals = []
        for p in VALUE_PARTS:
            lo, hi = rng[p]
            vals.append((pv[p] - lo) / (hi - lo) if hi > lo else 0.0)
        return sum(vals) / len(vals)

    for c in camps:
        c["value"] = {t: value(ct) for t, ct in c["turns"].items()}

    # ---- rows: one per choice. Both targets are FUTURE:
    #   target      = value(t+H) - value(t)                     (delta H turns ahead, H in 5..10)
    #   target_best = max value over turns AFTER t  - value(t)  (campaign best-future aggregate)
    header = (["campaign", "turn", "faction", "subculture", "lord", "action_type", "action_key"]
              + STATE_NUM + STATE_BOOL
              + ["value_now", "value_ahead", "value_bestfut", "target", "target_best"])
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for c in camps:
            ts = sorted(c["turns"])
            tmax = ts[-1]
            val = c["value"]
            for t, ct in sorted(c["turns"].items()):
                if t not in val:
                    continue
                vnow = val[t]
                vah = val.get(min(t + H, tmax), vnow)
                future = [val[tt] for tt in ts if tt > t]
                vbest = max(future) if future else vnow
                target = vah - vnow
                target_best = vbest - vnow
                s = ct["state"]
                base = [c["id"], t, c["player"], c["subculture"], c["lord"]]
                statef = [s.get(k) for k in STATE_NUM] + [int(bool(s.get(k))) for k in STATE_BOOL]
                for a in ct["actions"]:
                    w.writerow(base + [a["action"], choice_key(a)] + statef
                               + [round(vnow, 4), round(vah, 4), round(vbest, 4),
                                  round(target, 5), round(target_best, 5)])
                    n += 1
    print("\nwrote %d choice rows (%d campaigns, H=%d) -> %s" % (n, len(camps), H, out))
    print("value parts global ranges: %s" % {p: rng[p] for p in VALUE_PARTS})


if __name__ == "__main__":
    main()
