r"""model.py -- the v7 ranker: ONE model set over the whole faction.

STRUCTURE (carried over from v6, which the user confirmed):
    E1 = f(state, action)   CatBoost on the full feature row  -> the exploit signal
    E2 = g(state)           CatBoost on the state-only row    -> the per-entity baseline
    impact = E1 - E2        the action's predicted contribution

Why the subtraction matters here more than it did in v6: v7 makes ONE ranking across every entity,
and E2 is constant only WITHIN an entity, not across them. Subtracting it removes each entity's own
baseline, so "how much does this action add" is comparable between a lord's attack and a province's
building. Without it the ranking would just prefer whichever entity happens to sit in a good state.

    explore = IsolationForest novelty over the same rows, min-maxed  (v6's explorer, unchanged)
    combined = (1-BETA)*exploit + BETA*explore

TARGET (FW-6, unchanged from v6): per campaign-turn,
    0.5 * campaign_best_half + 0.5 * delta_half
where the best half is the mean of each part's per-campaign PEAK plus a survival leg (the campaign's
last recorded turn), and the delta half is the mean of each part's forward H-turn delta. Parts are
income, settlements, power_rank (stored INVERTED upstream so high=good), allies, vassals, each
min-max normalized INDIVIDUALLY so every component is monotone "higher is better".

Everything is featurized through advisor_v7/features.py from STORED DECISION RECORDS -- the same
function and the same records at fit time and at decision time. No bus, ever.

    python model.py train [runs_root]     fit from every run's decisions.sqlite
    python model.py report                what is in the store
"""
from __future__ import annotations

import glob
import json
import os
import pickle
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F                                       # noqa: E402
from store import DecisionStore, IncompatibleStore        # noqa: E402

MODEL_DIR = os.path.join(_HERE, "models_store")
RUNS_ROOT = "D:/twdata/runs/human"
H = 8                       # forward horizon for the delta half
BETA = 0.15                 # explore weight in `combined`
VALUE_PARTS = ("income", "settlements", "power_rank", "allies", "vassals")
MIN_ROWS = 40               # below this the policy stays cold-start (see policy.py)


# ------------------------------------------------------------------ FW-6 target
def _mm0(v, lo, hi):
    """min-max to [0,1]; a degenerate population range contributes 0.0, never a spurious 0.5."""
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0


def target_ranges(series):
    """Population ranges for the FW-6 blend, over {campaign: {turn: {part: value}}}."""
    peaks = {p: [] for p in VALUE_PARTS}
    deltas = {p: [] for p in VALUE_PARTS}
    maxturns = []
    for turns in series.values():
        if not turns:
            continue
        ts = sorted(turns)
        last = ts[-1]
        maxturns.append(last)
        for p in VALUE_PARTS:
            vals = [turns[t].get(p) for t in ts if turns[t].get(p) is not None]
            if vals:
                peaks[p].append(max(vals))
        for t in ts:
            now = turns[t]
            end = turns.get(min(t + H, last), now)          # CLAMP to the last recorded turn
            for p in VALUE_PARTS:
                a = now.get(p)
                if a is None:
                    continue
                deltas[p].append((end.get(p, a) if end.get(p) is not None else a) - a)

    def rng(xs):
        return (min(xs), max(xs)) if xs else (0.0, 1.0)
    return {"peak": {p: rng(peaks[p]) for p in VALUE_PARTS},
            "delta": {p: rng(deltas[p]) for p in VALUE_PARTS}, "maxturn": rng(maxturns)}


def target(series, campaign, turn, ranges):
    """The FW-6 value of one campaign-turn, or None when that turn was never recorded."""
    turns = series.get(campaign) or {}
    turn = int(turn or 0)
    if turn not in turns:
        return None
    ts = sorted(turns)
    last = ts[-1]
    now = turns[turn]
    end = turns.get(min(turn + H, last), now)
    best = []
    for p in VALUE_PARTS:
        vals = [turns[t].get(p) for t in ts if turns[t].get(p) is not None]
        if vals:
            best.append(_mm0(max(vals), *ranges["peak"][p]))
    best.append(_mm0(last, *ranges["maxturn"]))              # survival leg lives in the best half
    best_half = sum(best) / len(best) if best else 0.0
    deltas = []
    for p in VALUE_PARTS:
        a = now.get(p)
        if a is None:
            continue
        b = end.get(p, a)
        deltas.append(_mm0((a if b is None else b) - a, *ranges["delta"][p]))
    delta_half = sum(deltas) / len(deltas) if deltas else 0.0
    return 0.5 * best_half + 0.5 * delta_half


