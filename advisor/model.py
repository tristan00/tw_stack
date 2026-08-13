from __future__ import annotations

import glob
import json
import os
import pickle
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
from base_model import (RUNS_ROOT, TARGET_PARTS, target, SHORT_HORIZON, SHORT_WEIGHT,
                        decision_deltas, fit_es, future_best, params,
                        regressor, _pct, _ranks, _sd)
from store import DecisionStore, IncompatibleStore

MODEL_DIR = common.MODEL_GLOBAL
LOCAL_MODEL_DIR = common.MODEL_LOCAL
W_LOCAL = 0.25
LOCAL_KINDS = ("lord", "hero", "province")
MIN_ROWS = 40


def _local_delta(eseries, rec, taken):
    kind, cid = taken[0], str(taken[1])
    turns = (eseries.get(rec.get("campaign_id")) or {}).get((kind, cid))
    if not turns:
        return None
    turn = int(rec.get("turn") or 0)
    if kind in ("lord", "hero"):
        st = next((e.get("state") or {} for e in rec.get("entities") or []
                   if e.get("context_kind") == kind and str(e.get("context_id")) == cid), {})
        base = F._f(st.get("rank"))
    else:
        prior = [t for t in turns if t < turn and turns[t] is not None]
        base = turns[max(prior)] if prior else None
    if base is None:
        return None
    vals = [turns[t] for t in turns if t >= turn and turns[t] is not None]
    return (max(vals) - base) if vals else None


def gather(runs_root=RUNS_ROOT):
    dbs = common.run_dbs(runs_root)
    decisions, series, eseries, skipped_dbs = [], {}, {}, []
    for db in dbs:
        run_dir = os.path.dirname(db)
        try:
            s = DecisionStore(run_dir, readonly=True)
        except IncompatibleStore as e:
            skipped_dbs.append(os.path.basename(run_dir))
            sys.stderr.write("model: skipping %s -> %s\n" % (run_dir, str(e)[:120]))
            continue
        try:
            with s.snapshot_read():
                for rec, taken, counted in s.labelled_decisions():
                    decisions.append((rec, taken, counted))
                for camp, turns in s.target_series().items():
                    series.setdefault(camp, {}).update(turns)
                for camp, ents in s.entity_series().items():
                    for k, tv in ents.items():
                        eseries.setdefault(camp, {}).setdefault(k, {}).update(tv)
        finally:
            s.close()
    full, state, ys, groups, confirmed = [], [], [], [], []
    ylocal = []
    skipped = 0
    act_idx = {}
    mov_idx = {}
    hist = {}
    counts = {}
    deltas, ldeltas = [], []
    ldist = {}
    for rec, taken, was_counted in decisions:
        ik = (rec.get("campaign_id"), rec.get("turn"))
        act_idx[ik] = act_idx.get(ik, 0) + 1
        (rec.setdefault("campaign", {}))["act_index"] = act_idx[ik]
        rec["campaign"]["move_index"] = mov_idx.get(ik, 0) + 1
        if taken and taken[2] == "move":
            mov_idx[ik] = mov_idx.get(ik, 0) + 1
        h = hist.setdefault(rec.get("campaign_id"), [])
        cnt = counts.setdefault(rec.get("campaign_id"), {})
        F.stamp_prev_actions(rec["campaign"], h)
        F.stamp_action_counts(rec["campaign"], cnt)
        if taken and taken[2] != "noop":
            h.append(taken[2])
            del h[:-F.PREV_ACTIONS]
            F.bump_action_counts(cnt, taken[2])
        turns = series.get(rec.get("campaign_id")) or {}
        d = decision_deltas(rec.get("campaign"), turns, rec.get("turn"))
        deltas.append(d)
        ld = _local_delta(eseries, rec, taken) if (taken and taken[0] in LOCAL_KINDS) else None
        ldeltas.append(ld)
        if ld is not None:
            ldist.setdefault(taken[0], []).append(ld)
    for k in ldist:
        ldist[k].sort()
    for (rec, taken, was_counted), d, ld in zip(decisions, deltas, ldeltas):
        y = target(d)
        if y is None:
            skipped += 1
            continue
        bases = {}
        for ent, offer, row in F.decision_rows(rec, base_sink=bases):
            if (ent["context_kind"], str(ent["context_id"]),
                    offer["action_type"], str(offer["key"])) != taken:
                continue
            full.append(row)
            state.append(bases.get(str(ent["context_id"])) or F.state_row(rec, ent))
            ys.append(y)
            ylocal.append(_pct(ld, ldist.get(taken[0]) or []) if ld is not None else None)
            groups.append(rec.get("campaign_id"))
            confirmed.append(was_counted)
            break
    return {"full": full, "state": state, "y": ys, "y_local": ylocal,
            "n_local": sum(1 for v in ylocal if v is not None), "groups": groups,
            "confirmed": confirmed, "n_confirmed": sum(1 for c in confirmed if c),
            "n_decisions": len(decisions), "skipped_unlabelled": skipped,
            "runs": len(dbs) - len(skipped_dbs), "skipped_dbs": skipped_dbs,
            "campaigns": len(series)}


