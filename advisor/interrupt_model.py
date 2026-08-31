from __future__ import annotations

import json
import os
import random
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
import policy as P
from base_model import (CB_INTERRUPT_ITERATIONS, CB_INTERRUPT_PARAMS,
                        CB_INTERRUPT_TUNED_FROM, RUNS_ROOT, TARGET_PARTS,
                        TRAIN_WINDOW_CAMPAIGNS, target,
                        decision_deltas, fit_es, grouped_split, params, _ranks)
from store import DecisionStore, IncompatibleStore

MODEL_DIR = common.MODEL_INTERRUPT
MODEL_FILE = "model.cbm"
MIN_ROWS = 5


def _state_row(screen, n_options, campaign, world=None):
    row = F.campaign_block(campaign or {}, world or {})
    row["isc_screen"] = str(screen)
    row["isc_n_options"] = float(n_options or 0)
    return row


DEAL_ITEMS = ("confederation", "defensive_alliance", "military_alliance",
              "nonaggression_pact", "payment", "peace", "soft_access",
              "state_gift", "trade_agreement", "vassal")


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


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
    row["isc_dip_attitude"] = _num(p.get("attitude"))
    row["isc_dip_attitude_label"] = str(p.get("attitude_label") or "none")
    row["isc_dip_race"] = str(p.get("race") or "none")
    rel = p.get("reliability") or []
    row["isc_dip_reliability"] = str(rel[0]) if rel else "none"
    ranks = [_num(x) for x in (p.get("strength_ranks") or [])]
    row["isc_dip_strength_them"] = ranks[0] if ranks else 0.0
    row["isc_dip_strength_us"] = ranks[1] if len(ranks) > 1 else 0.0
    row["isc_dip_settlements"] = _num(p.get("settlements"))
    dem = [str(x) for x in (p.get("demands") or [])]
    off = [str(x) for x in (p.get("offers") or [])]
    for item in DEAL_ITEMS:
        row["isc_dip_dem_%s" % item] = float(dem.count(item))
        row["isc_dip_off_%s" % item] = float(off.count(item))
    row["isc_dip_dem_other"] = float(sum(1 for x in dem if x not in DEAL_ITEMS))
    row["isc_dip_off_other"] = float(sum(1 for x in off if x not in DEAL_ITEMS))
    row["isc_dip_n_demanded"] = float(len(dem))
    row["isc_dip_n_offered"] = float(len(off))
    row["isc_dip_n_treaties"] = float(len(p.get("treaties") or []))
    row["isc_dip_amount_demanded"] = _num(p.get("amount_demanded"))
    row["isc_dip_amount_offered"] = _num(p.get("amount_offered"))
    return row


def gather(runs_root=RUNS_ROOT, window=TRAIN_WINDOW_CAMPAIGNS):
    pending, deltas = [], []
    seen_runs = 0
    for db in common.run_dbs(runs_root):
        try:
            s = DecisionStore(os.path.dirname(db), readonly=True)
        except IncompatibleStore:
            continue
        seen_runs += 1
        try:
            series = s.target_series()
            floor = s.window_floor(window)
            keys = s.window_keys(window)
            seq, snaps = {}, {}
            for camp_id, ts, atype in s.action_sequence(min_decision=floor):
                seq.setdefault(camp_id, []).append((ts or 0.0, atype))
            for camp_id, ts, camp, world in s.campaign_snapshots(min_decision=floor):
                snaps.setdefault(camp_id, []).append((ts, camp, world))
            for r in s.interrupt_rows():
                if not r.get("chosen"):
                    continue
                if keys is not None and r.get("campaign_id") not in keys:
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
    rows, y, groups = [], [], []
    for (screen, chosen, n_opts, campaign, world, panel, camp_id, opts_meta, turn), d in zip(
            pending, deltas):
        yv = target(d)
        if yv is None:
            continue
        rows.append(_row(screen, chosen, n_opts, campaign, world, panel, opts_meta))
        y.append(yv)
        groups.append(camp_id)
    return {"rows": rows, "y": y, "groups": groups, "runs": seen_runs}


def train(runs_root=RUNS_ROOT, window=TRAIN_WINDOW_CAMPAIGNS):
    from catboost import Pool
    data = gather(runs_root, window=window)
    rows, y = data["rows"], data["y"]
    if len(rows) < MIN_ROWS:
        return {"trained": False, "rows": len(rows), "need": MIN_ROWS, "runs": data["runs"]}
    num, cat = F.split_columns(rows)
    X = F.matrix(rows, num, cat)
    n_rows = len(rows)
    screen_rows = {}
    for r in rows:
        k = r["isc_screen"]
        screen_rows[k] = screen_rows.get(k, 0) + 1
    rows = data["rows"] = None
    cat_idx = list(range(len(num), len(num) + len(cat)))
    fit_report = {}
    groups = data["groups"]
    m = fit_es(X, y, cat_idx, groups, "model", fit_report,
               base=CB_INTERRUPT_PARAMS, iterations=CB_INTERRUPT_ITERATIONS)
    preds = list(m.predict(Pool(X, cat_features=cat_idx)))
    X = None
    val, _trn = grouped_split(len(preds), groups)
    if val:
        pv = [preds[i] for i in val]
        yv = [y[i] for i in val]
        ybar = sum(yv) / len(yv)
        sst = sum((v - ybar) ** 2 for v in yv)
        if sst > 0:
            sse = sum((a - b) ** 2 for a, b in zip(yv, pv))
            fit_report["model"]["val_r2"] = round(1.0 - sse / sst, 5)
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    meta = {"num": num, "cat": cat, "rows": n_rows,
            "mae_in_sample": round(mae, 5), "fit": fit_report,
            "screens": sorted(screen_rows),
            "screen_rows": screen_rows,
            "campaigns": sorted(set(data["groups"])),
            "target": ("gain(future_max - decision_snapshot) over %s"
                       % ",".join(TARGET_PARTS))}
    stage = MODEL_DIR + ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    os.makedirs(MODEL_DIR, exist_ok=True)
    m.save_model(os.path.join(stage, MODEL_FILE))
    json.dump(meta, open(os.path.join(stage, "meta.json"), "w"))
    for name in (MODEL_FILE, "meta.json"):
        os.replace(os.path.join(stage, name), os.path.join(MODEL_DIR, name))
    shutil.rmtree(stage, ignore_errors=True)
    for stale in ("e1.cbm", "e2.cbm", "interrupt.cbm", "interrupt.cbm.superseded"):
        p = os.path.join(MODEL_DIR, stale)
        if os.path.exists(p):
            os.remove(p)
    return {"trained": True, "rows": n_rows, "mae_in_sample": round(mae, 5),
            "fit": fit_report,
            "params": params(iterations=CB_INTERRUPT_ITERATIONS, base=CB_INTERRUPT_PARAMS,
                             tuned_from=CB_INTERRUPT_TUNED_FROM),
            "screens": meta["screens"], "runs": data["runs"]}


