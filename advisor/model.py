from __future__ import annotations

import glob
import json
import os
import pickle
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F                                       # noqa: E402
from store import DecisionStore, IncompatibleStore        # noqa: E402

MODEL_DIR = r"D:\twdata\models\global"
LOCAL_MODEL_DIR = r"D:\twdata\models\local"
# local reward weight: one SD of local impact is worth W_LOCAL SDs of global.
# inference-time dial -- changing it needs no refit.
W_LOCAL = 0.25
# per-entity local reward: character level / summed main-settlement level
LOCAL_KINDS = ("lord", "hero", "province")
RUNS_ROOT = "D:/twdata/runs/human"
BETA = 0.10                 # explore weight
VALUE_PARTS = ("income", "settlements", "power_rank", "allies", "vassals", "lord_level")
TARGET_PARTS = ("settlements", "power_rank", "lord_level", "vassals")
LOWER_IS_BETTER = ("power_rank",)
MIN_ROWS = 40


def _mm0(v, lo, hi):
    """min-max to [0,1]; a degenerate range gives 0.0."""
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0


def _growth(turns, turn, part):
    """Improvement of `part` after `turn`: best future value minus now, sign-flipped for
    LOWER_IS_BETTER parts so every delta is higher-is-better. None if unlabelable."""
    now = turns.get(turn, {}).get(part)
    if now is None:
        return None
    vals = [turns[t].get(part) for t in turns if t > turn and turns[t].get(part) is not None]
    if not vals:
        return None
    return (now - min(vals)) if part in LOWER_IS_BETTER else (max(vals) - now)


def target_ranges(series):
    """Per part: the growth delta of every labelled (campaign, turn), sorted. Growth, not
    level: an absolute target makes turn-3 and turn-30 decisions train against different
    label regimes and rewards sitting on wealth; deltas are stationary across stage."""
    deltas = {p: [] for p in VALUE_PARTS}
    for turns in series.values():
        for t in sorted(turns):
            for p in VALUE_PARTS:
                d = _growth(turns, t, p)
                if d is not None:
                    deltas[p].append(d)
    return {"dist": {p: sorted(deltas[p]) for p in VALUE_PARTS}}


def _pct(v, sample, lower_is_better=False):
    """Mid-rank percentile of `v` within `sample`, in [0,1], always "higher is better"."""
    n = len(sample)
    if n == 0:
        return 0.5
    below = sum(1 for x in sample if x < v)
    equal = sum(1 for x in sample if x == v)
    pct = (below + 0.5 * equal) / n
    return (1.0 - pct) if lower_is_better else pct


def target(series, campaign, turn, ranges):
    """Mean percentile of each TARGET_PART's GROWTH after `turn`; None if unlabelled."""
    turns = series.get(campaign) or {}
    turn = int(turn or 0)
    if turn not in turns:
        return None
    if not any(t > turn for t in turns):
        return None
    parts = []
    for p in TARGET_PARTS:
        d = _growth(turns, turn, p)
        if d is None:
            continue
        parts.append(_pct(d, ranges["dist"].get(p) or []))
    return (sum(parts) / len(parts)) if parts else None


def local_ranges(eseries):
    """Per context_kind: the growth delta of every labelled (entity, turn), sorted. Kinds are
    NOT pooled -- a lord level and a settlement level have different scales."""
    deltas = {}
    for ents in eseries.values():
        for (kind, _cid), turns in ents.items():
            for t in sorted(turns):
                now = turns.get(t)
                if now is None:
                    continue
                vals = [v for ft, v in turns.items() if ft > t and v is not None]
                if vals:
                    deltas.setdefault(kind, []).append(max(vals) - now)
    return {k: sorted(v) for k, v in deltas.items()}


def local_target(eseries, campaign, kind, cid, turn, lranges):
    """Percentile of this entity's GROWTH (future max minus now), within its own kind."""
    ents = eseries.get(campaign) or {}
    turns = ents.get((kind, str(cid)))
    if not turns:
        return None
    turn = int(turn or 0)
    now = turns.get(turn)
    if now is None:
        return None
    vals = [turns[t] for t in turns if t > turn and turns[t] is not None]
    if not vals:
        return None
    return _pct(max(vals) - now, lranges.get(kind) or [])


