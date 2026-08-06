from __future__ import annotations

import json
import os
import pickle
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F
import policy as P
from base_model import (RUNS_ROOT, TARGET_PARTS, target, decision_deltas, fit_es,
                        isolation_forest, regressor, _encode, _mm0, _ranks, _sd)
from store import DecisionStore, IncompatibleStore

MODEL_DIR = r"D:\twdata\models\interrupt"
MIN_ROWS = 5


def _state_row(screen, n_options, campaign, world=None):
    row = F.campaign_block(campaign or {}, world or {})
    row["isc_screen"] = str(screen)
    row["isc_n_options"] = float(n_options or 0)
    return row


def _row(screen, option, n_options, campaign, world=None, panel=None, meta=None):
    row = _state_row(screen, n_options, campaign, world)
    row["isc_option"] = str(option)
    m = (meta or {}).get(option) or {}
    row["isc_dilemma_id"] = str(m.get("dilemma_id") or "none")
    row["isc_option_id"] = str(m.get("option_id") or "none")
    row["isc_option_label"] = str(m.get("text") or "none")
    row["isc_payload"] = " | ".join(sorted(str(x) for x in (m.get("payload") or []))) or "none"
    row["isc_n_payload"] = float(len(m.get("payload") or []))
    p = panel or {}
    row["isc_fc_result"] = str((p.get("result") or {}).get("state") or "none")
    row["isc_fc_casualties"] = str((p.get("casualties") or {}).get("state") or "none")
    return row


def gather(runs_root=RUNS_ROOT):
    pending, deltas = [], []
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
            seq, snaps = {}, {}
            for camp_id, ts, atype in s.action_sequence():
                seq.setdefault(camp_id, []).append((ts or 0.0, atype))
            for camp_id, ts, camp, world in s.campaign_snapshots():
                snaps.setdefault(camp_id, []).append((ts, camp, world))
            for r in s.interrupt_rows():
                if not r.get("chosen"):
                    continue
                rts = r.get("ts") or 0.0
                pre = [(c, w) for t, c, w in snaps.get(r.get("campaign_id")) or [] if rts >= t]
                base, base_world = pre[-1] if pre else ({}, {})
                d = decision_deltas(base, series.get(r.get("campaign_id")) or {}, r.get("turn"))
                if all(v is None for v in d.values()):
                    continue
                past = [a for t, a in seq.get(r.get("campaign_id")) or [] if rts > t]
                campaign = F.stamp_prev_actions(dict(base), past)
                pending.append((r.get("screen"), r.get("chosen"), len(r.get("options") or {}),
                                campaign, base_world, r.get("panel"), r.get("campaign_id"),
                                r.get("options") or {}, r.get("turn")))
                deltas.append(d)
        finally:
            try:
                s.close()
            except Exception:
                pass
    rows, srows, y, groups = [], [], [], []
    for (screen, chosen, n_opts, campaign, world, panel, camp_id, opts_meta, turn), d in zip(
            pending, deltas):
        yv = target(d)
        if yv is None:
            continue
        rows.append(_row(screen, chosen, n_opts, campaign, world, panel, opts_meta))
        srows.append(_state_row(screen, n_opts, campaign, world))
        y.append(yv)
        groups.append(camp_id)
    return {"rows": rows, "state": srows, "y": y, "groups": groups, "runs": seen_runs}


def train(runs_root=RUNS_ROOT):
    from catboost import Pool
    data = gather(runs_root)
    rows, srows, y = data["rows"], data["state"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, "runs": data["runs"]}
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    cat_idx = list(range(len(num), len(num) + len(cat)))
    snum, scat = F.split_columns(srows)
    Xs = F.matrix(srows, snum, scat)
    scat_idx = list(range(len(snum), len(snum) + len(scat)))
    fit_report = {}
    groups = data["groups"]
    e1 = fit_es(X, y, cat_idx, groups, "e1", fit_report)
    e2 = fit_es(Xs, y, scat_idx, groups, "e2", fit_report)
    os.makedirs(MODEL_DIR, exist_ok=True)
    Xa, cat_maps = _encode(rows, num, cat)
    iso = isolation_forest()
    iso.fit(Xa)
    nov = [-s for s in iso.score_samples(Xa)]
    with open(os.path.join(MODEL_DIR, "iso.pkl"), "wb") as fh:
        pickle.dump({"iso": iso, "cat_maps": cat_maps}, fh)
    preds = list(e1.predict(Pool(X, cat_features=cat_idx)))
    spreds = list(e2.predict(Pool(Xs, cat_features=scat_idx)))
    impacts = [a - b for a, b in zip(preds, spreds)]
    sd_global = _sd(impacts)
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat, "rows": len(rows),
            "screens": sorted({r["isc_screen"] for r in rows}),
            "screen_rows": {s: sum(1 for r in rows if r["isc_screen"] == s)
                            for s in {r["isc_screen"] for r in rows}},
            "exp_lo": min(impacts), "exp_hi": max(impacts), "sd_global": sd_global,
            "nov_lo": min(nov), "nov_hi": max(nov), "beta": P.BETA, "epsilon": P.EPSILON,
            "campaigns": sorted(set(data["groups"]))}
    e1.save_model(os.path.join(MODEL_DIR, "e1.cbm"))
    e2.save_model(os.path.join(MODEL_DIR, "e2.cbm"))
    json.dump(meta, open(os.path.join(MODEL_DIR, "meta.json"), "w"))
    legacy = os.path.join(MODEL_DIR, "interrupt.cbm")
    if os.path.exists(legacy):
        os.replace(legacy, legacy + ".superseded")
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5),
            "fit": fit_report,
            "sd_global": round(sd_global, 6), "screens": meta["screens"],
            "beta": P.BETA, "epsilon": P.EPSILON, "runs": data["runs"]}