# ------------------------------------------------------------------ dataset
def gather(runs_root=RUNS_ROOT):
    """Every confirmed decision point across every run, featurized into (E1 rows, E2 rows, y).

    Only the CHOSEN action of each decision point becomes a training row: the target describes what
    happened after that action, so attaching it to actions that were NOT taken would be a lie. The
    unchosen offers are still used -- at decision time, scored by the same model through their own
    feature rows. (This is v6's arrangement, unchanged.)
    """
    dbs = sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")))
    decisions, series, skipped_dbs = [], {}, []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            s = DecisionStore(run_dir)
        except IncompatibleStore as e:
            # a store from before the faction-wide redesign -- skip it LOUDLY rather than let one
            # legacy directory abort training over every campaign we do have
            skipped_dbs.append(os.path.basename(run_dir))
            sys.stderr.write("model: skipping %s -> %s\n" % (run_dir, str(e)[:120]))
            continue
        try:
            for rec, taken in s.labelled_decisions():
                decisions.append((rec, taken))
            # campaigns are keyed by their MINTED uuid, so several playthroughs can share one store
            # (and one run dir) without their turn-series merging
            for camp, turns in s.target_series().items():
                series.setdefault(camp, {}).update(turns)
        finally:
            s.close()
    ranges = target_ranges(series)
    full, state, ys, groups = [], [], [], []
    skipped = 0
    for rec, taken in decisions:
        y = target(series, rec.get("campaign_id"), rec.get("turn"), ranges)
        if y is None:
            skipped += 1                    # no reward row for that turn yet -> unlabelled
            continue
        for ent, offer, row in F.decision_rows(rec):
            if (ent["context_kind"], str(ent["context_id"]),
                    offer["action_type"], str(offer["key"])) != taken:
                continue
            full.append(row)
            state.append(F.state_row(rec, ent))
            ys.append(y)
            groups.append(rec.get("campaign_id"))
    return {"full": full, "state": state, "y": ys, "groups": groups,
            "n_decisions": len(decisions), "skipped_unlabelled": skipped,
            "runs": len(dbs) - len(skipped_dbs), "skipped_dbs": skipped_dbs,
            "campaigns": len(series)}


# ------------------------------------------------------------------ train / load / score
def train(runs_root=RUNS_ROOT):
    from catboost import CatBoostRegressor, Pool
    from sklearn.ensemble import IsolationForest
    data = gather(runs_root)
    rows, srows, y = data["full"], data["state"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, **_counts(data)}
    os.makedirs(MODEL_DIR, exist_ok=True)
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    e1 = CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05, loss_function="RMSE",
                           verbose=0)
    e1.fit(Pool(X, y, cat_features=cat_idx))
    e1.save_model(os.path.join(MODEL_DIR, "e1.cbm"))
    snum, scat = F.split_columns(srows)
    Xs = F.matrix(srows, snum, scat)
    scat_idx = list(range(len(snum), len(snum) + len(scat)))
    e2 = CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05, loss_function="RMSE",
                          verbose=0)
    e2.fit(Pool(Xs, y, cat_features=scat_idx))
    e2.save_model(os.path.join(MODEL_DIR, "e2.cbm"))
    # EXPLORE: IsolationForest novelty over the same (state+action) vectors, ordinal-encoded
    Xa, cat_maps = _encode(rows, num, cat)
    iso = IsolationForest(n_estimators=200, random_state=0, n_jobs=-1)
    iso.fit(Xa)
    nov = [-s for s in iso.score_samples(Xa)]
    pickle.dump({"iso": iso, "cat_maps": cat_maps}, open(os.path.join(MODEL_DIR, "iso.pkl"), "wb"))
    preds = list(e1.predict(Pool(X, cat_features=cat_idx)))
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat,
            "exp_lo": min(preds), "exp_hi": max(preds),
            "nov_lo": min(nov), "nov_hi": max(nov), "rows": len(rows),
            "campaigns": sorted(set(data["groups"])), "beta": BETA, "H": H}
    json.dump(meta, open(os.path.join(MODEL_DIR, "meta.json"), "w"))
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5), **_counts(data)}


def _counts(data):
    return {k: data[k] for k in ("n_decisions", "skipped_unlabelled", "runs", "campaigns")}


def _encode(rows, num, cat, cat_maps=None):
    """Numeric matrix for IsolationForest: numerics as-is, categoricals ordinal-encoded (an unseen
    category gets a fresh index, which the forest tends to isolate == naturally novel)."""
    if cat_maps is None:
        cat_maps = {c: {v: i for i, v in enumerate(sorted({str(r.get(c, "?")) for r in rows}))}
                    for c in cat}
    X = []
    for r in rows:
        v = [(F._f(r.get(c)) or 0.0) for c in num]
        for c in cat:
            mp = cat_maps[c]
            v.append(mp.get(str(r.get(c, "?")), len(mp)))
        X.append(v)
    return X, cat_maps