def gather(runs_root=RUNS_ROOT):
    """The chosen action of every labelled decision point, as (E1 rows, E2 rows, y)."""
    dbs = sorted(glob.glob(os.path.join(runs_root, "*", "decisions.sqlite")))
    decisions, series, eseries, skipped_dbs = [], {}, {}, []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            s = DecisionStore(run_dir)
        except IncompatibleStore as e:
            skipped_dbs.append(os.path.basename(run_dir))
            sys.stderr.write("model: skipping %s -> %s\n" % (run_dir, str(e)[:120]))
            continue
        try:
            for rec, taken, counted in s.labelled_decisions():
                decisions.append((rec, taken, counted))
            for camp, turns in s.target_series().items():
                series.setdefault(camp, {}).update(turns)
            for camp, ents in s.entity_series().items():
                for k, tv in ents.items():
                    eseries.setdefault(camp, {}).setdefault(k, {}).update(tv)
        finally:
            s.close()
    ranges = target_ranges(series)
    lranges = local_ranges(eseries)
    full, state, ys, groups, confirmed = [], [], [], [], []
    ylocal = []
    skipped = 0
    for rec, taken, was_counted in decisions:
        y = target(series, rec.get("campaign_id"), rec.get("turn"), ranges)
        if y is None:
            skipped += 1
            continue
        for ent, offer, row in F.decision_rows(rec):
            if (ent["context_kind"], str(ent["context_id"]),
                    offer["action_type"], str(offer["key"])) != taken:
                continue
            full.append(row)
            state.append(F.state_row(rec, ent))
            ys.append(y)
            ylocal.append(local_target(eseries, rec.get("campaign_id"),
                                       ent["context_kind"], ent["context_id"],
                                       rec.get("turn"), lranges))
            groups.append(rec.get("campaign_id"))
            confirmed.append(was_counted)
    return {"full": full, "state": state, "y": ys, "y_local": ylocal,
            "n_local": sum(1 for v in ylocal if v is not None), "groups": groups,
            "confirmed": confirmed, "n_confirmed": sum(1 for c in confirmed if c),
            "n_decisions": len(decisions), "skipped_unlabelled": skipped,
            "runs": len(dbs) - len(skipped_dbs), "skipped_dbs": skipped_dbs,
            "campaigns": len(series)}


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
                           verbose=0, train_dir=r"D:\twdata\tmp\catboost")
    e1.fit(Pool(X, y, cat_features=cat_idx))
    snum, scat = F.split_columns(srows)
    Xs = F.matrix(srows, snum, scat)
    scat_idx = list(range(len(snum), len(snum) + len(scat)))
    e2 = CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05, loss_function="RMSE",
                          verbose=0, train_dir=r"D:\twdata\tmp\catboost")
    e2.fit(Pool(Xs, y, cat_features=scat_idx))
    Xa, cat_maps = _encode(rows, num, cat)
    iso = IsolationForest(n_estimators=200, random_state=0, n_jobs=-1)
    iso.fit(Xa)
    nov = [-s for s in iso.score_samples(Xa)]
    preds = list(e1.predict(Pool(X, cat_features=cat_idx)))
    e2preds = list(e2.predict(Pool(Xs, cat_features=scat_idx)))
    impacts = [a - b for a, b in zip(preds, e2preds)]
    data["_impacts"] = impacts
    sd_global = _sd(impacts)
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat,
            "exp_lo": min(impacts), "exp_hi": max(impacts), "sd_global": sd_global,
            "w_local": W_LOCAL,
            "nov_lo": min(nov), "nov_hi": max(nov), "rows": len(rows),
            "campaigns": sorted(set(data["groups"])), "beta": BETA,
            "target": "growth(best_future-now: %s)" % ",".join(TARGET_PARTS)}
    # stage then os.replace: the four artefacts must land together or not at all
    stage = MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    e1.save_model(os.path.join(stage, "e1.cbm"))
    e2.save_model(os.path.join(stage, "e2.cbm"))
    pickle.dump({"iso": iso, "cat_maps": cat_maps}, open(os.path.join(stage, "iso.pkl"), "wb"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    for name in ("e1.cbm", "e2.cbm", "iso.pkl", "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    local = _train_local(data, num, cat, snum, scat, sd_global)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5),
            "local": local, **_counts(data)}


def _sd(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def _ranks(vals):
    """Fractional rank of each value in [0,1], ties sharing the average rank.

    Ranked WITHIN the offer set being scored, not against the training distribution. Training rows
    are only the actions the policy chose, so they are biased toward good ones -- ranking live
    offers against them pushed the majority below anything ever picked and collapsed them to 0.
    Ranking within the decision is uniform by construction and is what the score is used for:
    choosing among these offers, now.
    """
    n = len(vals)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: vals[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        share = (i + j) / 2.0 / (n - 1)             # average rank across the tie group
        for k in range(i, j + 1):
            out[order[k]] = share
        i = j + 1
    return out


def _train_local(data, num, cat, snum, scat, sd_global):
    """Second E1/E2 pair fitted on the LOCAL label, over the rows that have one.

    Returns a status dict. Never raises: no local rows simply means no local model, and the
    ranker then scores on global alone.
    """
    from catboost import CatBoostRegressor, Pool
    idx = [i for i, v in enumerate(data["y_local"]) if v is not None]
    if len(idx) < MIN_ROWS:
        return {"trained": False, "rows": len(idx), "need": MIN_ROWS}
    rows = [data["full"][i] for i in idx]
    srows = [data["state"][i] for i in idx]
    yl = [data["y_local"][i] for i in idx]
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    Xs = F.matrix(srows, snum, scat)
    scat_idx = list(range(len(snum), len(snum) + len(scat)))
    e1 = CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05, loss_function="RMSE",
                           verbose=0, train_dir=r"D:\twdata\tmp\catboost")
    e1.fit(Pool(X, yl, cat_features=cat_idx))
    e2 = CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05, loss_function="RMSE",
                           verbose=0, train_dir=r"D:\twdata\tmp\catboost")
    e2.fit(Pool(Xs, yl, cat_features=scat_idx))
    li = [a - b for a, b in zip(e1.predict(Pool(X, cat_features=cat_idx)),
                                e2.predict(Pool(Xs, cat_features=scat_idx)))]
    sd_local = _sd(li)
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat,
            "sd_local": sd_local, "rows": len(idx),
            "kinds": sorted({r.get("ctx_kind") or r.get("context_kind") or "?" for r in rows})}
    stage = LOCAL_MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    e1.save_model(os.path.join(stage, "e1.cbm"))
    e2.save_model(os.path.join(stage, "e2.cbm"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    os.makedirs(LOCAL_MODEL_DIR, exist_ok=True)
    for name in ("e1.cbm", "e2.cbm", "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(LOCAL_MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    return {"trained": True, "rows": len(idx), "sd_local": round(sd_local, 6),
            "sd_global": round(sd_global, 6)}


def _counts(data):
    return {k: data[k] for k in ("n_decisions", "skipped_unlabelled", "runs", "campaigns")}


def _encode(rows, num, cat, cat_maps=None):
    """Numeric matrix for IsolationForest; categoricals ordinal-encoded, unseen -> a fresh index."""
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
    """Loaded model set; `ready` is False when nothing is trained yet."""

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
            return
        # local pair is OPTIONAL: absent means score on global alone
        self.lmeta = None
        self.l1 = self.l2 = None
        lmeta_path = os.path.join(LOCAL_MODEL_DIR, "meta.json")
        try:
            if os.path.isfile(lmeta_path):
                from catboost import CatBoostRegressor as _C
                self.lmeta = json.load(open(lmeta_path))
                self.l1 = _C(); self.l1.load_model(os.path.join(LOCAL_MODEL_DIR, "e1.cbm"))
                self.l2 = _C(); self.l2.load_model(os.path.join(LOCAL_MODEL_DIR, "e2.cbm"))
        except Exception as e:
            # a missing or broken LOCAL pair must not disable the global ranker
            sys.stderr.write("model: local pair not loaded -> %s\n" % repr(e)[:160])
            self.lmeta = self.l1 = self.l2 = None

    def score(self, record, beta=BETA, w_local=None):
        """Every offer of a stored decision record, scored, sorted best-first."""
        triples = F.decision_rows(record)
        if not self.ready or not triples:
            # imports no catboost/sklearn: this path must work without them installed
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
        # E2 is per entity, not per offer
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
        # LOCAL impacts, when the second pair is loaded. An entity kind with no local label gets
        # None and is scored on global alone -- no penalty for lacking a local component.
        li = None
        lmeta = getattr(self, "lmeta", None) or {}
        lkinds = set(lmeta.get("kinds") or ())
        w = float(w_local if w_local is not None else (m.get("w_local") or W_LOCAL))
        if getattr(self, "l1", None) is not None and lkinds:
            lm = self.lmeta
            Xl = F.matrix(rows, lm["num"], lm["cat"])
            lf1 = list(self.l1.predict(Pool(Xl, cat_features=list(
                range(len(lm["num"]), len(lm["num"]) + len(lm["cat"]))))))
            Xls = F.matrix(srows, lm["state_num"], lm["state_cat"])
            lg = list(self.l2.predict(Pool(Xls, cat_features=list(
                range(len(lm["state_num"]), len(lm["state_num"]) + len(lm["state_cat"]))))))
            li = [lf1[j] - lg[order[j]] for j in range(len(rows))]
        # PERCENTILE BLEND. Each impact is ranked WITHIN this decision's offers and centred, giving
        # a uniform [-0.5, +0.5]; the two halves are then added. Uniform by construction, so no
        # distributional assumption and nothing to clamp against.
        impacts = [f1[i] - g[order[i]] for i in range(len(rows))]
        # Gate on the kinds the local pair was ACTUALLY trained on, read from its own meta -- not
        # the LOCAL_KINDS constant, which can drift out of step with a trained artefact. A
        # campaign-context offer has no local meaning and must not be ranked on one.
        local_imp = [li[i] if (li is not None and triples[i][0]["context_kind"] in lkinds) else None
                     for i in range(len(rows))]
        pg_rank = _ranks(impacts)
        # local ranks are taken among the covered rows ONLY, so an uncovered offer neither gets a
        # rank nor shifts anyone else's
        lidx = [i for i, v in enumerate(local_imp) if v is not None]
        pl_rank = [None] * len(rows)
        for pos, val in zip(lidx, _ranks([local_imp[i] for i in lidx])):
            pl_rank[pos] = val

        out = []
        for i, (e, o, _r) in enumerate(triples):
            impact = impacts[i]
            lo_imp = local_imp[i]
            pctg = pg_rank[i]
            pctl = pl_rank[i]
            if pctl is not None:
                # divide by the weight actually applied, so an offer WITHOUT a local component is
                # not shrunk toward the middle relative to one that has it
                exploit = ((pctg - 0.5) + w * (pctl - 0.5)) / (1.0 + w) + 0.5
            else:
                exploit = pctg
            explore = _mm(nov[i], m["nov_lo"], m["nov_hi"]) if nov[i] is not None else 0.5
            out.append({"context_kind": e["context_kind"], "context_id": e["context_id"],
                        "action_type": o["action_type"], "key": o["key"],
                        "available": o["available"], "gate": o["gate"],
                        "params": o.get("params") or {},
                        "impact": round(impact, 5),
                        "impact_local": (round(lo_imp, 5) if lo_imp is not None else None),
                        # the two halves of exploit, so the UI can show the split
                        "pct_global": round(pctg, 4),
                        "pct_local": (round(pctl, 4) if pctl is not None else None),
                        "w_local": w,
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
