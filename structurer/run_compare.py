r"""Run-similarity metric, BROKEN DOWN BY COMPONENT, across all v5/v6 runs pairwise.

Two components (a run is similar to another if BOTH are high):

  1. VERB similarity -- turn-segmented, activity-weighted Jaccard of the player's semantic-action
     verbs. For each turn t both runs share, Jaccard(verbs_A[t], verbs_B[t]); averaged over turns
     weighted by that turn's action volume (avg of the two runs' action counts). Range 0..1.

  2. STATE trajectory similarity -- per metric {cities, provinces, gross income, inverse power-rank},
     over the shared turns, 1 - mean(|a-b| / max(|a|,|b|)); i.e. mean per-turn relative closeness.
     Reported per metric so you can see WHICH component drives similarity/difference. Range 0..1.
     * cities  = faction.regions ; provinces = faction.num_provinces ; income = faction.income
     * power rank = the player's rank among ALL factions that turn by total military strength
       (sum of force.strength); inverse = 1/rank (rank 1 -> 1.0). (The captured `rank` field is
       imperium_level, a tier, NOT a standings rank -- so we derive the standings rank here.)

These are DIFFERENT starts/factions, so absolute values differ -- the point is the method + to see
the spread of similar vs different, not that the numbers are commensurable.

    python run_compare.py
"""
import itertools
import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import structurer                                          # noqa: E402

RUNS_ROOT = "D:/twdata/runs/human"
STATE_METRICS = ("cities", "provinces", "income", "inv_power_rank")


# ---- per-run extraction ---------------------------------------------------------------------
def verbs_by_turn(run_dir):
    by_turn = structurer.semantic_actions_by_turn(run_dir)
    verbs = {t: set(a["action"] for a in acts) for t, acts in by_turn.items()}
    counts = {t: len(acts) for t, acts in by_turn.items()}
    return verbs, counts


def state_by_turn(run_dir):
    player = structurer.player_faction(run_dir)
    fac = {}                                               # turn -> player's faction record
    strength = defaultdict(lambda: defaultdict(float))     # turn -> faction -> sum force strength
    income = defaultdict(dict)                             # turn -> faction -> income (rank fallback)
    for r in structurer.iter_twstate(run_dir):
        t = r.get("turn")
        if t is None:
            continue
        k = r.get("kind")
        if k == "faction":
            f = r.get("faction")
            if f == player and t not in fac:
                fac[t] = r
            if f and r.get("income") is not None:
                income[t][f] = r["income"]
        elif k == "force":
            s = r.get("strength")
            if s is not None:
                strength[t][r.get("faction")] += s
    out = {}
    for t, rec in fac.items():
        pr = None
        for pool in (strength.get(t, {}), income.get(t, {})):
            if pool and player in pool:
                ranked = sorted(pool.items(), key=lambda kv: -kv[1])
                pr = next((i + 1 for i, (f, _) in enumerate(ranked) if f == player), None)
                if pr:
                    break
        out[t] = {"cities": rec.get("regions"), "provinces": rec.get("num_provinces"),
                  "income": rec.get("income"), "power_rank": pr,
                  "inv_power_rank": (1.0 / pr if pr else None)}
    return out


# ---- component metrics ----------------------------------------------------------------------
def verb_similarity(vA, cA, vB, cB):
    turns = sorted(set(vA) & set(vB))
    if not turns:
        return None, 0
    num = den = 0.0
    for t in turns:
        a, b = vA[t], vB[t]
        j = (len(a & b) / len(a | b)) if (a | b) else 1.0
        w = ((cA.get(t, 0) + cB.get(t, 0)) / 2.0) or 1.0
        num += w * j
        den += w
    return (num / den if den else None), len(turns)


def state_similarity(sA, sB):
    turns = sorted(set(sA) & set(sB))
    sims = {}
    for m in STATE_METRICS:
        pairs = [(sA[t].get(m), sB[t].get(m)) for t in turns
                 if sA[t].get(m) is not None and sB[t].get(m) is not None]
        if not pairs:
            sims[m] = None
            continue
        sims[m] = sum(1 - abs(a - b) / max(abs(a), abs(b), 1e-9) for a, b in pairs) / len(pairs)
    return sims, len(turns)