class InterruptRanker:

    def __init__(self, model_dir=MODEL_DIR, seed=None):
        self.ready = False
        self.meta = {}
        self.e1 = self.e2 = None
        self._iso = None
        self._cat_maps = None
        self.rng = random.Random(seed)
        try:
            from catboost import CatBoostRegressor
            p1 = os.path.join(model_dir, "e1.cbm")
            p2 = os.path.join(model_dir, "e2.cbm")
            meta = os.path.join(model_dir, "meta.json")
            legacy = os.path.join(model_dir, "interrupt.cbm")
            if os.path.exists(legacy) and not (os.path.exists(p1) and os.path.exists(p2)):
                sys.stderr.write(
                    "interrupt_model: %s holds a single-model interrupt.cbm from before the "
                    "E1/E2 split and no e1.cbm/e2.cbm. Its predictions are raw values, not "
                    "impacts, and cannot be ranked as impacts -- REFUSING to load it. Retrain "
                    "(python advisor\\interrupt_model.py) to replace it.\n" % model_dir)
                return
            if os.path.exists(p1) and os.path.exists(p2) and os.path.exists(meta):
                self.e1 = CatBoostRegressor()
                self.e1.load_model(p1)
                self.e2 = CatBoostRegressor()
                self.e2.load_model(p2)
                self.meta = json.load(open(meta))
                self.ready = True
                if "screen_rows" not in self.meta:
                    sys.stderr.write(
                        "interrupt_model: meta.json predates screen_rows -- per-screen record "
                        "counts UNAVAILABLE. Screens in the fitted set %s keep the model; any "
                        "other screen is cold_random until the next retrain.\n"
                        % (self.meta.get("screens") or []))
            iso = os.path.join(model_dir, "iso.pkl")
            if self.ready and os.path.exists(iso):
                with open(iso, "rb") as fh:
                    blob = pickle.load(fh)
                self._iso, self._cat_maps = blob.get("iso"), blob.get("cat_maps")
        except Exception as e:
            sys.stderr.write("interrupt_model: could not load -> %s\n" % repr(e)[:140])

    def score(self, screen, options, campaign, panel=None, world=None, meta=None):
        if not self.ready or not options:
            return {}, {}
        try:
            from catboost import Pool
            m = self.meta
            num, cat = m.get("num") or [], m.get("cat") or []
            snum, scat = m.get("state_num") or [], m.get("state_cat") or []
            opts = list(options)
            rows = [_row(screen, o, len(opts), campaign, world, panel, meta) for o in opts]
            X = F.matrix(rows, num, cat)
            f1 = list(self.e1.predict(Pool(X, cat_features=list(
                range(len(num), len(num) + len(cat))))))
            Xs = F.matrix([_state_row(screen, len(opts), campaign, world)], snum, scat)
            g = list(self.e2.predict(Pool(Xs, cat_features=list(
                range(len(snum), len(snum) + len(scat))))))[0]
            exploit = dict(zip(opts, _ranks([v - g for v in f1])))
            if self._iso is None:
                return exploit, {}
            Xa, _ = _encode(rows, num, cat, self._cat_maps)
            nov = [-s for s in self._iso.score_samples(Xa)]
            nov_lo, nov_hi = m.get("nov_lo", 0.0), m.get("nov_hi", 1.0)
            explore = {o: _mm0(v, nov_lo, nov_hi) for o, v in zip(opts, nov)}
            return exploit, explore
        except Exception as e:
            sys.stderr.write("interrupt_model: scoring failed -> %s\n" % repr(e)[:140])
            return {}, {}

    def choose(self, screen, options, campaign, panel=None, world=None, meta=None):
        opts = sorted(options)
        if not opts:
            return None, "none", {}
        sr = self.meta.get("screen_rows")
        if sr is not None:
            seen = int(sr.get(str(screen), 0))
            why = "%d/%d rows recorded for this screen" % (seen, MIN_ROWS)
        else:
            seen = MIN_ROWS if str(screen) in (self.meta.get("screens") or []) else 0
            why = "screen not in the fitted set (meta predates screen_rows)"
        if seen < MIN_ROWS:
            pick = self.rng.choice(opts)
            sys.stderr.write("interrupt_model: %s -> %r (cold_random, %s)\n" % (screen, pick, why))
            return pick, "cold_random", {}
        exploit, explore = self.score(screen, options, campaign, panel, world, meta)
        if not exploit:
            return self.rng.choice(opts), "cold_random", {}
        rich = {o: {"exploit": exploit.get(o), "explore": explore.get(o)} for o in exploit}
        roll = self.rng.random()
        if P.EPSILON > 0.0 and roll < P.EPSILON:
            pick = self.rng.choice(sorted(exploit))
            sys.stderr.write("interrupt_model: %s -> %r (epsilon_random, exploit preferred %r)\n"
                             % (screen, pick, max(exploit, key=exploit.get)))
            return pick, "epsilon_random", rich
        if roll < P.EPSILON + P.BETA:
            if not explore:
                raise RuntimeError(
                    "interrupt_model: the explore branch was drawn but no explore model is "
                    "loaded (iso.pkl missing from %s) -- refusing to answer an explore draw "
                    "with an exploit pick" % MODEL_DIR)
            pick = max(explore, key=explore.get)
            return pick, "interrupt_explore", rich
        return max(exploit, key=exploit.get), "interrupt_exploit", rich


def main():
    print(json.dumps(train(), indent=2))


if __name__ == "__main__":
    main()
