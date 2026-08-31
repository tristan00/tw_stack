from __future__ import annotations

import json
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common
import campaign_growth as CG
import metrics_db
import psycopg
from decisions import pg

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)
sys.path.insert(0, common.DECISIONS)

import arms
import interrupt_model as IM
import journal
import loop as L
import policy as P
import strategies as ST
from interrupts import UnhandledScreen

RUNS_ROOT = common.RUNS_ROOT


MAX_LAUNCH_FAILURES = 3
LOG_ARCHIVE = common.ARCHIVE_SCRIPT_LOGS
ROTATE_MIN_AGE_S = 600


def _rotate_logs(log):
    import glob
    import shutil
    from bus_launcher import GAME_DIR
    dirs = [(os.path.join(GAME_DIR, "script_log_*.txt"), ROTATE_MIN_AGE_S)]
    try:
        rd = journal.current_run_dir(timeout=5.0)
        dirs.append((os.path.join(rd, "shots", "*"), 86400))
    except Exception as e:
        log("   rotation: run-dir shots skipped (no CURRENT_RUN: %s)" % repr(e)[:60])
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(LOG_ARCHIVE, stamp)
    moved, moved_mb, skipped = 0, 0.0, 0
    now = time.time()
    for pattern, min_age in dirs:
        for p in glob.glob(pattern):
            try:
                if now - os.path.getmtime(p) < min_age:
                    skipped += 1
                    continue
                mb = os.path.getsize(p) / (1024.0 * 1024.0)
                os.makedirs(dst, exist_ok=True)
                shutil.move(p, os.path.join(dst, os.path.basename(p)))
                moved += 1
                moved_mb += mb
            except OSError:
                skipped += 1
    return "moved %d files (%.0fMB) -> %s, skipped %d (fresh/locked)" % (
        moved, moved_mb, dst if moved else LOG_ARCHIVE, skipped)


BUS_FILES = (common.native(common.BUS_CMD_PATH),
             common.native(common.BUS_OUT_PATH))


def _bus_sizes():
    out = {}
    for p in BUS_FILES:
        try:
            out[os.path.basename(p)] = round(os.path.getsize(p) / 1e6, 2)
        except OSError:
            out[os.path.basename(p)] = None
    return out


_CAMPAIGN_LABEL = {"wh3_main_combi": "Immortal Empires",
                   "wh3_main_chaos": "Realm of Chaos"}


def _width_counts():
    try:
        con = pg.connect(autocommit=True, readonly=True)
    except psycopg.OperationalError:
        return {}
    try:
        return {(m, f): n for m, f, n in con.execute(
            "SELECT campaign_map, faction, n FROM start_counts")}
    except psycopg.errors.UndefinedTable:
        return {}
    finally:
        con.close()


import ucb_stats as UCB

UCB_WINDOW = UCB.WINDOW
UCB_MIN_PLAYS = UCB.MIN_PLAYS
_entropy_bits = UCB.entropy_bits
_blend = UCB.blend


def _start_gain_stats(window=UCB_WINDOW):
    try:
        con = pg.connect(autocommit=True, readonly=True)
    except psycopg.OperationalError:
        return {}, 0.0
    try:
        rewards = UCB.window_rewards(con, window)
    except psycopg.errors.UndefinedTable:
        return {}, 0.0
    finally:
        con.close()
    return UCB.start_stats(rewards), UCB.window_blend(rewards)


def _ucb_cell(score, n, mean, explore, p, blend=None, d=None, adjust=0.0):
    fin = lambda v: None if v == float("inf") else float(v)
    d = d or UCB.EMPTY
    return {"campaign_map": p["campaign_map"], "faction": p["faction"], "n": int(n),
            "mean": float(mean), "explore": fin(explore), "score": fin(score),
            "blend": (None if blend is None or int(n) < UCB_MIN_PLAYS else float(blend)),
            "entropy": float(d["entropy"]), "std": float(d["std"]),
            "adjust": float(adjust)}


def _ucb_pick(pool, stats, k, scale, rng, log=print, ts=None):
    total = max(1, sum(d["n"] for d in stats.values()))
    c = k * scale
    adjust, adjust_path = UCB.load_adjustments()
    scored = []
    for p in pool:
        d = stats.get((p["campaign_map"], p["faction"])) or dict(UCB.EMPTY)
        n, mean = d["n"], d["mean"]
        a = adjust.get(p["faction"], 0.0)
        blend, explore, score = UCB.score(d, c, total, a)
        scored.append((score, n, mean, explore, p, blend, d, a))
    scored.sort(key=lambda s: (-s[0], s[4]["file"]))
    if adjust:
        log("ucb manual adjust from %s: %s"
            % (adjust_path, ", ".join("%s%+g" % (f, v)
                                      for f, v in sorted(adjust.items()))))
    log("ucb table (k=%g x window blend %.3f = c %.3f, %d plays over the trailing %d "
        "campaigns): score = blend + c*sqrt(ln plays / n) + adjust, "
        "blend = (mean + H + std) / 3; "
        "under %d plays the score is inf | n | mean | H | std | start"
        % (k, scale, c, total, UCB_WINDOW, UCB_MIN_PLAYS))
    for score, n, mean, explore, p, blend, d, a in scored:
        log("  %8s = %+6.3f + %8s %s n=%-3d mean=%5.2f H=%4.2f sd=%4.2f  %s %s"
            % ("inf" if score == float("inf") else "%.3f" % score, blend,
               "inf" if explore == float("inf") else "%.3f" % explore,
               "%+4.1f" % a if a else "    ",
               n, mean, d["entropy"], d["std"], p["faction"], p["campaign_map"]))
    best = scored[0][0]
    top = [s for s in scored if s[0] == best]
    score, n, mean, explore, p, blend, d, a = (top[rng.randrange(len(top))]
                                               if len(top) > 1 else top[0])
    log("ucb k=%g c=%.3f: %s on %s -- score=%s blend=%+.3f adj=%+g n=%d mean=%.3f "
        "H=%.2f sd=%.2f (%d plays in window, %d tied)"
        % (k, c, p["faction"], p["campaign_map"],
           "inf" if score == float("inf") else "%.3f" % score, blend, a, n, mean,
           d["entropy"], d["std"], total, len(top)))
    journal.log_ucb_pick(common.RUN_DIR, {
        "ts": ts if ts is not None else time.time(), "c": c, "k": k, "scale": scale,
        "total_plays": total, "tied": len(top),
        "chosen": _ucb_cell(score, n, mean, explore, p, blend, d, a),
        "rows": [dict(_ucb_cell(*row), chosen=(row[4] is p)) for row in scored]})
    return p


def _tail_jsonl(path, n):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-n:]
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            out.append({"unparsed": ln[:300]})
    return out


