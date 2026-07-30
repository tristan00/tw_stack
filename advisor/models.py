r"""models.py -- one value model PER decision type + exploration -> the exploit/explore/blended score.

Trains on the REAL decision dataset (decision_instances + dilemmas across all valid campaigns). Each
row = a historical CHOSEN option, featurized (features.py, DB-backed) with the campaign's value TARGET
(FW-6, a blend of TWO blends: target = 0.5*campaign_best_HALF + 0.5*delta_HALF, where the best half is
the mean of each per-campaign PEAK part {income,settlements,inv-power-rank,allies,vassals} PLUS survival
{campaign max turn}, and the delta half is the mean of each part's forward H-turn delta -- every part
min-max-normalized INDIVIDUALLY and oriented high=GOOD; see _target). One CatBoost per type; scores
UNCHOSEN options via generic features. Exploration = count-based UCB novelty. combined = exploit +
beta*explore.  (Per direction: pipeline correctness > metric tuning; this model GUIDES data collection.)

  python models.py build         # gather+cache the dataset (streams the big logs ONCE)
  python models.py train         # fit per-type models from cache, print rows/type + rough metric
  python models.py demo <run>    # score a real decision per type -> exploit/explore/blended table
"""
import json
import os
import pickle
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("structurer", "runs"):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _p)))
sys.path.insert(0, _HERE)
import structurer                 # noqa: E402
import value_dataset              # noqa: E402
import decision_instances as DI   # noqa: E402
import features as F              # noqa: E402
try:
    import dilemmas as DL         # noqa: E402
except Exception as e:
    DL = None
    sys.stderr.write("models: dilemmas import unavailable -> %s\n" % repr(e)[:80])

MODEL_DIR = os.path.join(_HERE, "models_store")
CACHE = os.environ.get("ADVISOR_CACHE") or os.path.join(_HERE, "cache", "dataset.json")  # env override for baseline/identity runs
H = 8
BETA = 0.15
_STATE_WARNED = set()   # CHANGE B: types we've already warned lack a state model (log-once, not per-call)
MIN_TURNS = 5          # drop campaigns shorter than this from training
# value = mean of min-max-normalized parts. power_rank = the player's STANDINGS rank among all factions
# by military power, stored INVERTED (-rank) so higher = better (rank 1 -> largest -rank -> best).
# vassals = num_vassals (NEW twstate field; 0 on old runs, so it is inert until fresh data).
# FW-5 (VERIFIED): the advisor's value target uses power RANK (`power_rank` -- the standings position the
# player actually SEES in-game), NEVER the raw `power_score`. Keep it this way.
VALUE_PARTS = ("income", "settlements", "power_rank", "allies", "vassals")


# ---------- gather + cache the real decision dataset ----------
def _player_rank(log_path, player):
    """{turn: STANDINGS rank of `player` among ALL factions by summed force strength} (rank 1=strongest).
    Cheap force-only stream (substring-prefilter to kind:force lines before json.loads)."""
    per = {}
    _bad = 0
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"kind":"force"' not in line:
                continue
            i = line.find("TWSTATE ")
            if i < 0:
                continue
            try:
                r = json.loads(line[i + 8:])
            except Exception:
                _bad += 1            # hot per-line parse: count, don't log each
                continue
            tt, fac, s = r.get("turn"), r.get("faction"), r.get("strength")
            if tt is None or not fac or s is None:
                continue
            per.setdefault(tt, {})
            per[tt][fac] = per[tt].get(fac, 0.0) + s
    if _bad:
        sys.stderr.write("models._player_rank: skipped %d malformed force lines\n" % _bad)
    rank = {}
    for tt, d in per.items():
        for idx, (fac, _) in enumerate(sorted(d.items(), key=lambda kv: -kv[1]), 1):
            if fac == player:
                rank[tt] = idx
                break
    return rank