class InterruptRanker:

    def __init__(self, model_dir=MODEL_DIR, seed=None, strategies=None, ruleset=None):
        self.ready = False
        self.meta = {}
        self.model = None
        self.rng = random.Random(seed)
        self.strategies = P.normalize_interrupt_strategies(strategies)
        self.ruleset = ruleset
        try:
            from catboost import CatBoostRegressor
            mp = os.path.join(model_dir, MODEL_FILE)
            meta = os.path.join(model_dir, "meta.json")
            stale = [f for f in ("e1.cbm", "e2.cbm", "interrupt.cbm")
                     if os.path.exists(os.path.join(model_dir, f))]
            if stale and not os.path.exists(mp):
                sys.stderr.write(
                    "interrupt_model: %s holds %s from before the one-model interrupt and "
                    "no model.cbm -- REFUSING to load it. Retrain "
                    "(python advisor\\interrupt_model.py) to replace it.\n"
                    % (model_dir, "/".join(stale)))
                return
            if os.path.exists(mp) and os.path.exists(meta):
                self.model = CatBoostRegressor()
                self.model.load_model(mp)
                self.meta = json.load(open(meta))
                self.ready = True
        except Exception as e:
            sys.stderr.write("interrupt_model: could not load -> %s\n" % repr(e)[:140])

    def score(self, screen, options, campaign, panel=None, world=None, meta=None):
        if not self.ready or not options:
            return {}
        from catboost import Pool
        m = self.meta
        num, cat = m.get("num") or [], m.get("cat") or []
        opts = list(options)
        rows = [_row(screen, o, len(opts), campaign, world, panel, meta) for o in opts]
        X = F.matrix(rows, num, cat)
        preds = list(self.model.predict(Pool(X, cat_features=list(
            range(len(num), len(num) + len(cat))))))
        return dict(zip(opts, _ranks(preds)))

    def _draw(self):
        roll = self.rng.random()
        acc = 0.0
        names = sorted(self.strategies)
        for name in names:
            acc += self.strategies[name]
            if roll < acc:
                return name
        return names[-1]

    def _exploit_ready(self, screen):
        sr = self.meta.get("screen_rows")
        if sr is not None:
            seen = int(sr.get(str(screen), 0))
            return seen >= MIN_ROWS, "%d/%d rows recorded for this screen" % (seen, MIN_ROWS)
        seen = MIN_ROWS if str(screen) in (self.meta.get("screens") or []) else 0
        return seen >= MIN_ROWS, "screen not in the fitted set (meta predates screen_rows)"

    def choose(self, screen, options, campaign, panel=None, record=None, meta=None):
        opts = sorted(options)
        if not opts:
            return None, "none", {}
        world = (record or {}).get("world")

        usable, why = self._exploit_ready(screen)
        exploit = (self.score(screen, options, campaign, panel, world, meta)
                   if usable else {})
        rich = {o: {"exploit": exploit[o]} for o in opts if o in exploit}

        drawn = self._draw()
        if drawn == "random":
            return self.rng.choice(opts), "random", rich
        if drawn == "ruleset":
            hit = self.ruleset.match_screen(str(screen), opts) if self.ruleset else None
            if hit:
                return hit[0], "ruleset(%s)" % hit[1], rich
            return self.rng.choice(opts), "ruleset_random_fallback", rich
        if drawn != "greedy_catboost":
            raise RuntimeError("interrupt_model: drawn strategy %r has no interrupt branch -- "
                               "refusing to silently play greedy_catboost" % (drawn,))
        if not usable:
            pick = self.rng.choice(opts)
            sys.stderr.write("interrupt_model: %s -> %r (greedy_catboost_random_fallback, %s)\n"
                             % (screen, pick, why))
            return pick, "greedy_catboost_random_fallback", rich
        if not exploit:
            if self.ready:
                raise P.ModelUnavailable(
                    "interrupt_model: greedy_catboost drawn with a ready model but scoring "
                    "produced nothing for screen %r" % (screen,))
            return self.rng.choice(opts), "greedy_catboost_random_fallback", rich
        return max(exploit, key=exploit.get), "greedy_catboost", rich


def main():
    print(json.dumps(train(), indent=2))


if __name__ == "__main__":
    main()