def _ending_evidence(rd, entry, ex):
    out = {}
    try:
        con = pg.connect(autocommit=True, readonly=True)
        camp = entry.get("campaign_uuid")
        if not camp:
            row = con.execute("SELECT campaign_id FROM decision_points"
                              " ORDER BY decision_id DESC LIMIT 1").fetchone()
            camp = row[0] if row else None
            if camp:
                out["campaign_source"] = "newest_in_corpus"
        if camp:
            out["trajectory"] = [
                {"turn": t, "settlements": s, "income": i, "power_rank": p}
                for t, s, i, p in con.execute(
                    "SELECT turn, settlements, income, power_rank FROM turn_open"
                    " WHERE campaign_id=%s ORDER BY turn DESC LIMIT 6", (camp,))][::-1]
            out["recent_battles"] = [
                {"turn": t, "kind": k, "chosen": c, "confirmed": cf}
                for t, k, c, cf in con.execute(
                    "SELECT turn, kind, chosen, confirmed FROM interrupt_decisions"
                    " WHERE campaign_id=%s AND kind IN ('pre_battle','battle_results','occupation')"
                    " ORDER BY interrupt_id DESC LIMIT 6", (camp,))][::-1]
        con.close()
    except Exception as e:
        out["evidence_error"] = repr(e)[:120]
    try:
        out["defeat_row"] = bool(ex.defeated_row_seen())
    except Exception:
        out["defeat_row"] = None
    traj = out.get("trajectory") or []
    battles = out.get("recent_battles") or []
    last = traj[-1] if traj else {}
    reasons = []
    if out.get("defeat_row"):
        reasons.append("engine death row in this campaign's window")
    peak = max((t.get("settlements") or 0) for t in traj) if traj else 0
    lastn = last.get("settlements") or 0
    if traj and lastn == 0:
        reasons.append("settlements reached 0")
    elif traj and peak > lastn:
        reasons.append("settlement decline %g -> %g" % (peak, lastn))
    final_battle = any(b.get("turn") == last.get("turn") for b in battles)
    if final_battle:
        reasons.append("battle on the final turn")
    healthy = bool(traj) and peak <= lastn and not final_battle
    outcome = entry.get("outcome")
    if outcome == "stagnant":
        g = entry.get("growth") or {}
        verdict = ("abandoned_on_the_growth_bar at turn %g: %s"
                   % (float(g.get("turn") or 0),
                      "; ".join("%s %g->%g" % (m.get("label"), float(m.get("then") or 0),
                                               float(m.get("now") or 0))
                                for m in (g.get("metrics") or {}).values()) or "no metrics"))
    elif outcome == "defeated":
        verdict = ("consistent_with_real_defeat" if reasons
                   else "SUSPICIOUS: no supporting evidence -- review the screenshot")
    elif outcome in ("stuck", "error"):
        if out.get("defeat_row"):
            verdict = "MISLABELED? engine death row present -- likely a real defeat"
        elif str(entry.get("error") or "").startswith("turn_time_cap"):
            verdict = ("turn_time_cap_reached: the turn blew the hard wall-clock cap. This is a "
                       "SPEED bug -- multi-second waits, misfired clicks, or budget-burning "
                       "fallbacks inside the turn. Hunt the slow waits in the .err timeline; "
                       "do not treat the final screen as the culprit")
        elif healthy:
            verdict = "harness_failure_likely: state healthy at the wedge (%s settlements)" \
                      % last.get("settlements")
        else:
            verdict = "ambiguous -- see trajectory and screenshot"
    else:
        verdict = "n/a"
    out["plausibility"] = {"verdict": verdict, "evidence": reasons}
    return out


def _postmortem(runs_root, entry, ex, log):
    rec = {"ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "campaign_key": entry.get("campaign_uuid"),
           "campaign": entry.get("index"), "faction": entry.get("plan"),
           "picked_ts": entry.get("picked_ts"),
           "policy": entry.get("policy"), "outcome": entry.get("outcome"),
           "error": entry.get("error"), "seconds": round(time.time() - entry.get("started", 0), 1),
           "turns_played": entry.get("turns_played"), "actions": entry.get("actions"),
           "confirmed": entry.get("confirmed"), "ended_by": entry.get("ended_by"),
           "growth": entry.get("growth"), "run_dir": entry.get("run_dir"),
           "code_version": os.environ.get("TW_CODE_VERSION")}
    try:
        from bus import _game_alive
        rec["wh3_running"] = _game_alive()
    except Exception as e:
        rec["wh3_running"] = None
        rec["wh3_probe_error"] = repr(e)[:120]
    rd = entry.get("run_dir")
    if rd:
        rec["turn_tail"] = _tail_jsonl(os.path.join(rd, "loop_report.jsonl"), 6)
        if rec.get("turns_played") is None:
            since = float(entry.get("started") or 0)
            turn_rows = [r for r in _tail_jsonl(os.path.join(rd, "loop_report.jsonl"), 500)
                         if r.get("kind") == "turn" and float(r.get("ts") or 0) >= since]
            if turn_rows:
                rec["turns_played"] = len(turn_rows)
                rec["actions"] = sum(int(r.get("actions") or 0) for r in turn_rows)
                rec["confirmed"] = sum(int(r.get("confirmed") or 0) for r in turn_rows)
                rec["ended_by"] = [r.get("ended_by") for r in turn_rows]
        rec["errors_tail"] = _tail_jsonl(os.path.join(rd, "errors.log"), 4) or None
        rec.update(_ending_evidence(rd, entry, ex))
        try:
            logs_dir = os.path.join(rd, "logs")
            rec["game_logs"] = [{"name": f, "bytes": os.path.getsize(os.path.join(logs_dir, f))}
                                for f in sorted(os.listdir(logs_dir))] if os.path.isdir(logs_dir) else []
        except Exception:
            rec["game_logs"] = None
    rd = entry.get("run_dir") or journal.RUN_DIR
    try:
        journal.log_postmortem(rd, rec)
        log("   post-mortem -> %s (campaign=%s, roots=%s, shot=%s)"
            % (journal.DB_NAME, rec.get("campaign_key"),
               ",".join(str(r) for r in (rec.get("roots") or []))[:120],
               rec.get("screenshot")))
    except Exception as e:
        log("   !! post-mortem NOT written: %s" % repr(e)[:160])


def _require_models(mix, imix, cold, log, bootstrap=False):
    if cold:
        return
    import model as M
    problems = []
    if "greedy_catboost" in mix and not M.Ranker().ready:
        problems.append("greedy_catboost: main ranker did not load from %s" % common.MODELS)
    if "greedy_catboost" in imix and not IM.InterruptRanker(strategies=imix).ready:
        problems.append("greedy_catboost: interrupt model did not load from %s"
                        % IM.MODEL_DIR)
    if "greedy_gnn" in mix:
        if common.ROOT not in sys.path:
            sys.path.insert(0, common.ROOT)
        from advisor.mapgraph import greedy_rank as GGR
        if not GGR.Ranker().ready:
            problems.append("greedy_gnn: mapgraph greedy model did not load from %s"
                            % common.MODEL_MAPGRAPH_GREEDY)
    if problems:
        if bootstrap:
            log("model gate DEFERRED: --retrain-first trains before campaign 1 is played, "
                "and the retrain's own trained=false check kills the session if it does "
                "not produce usable models. Missing now:\n  " + "\n  ".join(problems))
            return
        raise SystemExit(
            "REFUSING TO START: the mix needs trained models that are not usable, and only "
            "--cold may run without models. Add --retrain-first to build them before "
            "campaign 1.\n  " + "\n  ".join(problems))
    log("model gate: every trainable arm in the mix loaded a usable model")


