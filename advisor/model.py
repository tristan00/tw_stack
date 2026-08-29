from __future__ import annotations

import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
import memory as MEM
from base_model import (CB_MAIN_ITERATIONS, RUNS_ROOT, TARGET_PARTS,
                        TRAIN_WINDOW_CAMPAIGNS, target,
                        SHORT_HORIZON, SHORT_WEIGHT, decision_deltas, fit_es,
                        grouped_split, params, _ranks)
from store import DecisionStore, IncompatibleStore

MODEL_DIR = common.MODEL_GLOBAL
MODEL_FILE = "model.cbm"
MIN_ROWS = 40


def _note_memory(mem, rec, taken, was_counted, pb_map):
    if not taken or not taken[2]:
        return
    st = None
    for e in rec.get("entities") or []:
        if (e.get("context_kind") == taken[0]
                and str(e.get("context_id")) == str(taken[1])):
            st = e.get("state") or {}
            break
    mem.note_pick(taken[0], taken[1], taken[2], st, was_counted)
    hit = pb_map.get(rec.get("decision_id"))
    if hit is not None:
        mem.note_prebattle(taken[0], taken[1], hit["action_type"], hit["key"],
                           hit["params"], None, hit["chosen"], hit["result"],
                           hit["casualties"], zone=hit["zone"])


def gather(runs_root=RUNS_ROOT, window=TRAIN_WINDOW_CAMPAIGNS):
    dbs = common.run_dbs(runs_root)
    full, ys, groups, confirmed = [], [], [], []
    n_decisions = skipped = 0
    campaigns_seen = set()
    skipped_dbs = []
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
                series = s.target_series()
                pb_map = MEM.prebattle_attributions(s.con)
                floor = s.window_floor(window)
                act_idx, mov_idx, hist, counts, mems = {}, {}, {}, {}, {}
                for rec, taken, was_counted in s.taken_rows(min_decision=floor):
                    n_decisions += 1
                    campaigns_seen.add(rec.get("campaign_id"))
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
                    mem = mems.setdefault(rec.get("campaign_id"), MEM.CampaignMemory())
                    mem.begin_turn(rec.get("turn"))
                    mem.stamp(rec["campaign"])
                    if taken and taken[2] != "noop":
                        h.append(taken[2])
                        del h[:-F.PREV_ACTIONS]
                        F.bump_action_counts(cnt, taken[2])
                    turns = series.get(rec.get("campaign_id")) or {}
                    y = target(decision_deltas(rec.get("campaign"), turns,
                                               rec.get("turn")))
                    triples = F.decision_rows(rec) if y is not None else None
                    _note_memory(mem, rec, taken, was_counted, pb_map)
                    if y is None:
                        skipped += 1
                        continue
                    if not triples:
                        continue
                    row = triples[0][2]
                    if F.MODEL_COLUMNS_ENABLED:
                        row = {k: row[k] for k in F.MODEL_COLUMNS & row.keys()}
                    full.append(row)
                    ys.append(y)
                    groups.append(rec.get("campaign_id"))
                    confirmed.append(was_counted)
        finally:
            s.close()
    return {"full": full, "y": ys, "groups": groups,
            "confirmed": confirmed, "n_confirmed": sum(1 for c in confirmed if c),
            "n_decisions": n_decisions, "skipped_unlabelled": skipped,
            "runs": len(dbs) - len(skipped_dbs), "skipped_dbs": skipped_dbs,
            "campaigns": len(campaigns_seen)}


def train(runs_root=RUNS_ROOT, window=TRAIN_WINDOW_CAMPAIGNS):
    from catboost import Pool
    data = gather(runs_root, window=window)
    rows, y = data["full"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, **_counts(data)}
    os.makedirs(MODEL_DIR, exist_ok=True)
    groups = data.get("groups")
    fit_report = {}
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    m = fit_es(X, y, cat_idx, groups, "model", fit_report,
               iterations=CB_MAIN_ITERATIONS)
    val, _trn = grouped_split(len(X), groups)
    if val:
        pv = list(m.predict(Pool([X[i] for i in val], cat_features=cat_idx)))
        yv = [y[i] for i in val]
        ybar = sum(yv) / len(yv)
        sst = sum((v - ybar) ** 2 for v in yv)
        if sst > 0:
            sse = sum((a - b) ** 2 for a, b in zip(yv, pv))
            fit_report["model"]["val_r2"] = round(1.0 - sse / sst, 5)
    preds = list(m.predict(Pool(X, cat_features=cat_idx)))
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    meta = {"num": num, "cat": cat,
            "pred_lo": min(preds), "pred_hi": max(preds),
            "mae_in_sample": round(mae, 5), "fit": fit_report, "rows": len(rows),
            "campaigns": sorted(set(data["groups"])),
            "short_horizon": SHORT_HORIZON, "short_weight": SHORT_WEIGHT,
            "target": ("gain(future_max - decision_snapshot) over %s"
                       % ",".join(TARGET_PARTS))}
    stage = MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    m.save_model(os.path.join(stage, MODEL_FILE))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    for name in (MODEL_FILE, "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    for stale in ("e1.cbm", "e2.cbm"):
        p = os.path.join(MODEL_DIR, stale)
        if os.path.exists(p):
            os.remove(p)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5),
            "fit": fit_report, "params": params(iterations=CB_MAIN_ITERATIONS),
            **_counts(data)}


def _counts(data):
    return {k: data[k] for k in ("n_decisions", "skipped_unlabelled", "runs", "campaigns")}


class Ranker:

    def __init__(self, model_dir=MODEL_DIR):
        self.ready = False
        self.meta = None
        self.model = None
        meta_path = os.path.join(model_dir, "meta.json")
        model_path = os.path.join(model_dir, MODEL_FILE)
        if not os.path.isfile(meta_path) or not os.path.isfile(model_path):
            return
        try:
            from catboost import CatBoostRegressor
            self.meta = json.load(open(meta_path))
            self.model = CatBoostRegressor()
            self.model.load_model(model_path)
            self.ready = True
        except Exception as e:
            sys.stderr.write("model: load failed -> %s\n" % repr(e)[:160])
            self.ready = False

    def score(self, record):
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
        preds = list(self.model.predict(Pool(X, cat_features=list(
            range(len(m["num"]), len(m["num"]) + len(m["cat"]))))))
        pct = _ranks(preds)
        out = []
        for i, (e, o, _r) in enumerate(triples):
            out.append({"context_kind": e["context_kind"], "context_id": e["context_id"],
                        "action_type": o["action_type"], "key": o["key"],
                        "params": o.get("params") or {},
                        "impact": round(preds[i], 5),
                        "pct_global": round(pct[i], 4),
                        "exploit": round(pct[i], 4), "score": round(pct[i], 4)})
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
