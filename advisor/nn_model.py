from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
from base_model import TARGET_PARTS
from model import MIN_ROWS, gather, _counts

MODEL_DIR = common.MODEL_NN_GLOBAL

HIDDEN = (256,)
DROPOUT = 0.2
INPUT_DROPOUT = 0.15
EPOCHS = 150
BATCH_SIZE = 256
LEARNING_RATE = 3e-4
WEIGHT_DECAY = 1e-5
EMB_MAX = 16
CARD_MAX = 16
CLIP = 5.0
N_QUANTILES = 1000
VAL_FRACTION = 0.15
PATIENCE = 20
SEED = 0
THREADS = 6


def _cfg(**over):
    c = {"hidden": list(HIDDEN), "dropout": DROPOUT, "input_dropout": INPUT_DROPOUT,
         "epochs": EPOCHS, "batch": BATCH_SIZE,
         "lr": LEARNING_RATE, "weight_decay": WEIGHT_DECAY, "emb_max": EMB_MAX,
         "card_max": CARD_MAX, "clip": CLIP, "n_quantiles": N_QUANTILES,
         "val_fraction": VAL_FRACTION,
         "patience": PATIENCE, "seed": SEED, "threads": THREADS}
    for k, v in (over or {}).items():
        if v is None:
            continue
        if k == "hidden" and not isinstance(v, list):
            v = [int(x) for x in str(v).split(",") if str(x).strip()]
        c[k] = v
    return c


def _torch():
    import torch
    return torch


def _raw_numeric(rows, num):
    import numpy as np
    A = np.full((len(rows), len(num)), np.nan, dtype="float64")
    for i, r in enumerate(rows):
        for j, c in enumerate(num):
            v = F._f(r.get(c))
            if v is not None:
                A[i, j] = v
    return A


def _fit_prep(rows, num, cat, cfg):
    import collections
    from sklearn.preprocessing import QuantileTransformer
    cap = int(cfg["card_max"])
    cat_maps = {}
    for c in cat:
        freq = collections.Counter(str(r.get(c, "?")) for r in rows)
        cat_maps[c] = {v: i for i, v in enumerate(sorted(v for v, _n in freq.most_common(cap)))}
    qt = QuantileTransformer(output_distribution="normal",
                             n_quantiles=int(min(cfg["n_quantiles"], max(len(rows), 2))),
                             subsample=1_000_000, random_state=int(cfg["seed"]))
    qt.fit(_raw_numeric(rows, num))
    return {"cat_maps": cat_maps, "qt": qt, "cards": [len(cat_maps[c]) + 1 for c in cat]}


def _apply_prep(rows, num, cat, prep, cfg):
    import numpy as np
    A = prep["qt"].transform(_raw_numeric(rows, num))
    A = np.clip(np.where(np.isfinite(A), A, 0.0), -cfg["clip"], cfg["clip"])
    C = np.zeros((len(rows), len(cat)), dtype="int64")
    for i, r in enumerate(rows):
        for j, c in enumerate(cat):
            mp = prep["cat_maps"][c]
            C[i, j] = mp.get(str(r.get(c, "?")), len(mp))
    return A.astype("float32"), C


def _to_normal(y, eps=1e-4):
    import numpy as np
    from scipy.stats import norm
    return [float(v) for v in norm.ppf(np.clip(np.asarray(y, dtype="float64"), eps, 1.0 - eps))]


def _emb_dim(card, emb_max):
    import math
    return int(max(1, min(emb_max, math.ceil(math.sqrt(max(card, 1))))))