def _gather_run(run):
    """WORKER (module-level so ProcessPool can pickle it): stream ONE run ->
    (camp:{ck:{player,sub,lord,turns:{t:parts}}}, decisions:[slim dicts]). Independent per run.
    Diligent timing: this thread's total + each major function; build_decisions logs its sub-phases."""
    import time
    pc = time.perf_counter
    rid = os.path.basename(run.rstrip("/\\"))[-6:]

    def log(phase, dt):
        print("  [%-9s] %-16s %7.1fs" % (rid, phase, dt), flush=True)
    t_all = pc()
    t = pc()
    camp = {}
    for c in structurer.list_campaigns(run):
        # A campaign can span several relaunch-session files (list_campaigns now returns ALL of them
        # in `logs`, largest-first; the largest carries the header). Read every file and merge by
        # turn so a multi-file campaign drops no turns -- the largest file alone would miss the rest.
        logs = c.get("logs") or [c["log"]]
        d = None
        for clog in logs:                                      # NB: not `log` -- that's the timer fn
            ex = value_dataset.extract_campaign(clog)
            if not ex:
                continue
            if d is None:                                      # first (largest) file seeds identity
                ck = "%s.%s" % (rid, ex["player"].split("_", 2)[-1][:12])
                d = camp.setdefault(ck, {"player": ex["player"], "sub": ex.get("subculture"),
                                         "lord": ex.get("lord"), "turns": {}})
            rank = _player_rank(clog, ex["player"])            # standings power-rank leg
            for tt, ct in ex["turns"].items():
                rk = rank.get(tt)
                d["turns"][str(tt)] = {"income": ct["state"].get("income") or 0,
                                       "settlements": ct["state"].get("regions") or 0,
                                       "power_rank": (-rk if rk is not None else -50.0),
                                       "allies": ct["state"].get("num_allies") or 0,
                                       "vassals": ct["state"].get("num_vassals") or 0}
    log("value_parts", pc() - t)
    t = pc()
    try:
        decs = DI.build_decisions(run)
    except Exception as e:
        decs = []; print("  [%s] build_decisions FAILED: %s" % (rid, e), flush=True)
    log("build_decisions", pc() - t)
    t = pc()
    if DL:
        try:
            decs += DL.build_dilemma_decisions(run)
        except Exception as e:
            print("  [%s] dilemmas FAILED: %s" % (rid, e), flush=True)
    log("dilemmas", pc() - t)
    slim = [{k: dec.get(k) for k in ("campaign", "turn", "type", "chosen", "chosen_linked",
                                     "chosen_source", "context", "options", "dilemma")} for dec in decs]
    log("TOTAL (%d dec, %d camp)" % (len(slim), len(camp)), pc() - t_all)
    return camp, slim