# ---- discovery + driver ---------------------------------------------------------------------
def all_campaigns():
    """EVERY campaign across all v5/v6 runs -- a run dir can hold several (restarts), each its own
    unit of analysis."""
    out = []
    for name in sorted(os.listdir(RUNS_ROOT)):
        d = os.path.join(RUNS_ROOT, name)
        mp = os.path.join(d, "meta.json")
        if not os.path.isfile(mp):
            continue
        try:
            v = json.load(open(mp)).get("recorder_version")
        except Exception as e:
            sys.stderr.write("run_compare: skip %s, meta.json unreadable -> %s\n" % (name, repr(e)[:60]))
            continue
        if v not in ("v5", "v6"):
            continue
        for c in structurer.list_campaigns(d):
            c = dict(c, run=name, v=v)
            c["id"] = "%s.%s.t%s" % (name[-6:], c["faction"].split("_", 2)[-1][:14], c["max_turn"])
            out.append(c)
    return out


def load_campaign(c):
    """Single pass over one campaign's log -> per-turn verbs (player semantic-action sets) + per-turn
    state (cities / provinces / income / inverse power-rank)."""
    log = c["log"]
    player = structurer.player_faction(log)
    ev_by_turn = defaultdict(list)
    fac = {}
    strength = defaultdict(lambda: defaultdict(float))
    income = defaultdict(dict)
    for r in structurer.iter_twstate(log):
        t = r.get("turn")
        if t is None:
            continue
        k = r.get("kind")
        if k == "event":
            facs = [v for kk, v in r.items() if "faction" in kk and v]
            if facs:
                if player in facs:
                    ev_by_turn[t].append(r)
            elif r.get("in_player_turn"):
                ev_by_turn[t].append(r)
        elif k == "faction":
            f = r.get("faction")
            if f == player and t not in fac:
                fac[t] = r
            if f and r.get("income") is not None:
                income[t][f] = r["income"]
        elif k == "force":
            s = r.get("strength")
            if s is not None:
                strength[t][r.get("faction")] += s

    verbs, counts = {}, {}
    for t, evs in ev_by_turn.items():
        acts = structurer._classify_turn(evs)
        verbs[t] = set(a["action"] for a in acts)
        counts[t] = len(acts)
    state = {}
    for t, rec in fac.items():
        pr = None
        for pool in (strength.get(t, {}), income.get(t, {})):
            if pool and player in pool:
                ranked = sorted(pool.items(), key=lambda kv: -kv[1])
                pr = next((i + 1 for i, (f, _) in enumerate(ranked) if f == player), None)
                if pr:
                    break
        state[t] = {"cities": rec.get("regions"), "provinces": rec.get("num_provinces"),
                    "income": rec.get("income"), "power_rank": pr,
                    "inv_power_rank": (1.0 / pr if pr else None)}
    return dict(c, faction=player, verbs=verbs, counts=counts, state=state,
                turns=sorted(set(verbs) | set(state)), nact=sum(counts.values()))


def _fmt(x):
    return "%.2f" % x if x is not None else " -- "


def main():
    camps = all_campaigns()
    print("discovered %d campaigns across v5/v6 runs; extracting (multi-GB logs take a while)...\n"
          % len(camps), flush=True)
    loaded = []
    for c in camps:
        r = load_campaign(c)
        print("  loaded %-24s %-34s turns=%-3d actions=%d"
              % (r["id"], r["faction"], len(r["turns"]), r["nact"]), flush=True)
        loaded.append(r)

    print("\nPAIRWISE similarity (higher = more similar; different starts -> not commensurable):")
    print("%-24s %-24s | verb | citis prov  incm invRk | shared" % ("A", "B"))
    print("-" * 104)
    for A, B in itertools.combinations(loaded, 2):
        vs, vt = verb_similarity(A["verbs"], A["counts"], B["verbs"], B["counts"])
        ss, st = state_similarity(A["state"], B["state"])
        print("%-24s %-24s | %s | %s  %s  %s  %s | v%d/s%d"
              % (A["id"], B["id"], _fmt(vs), _fmt(ss.get("cities")), _fmt(ss.get("provinces")),
                 _fmt(ss.get("income")), _fmt(ss.get("inv_power_rank")), vt, st))


if __name__ == "__main__":
    main()