def run_campaigns(n=3, turns=20, plan="all",
                  log=print, runs_root=RUNS_ROOT, retrain_every=0, seed=None,
                  cold=False, strategies=None, interrupt_strategies=None,
                  ruleset=None, retrain_first=False, presave_radius=None, width=0,
                  ucb=None):
    from bus import Bus
    from executor import Executor

    import model as M
    import random
    rng = random.Random(seed)
    ex = Executor(Bus())
    mix = ({"random": 1.0} if cold and strategies is None
           else P.normalize_strategies(strategies))
    imix = ({"random": 1.0} if cold and interrupt_strategies is None
            else P.interrupt_strategies_for(mix, interrupt_strategies))
    if presave_radius is None:
        raise SystemExit("--presave-radius is required: a session boots a baked start and "
                         "the map comes with it. Fresh campaigns are baked by bake.py.")
    sys.path.insert(0, common.ROOT)
    import presaves as PS
    presaves = PS.list_presaves(radius=presave_radius)
    if not presaves:
        raise SystemExit(
            "--presave-radius %s but nothing is baked at that radius in %s (have %s). "
            "A run that quietly fell back to fresh untrimmed campaigns would keep "
            "growing a corpus that does not match what was asked for."
            % (presave_radius, PS.presave_dir(), PS.presave_radii() or "none"))
    if plan != "all":
        wanted = set([plan] if isinstance(plan, str) else plan)
        picked = [p for p in presaves if p["faction"] in wanted]
        missing = sorted(wanted - {p["faction"] for p in picked})
        if missing:
            raise SystemExit(
                "the presave pool at r%g holds no baked save for: %s. Bake them first or "
                "drop them from --factions; a run that quietly substituted other starts "
                "would not be the run asked for." % (presave_radius, ", ".join(missing)))
        presaves = picked
    presaves = sorted(presaves, key=lambda p: p["file"])
    by_map = {}
    for p in presaves:
        by_map.setdefault(p["campaign_map"], []).append(p)
    for m in sorted(by_map):
        log("presaves on %s: %d start(s) at radius %s -- %s"
            % (_CAMPAIGN_LABEL.get(m, m), len(by_map[m]), presave_radius,
               ", ".join(sorted(p["faction"] for p in by_map[m]))))
    log("presave sampling: uniform over %d start(s), the campaign follows the draw"
        % len(presaves))
    if ucb is not None and width:
        raise SystemExit("--ucb and --width are both start selectors -- pick one")
    if cold and set(mix) != {"random"}:
        raise SystemExit("--cold is cold: it always plays pure random and takes no strategy "
                         "mix -- got %s; drop --strategies or drop --cold" % json.dumps(mix))
    if cold and set(imix) != {"random"}:
        raise SystemExit("--cold is cold: it always plays pure random on blocking screens "
                         "too -- got %s; drop --interrupt-strategies or drop --cold"
                         % json.dumps(imix))
    if cold and ruleset:
        raise SystemExit("--cold is cold: it always plays pure random -- drop --ruleset %r "
                         "or drop --cold" % ruleset)
    if ("ruleset" in mix or "ruleset" in imix) and not ruleset:
        raise SystemExit("a strategy mix includes 'ruleset' but no --ruleset <name> was given")
    if ruleset and "ruleset" not in mix and "ruleset" not in imix:
        raise SystemExit("--ruleset %r given but 'ruleset' is in neither the action mix %s "
                         "nor the interrupt mix %s"
                         % (ruleset, json.dumps(mix), json.dumps(imix)))
    _require_models(mix, imix, cold, log,
                    bootstrap=bool(retrain_every and retrain_first))
    ruleset_meta = None
    if ruleset:
        import ruleset as RS
        rs = RS.RuleSet.load(ruleset)
        ruleset_meta = {"name": rs.name, "sha256": rs.sha256}
        log("ruleset: %s (%d rules, sha256 %s)" % (rs.name, len(rs.rules), rs.sha256[:12]))
    log("strategy mix: %s" % json.dumps(mix))
    log("interrupt mix: %s" % json.dumps(imix))
    report = {"started": time.time(), "requested": {"campaigns": n, "turns": turns, "plan": plan,
                                                    "strategies": mix,
                                                    "interrupt_strategies": imix,
                                                    "ruleset": ruleset_meta,
                                                    "code_version":
                                                        os.environ.get("TW_CODE_VERSION")},
              "campaigns": []}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(runs_root, "session_%s.json" % stamp)
    report["session"] = out_path
    report["trials"] = []

    launch_failures = 0
    stretch, generation, trained = [], 0, None
    streams_thread = None
    ledger_reconciled = False

    for i in range(n):
        pool = presaves
        if width:
            counts = _width_counts()
            pool = [p for p in presaves
                    if counts.get((p["campaign_map"], p["faction"]), 0) < width]
            if not pool:
                log("width %d reached: every start in the pool has %d+ recorded "
                    "campaigns -- nothing left to widen, ending the session"
                    % (width, width))
                break
            log("width %d: %d of %d start(s) still below target"
                % (width, len(pool), len(presaves)))
        picked_ts = time.time()
        if ucb is not None:
            stats, scale = _start_gain_stats()
            this_presave = _ucb_pick(pool, stats, ucb, scale, rng, log=log, ts=picked_ts)
        else:
            this_presave = rng.choice(pool)
        this_campaign = _CAMPAIGN_LABEL.get(this_presave["campaign_map"],
                                            this_presave["campaign_map"])
        this_plan = this_presave["faction"]
        this_turns = rng.randint(turns[0], turns[1]) if isinstance(turns, (tuple, list)) \
            else int(turns)
        log("\n" + "=" * 78)
        log("CAMPAIGN %d/%d  (up to %d turns, map=%s, faction=%s, presave r%g)"
            % (i + 1, n, this_turns, this_campaign, this_plan,
               this_presave["radius"]))
        log("=" * 78)
        entry = {"index": i + 1, "started": time.time(), "picked_ts": picked_ts,
                 "plan": this_plan, "campaign": this_campaign,
                 "presave": this_presave["file"],
                 "presave_radius": this_presave["radius"],
                 "max_turns": this_turns}
        try:
            ex.mark_campaign_start()
        except Exception as e:
            log("   could not mark the bus offset for this campaign: %s" % repr(e)[:100])
        log("   log rotation: %s" % _rotate_logs(log))
        entry["bus_files"] = _bus_sizes()
        log("   bus: %s" % ", ".join("%s %.1fMB" % (k, v)
                                     for k, v in sorted(entry["bus_files"].items())))
        try:
            ex.rotate_out_log()
        except Exception as e:
            log("   out-log rotation failed: %s" % repr(e)[:90])
        if retrain_every and (i or retrain_first) and i % retrain_every == 0:
            _flush_generation(stretch, generation, report, trained, log)
            stretch = []
            generation += 1
            log("retraining -- the game is down between campaigns, trainers get the whole box")
            try:
                rep = None
                if "greedy_catboost" in mix:
                    t0 = time.time()
                    rep = M.train()
                    entry["retrain"] = dict(rep, seconds=round(time.time() - t0, 1))
                    trained = entry["retrain"]
                    report["_corpus"] = {"rows": rep.get("rows"), "runs": rep.get("runs"),
                                         "campaigns": rep.get("campaigns"),
                                         "n_decisions": rep.get("n_decisions")}
                    log("retrained before run %d: %s" % (i + 1, json.dumps(rep)[:220]))
                if "greedy_catboost" in imix:
                    irep = IM.train()
                    entry["retrain_interrupt"] = irep
                    log("   interrupt model: %s" % json.dumps(irep)[:200])
                if "greedy_gnn" in mix:
                    if common.ROOT not in sys.path:
                        sys.path.insert(0, common.ROOT)
                    from advisor.mapgraph import greedy_train as GGT
                    t1 = time.time()
                    ggrep = GGT.train(log=log)
                    ggrep["seconds"] = round(time.time() - t1, 1)
                    entry["retrain_greedy_gnn"] = ggrep
                    log("   greedy gnn model: %s" % json.dumps(ggrep, default=str)[:220])
                    trained = trained or entry.get("retrain_greedy_gnn")
                for what, r in (("catboost", rep), ("interrupt", entry.get("retrain_interrupt")),
                                ("greedy_gnn", entry.get("retrain_greedy_gnn"))):
                    if r is not None and not r.get("trained"):
                        raise P.ModelUnavailable(
                            "retrain %d: %s trainer reported trained=false (rows=%s need=%s "
                            "error=%s)" % (i + 1, what, r.get("rows"), r.get("need"),
                                           r.get("error")))
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as e:
                entry.setdefault("retrain", {})
                entry["retrain"]["error"] = repr(e)[:250]
                entry["outcome"] = "retrain_failed"
                trained = entry["retrain"]
                _write(out_path, dict(report, campaigns=report["campaigns"] + [entry]))
                log("!! retrain before run %d FAILED -- the run needs freshly usable models, "
                    "crashing instead of playing on without them: %s" % (i + 1, repr(e)[:180]))
                raise SystemExit("retrain before run %d failed: %s" % (i + 1, repr(e)[:250]))
            entry.setdefault("outcome", "in_progress")
            _write(out_path, dict(report, campaigns=report["campaigns"] + [entry]))
        _models = {}

        def _preload_models():
            try:
                r = M.Ranker() if ("greedy_catboost" in mix and not cold) else None
                _models["ranker"] = r
                _models["policy"] = P.Policy(r, strategies=mix, ruleset=ruleset)
            except BaseException as e:
                _models["error"] = e

        _preload = threading.Thread(target=_preload_models, name="model-preload", daemon=True)
        _preload.start()
        try:
            sys.path.insert(0, common.LAUNCHER)
            import presaves as PS
            import bus_launcher
            PS.restore_presave(this_presave, log=log)
            bl = bus_launcher.BusLauncher()
            state = bl.load_save(this_presave["file"])
            ex.bus = bl.bus or ex.bus
            entry["start_state"] = state
            run_dir = journal.current_run_dir(timeout=60.0)
            entry["run_dir"] = run_dir
            entry["stream_watermark"] = L.stream_watermark(run_dir)
            ex.shots_dir = os.path.join(run_dir, "shots")
            log("run dir: %s" % run_dir)
            if not ledger_reconciled:
                _reconcile_ledger(run_dir, log)
                ledger_reconciled = True

            _t_join = time.time()
            _preload.join()
            if "error" in _models:
                raise _models["error"]
            ranker, pol = _models["ranker"], _models["policy"]
            log("model preload: %.1fs still to wait after the game was up" % (time.time() - _t_join))
            entry["model_preload_wait_s"] = round(time.time() - _t_join, 1)
            if not cold:
                if "greedy_catboost" in mix and not ranker.ready:
                    raise P.ModelUnavailable(
                        "campaign %d: main ranker is not ready after preload (models under "
                        "%s)" % (i + 1, common.MODELS))
                if pol.ggnn is not None and not pol.ggnn.ready:
                    raise P.ModelUnavailable(
                        "campaign %d: mapgraph greedy model is not ready after preload (%s)"
                        % (i + 1, common.MODEL_MAPGRAPH_GREEDY))
            entry["policy"] = ("cold_random(forced)" if cold else
                               "trained(%d rows)"
                               % ((ranker.meta or {}).get("rows", 0) if ranker else 0))
            log("policy: %s" % entry["policy"])
            if pol.ggnn is not None:
                entry["greedy_gnn_policy"] = ("trained(%d rows)"
                                              % (pol.ggnn.meta or {}).get("rows", 0))
                log("greedy gnn: %s" % entry["greedy_gnn_policy"])
            _checkpoint_trial(stretch + [entry], generation, report, trained, log)

            def _flush_turn(so_far, _e=entry):
                _e.update(outcome="in_progress", turns_played=len(so_far),
                          actions=sum(r["actions"] for r in so_far),
                          confirmed=sum(r["confirmed"] for r in so_far),
                          ended_by=[r["ended_by"] for r in so_far])
                _write(out_path, dict(report, campaigns=report["campaigns"] + [_e]))

            rows = L.run_campaign(run_dir, ex, pol, turns=this_turns, log=log, cold=cold,
                                  on_turn=_flush_turn, interrupt_strategies=imix)
            entry.update(outcome="completed", turns_played=len(rows),
                         actions=sum(r["actions"] for r in rows),
                         confirmed=sum(r["confirmed"] for r in rows),
                         ended_by=[r["ended_by"] for r in rows],
                         campaign_uuid=_uuid_of(rows))
        except L.CampaignLost as e:
            entry.update(outcome="defeated", error=str(e)[:300], **_played(e))
            log("== campaign %d LOST (faction destroyed): %s" % (i + 1, str(e)[:200]))
        except L.CampaignStagnant as e:
            entry.update(outcome="stagnant", error=str(e)[:300],
                         growth=getattr(e, "verdict", None), **_played(e))
            log("== campaign %d ABANDONED (no growth): %s" % (i + 1, str(e)[:200]))
        except L.GameStuck as e:
            entry.update(outcome="stuck", error=str(e)[:300], **_played(e))
            log("!! campaign %d abandoned (stuck): %s" % (i + 1, str(e)[:200]))
        except (KeyboardInterrupt, SystemExit):
            raise
        except P.ModelUnavailable as e:
            entry.update(outcome="model_unavailable", error=str(e)[:300], **_played(e))
            report["campaigns"].append(entry)
            _write(out_path, report)
            log("!! campaign %d needs a model that is not usable -- crashing the session "
                "instead of playing on without it: %s" % (i + 1, str(e)[:250]))
            raise SystemExit("model unavailable in campaign %d: %s" % (i + 1, str(e)[:250]))
        except UnhandledScreen as e:
            _postmortem(runs_root, dict(entry, outcome="unhandled_screen",
                                        error=str(e)[:400]), ex, log)
            entry.update(outcome="unhandled_screen", error=str(e)[:300], **_played(e))
            log("!! campaign %d abandoned, screen unread: %s" % (i + 1, str(e)[:300]))
        except BaseException as e:
            dead = False
            try:
                dead = bool(ex.defeated_row_seen())
            except Exception:
                dead = False
            if dead:
                entry.update(outcome="defeated", error=repr(e)[:300], **_played(e))
                log("== campaign %d LOST (faction destroyed; surfaced as %s because the bus "
                    "stops answering on the defeat screen)" % (i + 1, type(e).__name__))
            else:
                entry.update(outcome="error", error=repr(e)[:300], **_played(e))
                log("!! campaign %d failed: %s" % (i + 1, repr(e)[:200]))
            if "did not load" in str(e) or "never logged" in str(e):
                launch_failures += 1
                if launch_failures >= MAX_LAUNCH_FAILURES:
                    log("!! %d consecutive launch failures -- the environment is broken, stopping "
                        "the batch instead of proving it %d more times"
                        % (launch_failures, n - (i + 1)))
                    report["campaigns"].append(entry)
                    stretch.append(entry)
                    break
            else:
                launch_failures = 0
        _t = time.time()
        _postmortem(runs_root, entry, ex, log)
        _pm_s = time.time() - _t
        entry["seconds"] = round(time.time() - entry["started"], 1)
        entry["streams"] = {"pending": True}

        def _verify_streams_bg(_e=entry, _rd=entry.get("run_dir"), _t0=time.time()):
            try:
                _e["streams"] = (L.verify_streams(_rd, since=_e.get("stream_watermark") or 0,
                                                  campaign=_e.get("campaign_uuid"))
                                 if _rd else None)
            except Exception as e:
                _e["streams"] = {"error": repr(e)[:160]}
            common.waitlog("verify_streams_bg", time.time() - _t0, True,
                           "campaign %s" % _e.get("index"))

        streams_thread = threading.Thread(target=_verify_streams_bg, name="verify-streams",
                                          daemon=True)
        streams_thread.start()
        report["campaigns"].append(entry)
        stretch.append(entry)
        log("campaign %d -> %s in %.0fs" % (i + 1, entry["outcome"], entry["seconds"]))
        _t = time.time()
        _write(out_path, report)
        _wr_s = time.time() - _t
        _t = time.time()
        _checkpoint_trial(stretch, generation, report, trained, log)
        log("   boundary bookkeeping: postmortem %.1fs, report %.1fs, trial_checkpoint %.1fs, "
            "verify_streams backgrounded" % (_pm_s, _wr_s, time.time() - _t))

    if streams_thread is not None and streams_thread.is_alive():
        streams_thread.join(20.0)
    _flush_generation(stretch, generation, report, trained, log)
    report["seconds"] = round(time.time() - report["started"], 1)
    report["totals"] = _totals(report)
    _write(out_path, report)
    log("\n" + "=" * 78)
    log("SESSION DONE in %.0fs -> %s" % (report["seconds"], out_path))
    for k, v in report["totals"].items():
        log("  %-22s %s" % (k, v))
    return report