def gather(rebuild=False):
    if os.path.isfile(CACHE) and not rebuild:
        return json.load(open(CACHE, encoding="utf-8"))
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "runs")))
    import runs
    import time as _time
    from concurrent.futures import ProcessPoolExecutor
    os.environ["ADVISOR_TIMING"] = "1"           # workers inherit -> build_decisions logs its sub-phases
    run_paths = [r["path"] for r in runs.list_valid_runs()]
    # PARALLEL UNIT = RUN (unchanged). Campaign-level splitting was evaluated and DROPPED: the recruit
    # pool's two-band Y threshold (recruit_pool.resolve_recruit_pools) is a RUN-GLOBAL computation, so
    # scoping build_decisions per campaign shifts it and flips real chosen_source labels (measured 12/35
    # in 20260727_144203) -> NOT data-identical. Keeping the run unit preserves ex.map submission order
    # (== the pre-change decision order) and the after-collection global value-norm, so the cache is
    # byte-identical to before; the speedup comes from the parse_events/correlate memoization (redundant
    # passes removed) + more workers so the many small runs no longer queue behind 4 lanes. Workers:
    # min(cpu-2, N) leaves ~2 cores for the live recorder/advisor/game (env override for baseline runs).
    nw = int(os.environ.get("ADVISOR_GATHER_WORKERS") or 0) or min((os.cpu_count() or 4) - 2, len(run_paths) or 1)
    print("gathering %d runs in parallel (%d workers, timing on)..." % (len(run_paths), nw), flush=True)
    _wall = _time.perf_counter()
    camp, decisions = {}, []
    with ProcessPoolExecutor(max_workers=max(1, nw)) as ex:
        for c, decs in ex.map(_gather_run, run_paths):      # one worker per run, concurrent (submission order)
            for ck, d in c.items():
                dd = camp.setdefault(ck, {"player": d["player"], "sub": d["sub"],
                                          "lord": d["lord"], "turns": {}})
                dd["turns"].update(d["turns"])
            decisions.extend(decs)
    # drop campaigns shorter than MIN_TURNS (too little signal), with their decisions -- and so they
    # don't skew the global value-normalization below
    short = {ck for ck, d in camp.items() if len(d["turns"]) < MIN_TURNS}
    for ck in short:
        del camp[ck]
    if short:
        decisions = [x for x in decisions if x.get("campaign") not in short]
        print("dropped %d campaigns <%d turns: %s" % (len(short), MIN_TURNS, sorted(short)), flush=True)
    all_parts = [p for d in camp.values() for p in d["turns"].values()]
    rng = {p: (min(x[p] for x in all_parts), max(x[p] for x in all_parts)) for p in VALUE_PARTS} if all_parts else {}

    def value(p):
        vals = []
        for k in VALUE_PARTS:
            lo, hi = rng[k]
            vals.append((p[k] - lo) / (hi - lo) if hi > lo else 0.0)
        return sum(vals) / len(vals)
    for ck, d in camp.items():
        d["value"] = {t: value(p) for t, p in d["turns"].items()}
    data = {"camp": camp, "decisions": decisions}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(data, open(CACHE, "w", encoding="utf-8"))
    print("cached %d decisions, %d campaigns -> %s   (gather WALL %.1fs)"
          % (len(decisions), len(camp), CACHE, _time.perf_counter() - _wall), flush=True)
    return data


def _target_ranges(camp):
    """FW-6 (two-blend reward): the training-population min-max ranges used by _target's
        target = 0.5 * campaign_best_HALF + 0.5 * delta_HALF
    blend. EVERY component is normalized INDIVIDUALLY (never the aggregate), so this precomputes ONCE
    (mirrors gather()'s global value normalization) the range of, over the whole training set:
      peak[part]  -- over CAMPAIGNS: that campaign's PEAK (max over its turns) of the raw part -> feeds
                     the campaign-best half; each part's per-campaign best is min-max'd on its OWN scale.
      delta[part] -- over ALL campaign-turns: the part's forward H-turn delta (clamped endpoint) -> feeds
                     the delta half; each part's delta min-max'd on its OWN scale.
      maxturn     -- over CAMPAIGNS: the campaign's last recorded turn = the SURVIVAL proxy leg (which
                     lives in the best half); longer-surviving campaigns -> higher normalized survival.
    Reads the RAW per-part dicts d["turns"][t] (dict of VALUE_PARTS), NOT the collapsed `value` scalar,
    so each part keeps its own distribution. (FW-5/directionality: `power_rank` is stored INVERTED
    (-rank) upstream in _gather_run so higher = better standing -- all parts are monotone high=GOOD.)"""
    peaks = {p: [] for p in VALUE_PARTS}
    deltas = {p: [] for p in VALUE_PARTS}
    maxturns = []
    for d in camp.values():
        turns = d.get("turns") or {}
        if not turns:
            continue
        ts = sorted(int(x) for x in turns)
        last = ts[-1]
        maxturns.append(last)                                  # campaign max turn = last recorded (survival)
        for p in VALUE_PARTS:                                  # per-campaign PEAK of each part (best half)
            pv = [turns[str(t)].get(p) for t in ts if turns[str(t)].get(p) is not None]
            if pv:
                peaks[p].append(max(pv))
        for t in ts:                                           # per-part forward delta at every turn
            now = turns[str(t)]
            end = turns.get(str(min(t + H, last)), now)        # CLAMP endpoint to last recorded turn
            for p in VALUE_PARTS:
                a = now.get(p)
                if a is None:
                    continue                                   # part missing on this turn -> skip (no crash)
                b = end.get(p, a)
                deltas[p].append((a if b is None else b) - a)

    def _rng(xs):
        return (min(xs), max(xs)) if xs else (0.0, 1.0)        # empty -> (0,1) fallback (kept)
    return {"peak": {p: _rng(peaks[p]) for p in VALUE_PARTS},
            "delta": {p: _rng(deltas[p]) for p in VALUE_PARTS},
            "maxturn": _rng(maxturns)}


