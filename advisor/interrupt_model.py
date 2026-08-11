from __future__ import annotations

import json
import os
import pickle
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.DECISIONS)

import features as F
import policy as P
from base_model import (RUNS_ROOT, TARGET_PARTS, target, decision_deltas, fit_es,
                        params, regressor, _ranks, _sd)
from store import DecisionStore, IncompatibleStore

MODEL_DIR = common.MODEL_INTERRUPT
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
    preds = list(e1.predict(Pool(X, cat_features=cat_idx)))
    spreds = list(e2.predict(Pool(Xs, cat_features=scat_idx)))
    impacts = [a - b for a, b in zip(preds, spreds)]
    sd_global = _sd(impacts)
    meta = {"num": num, "cat": cat, "state_num": snum, "state_cat": scat, "rows": len(rows),
            "screens": sorted({r["isc_screen"] for r in rows}),
            "screen_rows": {s: sum(1 for r in rows if r["isc_screen"] == s)
                            for s in {r["isc_screen"] for r in rows}},
            "exp_lo": min(impacts), "exp_hi": max(impacts), "sd_global": sd_global,
            "beta": P.BETA, "epsilon": P.EPSILON,
            "campaigns": sorted(set(data["groups"]))}
    e1.save_model(os.path.join(MODEL_DIR, "e1.cbm"))
    e2.save_model(os.path.join(MODEL_DIR, "e2.cbm"))
    json.dump(meta, open(os.path.join(MODEL_DIR, "meta.json"), "w"))
    legacy = os.path.join(MODEL_DIR, "interrupt.cbm")
    if os.path.exists(legacy):
        os.replace(legacy, legacy + ".superseded")
    mae = sum(abs(a - b) for a, b in zip(preds, y)) / len(y)
    return {"trained": True, "rows": len(rows), "mae_in_sample": round(mae, 5),
            "fit": fit_report, "params": params(),
            "sd_global": round(sd_global, 6), "screens": meta["screens"],
            "beta": P.BETA, "epsilon": P.EPSILON, "runs": data["runs"]}


class InterruptRanker:

    def __init__(self, model_dir=MODEL_DIR, seed=None, strategies=None, ruleset=None):
        self.ready = False
        self.meta = {}
        self.e1 = self.e2 = None
        self.rng = random.Random(seed)
        self.strategies = P.normalize_strategies(strategies)
        self.ruleset = ruleset
        # The graph arm. Its own model in its own directory -- this is not the action
        # model's encoder, exactly as e1/e2 here are not model.py's e1/e2.
        self.gnn = None
        self.gnn_score_errors = 0
        if "gnn_marwil" in self.strategies and model_dir != common.MODEL_COLD_START:
            try:
                if common.ROOT not in sys.path:
                    sys.path.insert(0, common.ROOT)
                from advisor.mapgraph import interrupt_rank as GNN
                self.gnn = GNN.Ranker()
            except Exception as e:
                sys.stderr.write("interrupt_model: graph arm unavailable -> %s -- gnn "
                                 "draws fall back to random\n" % repr(e)[:160])
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
        except Exception as e:
            sys.stderr.write("interrupt_model: could not load -> %s\n" % repr(e)[:140])

    def score(self, screen, options, campaign, panel=None, world=None, meta=None):
        if not self.ready or not options:
            return {}
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
            return dict(zip(opts, _ranks([v - g for v in f1])))
        except Exception as e:
            sys.stderr.write("interrupt_model: scoring failed -> %s\n" % repr(e)[:140])
            return {}

    def _draw(self):
        roll = self.rng.random()
        acc = 0.0
        names = sorted(self.strategies)
        for name in names:
            acc += self.strategies[name]
            if roll < acc:
                return name
        return names[-1]

    def _score_with_gnn(self, screen, opts, record, panel, meta):
        """Graph scores for this screen, or {}. Swallows its own failures on purpose:
        an unscored screen costs a comparison, a raised one costs the run."""
        if self.gnn is None or not self.gnn.ready:
            return {}
        try:
            return self.gnn.score(screen, opts, record, panel, meta)
        except Exception as e:
            self.gnn_score_errors += 1
            sys.stderr.write("interrupt_model: gnn scoring failed (%d so far) -> %s\n"
                             % (self.gnn_score_errors, repr(e)[:140]))
            return {}

    def _exploit_ready(self, screen):
        """(usable, why) for the CatBoost arm on THIS screen -- it is fitted per screen."""
        sr = self.meta.get("screen_rows")
        if sr is not None:
            seen = int(sr.get(str(screen), 0))
            return seen >= MIN_ROWS, "%d/%d rows recorded for this screen" % (seen, MIN_ROWS)
        seen = MIN_ROWS if str(screen) in (self.meta.get("screens") or []) else 0
        return seen >= MIN_ROWS, "screen not in the fitted set (meta predates screen_rows)"

    def choose(self, screen, options, campaign, panel=None, record=None, meta=None):
        """Pick one option, and score the screen with EVERY arm that can score it.

        Both models run on every screen regardless of which one the draw hands the
        decision to -- which is what policy.py:113-135 already does on the action path,
        and it is what makes the two arms comparable at all. Whatever they produce is
        returned as the rich scores, lands in options_json, and is the evidence for
        whether the graph arm is worth more than 10%.
        """
        opts = sorted(options)
        if not opts:
            return None, "none", {}
        world = (record or {}).get("world")

        usable, why = self._exploit_ready(screen)
        exploit = (self.score(screen, options, campaign, panel, world, meta)
                   if usable else {})
        gnn = self._score_with_gnn(screen, opts, record, panel, meta)
        rich = {}
        for o in opts:
            cell = {}
            if o in exploit:
                cell["exploit"] = exploit[o]
            if o in gnn:
                cell["gnn"] = gnn[o]
            if cell:
                rich[o] = cell

        drawn = self._draw()
        if drawn == "random":
            return self.rng.choice(opts), "random", rich
        if drawn == "ruleset":
            hit = self.ruleset.match_screen(str(screen), opts) if self.ruleset else None
            if hit:
                return hit[0], "ruleset(%s)" % hit[1], rich
            return self.rng.choice(opts), "ruleset_random_fallback", rich
        if drawn == "gnn_marwil":
            # No delegation. This arm used to be handed to exploit_tree and recorded as
            # gnn_delegated_exploit_tree, which meant the gnn share of the mix was decided
            # by CatBoost on every blocking screen and the arm's measured share was
            # fiction. It has its own model now; when that model cannot score, the arm
            # falls back to RANDOM and says so, the same way policy.py:141-148 does.
            if not gnn:
                return self.rng.choice(opts), "gnn_marwil_random_fallback", rich
            return max(gnn, key=gnn.get), "gnn_marwil", rich
        if drawn != "exploit_tree":
            raise RuntimeError("interrupt_model: drawn strategy %r has no interrupt branch -- "
                               "refusing to silently play exploit_tree" % (drawn,))
        if not usable:
            pick = self.rng.choice(opts)
            sys.stderr.write("interrupt_model: %s -> %r (exploit_tree_random_fallback, %s)\n"
                             % (screen, pick, why))
            return pick, "exploit_tree_random_fallback", rich
        if not exploit:
            return self.rng.choice(opts), "exploit_tree_random_fallback", rich
        return max(exploit, key=exploit.get), "exploit_tree", rich


def main():
    print(json.dumps(train(), indent=2))


if __name__ == "__main__":
    main()