METRICS_DIR = common.METRICS_DIR


TARGET_PARTS = ("settlements", "lord_level")


def _uuid_of(rows):
    for r in rows or ():
        u = r.get("campaign_uuid")
        if u:
            return u
    return None


def _db(run_dir):
    return pg.connect(autocommit=True, readonly=True, row_factory=pg.row_factory)


_TURNS_CACHE = {}


def _loop_turns(run_dir):
    path = os.path.join(run_dir, "loop_report.jsonl")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _TURNS_CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    by_uuid = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("kind") != "turn":
                    continue
                u = o.get("campaign_uuid")
                if u:
                    by_uuid.setdefault(u, []).append(o)
    except OSError:
        return {}
    _TURNS_CACHE[path] = (stamp, by_uuid)
    return by_uuid


def _resolve_uuid(con, c):
    if c.get("campaign_uuid"):
        return c["campaign_uuid"]
    plan, t0 = c.get("plan"), float(c.get("started") or 0)
    if not plan or not t0:
        return None
    t1 = t0 + float(c.get("seconds") or 0) + 60
    hits = [cid for cid, first in con.execute(
        "SELECT campaign_id, MIN(ts) FROM decision_points GROUP BY campaign_id"
        " HAVING MIN(ts) BETWEEN %s AND %s", (t0, t1))
        if str(cid).startswith(plan + "_")]
    return hits[0] if len(hits) == 1 else None