def _target(camp, ck, turn, ranges=None):
    """FW-6 reward (USER spec -- a blend of TWO blends, 50/50; NOT the earlier equal-thirds):
        target = 0.5 * campaign_best_HALF + 0.5 * delta_HALF
    * campaign_best_HALF = mean over parts of _mm0(that part's per-campaign PEAK) PLUS a survival part
      _mm0(campaign max turn). Each part is min-max'd INDIVIDUALLY across the population BEFORE the mean,
      so all are comparable. Rewards decisions in campaigns that reach a strong absolute state AND survive
      long -- SURVIVAL lives HERE, in the best half (a run that dies early -> low max-turn -> lower target).
    * delta_HALF = mean over parts of _mm0(that part's forward H-turn delta). Each part's delta is min-max'd
      INDIVIDUALLY across all campaign-turns before the mean. Rewards decisions that improve the state next.
    DIRECTIONALITY (verified high=GOOD for EVERY component, so the blend is one coherent "higher is better"
    number): income^, settlements^, allies^, vassals^, survival/max-turn^, power via INVERTED rank
    (`power_rank` stored -rank upstream so ^ = better standing), and each forward delta^ = improvement^.
    Because no component is backwards, raising ANY single future part value cannot DECREASE the target.
    EDGES: forward endpoint is CLAMPed to the last recorded turn (min(t+H,last)); a decision on the FINAL
    recorded turn -> end==now -> delta 0 (neutral) -- early-death badness is carried by the SURVIVAL leg,
    not the delta. A part whose population range is degenerate (hi==lo, e.g. vassals always 0) contributes
    0.0 via _mm0 (no div0). A part missing on a turn is skipped. `ranges` None (defensive/ad-hoc callers)
    -> raw mean forward delta. Reads the RAW per-part dicts d["turns"][t], NOT the `value` scalar (each
    part normalized on its own). NB: `last recorded turn` is a SURVIVAL PROXY -- it does not distinguish
    true defeat from a stopped recording; acceptable per spec, NOT death-detection."""
    d = camp.get(ck)
    if not d:
        return None
    turns = d.get("turns") or {}
    t = str(turn)
    if t not in turns:
        return None
    ts = sorted(int(x) for x in turns)
    last = ts[-1]
    now = turns[t]
    end = turns.get(str(min(int(turn) + H, last)), now)        # clamp; final turn -> end is `now` -> delta 0
    if not ranges:                                             # no population ranges -> raw mean delta (fallback)
        ds = []
        for p in VALUE_PARTS:
            a = now.get(p)
            if a is None:
                continue
            b = end.get(p, a)
            ds.append((a if b is None else b) - a)
        return (sum(ds) / len(ds)) if ds else 0.0
    peak_rng, delta_rng = ranges["peak"], ranges["delta"]
    mlo, mhi = ranges.get("maxturn", (0.0, 1.0))
    # --- campaign-best HALF: each part's per-campaign PEAK (normalized on its OWN scale) + survival(max turn)
    peaks = d.get("_peaks")                                    # per-campaign constant; cache in-memory (never cached)
    if peaks is None:
        peaks = {p: (lambda pv: max(pv) if pv else None)(
                     [turns[str(tt)].get(p) for tt in ts if turns[str(tt)].get(p) is not None])
                 for p in VALUE_PARTS}
        d["_peaks"] = peaks
    best_parts = [_mm0(peaks[p], *peak_rng[p]) for p in VALUE_PARTS if peaks[p] is not None]
    best_parts.append(_mm0(last, mlo, mhi))                    # survival leg lives in the best half
    best_half = sum(best_parts) / len(best_parts) if best_parts else 0.0
    # --- delta HALF: each part's forward H-turn delta (normalized on its OWN scale)
    delta_parts = []
    for p in VALUE_PARTS:
        a = now.get(p)
        if a is None:
            continue
        b = end.get(p, a)
        delta_parts.append(_mm0((a if b is None else b) - a, *delta_rng[p]))
    delta_half = sum(delta_parts) / len(delta_parts) if delta_parts else 0.0
    return 0.5 * best_half + 0.5 * delta_half