def train(runs_root=RUNS_ROOT):
    from catboost import Pool
    data = gather(runs_root)
    rows, srows, y = data["full"], data["state"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, **_counts(data)}
    os.makedirs(MODEL_DIR, exist_ok=True)
    groups = data.get("groups")
    fit_report = {}
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    e1 = fit_es(X, y, cat_idx, groups, "e1", fit_report)
    snum, scat = F.split_columns(srows)
    Xs = F.matrix(srows, snum, scat)
    scat_idx = list(range(len(snum), len(snum) + len(scat)))
    e2 = fit_es(Xs, y, scat_idx, groups, "e2", fit_report)
    preds = list(e1.predict(Pool(X, cat_features=cat_idx)))
    e2preds = list(e2.predict(Pool(Xs, cat_features=scat_idx)))
    impacts = [a - b for a, b in zip(preds, e2preds)]
    data["_impacts"] = impacts
    sd_global = _sd(impacts)
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat,
            "exp_lo": min(impacts), "exp_hi": max(impacts), "sd_global": sd_global,
            "mae_in_sample": round(mae, 5), "fit": fit_report,
            "w_local": W_LOCAL, "rows": len(rows),
            "campaigns": sorted(set(data["groups"])),
            "short_horizon": SHORT_HORIZON, "short_weight": SHORT_WEIGHT,
            "target": ("gain(future_max - decision_snapshot) over %s"
                       % ",".join(TARGET_PARTS))}
    stage = MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    e1.save_model(os.path.join(stage, "e1.cbm"))
    e2.save_model(os.path.join(stage, "e2.cbm"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    for name in ("e1.cbm", "e2.cbm", "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    local = _train_local(data, num, cat, snum, scat, sd_global)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5),
            "fit": fit_report, "local": local, "params": params(), **_counts(data)}


def _train_local(data, num, cat, snum, scat, sd_global):
    from catboost import Pool
    idx = [i for i, v in enumerate(data["y_local"]) if v is not None]
    if len(idx) < MIN_ROWS:
        return {"trained": False, "rows": len(idx), "need": MIN_ROWS}
    rows = [data["full"][i] for i in idx]
    srows = [data["state"][i] for i in idx]
    yl = [data["y_local"][i] for i in idx]
    lgroups = [data["groups"][i] for i in idx] if data.get("groups") else None
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    Xs = F.matrix(srows, snum, scat)
    scat_idx = list(range(len(snum), len(snum) + len(scat)))
    lfit = {}
    e1 = fit_es(X, yl, cat_idx, lgroups, "local_e1", lfit)
    e2 = fit_es(Xs, yl, scat_idx, lgroups, "local_e2", lfit)
    li = [a - b for a, b in zip(e1.predict(Pool(X, cat_features=cat_idx)),
                                e2.predict(Pool(Xs, cat_features=scat_idx)))]
    sd_local = _sd(li)
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat,
            "sd_local": sd_local, "rows": len(idx), "fit": lfit,
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
    return {"trained": True, "rows": len(idx), "fit": lfit, "sd_local": round(sd_local, 6),
            "sd_global": round(sd_global, 6)}


def _counts(data):
    return {k: data[k] for k in ("n_decisions", "skipped_unlabelled", "runs", "campaigns")}


class Ranker:

    def __init__(self, model_dir=MODEL_DIR):
        self.ready = False
        self.meta = None
        self.e1 = self.e2 = None
        meta_path = os.path.join(model_dir, "meta.json")
        if not os.path.isfile(meta_path):
            return
        try:
            from catboost import CatBoostRegressor
            self.meta = json.load(open(meta_path))
            self.e1 = CatBoostRegressor(); self.e1.load_model(os.path.join(model_dir, "e1.cbm"))
            self.e2 = CatBoostRegressor(); self.e2.load_model(os.path.join(model_dir, "e2.cbm"))
            self.ready = True
        except Exception as e:
            sys.stderr.write("model: load failed -> %s\n" % repr(e)[:160])
            self.ready = False
            return
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
            sys.stderr.write("model: local pair not loaded -> %s\n" % repr(e)[:160])
            self.lmeta = self.l1 = self.l2 = None

    def score(self, record, w_local=None):
        triples = F.decision_rows(record)
        if not self.ready or not triples:
            return [{"context_kind": e["context_kind"], "context_id": e["context_id"],
                     "action_type": o["action_type"], "key": o["key"],
                     "params": o.get("params") or {},
                     "exploit": None, "impact": None, "score": None}
                    for e, o, _r in triples]
        from catboost import Pool
        m = self.meta
        rows = [r for _e, _o, r in triples]
        X = F.matrix(rows, m["num"], m["cat"])
        f1 = list(self.e1.predict(Pool(X, cat_features=list(
            range(len(m["num"]), len(m["num"]) + len(m["cat"]))))))
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
        impacts = [f1[i] - g[order[i]] for i in range(len(rows))]
        local_imp = [li[i] if (li is not None and triples[i][0]["context_kind"] in lkinds) else None
                     for i in range(len(rows))]
        pg_rank = _ranks(impacts)
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
                exploit = ((pctg - 0.5) + w * (pctl - 0.5)) / (1.0 + w) + 0.5
            else:
                exploit = pctg
            out.append({"context_kind": e["context_kind"], "context_id": e["context_id"],
                        "action_type": o["action_type"], "key": o["key"],
                        "params": o.get("params") or {},
                        "impact": round(impact, 5),
                        "impact_local": (round(lo_imp, 5) if lo_imp is not None else None),
                        "pct_global": round(pctg, 4),
                        "pct_local": (round(pctl, 4) if pctl is not None else None),
                        "w_local": w,
                        "exploit": round(exploit, 4), "score": round(exploit, 4)})
        out.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1))
        return out


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