GROWTH_BASELINE = "first_decision_snapshot->peak"


def _campaign_growth(con, uuid):
    row = con.execute(CG.TRAJECTORY_SQL_ONE, (uuid,)).fetchone()
    if not row:
        return None
    t = dict(row)
    turn_rows = int(t.get("turn_rows") or 0)
    out = {"campaign_uuid": uuid, "turns_recorded": turn_rows,
           "growth_state": CG.state_of(turn_rows),
           "baseline": GROWTH_BASELINE}
    for part in TARGET_PARTS:
        first, last = t.get("first_" + part), t.get("final_" + part)
        if first is None:
            continue
        out[part + "_start"] = float(first)
        out[part + "_peak"] = float(t.get("peak_" + part) or first)
        out[part + "_final"] = (float(last) if last is not None else None)
        out[part + "_gained"] = CG.delta(first, out[part + "_peak"], turn_rows)
    return out


def _campaign_timing(con, uuid, c, turns_by_uuid):
    n, t0, t1, roundtrip = con.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts),"
        " SUM(COALESCE((timings::jsonb->>'roundtrip_ms')::double precision,0))"
        " FROM decision_points WHERE campaign_id=%s", (uuid,)).fetchone() or (0, None, None, 0)
    act_ms, wasted_ms, acts = con.execute(
        "SELECT SUM(COALESCE((a.timing::jsonb->>'total_ms')::double precision,0)) act_ms,"
        " SUM(COALESCE((a.timing::jsonb->>'confirm_wasted_ms')::double precision,0)) wasted,"
        " COUNT(*) n"
        " FROM action_taken a JOIN decision_points d ON d.decision_id=a.decision_id"
        " WHERE d.campaign_id=%s", (uuid,)).fetchone() or (0, 0, 0)
    turns = turns_by_uuid.get(uuid) or []
    ends = [float(t.get("ts") or 0) for t in turns] + [float(t1 or 0)]
    play = max(0.0, max(ends) - float(t0 or max(ends)))
    wall = float(c.get("seconds") or 0)
    return {"wall_s": round(wall, 1), "play_s": round(play, 1),
            "overhead_s": round(max(0.0, wall - play), 1),
            "turns": len(turns) or int(c.get("turns_played") or 0),
            "decisions": n, "actions": acts or 0,
            "decision_s": round((roundtrip or 0) / 1000.0, 1),
            "action_s": round((act_ms or 0) / 1000.0, 1),
            "wasted_confirm_s": round((wasted_ms or 0) / 1000.0, 1),
            "inter_turn_s": round(sum(float((t.get("inter_turn") or {}).get("waited_s") or 0)
                                      for t in turns), 1)}