def _state_feats(dtype, ctx, faction, sub, lord):
    """CHANGE B: the STATE-ONLY feature dict for a decision -- context + per-type context features
    (features.context_features + context_type_features), i.e. every ctx_* signal that depends ONLY on
    the game state, NOT on the option/action being scored. This is g(s)'s feature set; it is identical
    for every option in one menu, so g(s) is a per-decision constant. (The opt_* option features and the
    _cross_features option x state interactions are deliberately excluded -- they vary per option.)"""
    r = F.context_features(ctx, faction, sub, lord)
    r.update(F.context_type_features(dtype, ctx))
    return r


def build_rows(data):
    camp = data["camp"]
    ranges = _target_ranges(camp)          # FW-6: training-set ranges for the equal-thirds blend (once)
    rows = defaultdict(list)
    for dec in data["decisions"]:
        if not dec.get("chosen_linked") or dec.get("chosen") is None:
            continue
        tgt = _target(camp, dec["campaign"], dec["turn"], ranges)
        if tgt is None:
            continue
        d = camp.get(dec["campaign"], {})
        # dilemmas: pass the dilemma key + the CHOSEN option's label so the featurizer can emit the
        # opt_c_dilemma/opt_c_choice categoricals (options carried in the slim dict; None for others).
        choice_label = None
        if dec.get("type") == "dilemma":
            ch = dec.get("chosen")
            choice_label = next((o.get("label") or o.get("key")
                                 for o in (dec.get("options") or []) if o.get("ordinal") == ch), None)
        # the chosen option's FULL record (per-candidate fields -- trait/level/cost/faction stats -- that
        # the new per-option types read). None for synthesized/empty-option decisions; the featurizer
        # defaults every such field so old data stays inert.
        chosen_opt = next((o for o in (dec.get("options") or [])
                           if str(o.get("key")) == str(dec.get("chosen"))
                           or str(o.get("id")) == str(dec.get("chosen"))), None)
        row = F.featurize(dec["type"], dec.get("context"), d.get("player"), d.get("sub"), d.get("lord"),
                          dec["chosen"], dec.get("chosen_source"),
                          dilemma=dec.get("dilemma"), choice=choice_label, option=chosen_opt)
        row["_target"] = round(tgt, 5)
        row["_group"] = dec["campaign"]
        # CHANGE B: the state-only feature dict (g(s)'s inputs), carried alongside for the state model.
        row["_state"] = _state_feats(dec["type"], dec.get("context"), d.get("player"),
                                     d.get("sub"), d.get("lord"))
        rows[dec["type"]].append(row)
    return rows