def _build_net(n_num, cards, cfg):
    torch = _torch()
    nn = torch.nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.embs = nn.ModuleList([nn.Embedding(c, _emb_dim(c, cfg["emb_max"]))
                                       for c in cards])
            d = n_num + sum(_emb_dim(c, cfg["emb_max"]) for c in cards)
            layers, prev = [nn.Dropout(cfg["input_dropout"])] if cfg["input_dropout"] else [], d
            for h in cfg["hidden"]:
                layers += [nn.Linear(prev, int(h)), nn.LayerNorm(int(h)), nn.ReLU(),
                           nn.Dropout(cfg["dropout"])]
                prev = int(h)
            layers.append(nn.Linear(prev, 1))
            self.mlp = nn.Sequential(*layers)
            self.in_dim = d

        def forward(self, xn, xc):
            parts = [xn] + [e(xc[:, j]) for j, e in enumerate(self.embs)]
            return self.mlp(torch.cat(parts, dim=1))

    return Net()


def _split(n, groups, frac, seed):
    import random
    if not groups or len(groups) != n:
        rng = random.Random(seed)
        idx = list(range(n))
        rng.shuffle(idx)
        cut = max(1, int(n * frac)) if n > 20 else 0
        return idx[:cut], idx[cut:]
    uniq = sorted(set(groups))
    rng = random.Random(seed)
    rng.shuffle(uniq)
    want = max(1, int(len(uniq) * frac)) if len(uniq) > 2 else 0
    held = set(uniq[:want])
    vidx = [i for i, g in enumerate(groups) if g in held]
    tidx = [i for i, g in enumerate(groups) if g not in held]
    if not vidx or not tidx:
        return [], list(range(n))
    return vidx, tidx