def _measure(stretch, log):
    cons, out, unmeasured = {}, [], []
    for c in stretch:
        rd = c.get("run_dir") or journal.RUN_DIR
        if rd not in cons:
            try:
                cons[rd] = (_db(rd), _loop_turns(rd))
            except Exception as e:
                cons[rd] = None
                log("!! %s/%s unreadable -- campaigns there stay UNMEASURED: %s"
                    % (rd, journal.DB_NAME, repr(e)[:90]))
        e = {k: v for k, v in c.items()
             if k not in ("settlements_start", "settlements_peak", "settlements_gained",
                          "lord_level_start", "lord_level_peak", "lord_level_gained")}
        pair = cons.get(rd)
        uuid = _resolve_uuid(pair[0], c) if pair else None
        growth = _campaign_growth(pair[0], uuid) if uuid else None
        if growth:
            e.update(growth, growth_source="decisions_db",
                     timing=_campaign_timing(pair[0], uuid, c, pair[1]))
        else:
            unmeasured.append("%s#%s" % (c.get("plan"), c.get("index")))
        out.append(e)
    if unmeasured:
        log("   %d campaign(s) UNMEASURED (no decision rows to baseline against): %s"
            % (len(unmeasured), ", ".join(unmeasured[:8])))
    for con, _ in (v for v in cons.values() if v):
        try:
            con.close()
        except Exception:
            pass
    return out


def _timing_stats(campaigns):
    rows = [c["timing"] for c in campaigns if c.get("timing")]
    if not rows:
        return None
    n = len(rows)
    turns = sum(int(r.get("turns") or 0) for r in rows)
    tot = lambda k: sum(float(r.get(k) or 0) for r in rows)
    per_turn = lambda k: round(tot(k) / turns, 1) if turns else None
    return {"campaigns_timed": n, "turns": turns,
            "wall_s": round(tot("wall_s"), 1), "play_s": round(tot("play_s"), 1),
            "s_per_campaign": round(tot("wall_s") / n, 1),
            "s_per_turn": per_turn("play_s"),
            "overhead_s_per_campaign": round(tot("overhead_s") / n, 1),
            "inter_turn_s_per_turn": per_turn("inter_turn_s"),
            "action_s_per_turn": per_turn("action_s"),
            "decision_s_per_turn": per_turn("decision_s"),
            "wasted_confirm_s_per_turn": per_turn("wasted_confirm_s"),
            "decisions_per_turn": round(sum(int(r.get("decisions") or 0) for r in rows) / turns, 1)
            if turns else None}


def _turns_of(c):
    return int(c.get("turns_played") or c.get("turns_recorded") or 0)


def _gain_stats(stretch, part):
    measured = [c for c in stretch if c.get(part + "_gained") is not None]
    if not measured:
        return {"total": None, "mean": None, "per_turn": None, "campaigns_measured": 0,
                "campaigns_that_gained": None, "hist": {}}
    vals = [float(c[part + "_gained"]) for c in measured]
    turns = sum(_turns_of(c) for c in measured)
    return {"total": round(sum(vals), 2),
            "mean": round(sum(vals) / len(vals), 3),
            "per_turn": round(sum(vals) / turns, 4) if turns else None,
            "campaigns_measured": len(measured),
            "campaigns_that_gained": sum(1 for v in vals if v >= 1),
            "campaigns_that_lost": sum(1 for v in vals if v <= -1),
            "hist": {str(int(v)): vals.count(v) for v in sorted(set(vals))}}


def _trial_row(stretch, gen_n, report, trained, log):
    if not stretch:
        return None
    stretch = _measure(stretch, log)
    first, last = stretch[0], stretch[-1]
    played = [c for c in stretch
              if (c.get("outcome") or "in_progress") not in ("in_progress", "in_flight")]
    secs = sum(float(c.get("seconds") or 0.0) for c in played)
    turns = sum(_turns_of(c) for c in played)
    outcomes, policies = {}, {}
    for c in stretch:
        oc = c.get("outcome") or "in_progress"
        outcomes[oc] = outcomes.get(oc, 0) + 1
        policies[c.get("policy")] = policies.get(c.get("policy"), 0) + 1
    session = str(report.get("session") or "")
    trial = os.path.basename(session).replace("session_", "").replace(".json", "")
    row = {"trial": "%s-g%d" % (trial or "unstamped", gen_n),
           "ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "session": session or None,
           "generation": gen_n,
           "started": first.get("started"),
           "campaign_index": [first.get("index"), last.get("index")],
           "campaigns": len(played),
           "strategies": (report.get("requested") or {}).get("strategies"),
           "interrupt_strategies": (report.get("requested") or {}).get("interrupt_strategies"),
           "ruleset": (report.get("requested") or {}).get("ruleset"),
           "feature_version": _feature_version(),
           "code_version": (report.get("requested") or {}).get("code_version"),
           "corpus_at_train": report.get("_corpus"),
           "fit": trained,
           "policies": policies,
           "settlements": _gain_stats(played, "settlements"),
           "lord_level": _gain_stats(played, "lord_level"),
           "outcomes": outcomes,
           "turns_total": turns,
           "turns_per_campaign": (round(float(turns) / len(played), 2) if played else None),
           "campaigns_per_hour": (round(len(played) / (secs / 3600.0), 2) if secs else None),
           "campaign_hours": round(secs / 3600.0, 2),
           "timing": _timing_stats(played),
           "baseline": GROWTH_BASELINE,
           "campaign_uuids": [c["campaign_uuid"] for c in stretch if c.get("campaign_uuid")],
           "run_dirs": sorted({c["run_dir"] for c in stretch if c.get("run_dir")})}
    if report.get("stopped_short"):
        row["stopped_short"] = True
    if report.get("running"):
        row["running"] = True
    return row


def _flush_generation(stretch, gen_n, report, trained, log):
    row = _trial_row(stretch, gen_n, report, trained, log)
    if row is None:
        return None
    _write_trial(row, log)
    report.setdefault("trials", []).append(row)
    return row


def _checkpoint_trial(stretch, gen_n, report, trained, log):
    row = _trial_row(stretch, gen_n, dict(report, running=True), trained, log)
    if row is not None:
        _write_trial(row, log, quiet=True)
    return row


