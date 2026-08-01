from __future__ import annotations

import json
import os
import pickle
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F                                          # noqa: E402
import model as M                                             # noqa: E402
from store import DecisionStore, IncompatibleStore            # noqa: E402

MODEL_DIR = r"D:\twdata\models\interrupt"
MIN_ROWS = 5
EPSILON = 0.3
BETA = 0.40                      # explore weight


def _row(screen, option, n_options, campaign):
    """One scoreable row: the campaign block, plus the screen, the option and the option count."""
    row = F.campaign_block(campaign or {}, {})
    row["isc_screen"] = str(screen)
    row["isc_option"] = str(option)
    row["isc_n_options"] = float(n_options or 0)
    return row


def gather(runs_root=M.RUNS_ROOT):
    """(rows, y, groups) over every recorded interrupt decision that has a future to judge it."""
    rows, y, groups = [], [], []
    seen_runs = 0
    for name in sorted(os.listdir(runs_root)) if os.path.isdir(runs_root) else []:
        db = os.path.join(runs_root, name, "decisions.sqlite")
        if not os.path.exists(db):
            continue
        try:
            s = DecisionStore(os.path.join(runs_root, name))
        except IncompatibleStore:
            continue
        seen_runs += 1
        try:
            series = s.target_series()
            ranges = M.target_ranges(series)
            for r in s.interrupt_rows():
                if not r.get("chosen"):
                    continue
                lab = M.target(series, r.get("campaign_id"), r.get("turn"), ranges)
                if lab is None:
                    continue
                rows.append(_row(r.get("screen"), r.get("chosen"),
                                 len(r.get("options") or {}), r.get("campaign")))
                y.append(lab)
                groups.append(r.get("campaign_id"))
        finally:
            try:
                s.close()
            except Exception:
                pass
    return {"rows": rows, "y": y, "groups": groups, "runs": seen_runs}


def train(runs_root=M.RUNS_ROOT):
    """Fit the interrupt ranker. Returns a status dict; never raises on too-little-data."""
    from catboost import CatBoostRegressor, Pool
    from sklearn.ensemble import IsolationForest
    data = gather(runs_root)
    rows, y = data["rows"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, "runs": data["runs"]}
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    m = CatBoostRegressor(iterations=300, depth=6, learning_rate=0.08, verbose=0,
                          loss_function="RMSE", train_dir=r"D:\twdata\tmp\catboost")
    m.fit(Pool(X, y, cat_features=cat_idx))
    os.makedirs(MODEL_DIR, exist_ok=True)
    m.save_model(os.path.join(MODEL_DIR, "interrupt.cbm"))
    Xa, cat_maps = M._encode(rows, num, cat)
    iso = IsolationForest(n_estimators=200, random_state=0, n_jobs=-1)
    iso.fit(Xa)
    nov = [-s for s in iso.score_samples(Xa)]
    with open(os.path.join(MODEL_DIR, "iso.pkl"), "wb") as fh:
        pickle.dump({"iso": iso, "cat_maps": cat_maps}, fh)
    preds = list(m.predict(Pool(X, cat_features=cat_idx)))
    meta = {"num": num, "cat": cat, "rows": len(rows),
            "screens": sorted({r["isc_screen"] for r in rows}),
            "exp_lo": min(preds), "exp_hi": max(preds),
            "nov_lo": min(nov), "nov_hi": max(nov), "beta": BETA, "epsilon": EPSILON,
            "campaigns": sorted(set(data["groups"]))}
    json.dump(meta, open(os.path.join(MODEL_DIR, "meta.json"), "w"))
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5),
            "screens": meta["screens"], "beta": BETA, "epsilon": EPSILON, "runs": data["runs"]}


class InterruptRanker:
    """Loads the fitted model and scores the options a screen is offering."""

    def __init__(self, model_dir=MODEL_DIR, seed=None):
        self.ready = False
        self.meta = {}
        self._m = None
        self._iso = None
        self._cat_maps = None
        self.rng = random.Random(seed)
        try:
            from catboost import CatBoostRegressor
            mp = os.path.join(model_dir, "interrupt.cbm")
            meta = os.path.join(model_dir, "meta.json")
            if os.path.exists(mp) and os.path.exists(meta):
                self._m = CatBoostRegressor()
                self._m.load_model(mp)
                self.meta = json.load(open(meta))
                self.ready = True
            iso = os.path.join(model_dir, "iso.pkl")
            if self.ready and os.path.exists(iso):
                with open(iso, "rb") as fh:
                    blob = pickle.load(fh)
                self._iso, self._cat_maps = blob.get("iso"), blob.get("cat_maps")
        except Exception as e:
            sys.stderr.write("interrupt_model: could not load -> %s\n" % repr(e)[:140])

    def score(self, screen, options, campaign):
        """{option: (1-BETA)*exploit + BETA*novelty}, both min-maxed to [0,1]; {} when not fitted."""
        if not self.ready or not options:
            return {}
        try:
            from catboost import Pool
            num, cat = self.meta.get("num") or [], self.meta.get("cat") or []
            rows = [_row(screen, o, len(options), campaign) for o in options]
            X = F.matrix(rows, num, cat)
            cat_idx = list(range(len(num), len(num) + len(cat)))
            preds = list(self._m.predict(Pool(X, cat_features=cat_idx)))
            exp_lo, exp_hi = self.meta.get("exp_lo", 0.0), self.meta.get("exp_hi", 1.0)
            if self._iso is None:
                return {o: M._mm0(p, exp_lo, exp_hi) for o, p in zip(options, preds)}
            Xa, _ = M._encode(rows, num, cat, self._cat_maps)
            nov = [-s for s in self._iso.score_samples(Xa)]
            nov_lo, nov_hi = self.meta.get("nov_lo", 0.0), self.meta.get("nov_hi", 1.0)
            beta = self.meta.get("beta", BETA)
            return {o: (1.0 - beta) * M._mm0(p, exp_lo, exp_hi) + beta * M._mm0(v, nov_lo, nov_hi)
                    for o, p, v in zip(options, preds, nov)}
        except Exception as e:
            sys.stderr.write("interrupt_model: scoring failed -> %s\n" % repr(e)[:140])
            return {}

    def choose(self, screen, options, campaign):
        """(pick, policy). Always picks: a uniform draw over `options` when nothing is fitted."""
        opts = sorted(options)
        if not opts:
            return None, "none"
        s = self.score(screen, options, campaign)
        if not s:
            return self.rng.choice(opts), "cold_random"
        if EPSILON > 0.0 and self.rng.random() < EPSILON:
            pick = self.rng.choice(sorted(s))
            sys.stderr.write("interrupt_model: %s -> %r (epsilon explore, model preferred %r)\n"
                             % (screen, pick, max(s, key=s.get)))
            return pick, "epsilon_random"
        return max(s, key=s.get), "model"


def main():
    print(json.dumps(train(), indent=2))


if __name__ == "__main__":
    main()