def _fit(net, Xn, Xc, y, cfg, log, groups=None):
    torch = _torch()
    torch.manual_seed(cfg["seed"])
    n = Xn.shape[0]
    g = torch.Generator().manual_seed(cfg["seed"])
    v, t = _split(n, groups, cfg["val_fraction"], int(cfg["seed"]))
    vidx = torch.tensor(v, dtype=torch.long)
    tidx = torch.tensor(t, dtype=torch.long)
    n_val = len(v)
    XN, XC = torch.tensor(Xn), torch.tensor(Xc)
    Y = torch.tensor([[float(v)] for v in y], dtype=torch.float32)
    opt = torch.optim.Adam(net.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    lossf = torch.nn.MSELoss()
    best, best_state, bad, ep = float("inf"), None, 0, 0
    for ep in range(int(cfg["epochs"])):
        net.train()
        p = tidx[torch.randperm(tidx.shape[0], generator=g)]
        for i in range(0, p.shape[0], int(cfg["batch"])):
            b = p[i:i + int(cfg["batch"])]
            opt.zero_grad()
            lossf(net(XN[b], XC[b]), Y[b]).backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            idx = vidx if n_val else tidx
            v = float(lossf(net(XN[idx], XC[idx]), Y[idx]))
        if v < best - 1e-6:
            best, bad = v, 0
            best_state = {k: t.clone() for k, t in net.state_dict().items()}
        else:
            bad += 1
            if bad >= int(cfg["patience"]):
                log("   nn: early stop at epoch %d (val %.5f, best %.5f)" % (ep + 1, v, best))
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    log("   nn: in_dim=%d val_mse=%.5f after %d epochs (%d val rows held out by campaign)"
        % (net.in_dim, best, ep + 1, n_val))
    with torch.no_grad():
        preds = net(XN, XC).squeeze(1)
    return [float(v) for v in preds], {"val_mse": round(best, 6), "epochs_run": ep + 1}


def _predict(net, Xn, Xc):
    torch = _torch()
    with torch.no_grad():
        return [float(v) for v in net(torch.tensor(Xn), torch.tensor(Xc)).squeeze(1)]


def train(runs_root=None, log=print, **over):
    import shutil
    from model import RUNS_ROOT
    cfg = _cfg(**over)
    torch = _torch()
    torch.set_num_threads(int(cfg["threads"]))
    data = gather(runs_root or RUNS_ROOT)
    rows, y = data["full"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, "backend": "nn",
                **_counts(data)}
    import pickle
    num, cat = F.split_columns(rows)
    prep = _fit_prep(rows, num, cat, cfg)
    Xn, Xc = _apply_prep(rows, num, cat, prep, cfg)
    yz = _to_normal(y)
    log("   nn: %d rows | %d num + %d cat (card_max=%d) | hidden=%s | target N(0,1)"
        % (len(rows), len(num), len(cat), cfg["card_max"], cfg["hidden"]))
    net = _build_net(len(num), prep["cards"], cfg)
    preds, stats = _fit(net, Xn, Xc, yz, cfg, log, groups=data.get("groups"))
    meta = {"backend": "nn", "cfg": cfg, "num": num, "cat": cat,
            "cards": prep["cards"], "rows": len(rows), "fit": stats,
            "pred_lo": min(preds), "pred_hi": max(preds),
            "target_transform": "probit(percentile) -> N(0,1)",
            "input_transform": "per-column quantile -> N(0,1), NaN -> 0, clip +/-%s" % cfg["clip"],
            "campaigns": sorted(set(data["groups"])),
            "target": ("gain(future_max - decision_snapshot) over %s" % ",".join(TARGET_PARTS))}
    stage = MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    torch.save(net.state_dict(), os.path.join(stage, "net.pt"))
    pickle.dump(prep, open(os.path.join(stage, "prep.pkl"), "wb"))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    os.makedirs(MODEL_DIR, exist_ok=True)
    for name in ("net.pt", "prep.pkl", "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    mae = sum(abs(a - b) for a, b in zip(preds, yz)) / len(yz)
    return {"trained": True, "backend": "nn", "rows": len(rows),
            "mae_in_sample": round(mae, 5), "cfg": cfg, "fit": stats,
            "local": {"trained": False, "reason": "nn backend predicts the global target only"},
            **_counts(data)}


class Ranker:

    def __init__(self, model_dir=MODEL_DIR, seed=None):
        import random
        self.ready = False
        self.meta = None
        self.lmeta = None
        self.rng = random.Random(seed)
        meta_path = os.path.join(model_dir, "meta.json")
        if not os.path.isfile(meta_path):
            return
        try:
            import pickle
            torch = _torch()
            self.meta = json.load(open(meta_path))
            cfg = self.meta["cfg"]
            torch.set_num_threads(int(cfg.get("threads") or THREADS))
            self.prep = pickle.load(open(os.path.join(model_dir, "prep.pkl"), "rb"))
            self.net = _build_net(len(self.meta["num"]), self.meta["cards"], cfg)
            self.net.load_state_dict(torch.load(os.path.join(model_dir, "net.pt"),
                                                map_location="cpu"))
            self.net.eval()
            self.ready = True
        except Exception as e:
            sys.stderr.write("nn_model: load failed -> %s\n" % repr(e)[:200])
            self.ready = False

    def score(self, record, w_local=None):
        triples = F.decision_rows(record)
        if not self.ready or not triples:
            return [{"context_kind": e["context_kind"], "context_id": e["context_id"],
                     "action_type": o["action_type"], "key": o["key"],
                     "available": o["available"], "gate": o["gate"],
                     "params": o.get("params") or {},
                     "exploit": None, "impact": None, "score": None}
                    for e, o, _r in triples]
        m = self.meta
        rows = [r for _e, _o, r in triples]
        Xn, Xc = _apply_prep(rows, m["num"], m["cat"], self.prep, m["cfg"])
        preds = _predict(self.net, Xn, Xc)
        out = []
        for i, (e, o, _r) in enumerate(triples):
            out.append({"context_kind": e["context_kind"], "context_id": e["context_id"],
                        "action_type": o["action_type"], "key": o["key"],
                        "available": o["available"], "gate": o["gate"],
                        "params": o.get("params") or {},
                        "impact": round(preds[i], 5), "impact_local": None,
                        "pct_global": None, "pct_local": None, "w_local": None,
                        "exploit": round(preds[i], 5), "score": round(preds[i], 5)})
        out.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1))
        return out


if __name__ == "__main__":
    print(json.dumps(train(), indent=2, default=str))