def backfill_trials(runs_root=RUNS_ROOT, log=print, recompute=False):
    have = set() if recompute else metrics_db.trial_ids()
    written = 0
    for path, rep in _session_reports(runs_root):
        for gen, stretch in _stretches(rep["campaigns"]):
            stretch = [c for c in stretch if c.get("outcome")]
            if not stretch:
                continue
            extra = {} if rep.get("totals") else {"stopped_short": True}
            shadow, trained = _stretch_context(path, rep, gen, stretch, extra)
            row = _trial_row(stretch, gen, shadow, trained, log)
            if row is None or row["trial"] in have:
                continue
            _write_trial(row, log, quiet=True)
            have.add(row["trial"])
            written += 1
            log("   backfilled %s: %d campaigns" % (row["trial"], row["campaigns"]))
    log("backfill: %d trial(s) written to %s" % (written, metrics_db.DB_PATH))
    return 0


def _reconcile_ledger(run_dir, log):
    have = set()
    try:
        con = pg.connect(autocommit=True, readonly=True)
    except psycopg.OperationalError:
        con = None
    if con is not None:
        try:
            have = {r[0] for r in con.execute("SELECT campaign_key FROM campaigns")}
        except psycopg.errors.UndefinedTable:
            pass
        finally:
            con.close()
    gone = metrics_db.prune_unmatched(have)
    if gone:
        log("ledger reconciled with %s: %d trial(s) moved to trials_archive, "
            "%d campaign(s) in the corpus -- %s"
            % (pg.DB, len(gone), len(have), ", ".join(sorted(gone))[:300]))


def _write_trial(row, log, quiet=False):
    try:
        metrics_db.write_trial(row)
    except Exception as e:
        raise RuntimeError("trial %s NOT written to %s -- refusing to run unrecorded "
                           "experiments: %s"
                           % (row.get("trial"), metrics_db.DB_PATH, repr(e)[:120]))
    if quiet:
        return
    s, l, t = row["settlements"], row["lord_level"], row.get("timing") or {}
    num = lambda v, f="%+.2f": "unmeasured" if v is None else f % v
    log("   trial %s logged: %d campaigns (%s), settlements %s total / %s mean / %s campaigns"
        " grew, lord level %s total, %.2f turns per campaign, %ss per campaign / %ss per turn -> %s"
        % (row["trial"], row["campaigns"],
           ", ".join(sorted(str(p) for p in row["policies"])) or "?",
           num(s["total"]), num(s["mean"], "%.3f"), num(s["campaigns_that_gained"], "%d"),
           num(l["total"]), row["turns_per_campaign"],
           t.get("s_per_campaign", "?"), t.get("s_per_turn", "?"), metrics_db.DB_PATH))


def _stretches(campaigns):
    out, cur, gen = [], [], 0
    for c in campaigns:
        if c.get("retrain") and cur:
            out.append((gen, cur))
            cur = []
        if c.get("retrain"):
            gen += 1
        cur.append(c)
    if cur:
        out.append((gen, cur))
    return out