class Ranker:
    """Loaded model set. `ready` is False when nothing is trained yet -- the policy then runs its
    cold-start behaviour instead of pretending to have an opinion."""

    def __init__(self, model_dir=MODEL_DIR):
        self.ready = False
        self.meta = None
        self.e1 = self.e2 = self.iso = None
        meta_path = os.path.join(model_dir, "meta.json")
        if not os.path.isfile(meta_path):
            return
        try:
            from catboost import CatBoostRegressor
            self.meta = json.load(open(meta_path))
            self.e1 = CatBoostRegressor(); self.e1.load_model(os.path.join(model_dir, "e1.cbm"))
            self.e2 = CatBoostRegressor(); self.e2.load_model(os.path.join(model_dir, "e2.cbm"))
            p = os.path.join(model_dir, "iso.pkl")
            self.iso = pickle.load(open(p, "rb")) if os.path.isfile(p) else None
            self.ready = True
        except Exception as e:
            sys.stderr.write("model: load failed -> %s\n" % repr(e)[:160])
            self.ready = False

    def score(self, record, beta=BETA):
        """Rank EVERY offer of a stored decision record.

        Returns [{context_kind, context_id, action_type, key, available, gate, params,
                  exploit, explore, impact, score}] sorted best-first. `score` is what the policy
        picks on; `impact` (E1-E2) is the cross-entity-comparable quantity it is built from.
        """
        triples = F.decision_rows(record)
        if not self.ready or not triples:
            # COLD PATH: deliberately imports nothing from catboost/sklearn, so a fresh machine can
            # collect the first campaign's data before any ML dependency is installed.
            return [{"context_kind": e["context_kind"], "context_id": e["context_id"],
                     "action_type": o["action_type"], "key": o["key"],
                     "available": o["available"], "gate": o["gate"], "params": o.get("params") or {},
                     "exploit": None, "explore": None, "impact": None, "score": None}
                    for e, o, _r in triples]
        from catboost import Pool
        m = self.meta
        rows = [r for _e, _o, r in triples]
        X = F.matrix(rows, m["num"], m["cat"])
        f1 = list(self.e1.predict(Pool(X, cat_features=list(
            range(len(m["num"]), len(m["num"]) + len(m["cat"]))))))
        # E2 is per ENTITY, not per offer -- compute it once per entity and reuse
        seen, srows, order = {}, [], []
        for e, _o, _r in triples:
            k = (e["context_kind"], str(e["context_id"]))
            if k not in seen:
                seen[k] = len(srows)
                srows.append(F.state_row(record, e))
            order.append(seen[k])
        Xs = F.matrix(srows, m["state_num"], m["state_cat"])
        g = list(self.e2.predict(Pool(Xs, cat_features=list(
            range(len(m["state_num"]), len(m["state_num"]) + len(m["state_cat"]))))))
        nov = [None] * len(rows)
        if self.iso is not None:
            Xa, _ = _encode(rows, m["num"], m["cat"], self.iso["cat_maps"])
            nov = [-float(s) for s in self.iso["iso"].score_samples(Xa)]
        out = []
        for i, (e, o, _r) in enumerate(triples):
            exploit = _mm(f1[i], m["exp_lo"], m["exp_hi"])
            explore = _mm(nov[i], m["nov_lo"], m["nov_hi"]) if nov[i] is not None else 0.5
            out.append({"context_kind": e["context_kind"], "context_id": e["context_id"],
                        "action_type": o["action_type"], "key": o["key"],
                        "available": o["available"], "gate": o["gate"],
                        "params": o.get("params") or {},
                        "impact": round(f1[i] - g[order[i]], 5),
                        "exploit": round(exploit, 4), "explore": round(explore, 4),
                        "score": round((1 - beta) * exploit + beta * explore, 4)})
        out.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1))
        return out


def _mm(v, lo, hi):
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.5


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    root = sys.argv[2] if len(sys.argv) > 2 else RUNS_ROOT
    if cmd == "train":
        print(json.dumps(train(root), indent=2))
    elif cmd == "report":
        d = gather(root)
        print("runs=%(runs)d campaigns=%(campaigns)d decisions=%(n_decisions)d "
              "labelled_rows=%%d unlabelled=%(skipped_unlabelled)d" % d % len(d["full"]))
        if d["full"]:
            num, cat = F.split_columns(d["full"])
            print("features: %d numeric + %d categorical" % (len(num), len(cat)))