# ---------- per-type training: EXPLOIT (value) + EXPLORE (novelty), both normalized to [0,1] ----------
def _encode(feat_dicts, num, cat, cat_maps=None):
    """Numeric matrix for IsolationForest: numerics as-is, categoricals ordinal-encoded (an unseen
    category -> a fresh index the forest tends to isolate = naturally novel). Returns (X, cat_maps)."""
    if cat_maps is None:
        cat_maps = {c: {v: i for i, v in enumerate(sorted({str(r.get(c, "?")) for r in feat_dicts}))}
                    for c in cat}
    X = []
    for r in feat_dicts:
        row = [float(r.get(c, 0) or 0) for c in num]
        for c in cat:
            mp = cat_maps[c]
            row.append(mp.get(str(r.get(c, "?")), len(mp)))
        X.append(row)
    return X, cat_maps


def _mm(v, lo, hi):
    """min-max to [0,1] (0.5 if the training range is degenerate)."""
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.5


def _mm0(v, lo, hi):
    """min-max to [0,1] for TARGET parts: a degenerate/zero population range (hi<=lo) contributes 0.0
    (NOT the neutral 0.5 that _mm uses for scoring). A part that never varies across the training set
    (e.g. vassals all 0) then adds NOTHING to the blend instead of a spurious 0.5. (score_decision's
    exploit/explore keep _mm's 0.5.)"""
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0


def train_all(rebuild=False):
    from catboost import CatBoostRegressor, Pool
    from sklearn.ensemble import IsolationForest
    import numpy as np
    data = gather(rebuild=rebuild)
    rows = build_rows(data)
    os.makedirs(MODEL_DIR, exist_ok=True)
    meta, report = {}, {}
    for dtype, rs in sorted(rows.items()):
        num, cat = F.split_columns(rs)
        cat_idx = list(range(len(num), len(num) + len(cat)))
        # EXPLOIT: CatBoost value model
        Xc = [[r.get(c, 0) for c in num] + [str(r.get(c, "?")) for c in cat] for r in rs]
        y = [r["_target"] for r in rs]
        m = CatBoostRegressor(iterations=200, depth=4, learning_rate=0.05, loss_function="RMSE", verbose=0)
        m.fit(Pool(Xc, y, cat_features=cat_idx))
        m.save_model(os.path.join(MODEL_DIR, "%s.cbm" % dtype))
        preds = list(m.predict(Pool(Xc, cat_features=cat_idx)))
        exp_lo, exp_hi = (min(preds), max(preds)) if preds else (0.0, 1.0)
        # CHANGE B (IMPACT): a cheap STATE-ONLY baseline g(s) -- SAME rows/target, but features = the
        # state dict only (context + per-type context; drops opt_* and the option x state cross features).
        # Same CatBoost config. score_decision uses raw_exploit = f(s,a) - g(s) as the cross-type impact
        # sort key. Saved as <type>.state.cbm; its column split is stored in meta (state_num/state_cat).
        srs = [r.get("_state") or {} for r in rs]
        snum, scat = F.split_columns(srs)
        scat_idx = list(range(len(snum), len(snum) + len(scat)))
        Xs = [[r.get(c, 0) for c in snum] + [str(r.get(c, "?")) for c in scat] for r in srs]
        sm = CatBoostRegressor(iterations=200, depth=4, learning_rate=0.05, loss_function="RMSE", verbose=0)
        sm.fit(Pool(Xs, y, cat_features=scat_idx))
        sm.save_model(os.path.join(MODEL_DIR, "%s.state.cbm" % dtype))
        # EXPLORE: IsolationForest novelty over the SAME feature vectors (context + option)
        Xa, cat_maps = _encode(rs, num, cat)
        # n_jobs=-1: trees are independent; with random_state=0 the forest is byte-identical regardless of
        # worker count -- parallelism only changes WHERE each tree is built, not its content. (Was single-
        # core.) CatBoost fits already use all cores per fit, so training stays sequential per type.
        iso = IsolationForest(n_estimators=200, random_state=0, n_jobs=-1)
        iso.fit(Xa)
        nov = [-s for s in iso.score_samples(Xa)]              # higher = more novel/anomalous
        nov_lo, nov_hi = (min(nov), max(nov)) if nov else (0.0, 1.0)
        pickle.dump({"iso": iso, "cat_maps": cat_maps},
                    open(os.path.join(MODEL_DIR, "%s.iso.pkl" % dtype), "wb"))
        # store each metric's training range so scoring maps BOTH to a common [0,1] scale (aggregatable)
        meta[dtype] = {"num": num, "cat": cat, "exp_lo": exp_lo, "exp_hi": exp_hi,
                       "nov_lo": nov_lo, "nov_hi": nov_hi,
                       "state_num": snum, "state_cat": scat}   # CHANGE B: g(s)'s column split
        mae = float(np.mean(np.abs(np.array(preds) - np.array(y)))) if y else None
        report[dtype] = {"rows": len(rs), "groups": len(set(r["_group"] for r in rs)), "mae": mae}
    json.dump(meta, open(os.path.join(MODEL_DIR, "meta.json"), "w"))
    return report