def _session_reports(runs_root):
    import glob
    for path in sorted(glob.glob(os.path.join(runs_root, "session_*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                rep = json.load(fh)
        except (OSError, ValueError):
            continue
        if rep.get("campaigns"):
            yield path, rep


def _stretch_context(path, rep, gen, stretch, extra):
    trained = stretch[0].get("retrain")
    corpus = ({k: trained.get(k) for k in ("rows", "runs", "campaigns", "n_decisions")}
              if trained and not trained.get("error") else None)
    req = rep.get("requested") or {}
    shadow = dict(extra, session=path, _corpus=corpus, requested=req)
    return shadow, trained


IN_FLIGHT_S = 600
LIVE_LOG_S = 1800
CURRENT_SESSION_LOG = common.CURRENT_SESSION_LOG


def _feature_version():
    import hashlib
    for d in (common.MODEL_GLOBAL,):
        p = os.path.join(d, "meta.json")
        if not os.path.isfile(p):
            continue
        try:
            m = json.load(open(p, encoding="utf-8"))
            cols = sorted((m.get("num") or []) + (m.get("cat") or []))
            if cols:
                return "%s:%d" % (hashlib.sha1(",".join(cols).encode()).hexdigest()[:8],
                                  len(cols))
        except Exception:
            continue
    return "unfitted"


def _played(exc):
    rows = getattr(exc, "rows", None)
    if not rows:
        return {}
    return {"campaign_uuid": _uuid_of(rows),
            "turns_played": len(rows),
            "actions": sum(int(r.get("actions") or 0) for r in rows),
            "confirmed": sum(int(r.get("confirmed") or 0) for r in rows),
            "ended_by": [r.get("ended_by") for r in rows]}


def _throughput(campaigns):
    n = len(campaigns)
    secs = sum(float(c.get("seconds") or 0) for c in campaigns)
    turns = sum(int(c.get("turns_played") or 0) for c in campaigns)
    hours = secs / 3600.0
    return {"campaigns_per_hour": round(n / hours, 2) if hours else None,
            "turns_per_hour": round(turns / hours, 2) if hours else None,
            "turns_per_campaign": round(float(turns) / n, 2) if n else None,
            "campaign_hours": round(hours, 2)}


def _totals(report):
    done = [c for c in report["campaigns"] if c.get("outcome") == "completed"]
    return dict(_throughput(report["campaigns"]), **{"campaigns": len(report["campaigns"]),
            "completed": len(done),
            "stuck": sum(1 for c in report["campaigns"] if c.get("outcome") == "stuck"),
            "defeated": sum(1 for c in report["campaigns"] if c.get("outcome") == "defeated"),
            "stagnant": sum(1 for c in report["campaigns"] if c.get("outcome") == "stagnant"),
            "errored": sum(1 for c in report["campaigns"] if c.get("outcome") == "error"),
            "turns_played": sum(int(c.get("turns_played") or 0) for c in report["campaigns"]),
            "actions": sum(int(c.get("actions") or 0) for c in report["campaigns"]),
            "confirmed": sum(int(c.get("confirmed") or 0) for c in report["campaigns"]),
            "run_dirs": [c.get("run_dir") for c in report["campaigns"] if c.get("run_dir")]})


def _write(path, report):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    except OSError as e:
        sys.stderr.write("session: could not write the report -> %s\n" % repr(e)[:90])


def _parse_strategies(argv, flag="--strategies", normalize=None):
    if flag not in argv:
        return None
    try:
        raw = argv[argv.index(flag) + 1].strip()
    except IndexError:
        raise SystemExit("%s needs a value: name=weight[,name=weight...]" % flag)
    if not raw or raw.startswith("--"):
        raise SystemExit("%s needs a value: name=weight[,name=weight...]" % flag)
    mix = {}
    for part in raw.split(","):
        name, sep, w = part.partition("=")
        name, w = name.strip(), w.strip()
        if not sep or not name or not w:
            raise SystemExit("%s expects name=weight[,name=weight...], got %r" % (flag, part))
        if name in mix:
            raise SystemExit("%s names %r twice" % (flag, name))
        try:
            mix[name] = float(w)
        except ValueError:
            raise SystemExit("%s weight for %r is not a number: %r" % (flag, name, w))
    try:
        return (normalize or P.normalize_strategies)(mix)
    except ValueError as e:
        raise SystemExit(str(e))


def _parse_ruleset(argv):
    if "--ruleset" not in argv:
        return None
    try:
        name = argv[argv.index("--ruleset") + 1].strip()
    except IndexError:
        raise SystemExit("--ruleset needs a name (<TWDATA>/rules\\<name>.json)")
    if not name or name.startswith("--"):
        raise SystemExit("--ruleset needs a name (<TWDATA>/rules\\<name>.json)")
    return name


def _parse_turns(arg):
    s = str(arg)
    if "-" in s:
        lo, hi = s.split("-", 1)
        lo, hi = int(lo), int(hi)
        if lo < 1 or hi < lo:
            raise SystemExit("bad turns range %r -- want MIN-MAX with 1 <= MIN <= MAX" % s)
        return (lo, hi)
    return int(s)


def main():
    common.install_stamped_logs()
    sys.path.insert(0, _HERE)
    if "--backfill-trials" in sys.argv:
        return backfill_trials(recompute="--recompute" in sys.argv)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    turns = _parse_turns(sys.argv[2]) if len(sys.argv) > 2 else 20
    for gone, hint in (("--model", "the exploit scorer is fixed; models are chosen via "
                                   "--strategies"),
                       ("--backend", "backends were removed entirely; every strategy owns "
                                     "and trains its own model"),
                       ("--epsilon", "give --strategies greedy_catboost=1-E,random=E"),
                       ("--retrain", "the cadence is --retrain-every N [--retrain-first]")):
        if gone in sys.argv:
            raise SystemExit("%s was removed: %s" % (gone, hint))
    if any(a.startswith("--nn-") for a in sys.argv):
        raise SystemExit("--nn-* hyperparameter flags were removed; they were parsed into "
                         "nothing (backends.parse_cfg always returned {})")
    if "--factions" not in sys.argv:
        raise SystemExit("usage: session.py <campaigns> <turns|min-max> --factions "
                         "all|<key,key,...> --presave-radius R\n"
                         "                  [--strategies a=x,b=y[,c=z]]\n"
                         "                  [--interrupt-strategies a=x,b=y] [--ruleset <name>]\n"
                         "  --strategies -- per-decision sampling mix over %s\n"
                         "                 (default greedy_catboost=0.8,random=0.2; the weights "
                         "must sum to 1). Trainable arms (%s) require their trained models and "
                         "hard-crash without them; only --cold runs modelless\n"
                         "  --interrupt-strategies -- the mix for blocking screens, over %s "
                         "only (the gnn arms have no interrupt model); defaults to the "
                         "action mix when that mix has no gnn arm, else required\n"
                         "  --ruleset   -- rule file name under <TWDATA>/rules\\<name>.json "
                         "(required when 'ruleset' is in a mix)\n"
                         % (",".join(ST.NAMES), ",".join(ST.TRAINABLE),
                            ",".join(arms.INTERRUPT_NAMES))
                         + "\n"
                         "  --presave-radius R -- required: the session boots starts\n"
                         "                 baked at radius R, and each start carries\n"
                         "                 its own map, so nothing here picks one.\n"
                         "                 bake.py makes them.\n"
                         "  all         -- every start in the baked pool\n"
                         "  <key,...>   -- game faction keys, e.g.\n"
                         "                 wh2_main_hef_nagarythe; every key named\n"
                         "                 must be baked at that radius\n"
                         "  --width N   -- sample only starts whose "
                         "(map, faction) has fewer than N recorded campaigns in the store, "
                         "re-checked per campaign; the session ends when none remain\n"
                         "  --ucb C     -- UCB1 start selector over the trailing %d campaigns "
                         "(campaign_gains view; reward = settlements gained plus lord levels "
                         "gained). Starts with fewer than %d plays come first; then "
                         "blend + c*sqrt(ln(total)/n) + adjust, blend = (mean reward "
                         "+ reward entropy + reward std) / 3 over the start's plays in the "
                         "window, c = K times the same blend taken over every gain in the "
                         "window, adjust = the per-faction delta in rules/%s "
                         "(<TWDATA>/rules overrides)"
                         % (UCB_WINDOW, UCB_MIN_PLAYS, UCB.ADJUST_FILE))
    arg = sys.argv[sys.argv.index("--factions") + 1].strip()
    if arg == "no-cutscene":
        raise SystemExit(
            "--factions no-cutscene classifies frontend starts by their intro movie, and a "
            "session boots a baked save instead -- give --factions all or an explicit list")
    plan = "all" if arg == "all" else [k.strip() for k in arg.split(",") if k.strip()]
    if not plan:
        raise SystemExit("--factions given but empty")
    every = 0
    if "--retrain-every" in sys.argv:
        every = int(sys.argv[sys.argv.index("--retrain-every") + 1])
        if every < 0:
            raise SystemExit("--retrain-every must be >= 0")
    retrain_first = "--retrain-first" in sys.argv
    if retrain_first and not every:
        raise SystemExit("--retrain-first sets the cadence's first window at campaign 1; it "
                         "needs --retrain-every N to have a cadence at all")
    cold = "--cold" in sys.argv
    if "--dev" in sys.argv:
        os.environ["TW_DEV"] = "1"
        sys.stderr.write("session: DEV logging on -- UI scrapes and overlay dumps enabled\n")
    if cold and every:
        raise SystemExit("--cold cannot be combined with --retrain-every: a cold run "
                         "deliberately ignores the fitted model, so retraining it is wasted work")
    strategies = _parse_strategies(sys.argv)
    interrupt_strategies = _parse_strategies(sys.argv, "--interrupt-strategies",
                                             P.normalize_interrupt_strategies)
    ruleset = _parse_ruleset(sys.argv)
    if "--presave-radius" not in sys.argv:
        raise SystemExit("--presave-radius R is required: a session boots a start baked at "
                         "that radius and the map comes with it. bake.py makes them.")
    presave_radius = float(sys.argv[sys.argv.index("--presave-radius") + 1])
    width = 0
    if "--width" in sys.argv:
        width = int(sys.argv[sys.argv.index("--width") + 1])
        if width < 1:
            raise SystemExit("--width must be >= 1")
    ucb = None
    if "--ucb" in sys.argv:
        ucb = float(sys.argv[sys.argv.index("--ucb") + 1])
        if ucb <= 0:
            raise SystemExit("--ucb needs a positive exploration constant")
    r = run_campaigns(n, turns, plan=plan,
                      retrain_every=every, cold=cold, strategies=strategies,
                      interrupt_strategies=interrupt_strategies,
                      ruleset=ruleset, retrain_first=retrain_first,
                      presave_radius=presave_radius, width=width, ucb=ucb)
    return 0 if r["totals"]["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
