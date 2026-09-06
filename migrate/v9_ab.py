import argparse
import io
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLD = "http://127.0.0.1:8791"
NEW = "http://127.0.0.1:8792"

SKIP_KEYS = {"age_seconds", "Server-Timing"}

ROUTE_DROPS = {
    "/api/run": ["services", "throughput"],
    "/api/campaigns": ["unjoined", "turn_rows"],
    "/api/models/training": ["reward", "trials"],
    "/api/health": ["stamp"],
    "/api/campaigns/picks": ["margin"],
    "/api/decisions/diplomacy": ["versions"],
    "/api/models/forcing": ["versions"],
    "/api/models/correlations": ["versions"],
    "/api/choices/building": ["label"],
}

AGREEMENT_ROUTES = ("/api/models/agreement", "/api/models/agreement/series")

DIPLO_DROPS = {"outcome", "deal_score", "standing", "state"}


def log(msg):
    sys.stderr.write("%.3f  v9 %s\n" % (time.time(), msg))


def fetch(base, path):
    with urllib.request.urlopen(base + path, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def strip(v, drops):
    if isinstance(v, dict):
        return {k: strip(x, drops) for k, x in v.items()
                if k not in SKIP_KEYS and k not in drops}
    if isinstance(v, list):
        return [strip(x, drops) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


SORT_LISTS = {"settlements", "resources", "hero_types", "factions", "starts",
              "maps", "races", "lords", "cultures", "related", "arms",
              "by_character", "chain"}


def sort_unstable(v):
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            val = sort_unstable(val)
            if k in SORT_LISTS and isinstance(val, list):
                val = sorted(val, key=lambda x: json.dumps(x, sort_keys=True))
            if k == "forks" and isinstance(val, list):
                val = sorted(val, key=lambda x: str(x.get("fork"))
                             if isinstance(x, dict) else "")
            out[k] = val
        return out
    if isinstance(v, list):
        return [sort_unstable(x) for x in v]
    return v


def diff(a, b, path, out, cap=12):
    if len(out) >= cap:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append("%s.%s only-new=%r" % (path, k, _short(b[k])))
            elif k not in b:
                out.append("%s.%s only-old=%r" % (path, k, _short(a[k])))
            else:
                diff(a[k], b[k], "%s.%s" % (path, k), out, cap)
            if len(out) >= cap:
                return
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append("%s len %d != %d" % (path, len(a), len(b)))
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, "%s[%d]" % (path, i), out, cap)
            if len(out) >= cap:
                return
    elif a != b:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
                and abs(float(a) - float(b)) <= max(1e-6, 3e-6 * max(abs(float(a)), abs(float(b)))):
            return
        out.append("%s %r != %r" % (path, _short(a), _short(b)))


def _short(v):
    s = repr(v)
    return s[:80] + "..." if len(s) > 80 else s


def compare(path, drops=(), old_path=None):
    t0 = time.time()
    try:
        a = fetch(OLD, old_path or path)
    except Exception as e:
        print("SKIP  %-70s old fetch failed: %r" % (path, e))
        return None
    try:
        b = fetch(NEW, path)
    except Exception as e:
        print("ERROR %-70s new fetch failed: %r" % (path, e))
        return False
    drops = set(drops) | set(ROUTE_DROPS.get(path.split("?")[0], []))
    a, b = sort_unstable(strip(a, drops)), sort_unstable(strip(b, drops))
    out = []
    diff(a, b, "", out)
    ms = (time.time() - t0) * 1000
    if not out:
        print("EQUAL %-70s (%.0f ms)" % (path, ms))
        return True
    print("DIFF  %-70s %d+ diffs (%.0f ms)" % (path, len(out), ms))
    for line in out:
        print("      " + line)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaigns", type=int, default=10)
    ap.add_argument("--decisions", type=int, default=20)
    ap.add_argument("--keys", type=int, default=5)
    args = ap.parse_args()
    results = {}

    def run(path, **kw):
        results[path] = compare(path, **kw)

    run("/api/health")
    run("/api/run")
    run("/api/campaigns?page_size=200")
    run("/api/campaigns/starts")
    run("/api/campaigns/picks")
    run("/api/decisions")
    run("/api/decisions/actions")
    run("/api/decisions/menus",
        drops={"interrupt_id", "legacy_interrupt_id", "chosen_context"})
    run("/api/decisions/timeline")
    run("/api/decisions/diplomacy")
    run("/api/models")
    run("/api/models/forcing")
    run("/api/models/correlations")
    run("/api/models/training")
    run("/api/items")
    run("/api/items/swaps")
    for fam in ("buildings", "research", "skills", "traits"):
        run("/api/%s" % fam)
    run("/api/choices/research")
    run("/api/choices/skills")
    run("/api/choices/building")
    run("/api/overtime/research")
    run("/api/positions")
    run("/api/lookup")
    run("/api/lookup/facets")
    run("/api/reward-weights")

    camps = fetch(OLD, "/api/campaigns?page_size=%d" % args.campaigns)
    keys = [r["campaign"]["raw"] for r in camps.get("rows", [])][:args.campaigns]
    for ck in keys:
        run("/api/campaigns/%s" % ck, drops={"diplomacy", "turn_rows"})
        run("/api/campaigns/%s/state" % ck)
        run("/api/campaigns/%s/buildings" % ck)
        run("/api/campaigns/%s/research" % ck)
        run("/api/campaigns/%s/skills" % ck)
        run("/api/campaigns/%s/items" % ck)

    starts = fetch(OLD, "/api/campaigns/starts")
    srows = [r for r in starts.get("rows", []) if r.get("n", 0) > 3][:3]
    for r in srows:
        mk = (r.get("campaign_map") or {}).get("raw") or ""
        fk = r["faction"]["raw"]
        base = "/api/campaigns/starts/%s/%s" % (mk, fk)
        run(base)
        for sub in ("performance", "openings", "campaigns", "research",
                    "skills", "items", "buildings"):
            run("%s/%s" % (base, sub))

    decs = fetch(OLD, "/api/decisions?limit=%d" % args.decisions)
    for r in decs.get("rows", [])[:args.decisions]:
        run("/api/decisions/%d" % r["decision_id"], drops={"agreement"})

    picks = fetch(OLD, "/api/campaigns/picks?limit=5")
    for pr in picks.get("picks", [])[:5]:
        run("/api/campaigns/picks/%d" % pr["pick_id"])

    for fam, route in (("building", "buildings"), ("research", "research"),
                       ("skills", "skills"), ("traits", "traits")):
        idx = fetch(OLD, "/api/%s" % route)
        for r in idx.get("rows", [])[:args.keys]:
            run("/api/%s/%s" % (route, r["key"]))
    items = fetch(OLD, "/api/items")
    for r in items.get("rows", [])[:args.keys]:
        run("/api/items/%s" % r["key"])

    total = len(results)
    equal = sum(1 for v in results.values() if v)
    failed = [k for k, v in results.items() if v is False]
    print("\n%d routes compared, %d equal, %d differ" % (total, equal, len(failed)))
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v9_ab.json")
    io.open(outpath, "w", encoding="utf-8", newline="\n").write(json.dumps(
        {"ts": time.time(), "total": total, "equal": equal, "differ": failed},
        indent=2) + "\n")


if __name__ == "__main__":
    main()
