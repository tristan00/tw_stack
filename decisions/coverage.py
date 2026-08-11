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
from decisions import dbopen  # noqa: E402

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
    ("state:hero", "besieging"):
        "laying siege is a property of an army (CcoCampaignMilitaryForce.IsLayingSiege) "
        "and a hero commands none, so False for every hero is the game speaking. Note "
        "this is NOT the reason state:hero.garrisoned was constant: IsGarrisoned exists "
        "on CcoCampaignCharacter too, we were simply reading it off the force",
    ("world:relations", "def_ally"):
        "no defensive alliance ever existed to report. The read works -- the identical "
        "b() helper over the same faction list returns mil_ally 95, nap 1,843 and "
        "mil_access 353 -- and the agent's 105 defensive_alliance proposals were accepted "
        "0 times, in company with 0/95 military_alliance, 0/103 vassal and 0/97 "
        "confederation. The AI accepting nothing is a fact about the game; if that "
        "changes, this entry should be deleted rather than kept",
    ("offer:recruit_hero", "traits"):
        "an agent pool candidate carries no traits until it is recruited. The route is "
        "proven live by the identical read on lords, which returned traits for 241 of "
        "159,336 candidates -- so this is an empty list, not a failed read, and the "
        "collector now emits TRAITS_UNREAD when it actually cannot read the list",
    ("offer:recruit_hero", "n_traits"): "see offer:recruit_hero.traits",
    ("offer:recruit_hero", "trait"): "see offer:recruit_hero.traits",
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


def _rows(con, col, table, where=(), args=(), sample=None):
    """A sample spread over the whole corpus, not the first N.

    `LIMIT N` with no ORDER BY returns the N lowest rowids -- the earliest decisions of
    the earliest campaigns. Anything that only happens later in a campaign is then absent
    from the sample by construction and gets reported as a dead field. That is not
    hypothetical: `diplomacy.their_vassal` was flagged constant-False off the first 40,000
    rows and is in fact true in 8,256 of 2,131,584 -- vassals do not exist on turn 1. A
    checker that manufactures its own false positives cannot be an acceptance gate.

    The stride is on `decision_id` rather than on rowid, for two reasons: it is uniform in
    campaign time, which is the axis the bias was on, and the v2 store serves these table
    names as views, which have no rowid at all. Rows are near-uniformly distributed across
    decisions, so a step derived from the row count still lands near `sample` rows.
    """
    sql = "SELECT %s FROM %s" % (col, table)
    cond = list(where)
    if sample:
        n = con.execute("SELECT count(*) FROM %s%s"
                        % (table, (" WHERE " + " AND ".join(where)) if where else ""),
                        args).fetchone()[0]
        step = max(1, n // sample)
        if step > 1:
            cond.append("decision_id %% %d = 0" % step)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    return (r[0] for r in con.execute(sql, args))


def check(db=None, sample=None, min_rows=200, verbose=True):
    db = db or os.path.join(common.RUNS_ROOT.replace("/", os.sep), "run",
                            "decisions.sqlite")
    con = dbopen.connect(db, timeout=300)
    out = defaultdict(_blank)

    _scan(_rows(con, "campaign", "decision_points", sample=sample), "campaign", out)
    for (kind,) in con.execute("SELECT DISTINCT context_kind FROM entity_snapshots"):
        _scan(_rows(con, "features", "entity_snapshots", ["context_kind=?"], (kind,),
                    sample), "state:%s" % kind, out)
    for (at,) in con.execute("SELECT DISTINCT action_type FROM action_offers"):
        _scan(_rows(con, "params", "action_offers", ["action_type=?"], (at,), sample),
              "offer:%s" % at, out)

    # world holds lists of dicts, one level in
    wrows = defaultdict(_blank)
    for w in _rows(con, "world", "decision_points", sample=sample):
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

    # decision_points.campaign and entity_snapshots[campaign].features are the same blob
    # written twice, so the same field arrives under two scopes and was reported twice.
    merged = defaultdict(_blank)
    for (scope0, field), s in out.items():
        m = merged[(_norm(scope0), field)]
        m["n"] += s["n"]
        m["empty"] += s["empty"]
        m["vals"].update(s["vals"])

    dead, thin = [], []
    for (scope, field), s in sorted(merged.items()):
        if s["n"] < min_rows:
            thin.append((scope, field, s["n"]))
            continue
        if len(s["vals"]) <= 1:
            only = next(iter(s["vals"]), "")
            dead.append((scope, field, s["n"], only,
                         JUSTIFIED.get((scope, field))))

    if verbose:
        unjust = [d for d in dead if not d[4]]
        print("fields examined      : %d" % len(merged))
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
