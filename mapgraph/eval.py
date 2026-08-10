from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor"))
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

try:
    from mapgraph import schema as S
    from mapgraph import build as B
    from mapgraph import train as T
    from mapgraph import rank as RK
except ImportError:
    import schema as S
    import build as B
    import train as T
    import rank as RK

BOOT = 1000


def _spearman(a, b):
    from scipy.stats import spearmanr
    rho, p = spearmanr(a, b)
    return float(rho), float(p)


def _boot_ci(pairs, seed=0, n=BOOT):
    by_camp = {}
    for camp, q, y in pairs:
        by_camp.setdefault(camp, []).append((q, y))
    camps = sorted(by_camp)
    rng = random.Random(seed)
    rhos = []
    for _ in range(n):
        sample = [rng.choice(camps) for _ in camps]
        qa, ya = [], []
        for c in sample:
            for q, y in by_camp[c]:
                qa.append(q)
                ya.append(y)
        if len(set(qa)) < 2 or len(set(ya)) < 2:
            continue
        rhos.append(_spearman(qa, ya)[0])
    rhos.sort()
    if not rhos:
        return None, None
    return rhos[int(0.025 * len(rhos))], rhos[int(0.975 * len(rhos))]


def main():
    import torch
    import features as F
    from base_model import grouped_split, fit_es
    from torch_geometric.loader import DataLoader

    w = T.walk()
    examples = w["examples"]
    if len(examples) < S.MIN_ROWS:
        raise SystemExit("eval: only %d examples" % len(examples))
    r = RK.Ranker()
    if not r.ready:
        raise SystemExit("eval: gnn model not trained (dir=%s) -- run train.py train first"
                         % r.model_dir)
    known = set(r.meta.get("campaigns") or [])
    if known:
        examples = [e for e in examples if e["campaign_id"] in known]
    ys = [e["y"] for e in examples]
    groups = [e["campaign_id"] for e in examples]
    val_idx, trn_idx = grouped_split(len(examples), groups)
    if not val_idx:
        raise SystemExit("eval: too few campaigns for a held-out split")
    y_mean = float(r.meta["y_mean"])
    y_sd = float(r.meta["y_sd"])
    datas = T._tensorize(examples, w["dists"])
    net = r.net
    net.eval()
    val = [datas[i] for i in val_idx]
    qs, vs = [], []
    with torch.no_grad():
        for batch in DataLoader(val, batch_size=64):
            out = net(batch)
            qs.extend(out["q"].tolist())
            vs.extend(out["v"].tolist())
    y_val = [ys[i] for i in val_idx]
    q_raw = [q * y_sd + y_mean for q in qs]
    rmse_gnn = (sum((a - b) ** 2 for a, b in zip(q_raw, y_val)) / len(y_val)) ** 0.5

    from store import DecisionStore
    rows, srows = [], []
    kept = []
    for i, ex in enumerate(examples):
        run_dir, did = ex["src"]
        kept.append((i, run_dir, did))
    by_run = {}
    for i, run_dir, did in kept:
        by_run.setdefault(run_dir, {})[did] = i
    full = [None] * len(examples)
    state = [None] * len(examples)
    act_idx, mov_idx, hist, counts = {}, {}, {}, {}
    for run_dir, dids in sorted(by_run.items()):
        s = DecisionStore(run_dir, readonly=True)
        try:
            with s.snapshot_read():
                for rec, taken, counted in s.labelled_decisions():
                    ik = (rec.get("campaign_id"), rec.get("turn"))
                    act_idx[ik] = act_idx.get(ik, 0) + 1
                    rec.setdefault("campaign", {})["act_index"] = act_idx[ik]
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
                    i = dids.get(rec.get("decision_id"))
                    if i is None:
                        continue
                    bases = {}
                    for ent, offer, row in F.decision_rows(rec, base_sink=bases):
                        if (ent["context_kind"], str(ent["context_id"]),
                                offer["action_type"], str(offer["key"])) != taken:
                            continue
                        full[i] = row
                        state[i] = bases.get(str(ent["context_id"])) or F.state_row(rec, ent)
                        break
        finally:
            s.close()
    cb_idx = [i for i in range(len(examples)) if full[i] is not None]
    cb_val = [i for i in val_idx if full[i] is not None]
    frows = [full[i] for i in cb_idx]
    fgroups = [groups[i] for i in cb_idx]
    fy = [ys[i] for i in cb_idx]
    num, cat = F.split_columns(frows)
    X = F.matrix(frows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    report = {}
    e1 = fit_es(X, fy, cat_idx, fgroups, "e1", report)
    from catboost import Pool
    pos = {gi: k for k, gi in enumerate(cb_idx)}
    Xv = [X[pos[i]] for i in cb_val]
    yv = [ys[i] for i in cb_val]
    preds = list(e1.predict(Pool(Xv, cat_features=cat_idx)))
    rmse_cb = (sum((a - b) ** 2 for a, b in zip(preds, yv)) / len(yv)) ** 0.5

    rho, p = _spearman(q_raw, y_val)
    pairs = [(groups[i], q_raw[k], y_val[k]) for k, i in enumerate(val_idx)]
    lo, hi = _boot_ci(pairs)

    rng = random.Random(0)
    sample = rng.sample(val_idx, min(200, len(val_idx)))
    impacts = []
    agree = comp = 0
    for i in sample:
        run_dir, did = examples[i]["src"]
        s = DecisionStore(run_dir, readonly=True)
        try:
            with s.snapshot_read():
                rec = s.read_decision(did)
        finally:
            s.close()
        rec.setdefault("campaign", {})["act_index"] = examples[i].get("act_index") or 0
        elig = [dict(o, context_kind=e["context_kind"], context_id=e["context_id"])
                for e in rec.get("entities") or [] for o in e.get("offers") or []
                if o.get("available")]
        if len(elig) < 2:
            continue
        try:
            imp = r.score_elig(elig, rec)
        except ValueError:
            continue
        impacts.extend(imp)
        comp += 1
    import statistics
    impact_sd = statistics.pstdev(impacts) * y_sd if len(impacts) > 1 else 0.0

    go = {"rmse_ratio_ok": bool(rmse_gnn <= 1.05 * rmse_cb),
          "spearman_ci_excludes_0": bool(lo is not None and lo > 0.0),
          "impact_sd_ok": bool(impact_sd > 0.05 * y_sd)}
    print(json.dumps({
        "n_examples": len(examples), "val_rows": len(val_idx),
        "rmse_gnn_raw": round(rmse_gnn, 5), "rmse_cb_raw": round(rmse_cb, 5),
        "cb_fit": report.get("e1"),
        "spearman": {"rho": round(rho, 4), "p": round(p, 6),
                     "boot_ci95": [round(lo, 4) if lo is not None else None,
                                   round(hi, 4) if hi is not None else None]},
        "impact_sd_raw": round(impact_sd, 5), "y_sd": round(y_sd, 5),
        "full_offer_decisions_scored": comp,
        "go_no_go": go, "GO": all(go.values()),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