def load_all():
    """Load every per-type model set: (models f(s,a), explorers, meta, state_models g(s)). state_models
    holds the CHANGE-B state-only baselines (<type>.state.cbm); a type missing one simply isn't in the
    dict, and score_decision degrades to raw_exploit = f(s,a) for it."""
    from catboost import CatBoostRegressor
    meta = json.load(open(os.path.join(MODEL_DIR, "meta.json")))
    models, explorers, state_models = {}, {}, {}
    for dtype in meta:
        m = CatBoostRegressor(); m.load_model(os.path.join(MODEL_DIR, "%s.cbm" % dtype))
        models[dtype] = m
        p = os.path.join(MODEL_DIR, "%s.iso.pkl" % dtype)
        if os.path.isfile(p):
            explorers[dtype] = pickle.load(open(p, "rb"))
        sp = os.path.join(MODEL_DIR, "%s.state.cbm" % dtype)   # CHANGE B: state-only baseline g(s)
        if os.path.isfile(sp):
            sm = CatBoostRegressor(); sm.load_model(sp)
            state_models[dtype] = sm
    return models, explorers, meta, state_models


def score_decision(dec, models, explorers, meta, beta=BETA, state_models=None):
    """Per option: exploit = normalized predicted value, explore = normalized IsolationForest novelty
    (BOTH in [0,1], same scale), aggregated combined = (1-beta)*exploit + beta*explore. Sorted.

    CROSS-TYPE key: `raw_exploit` = the action's predicted IMPACT = f(s,a) - g(s) (CHANGE B), where
    f(s,a) is the per-type value model and g(s) is the per-type STATE-ONLY baseline (state_models). g(s)
    is a per-decision constant (state is fixed across the menu), so subtracting it does NOT change the
    within-menu order, but it RE-CENTERS the cross-type scale: inconsequential decisions cluster ~0 and
    consequential (campaign-shaping) ones spread - / +. Because every per-type model predicts the SAME
    target, the impact is on a COMMON scale across decision types, so screens.combine_screen sorts a
    merged multi-type list on this key. If a type has no state model, raw_exploit falls back to f(s,a)
    (logged once). raw_exploit is None when the type has no value model at all. The per-type _mm
    exploit/explore/combined and the by-combined sort are EXACTLY as before (exploit still on raw f(s,a))."""
    from catboost import Pool
    dtype = dec["type"]
    m = models.get(dtype); mt = meta.get(dtype); e = explorers.get(dtype)
    sm = (state_models or {}).get(dtype)
    ctx = dec.get("context") or {}
    faction = ctx.get("faction"); subc = ctx.get("subculture")
    # CHANGE B: g(s) -- computed ONCE per decision (state is fixed across the menu's options). Same state
    # feature dict the state model trained on (context + per-type context; no option/action features).
    gs = None
    if sm is not None and mt is not None and mt.get("state_num") is not None:
        snum, scat = mt["state_num"], mt["state_cat"]
        sf = F.context_features(ctx, faction, subc, "?")
        sf.update(F.context_type_features(dtype, ctx))
        Xs = [[sf.get(c, 0) for c in snum] + [str(sf.get(c, "?")) for c in scat]]
        try:
            gs = float(sm.predict(Pool(Xs, cat_features=list(range(len(snum), len(snum) + len(scat)))))[0])
        except Exception as ex:
            sys.stderr.write("models: g(s) predict %s -> %s\n" % (dtype, repr(ex)[:80]))
            gs = None
    if m is not None and gs is None and dtype not in _STATE_WARNED:  # log-once graceful degrade
        _STATE_WARNED.add(dtype)
        sys.stderr.write("models: no state model for %s -> raw_exploit = f(s,a) (impact off)\n" % dtype)
    out = []
    for o in dec["options"]:
        okey = o.get("key") if o.get("key") is not None else o.get("id")
        feats = F.featurize(dtype, ctx, faction, subc, "?", okey, o.get("source"),
                            dilemma=dec.get("dilemma"), choice=(o.get("label") or o.get("key")),
                            option=o)
        exploit = explore = 0.5
        raw = None
        if m is not None and mt is not None:
            num, cat = mt["num"], mt["cat"]
            Xc = [[feats.get(c, 0) for c in num] + [str(feats.get(c, "?")) for c in cat]]
            pred = float(m.predict(Pool(Xc, cat_features=list(range(len(num), len(num) + len(cat)))))[0])
            raw = pred if gs is None else (pred - gs)        # CHANGE B: impact = f(s,a) - g(s)
            exploit = _mm(pred, mt["exp_lo"], mt["exp_hi"])   # per-type exploit stays on raw f(s,a)
        if e is not None and mt is not None:
            Xa, _ = _encode([feats], mt["num"], mt["cat"], e["cat_maps"])
            nov = -float(e["iso"].score_samples(Xa)[0])
            explore = _mm(nov, mt["nov_lo"], mt["nov_hi"])
        combined = (1 - beta) * exploit + beta * explore
        # carry the recorder's single captured on-screen label through to the scored row so the advisor's
        # label resolver can prefer it verbatim (JOB 1); None when the option is a keyed (non-icon) option.
        out.append({"key": okey, "label": o.get("label"), "available": o.get("available"),
                    "onscreen": o.get("onscreen"),
                    "raw_exploit": (round(raw, 5) if raw is not None else None),
                    "exploit": round(exploit, 3), "explore": round(explore, 3),
                    "combined": round(combined, 3)})
    out.sort(key=lambda r: -r["combined"])
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    if cmd == "build":
        gather(rebuild=True)
    elif cmd == "train":
        rep = train_all(rebuild=("--rebuild" in sys.argv))
        print("%-11s %6s %7s %8s" % ("type", "rows", "camps", "MAE(in)"))
        for t, r in sorted(rep.items()):
            print("%-11s %6d %7d %8s" % (t, r["rows"], r["groups"],
                  ("%.4f" % r["mae"]) if r["mae"] is not None else "--"))
    elif cmd == "demo":
        run = sys.argv[2]
        models, counts, meta, state_models = load_all()
        ds = DI.build_decisions(run)
        if DL:
            try:
                ds += DL.build_dilemma_decisions(run)
            except Exception as e:
                sys.stderr.write("models: demo dilemmas skipped -> %s\n" % repr(e)[:80])
        seen = set()
        for d in ds:
            if d["type"] in seen or not d.get("chosen_linked"):
                continue
            seen.add(d["type"])
            tbl = score_decision(d, models, counts, meta, state_models=state_models)
            print("\nturn %s  %s  (%d options)  chosen=%r" % (d["turn"], d["type"].upper(), len(tbl), d["chosen"]))
            print("   combined  exploit  explore  avail  option")
            for r in tbl[:8]:
                mark = "  <==" if str(r["key"]) == str(d["chosen"]) else ""
                print("   %+8.3f %+8.3f %8.3f  %-5s  %s%s"
                      % (r["combined"], r["exploit"], r["explore"], str(r["available"]), str(r["key"])[:40], mark))


if __name__ == "__main__":
    main()
