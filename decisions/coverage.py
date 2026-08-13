from __future__ import annotations


import json
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
import common
from decisions import dbopen

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
    return "campaign" if scope == "state:campaign" else scope


def _rows(con, col, table, where=(), args=(), sample=None):
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


def declared_types():
    import importlib
    sys.path.insert(0, os.path.join(common.ADVISOR, "mapgraph"))
    sys.path.insert(0, common.ADVISOR)
    feats = importlib.import_module("features")
    gs = importlib.import_module("advisor.mapgraph.schema")
    a, b = set(feats.ACTION_TYPES), set(gs.ACTION_TYPES) - set(gs.SCREEN_ACTION_TYPES)
    if a != b:
        raise SystemExit(
            "the action-type registries disagree -- advisor/features.py has %s and "
            "mapgraph/schema.py has %s. One of them is training on a vocabulary the other "
            "does not have." % (sorted(a - b) or "none extra", sorted(b - a) or "none extra"))
    for at in sorted(set(gs.ACTION_TYPES)):
        if gs.atype_index(at) == 0:
            raise SystemExit("action type %r has no index in mapgraph.schema.atype_index "
                             "-- it would embed as the unknown-type row" % at)
    return sorted(a)


def survivor_report(con):
    have = {r[0]: r[1] for r in con.execute(
        "SELECT action_type, COUNT(*) FROM action_offers GROUP BY action_type")}
    return [(at, have.get(at, 0)) for at in declared_types()]


def check(db=None, sample=None, min_rows=200, verbose=True):
    db = db or os.path.join(common.RUNS_ROOT.replace("/", os.sep), "run",
                            "decisions.sqlite")
    con = dbopen.connect(db, timeout=300)
    out = defaultdict(_blank)

    _scan(_rows(con, "campaign", "decision_points", sample=sample), "campaign", out)
    for (kind,) in con.execute("SELECT DISTINCT context_kind FROM entity_snapshots"):
        _scan(_rows(con, "features", "entity_snapshots", ["context_kind=?"], (kind,),
                    sample), "state:%s" % kind, out)
    seen_types = {r[0] for r in con.execute(
        "SELECT DISTINCT action_type FROM action_offers")}
    for at in sorted(seen_types):
        _scan(_rows(con, "params", "action_offers", ["action_type=?"], (at,), sample),
              "offer:%s" % at, out)

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

    merged = defaultdict(_blank)
    for (scope0, field), s in out.items():
        m = merged[(_norm(scope0), field)]
        m["n"] += s["n"]
        m["empty"] += s["empty"]
        m["vals"].update(s["vals"])

    zero = [(at, n) for at, n in survivor_report(dbopen.connect(db, timeout=300))
            if n == 0]
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
        print("declared action types : %d  (%d with zero surviving options)"
              % (len(declared_types()), len(zero)))
        if zero:
            print("\nDECLARED BUT NEVER A CANDIDATE:")
            for at, _n in zero:
                print("  %-26s 0 options survived the gate" % at)
        if unjust:
            print("\nCARRY NO INFORMATION AND NOBODY HAS SAID WHY:")
            for scope, field, n, only, _ in unjust:
                print("  %-26s %-24s %8s rows, always %s"
                      % (scope, field, "{:,}".format(n), only[:44]))
        for scope, field, n, only, why in dead:
            if why and verbose:
                pass
    return [d for d in dead if not d[4]] + [("declared", at, 0, "no options", None)
                                            for at, _n in zero]


def selftest():
    import shutil
    import tempfile
    sys.path.insert(0, common.DECISIONS)
    from decisions.store import DecisionStore

    def _build(run, constant):
        st = DecisionStore(run)
        st.register_collector("sha-selftest")
        for turn in range(1, 6):
            snap = {"ts": 1000.0 + turn,
                    "campaign": {"faction": "f", "campaign_uuid": "u", "turn": turn,
                                 "treasury": 10 if constant else 10 * turn},
                    "world": {}, "entities": [
                        {"context_kind": "lord", "context_id": "1",
                         "state": {"cqi": "1", "rank": 1 if constant else turn}}]}
            did = st.write_decision(snap, decision_seq=turn)
            st.attach_options(did, [{"context_kind": "lord", "context_id": "1",
                                     "action_type": "noop", "key": "noop", "params": {}}])
        st.close()

    d = tempfile.mkdtemp(prefix="covselftest_")
    try:
        bad_dir = os.path.join(d, "constant")
        good_dir = os.path.join(d, "varying")
        os.makedirs(bad_dir)
        os.makedirs(good_dir)
        _build(bad_dir, True)
        _build(good_dir, False)
        bad = check(os.path.join(bad_dir, "decisions.sqlite"), min_rows=1, verbose=False)
        good = check(os.path.join(good_dir, "decisions.sqlite"), min_rows=1, verbose=False)
        bad_fields = {f for s, f, _n, _o, _w in bad if s != "declared"}
        good_fields = {f for s, f, _n, _o, _w in good if s != "declared"}
        ok1 = "treasury" in bad_fields and "rank" in bad_fields
        ok2 = "treasury" not in good_fields and "rank" not in good_fields
        print("  %s constant fields are reported   (%s)"
              % ("ok  " if ok1 else "FAIL", ", ".join(sorted(bad_fields))[:60]))
        print("  %s varying fields are not         (%s)"
              % ("ok  " if ok2 else "FAIL", ", ".join(sorted(good_fields))[:60] or "none"))
        return 0 if (ok1 and ok2) else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    common.require_venv()
    a = sys.argv[1:]

    db = common.cli_path(a, ("--sample", "--min-rows"))

    def _opt(name, default):
        return int(a[a.index(name) + 1]) if name in a else default

    if "--selftest" in a:
        raise SystemExit(selftest())
    bad = check(db, sample=_opt("--sample", 0) or None, min_rows=_opt("--min-rows", 200))
    print("\n%s" % ("coverage OK" if not bad else
                    "%d FIELD(S) CARRY NO INFORMATION" % len(bad)))
    raise SystemExit(1 if bad else 0)
