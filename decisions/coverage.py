from __future__ import annotations

"""Fail when a recorded field carries no information.

The instruction that produced the previous corpus was "be complete". It failed, and it
failed silently, because a field that is empty because nobody populated it looks exactly
like a field that is empty because the game has nothing to say. `campaign.defeated` was
the hardcoded literal False in 22,136 of 22,136 decisions while the harness independently
recorded 18 defeated campaigns -- and it is the ground truth for the survival term in the
label. Nothing complained for the life of the corpus.

So: any recorded field that is constant, null or empty across the sample FAILS, and the
only way to make it pass is an entry in JUSTIFIED naming a reason. That converts
"did we collect everything?" from a claim into a test, and it puts the burden on the
person who wants to keep writing a dead field.

    python -m decisions.coverage [db_path] [--sample N] [--min-rows N]

Exit code 1 means at least one field carries no information and nobody has said why.
"""

import json
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import common  # noqa: E402

# A field may only be exempt for a reason that is about the GAME, never about the
# collector. "The recorder does not populate it" is the bug this file exists to catch.
JUSTIFIED = {
    ("state:hero", "stance"):
        "heroes command no army, so they have no army stance -- verified 'none' in "
        "11,096 of 11,096 hero snapshots while lord stance carries 14 distinct values",
    ("state:hero", "hp"): "heroes have no army, so no aggregate hp",
    ("state:hero", "units"): "heroes have no army, so no unit count",
    ("state:hero", "pending_recruits"): "heroes cannot recruit",
    ("state:hero", "pending_recruit_keys"): "heroes cannot recruit",
    ("campaign", "game_version"):
        "one game build per corpus by construction; a change here is an era boundary "
        "and should invalidate the corpus rather than vary within it",
    ("state:province", "settlement_present"):
        "the recorder only emits provinces that have a settlement, so this is a "
        "tautology of the filter -- remove the field or widen the filter",
}


def _scan(rows, scope, out):
    for r in rows:
        try:
            d = json.loads(r or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            s = out[(scope, k)]
            s["n"] += 1
            if isinstance(v, (dict, list)):
                s["vals"][json.dumps(v, sort_keys=True)[:200]] += 1
                if not v:
                    s["empty"] += 1
            else:
                s["vals"][repr(v)] += 1
                if v is None or v == "":
                    s["empty"] += 1


def _blank():
    return {"n": 0, "empty": 0, "vals": Counter()}


def _norm(scope):
    """decision_points.campaign and entity_snapshots[campaign].features are the same
    blob written twice -- byte for byte. Judge them once. (That duplication is itself
    worth removing; the interned-blob schema does it for free.)"""
    return "campaign" if scope == "state:campaign" else scope


def check(db=None, sample=None, min_rows=200, verbose=True):
    import sqlite3
    db = db or os.path.join(common.RUNS_ROOT.replace("/", os.sep), "run",
                            "decisions.sqlite")
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=300)
    out = defaultdict(_blank)
    lim = (" LIMIT %d" % sample) if sample else ""

    _scan((r[0] for r in con.execute("SELECT campaign FROM decision_points" + lim)),
          "campaign", out)
    for (kind,) in con.execute("SELECT DISTINCT context_kind FROM entity_snapshots"):
        _scan((r[0] for r in con.execute(
            "SELECT features FROM entity_snapshots WHERE context_kind=?" + lim, (kind,))),
            "state:%s" % kind, out)
    for (at,) in con.execute("SELECT DISTINCT action_type FROM action_offers"):
        _scan((r[0] for r in con.execute(
            "SELECT params FROM action_offers WHERE action_type=?" + lim, (at,))),
            "offer:%s" % at, out)

    # world holds lists of dicts, one level in
    wrows = defaultdict(_blank)
    for (w,) in con.execute("SELECT world FROM decision_points" + lim):
        try:
            d = json.loads(w or "{}")
        except (ValueError, TypeError):
            continue
        for coll, items in (d.items() if isinstance(d, dict) else []):
            if isinstance(items, list):
                _scan((json.dumps(i) for i in items if isinstance(i, dict)),
                      "world:%s" % coll, wrows)
    out.update(wrows)
    con.close()

    dead, thin = [], []
    for (scope0, field), s in sorted(out.items()):
        scope = _norm(scope0)
        if s["n"] < min_rows:
            thin.append((scope, field, s["n"]))
            continue
        if len(s["vals"]) <= 1:
            only = next(iter(s["vals"]), "")
            dead.append((scope, field, s["n"], only,
                         JUSTIFIED.get((scope, field))))

    if verbose:
        unjust = [d for d in dead if not d[4]]
        print("fields examined      : %d" % len(out))
        print("constant fields      : %d  (%d justified, %d NOT)"
              % (len(dead), len(dead) - len(unjust), len(unjust)))
        print("below --min-rows %-4d: %d (not judged)" % (min_rows, len(thin)))
        if unjust:
            print("\nCARRY NO INFORMATION AND NOBODY HAS SAID WHY:")
            for scope, field, n, only, _ in unjust:
                print("  %-26s %-24s %8s rows, always %s"
                      % (scope, field, "{:,}".format(n), only[:44]))
        for scope, field, n, only, why in dead:
            if why and verbose:
                pass
    return [d for d in dead if not d[4]]


if __name__ == "__main__":
    a = sys.argv[1:]
    db = next((x for x in a if not x.startswith("--")), None)

    def _opt(name, default):
        return int(a[a.index(name) + 1]) if name in a else default

    bad = check(db, sample=_opt("--sample", 0) or None, min_rows=_opt("--min-rows", 200))
    print("\n%s" % ("coverage OK" if not bad else
                    "%d FIELD(S) CARRY NO INFORMATION" % len(bad)))
    raise SystemExit(1 if bad else 0)
