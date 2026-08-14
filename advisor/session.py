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
from decisions import dbopen

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)
sys.path.insert(0, common.DECISIONS)

import interrupt_model as IM
import journal
import loop as L
import model as M
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


def _pick_plan(plan, rng):
    if isinstance(plan, (list, tuple, set)):
        choices = sorted(plan)
        if not choices:
            raise RuntimeError("no faction keys to sample from")
        return rng.choice(choices)
    if not str(plan or "").strip():
        raise RuntimeError("no faction key given -- session will not pick one for you")
    return plan


_CAMPAIGN_LABEL = {"wh3_main_combi": "Immortal Empires",
                   "wh3_main_chaos": "Realm of Chaos"}


def normalize_campaigns(spec):
    if isinstance(spec, dict):
        mix = dict(spec)
    else:
        mix = {}
        for part in str(spec or "").split(","):
            part = part.strip()
            if not part:
                continue
            name, sep, weight = part.partition("=")
            name = name.strip()
            if not name:
                raise ValueError("campaign mix %r has an entry with no name" % (spec,))
            try:
                mix[name] = float(weight) if sep else 1.0
            except ValueError:
                raise ValueError("campaign %r has a non-numeric weight %r" % (name, weight))
    if not mix:
        raise ValueError("empty campaign mix")
    for name, w in mix.items():
        if w < 0.0:
            raise ValueError("campaign %r has a negative weight %r" % (name, w))
    total = sum(mix.values())
    if total <= 0.0:
        raise ValueError("campaign mix %r sums to zero" % (spec,))
    return {k: w / total for k, w in mix.items()}


def _pick_campaign(mix, rng):
    names = sorted(mix)
    if len(names) == 1:
        return names[0]
    r, acc = rng.random(), 0.0
    for name in names:
        acc += mix[name]
        if r < acc:
            return name
    return names[-1]


def _plan_for(plan, campaign):
    if not isinstance(plan, dict):
        return plan
    keys = plan.get(campaign)
    if not keys:
        raise RuntimeError(
            "no startable factions for campaign %r -- the roster is keyed by map and this "
            "one is absent, so the session cannot pick a start for it" % campaign)
    return keys


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
    import sqlite3
    out = {}
    try:
        con = dbopen.connect(os.path.join(str(rd), "decisions.sqlite"), timeout=5.0)
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
                    "SELECT turn, settlements, income, power_rank FROM target_rows"
                    " WHERE campaign_id=? ORDER BY turn DESC LIMIT 6", (camp,))][::-1]
            out["recent_battles"] = [
                {"turn": t, "kind": k, "chosen": c, "confirmed": cf}
                for t, k, c, cf in con.execute(
                    "SELECT turn, kind, chosen, confirmed FROM interrupt_decisions"
                    " WHERE campaign_id=? AND kind IN ('pre_battle','battle_results','occupation')"
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
           "policy": entry.get("policy"), "outcome": entry.get("outcome"),
           "error": entry.get("error"), "seconds": round(time.time() - entry.get("started", 0), 1),
           "turns_played": entry.get("turns_played"), "actions": entry.get("actions"),
           "confirmed": entry.get("confirmed"), "ended_by": entry.get("ended_by"),
           "growth": entry.get("growth"), "run_dir": entry.get("run_dir")}
    try:
        from bus import _game_alive
        rec["wh3_running"] = _game_alive()
    except Exception as e:
        rec["wh3_running"] = None
        rec["wh3_probe_error"] = repr(e)[:120]
    if entry.get("outcome") == "defeated" and rec["wh3_running"]:
        rec.update(roots=None, ui_state=None, turn_at_death=None,
                   probes_skipped="defeat_modal_tick_paused")
    else:
        for name, fn in (("roots", lambda: ex.visible_roots()),
                         ("ui_state", lambda: ex.ui_state()),
                         ("turn_at_death", lambda: ex.turn_number())):
            try:
                rec[name] = fn()
            except Exception as e:
                rec[name] = None
                rec[name + "_error"] = repr(e)[:120]
    try:
        rec["screenshot"] = ex.screenshot("end_%s_%s_%d"
                                          % (entry.get("index"), entry.get("outcome"),
                                             int(time.time())))
    except Exception as e:
        rec["screenshot_error"] = repr(e)[:120]
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


def _epsilon_mix(epsilon):
    e = float(epsilon)
    if not 0.0 <= e <= 1.0:
        raise SystemExit("--epsilon must be in [0, 1]")
    return {"greedy_catboost": 1.0 - e, "random": e}


def run_campaigns(n=3, turns=20, plan="nagarythe", campaign="Immortal Empires",
                  log=print, runs_root=RUNS_ROOT, retrain=False, retrain_every=0, seed=None,
                  cold=False, backend=None, backend_cfg=None, epsilon=None, strategies=None,
                  ruleset=None, retrain_first=False, presave_radius=None):
    from bus import Bus
    from executor import Executor

    import backends as B
    import random
    rng = random.Random(seed)
    ex = Executor(Bus())
    backend = backend or B.DEFAULT
    backend_cfg = backend_cfg or {}
    MB = B.resolve(backend)
    if epsilon is not None:
        if strategies is not None:
            raise SystemExit("--epsilon is legacy sugar for --strategies; give one, not both")
        strategies = _epsilon_mix(epsilon)
        log("legacy --epsilon %s mapped to strategies %s"
            % (epsilon, json.dumps(strategies)))
    mix = P.normalize_strategies(strategies)
    cmix = normalize_campaigns(campaign)
    presaves = None
    if presave_radius is not None:
        sys.path.insert(0, common.ROOT)
        import bake_saves
        presaves = bake_saves.list_presaves(radius=presave_radius)
        if not presaves:
            raise SystemExit(
                "--presave-radius %s but nothing is baked at that radius in %s (have %s). "
                "A run that quietly fell back to fresh untrimmed campaigns would keep "
                "growing a corpus that does not match what was asked for."
                % (presave_radius, bake_saves.presave_dir(),
                   bake_saves.presave_radii() or "none"))
        log("presaves: %d baked start(s) at radius %s -- %s"
            % (len(presaves), presave_radius,
               ", ".join(sorted({p["faction"] for p in presaves}))))
    if "ruleset" in mix and not ruleset:
        raise SystemExit("strategy mix includes 'ruleset' but no --ruleset <name> was given")
    if ruleset and "ruleset" not in mix:
        raise SystemExit("--ruleset %r given but 'ruleset' is not in the strategy mix %s"
                         % (ruleset, json.dumps(mix)))
    if cold and "marwil_gnn" in mix:
        raise SystemExit("--cold forces cold_random but the mix includes 'marwil_gnn' -- a cold "
                         "run must not load a trained model; drop 'marwil_gnn' or drop --cold")
    if retrain and not retrain_every:
        raise SystemExit("--retrain no longer fits before campaign 1: the start retrain was "
                         "removed because it refits a model that is already on disk under "
                         "%s and delays every relaunch and every babysitter restart. Pass "
                         "--retrain-every N for the cadence, or drop --retrain." % common.MODELS)
    ruleset_meta = None
    if ruleset:
        import ruleset as RS
        rs = RS.RuleSet.load(ruleset)
        ruleset_meta = {"name": rs.name, "sha256": rs.sha256}
        log("ruleset: %s (%d rules, sha256 %s)" % (rs.name, len(rs.rules), rs.sha256[:12]))
    log("strategy mix: %s" % json.dumps(mix))
    log("campaign mix: %s" % json.dumps(cmix))
    log("model backend: %s -- %s%s"
        % (backend, B.label(backend),
           ("  cfg=%s" % json.dumps(backend_cfg)) if backend_cfg else ""))
    report = {"started": time.time(), "requested": {"campaigns": n, "turns": turns, "plan": plan,
                                                    "backend": backend,
                                                    "backend_cfg": backend_cfg,
                                                    "epsilon": epsilon,
                                                    "strategies": mix,
                                                    "ruleset": ruleset_meta},
              "campaigns": []}
    if isinstance(plan, dict):
        for _c in sorted(plan):
            log("sampling the start on %s from %d: %s"
                % (_c, len(plan[_c]), ", ".join(sorted(plan[_c]))))
    elif isinstance(plan, (list, tuple, set)):
        log("sampling the start per campaign from: %s" % ", ".join(sorted(plan)))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(runs_root, "session_%s.json" % stamp)
    report["session"] = out_path
    report["trials"] = []

    hard_restart_next, prev_outcome = True, "session start"
    launch_failures = 0
    stretch, generation, trained = [], 0, None

    for i in range(n):
        this_presave = None
        if presaves is not None:
            this_presave = rng.choice(sorted(presaves, key=lambda p: p["file"]))
            this_campaign = _CAMPAIGN_LABEL.get(this_presave["campaign_map"],
                                                this_presave["campaign_map"])
            this_plan = this_presave["faction"]
        else:
            this_campaign = _pick_campaign(cmix, rng)
            this_plan = _pick_plan(_plan_for(plan, this_campaign), rng)
        this_turns = rng.randint(turns[0], turns[1]) if isinstance(turns, (tuple, list)) \
            else int(turns)
        log("\n" + "=" * 78)
        log("CAMPAIGN %d/%d  (up to %d turns, map=%s, faction=%s%s)"
            % (i + 1, n, this_turns, this_campaign, this_plan,
               ", presave r%g" % this_presave["radius"] if this_presave else ""))
        log("=" * 78)
        entry = {"index": i + 1, "started": time.time(), "plan": this_plan,
                 "campaign": this_campaign,
                 "presave": this_presave["file"] if this_presave else None,
                 "presave_radius": this_presave["radius"] if this_presave else None,
                 "max_turns": this_turns}
        try:
            ex.mark_campaign_start()
        except Exception as e:
            log("   could not mark the bus offset for this campaign: %s" % repr(e)[:100])
        if hard_restart_next:
            if prev_outcome == "defeated" and ex.leave_campaign_via_click():
                log("previous campaign ended defeated -- returned to the main menu by "
                    "clicking the panel button; the game is kept")
                hard_restart_next = False
            else:
                log("previous campaign ended %s -- killing the game now" % prev_outcome)
                ex.kill_game()
        log("   log rotation: %s" % _rotate_logs(log))
        entry["bus_files"] = _bus_sizes()
        log("   bus: %s" % ", ".join("%s %.1fMB" % (k, v)
                                     for k, v in sorted(entry["bus_files"].items())))
        try:
            ex.rotate_out_log()
        except Exception as e:
            log("   out-log rotation failed: %s" % repr(e)[:90])
        if retrain_every and (i or retrain_first) and i % retrain_every == 0:
            _flush_generation(stretch, backend, backend_cfg, generation, report, trained, log)
            stretch = []
            generation += 1
            log("taking the game down for retraining -- trainers get the whole box")
            ex.kill_game()
            if ex.wait_game_down(log=log):
                log("   game down, gpu is free")
            else:
                log("!! GAME STILL RUNNING after two kills -- training anyway, but it is "
                    "contending with the game for the gpu")
            hard_restart_next, prev_outcome = True, "retrain window"
            try:
                t0 = time.time()
                rep = MB.train(**backend_cfg) if backend_cfg else MB.train()
                entry["retrain"] = dict(rep, seconds=round(time.time() - t0, 1),
                                        backend=backend)
                trained = entry["retrain"]
                report["_corpus"] = {"rows": rep.get("rows"), "runs": rep.get("runs"),
                                     "campaigns": rep.get("campaigns"),
                                     "n_decisions": rep.get("n_decisions")}
                log("retrained before run %d: %s" % (i + 1, json.dumps(rep)[:220]))
                irep = IM.train()
                entry["retrain_interrupt"] = irep
                log("   interrupt model: %s" % json.dumps(irep)[:200])
                if "marwil_gnn" in mix:
                    try:
                        if common.ROOT not in sys.path:
                            sys.path.insert(0, common.ROOT)
                        from advisor.mapgraph import train as GT
                        t1 = time.time()
                        grep = GT.train(log=log)
                        grep["seconds"] = round(time.time() - t1, 1)
                    except Exception as ge:
                        grep = {"trained": False, "error": repr(ge)[:250]}
                        log("!! gnn retrain before run %d failed (continuing on the previous "
                            "gnn): %s" % (i + 1, repr(ge)[:180]))
                    entry["retrain_gnn"] = grep
                    entry["retrain"]["gnn"] = grep
                    log("   gnn model: %s" % json.dumps(grep, default=str)[:220])
                    try:
                        from advisor.mapgraph import interrupt_train as GIT
                        t1 = time.time()
                        girep = GIT.train(log=log)
                        girep["seconds"] = round(time.time() - t1, 1)
                    except Exception as ge:
                        girep = {"trained": False, "error": repr(ge)[:250]}
                        log("!! gnn interrupt retrain before run %d failed (continuing on "
                            "the previous one): %s" % (i + 1, repr(ge)[:180]))
                    entry["retrain_gnn_interrupt"] = girep
                    entry["retrain"]["gnn_interrupt"] = girep
                    log("   gnn interrupt model: %s" % json.dumps(girep, default=str)[:220])
                if not rep.get("trained"):
                    log("!! retrain %d DID NOT FIT (rows=%s need=%s) -- this campaign will NOT be "
                        "played by a freshly trained model" % (i + 1, rep.get("rows"), rep.get("need")))
            except Exception as e:
                entry["retrain"] = {"error": repr(e)[:250]}
                trained = entry["retrain"]
                if "marwil_gnn" in mix and "retrain_gnn" not in entry:
                    entry["retrain_gnn"] = {"trained": False,
                                            "error": "skipped: upstream retrain raised"}
                if "marwil_gnn" in mix and "retrain_gnn_interrupt" not in entry:
                    entry["retrain_gnn_interrupt"] = {
                        "trained": False, "error": "skipped: upstream retrain raised"}
                log("!! retrain before run %d failed (continuing on the previous model): %s"
                    % (i + 1, repr(e)[:180]))
            entry.setdefault("outcome", "in_progress")
            _write(out_path, dict(report, campaigns=report["campaigns"] + [entry]))
        _models = {}

        def _preload_models():
            try:
                r = MB.Ranker(B.NO_MODEL_DIR) if cold else MB.Ranker()
                _models["ranker"] = r
                _models["policy"] = P.Policy(r, strategies=mix, ruleset=ruleset)
            except BaseException as e:
                _models["error"] = e

        _preload = threading.Thread(target=_preload_models, name="model-preload", daemon=True)
        _preload.start()
        try:
            if this_presave is not None:
                sys.path.insert(0, common.LAUNCHER)
                import bake_saves
                import bus_launcher
                bake_saves.restore_presave(this_presave, log=log)
                ex.kill_game()
                ex.wait_game_down(timeout=60, log=log)
                bl = bus_launcher.BusLauncher()
                state = bl.load_save(this_presave["file"])
                ex.bus = bl.bus or ex.bus
            elif hard_restart_next:
                log("previous campaign ended %s -- killing the game rather than asking it to quit"
                    % prev_outcome)
                state = ex.hard_restart(plan=this_plan, campaign=this_campaign)
            else:
                state = ex.ensure_campaign(plan=this_plan, campaign=this_campaign, fresh=True)
            entry["start_state"] = state
            run_dir = journal.current_run_dir(timeout=180.0)
            entry["run_dir"] = run_dir
            ex.shots_dir = os.path.join(run_dir, "shots")
            log("run dir: %s" % run_dir)

            _t_join = time.time()
            _preload.join()
            if "error" in _models:
                raise _models["error"]
            ranker, pol = _models["ranker"], _models["policy"]
            log("model preload: %.1fs still to wait after the game was up" % (time.time() - _t_join))
            entry["model_preload_wait_s"] = round(time.time() - _t_join, 1)
            entry["backend"] = backend
            entry["policy"] = ("cold_random(forced)" if cold else
                               "trained(%d rows)" % (ranker.meta or {}).get("rows", 0)
                               if ranker.ready else "cold_random")
            log("policy: %s" % entry["policy"])
            if pol.gnn is not None:
                entry["gnn_policy"] = ("trained(%d rows)" % (pol.gnn.meta or {}).get("rows", 0)
                                       if pol.gnn.ready else "unready->gnn_random_fallback")
                log("gnn: %s" % entry["gnn_policy"])
            _checkpoint_trial(stretch + [entry], backend, backend_cfg, generation, report,
                              trained, log)

            def _flush_turn(so_far, _e=entry):
                _e.update(outcome="in_progress", turns_played=len(so_far),
                          actions=sum(r["actions"] for r in so_far),
                          confirmed=sum(r["confirmed"] for r in so_far),
                          ended_by=[r["ended_by"] for r in so_far])
                _write(out_path, dict(report, campaigns=report["campaigns"] + [_e]))

            rows = L.run_campaign(run_dir, ex, pol, turns=this_turns, log=log, cold=cold,
                                  on_turn=_flush_turn)
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
        except UnhandledScreen as e:
            _postmortem(runs_root, dict(entry, outcome="unhandled_screen",
                                        error=str(e)[:400]), ex, log)
            log("!! UNHANDLED SCREEN on campaign %d -- killing the session rather than "
                "recording another campaign of unusable data:\n%s" % (i + 1, str(e)[:600]))
            raise
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
            try:
                entry["screenshot"] = ex.screenshot("session_fail_%d_%d" % (i + 1, int(time.time())))
            except Exception:
                pass
        _postmortem(runs_root, entry, ex, log)
        prev_outcome = entry.get("outcome")
        hard_restart_next = prev_outcome in ("stuck", "error", "defeated")
        entry["seconds"] = round(time.time() - entry["started"], 1)
        try:
            entry["streams"] = L.verify_streams(entry["run_dir"]) if entry.get("run_dir") else None
        except Exception as e:
            entry["streams"] = {"error": repr(e)[:160]}
        report["campaigns"].append(entry)
        stretch.append(entry)
        log("campaign %d -> %s in %.0fs" % (i + 1, entry["outcome"], entry["seconds"]))
        _write(out_path, report)
        _checkpoint_trial(stretch, backend, backend_cfg, generation, report, trained, log)

    _flush_generation(stretch, backend, backend_cfg, generation, report, trained, log)
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
        u = (r.get("target") or {}).get("campaign_uuid")
        if u:
            return u
    return None


def _db(run_dir):
    import sqlite3
    con = dbopen.connect(os.path.join(run_dir or journal.RUN_DIR, journal.DB_NAME))
    con.row_factory = sqlite3.Row
    return con


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
                u = (o.get("target") or {}).get("campaign_uuid")
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
        " HAVING MIN(ts) BETWEEN ? AND ?", (t0, t1))
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
        "SELECT COUNT(*), MIN(ts), MAX(ts), SUM(COALESCE(json_extract(timings,'$.roundtrip_ms'),0))"
        " FROM decision_points WHERE campaign_id=?", (uuid,)).fetchone() or (0, None, None, 0)
    act_ms, wasted_ms, acts = con.execute(
        "SELECT SUM(COALESCE(json_extract(a.timing,'$.total_ms'),0)),"
        " SUM(COALESCE(json_extract(a.timing,'$.confirm_wasted_ms'),0)), COUNT(*)"
        " FROM action_taken a JOIN decision_points d ON d.decision_id=a.decision_id"
        " WHERE d.campaign_id=?", (uuid,)).fetchone() or (0, 0, 0)
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


def _trial_row(stretch, backend, backend_cfg, gen_n, report, trained, log):
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
           "backend": backend, "backend_cfg": backend_cfg,
           "epsilon": (report.get("requested") or {}).get("epsilon"),
           "strategies": (report.get("requested") or {}).get("strategies"),
           "ruleset": (report.get("requested") or {}).get("ruleset"),
           "feature_version": _feature_version(),
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


def _flush_generation(stretch, backend, backend_cfg, gen_n, report, trained, log):
    row = _trial_row(stretch, backend, backend_cfg, gen_n, report, trained, log)
    if row is None:
        return None
    _write_trial(row, log)
    report.setdefault("trials", []).append(row)
    return row


def _checkpoint_trial(stretch, backend, backend_cfg, gen_n, report, trained, log):
    row = _trial_row(stretch, backend, backend_cfg, gen_n,
                     dict(report, running=True), trained, log)
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
            backend, cfg, shadow, trained = _stretch_context(path, rep, gen, stretch, extra)
            row = _trial_row(stretch, backend, cfg, gen, shadow, trained, log)
            if row is None or row["trial"] in have:
                continue
            _write_trial(row, log, quiet=True)
            have.add(row["trial"])
            written += 1
            log("   backfilled %s: %d campaigns" % (row["trial"], row["campaigns"]))
    log("backfill: %d trial(s) written to %s" % (written, metrics_db.DB_PATH))
    return 0


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
    return (req.get("backend") or stretch[0].get("backend"), req.get("backend_cfg") or {},
            shadow, trained)


IN_FLIGHT_S = 600
LIVE_LOG_S = 1800
CURRENT_SESSION_LOG = common.CURRENT_SESSION_LOG


def _live_log():
    try:
        path = open(CURRENT_SESSION_LOG, encoding="utf-8-sig").read().strip()
        if time.time() - os.path.getmtime(path) > LIVE_LOG_S:
            return None
    except OSError:
        return None
    return path


def _read_report(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _live_session(runs_root):
    import glob
    import re
    path = _live_log()
    if not path:
        return None
    name = os.path.basename(path)
    stamp = re.search(r"(\d{8}_\d{6})", name)
    if not stamp:
        return None
    stamp = stamp.group(1)
    try:
        started = time.mktime(time.strptime(stamp, "%Y%m%d_%H%M%S"))
    except ValueError:
        return None
    backend = re.search(r"session_(?:cold_)?([A-Za-z0-9]+)_\d+x", name)
    report = os.path.join(runs_root, "session_%s.json" % stamp)
    rep = _read_report(report)
    if rep is None:
        best = None
        for p in glob.glob(os.path.join(runs_root, "session_*.json")):
            r = _read_report(p)
            st = (r or {}).get("started")
            if st is None:
                continue
            if -5.0 <= (float(st) - started) <= 120.0 and (best is None or st > best[2]):
                best = (p, r, float(st))
        if best is not None:
            report, rep = best[0], best[1]
    if rep is None:
        gen = 0
        try:
            gen = 1 if "retrained before run 1:" in open(path, encoding="utf-8",
                                                         errors="replace").read() else 0
        except OSError:
            pass
        rep = {"campaigns": [], "requested": {"backend": backend.group(1) if backend else None},
               "_first_gen": gen}
    return report, rep, started


def _in_flight(stretch, since, log):
    rd = journal.RUN_DIR
    try:
        con = _db(rd)
    except Exception as e:
        log("   in-flight campaign not read: %s" % repr(e)[:90])
        return None
    try:
        done = {c.get("campaign_uuid") for c in stretch if c.get("campaign_uuid")}
        done |= {u for u in (_resolve_uuid(con, c) for c in stretch) if u}
        newest = con.execute("SELECT campaign_id, MIN(ts), MAX(ts) FROM decision_points"
                             " GROUP BY campaign_id ORDER BY MIN(decision_id) DESC"
                             " LIMIT 1").fetchone()
    finally:
        con.close()
    if not newest or newest[0] in done:
        return None
    uuid, first, last = newest
    if float(first or 0) < since or time.time() - float(last or 0) > IN_FLIGHT_S:
        return None
    prev = stretch[-1] if stretch else {}
    return {"index": (prev.get("index") or len(stretch)) + 1,
            "plan": str(uuid).rsplit("_", 1)[0], "campaign_uuid": uuid, "started": first,
            "seconds": round(time.time() - float(first or 0), 1), "outcome": "in_flight",
            "policy": prev.get("policy"), "backend": prev.get("backend"), "run_dir": rd}


def live_trial(runs_root=RUNS_ROOT, log=lambda m: None, with_in_flight=True):
    found = _live_session(runs_root)
    if not found:
        return None
    path, rep, started = found
    if rep.get("totals"):
        return None
    stretches = _stretches(rep["campaigns"])
    gen, stretch = stretches[-1] if stretches else (rep.get("_first_gen", 0), [])
    if with_in_flight:
        flying = _in_flight(stretch, started, log)
        if flying:
            stretch = stretch + [flying]
    if not stretch:
        return None
    backend, cfg, shadow, trained = _stretch_context(path, rep, gen, stretch, {"running": True})
    return _trial_row(stretch, backend, cfg, gen, shadow, trained, log)


def _fit_pair(report, prefix=""):
    fit = (report or {}).get("fit") or {}
    out = {}
    for slot in ("e1", "e2"):
        f = fit.get(prefix + slot) or {}
        out[slot] = {"val_rmse": f.get("val_rmse"), "best_iteration": f.get("best_iteration"),
                     "iterations": f.get("iterations"), "val_rows": f.get("val_rows"),
                     "train_rows": f.get("train_rows"),
                     "early_stopping": f.get("early_stopping"),
                     "reason": f.get("reason")}
    return out


def train_events(runs_root=RUNS_ROOT):
    ledger = {r.get("trial"): r for r in metrics_db.trials()}
    live = live_trial()
    if live:
        ledger[live["trial"]] = live
    out = []
    for path, rep in _session_reports(runs_root):
        stamp = os.path.basename(path).replace("session_", "").replace(".json", "")
        gen = 0
        for c in rep["campaigns"]:
            rt = c.get("retrain")
            if not rt:
                continue
            gen += 1
            irt = c.get("retrain_interrupt") or {}
            trial = ledger.get("%s-g%d" % (stamp, gen)) or {}
            local = rt.get("local") or {}
            out.append({
                "when": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(float(c.get("started") or 0))),
                "ts": float(c.get("started") or 0),
                "trial": "%s-g%d" % (stamp, gen), "session": path,
                "backend": rt.get("backend") or c.get("backend"),
                "trained": rt.get("trained"), "error": rt.get("error"),
                "seconds": rt.get("seconds"),
                "params": rt.get("params"),
                "rows": rt.get("rows"), "campaigns": rt.get("campaigns"),
                "n_decisions": rt.get("n_decisions"), "runs": rt.get("runs"),
                "skipped_unlabelled": rt.get("skipped_unlabelled"),
                "mae_in_sample": rt.get("mae_in_sample"),
                "global": _fit_pair(rt),
                "local": dict(_fit_pair(local, "local_"), rows=local.get("rows"),
                              sd_local=local.get("sd_local"), sd_global=local.get("sd_global"),
                              trained=local.get("trained")),
                "interrupt": dict(_fit_pair(irt), rows=irt.get("rows"),
                                  mae_in_sample=irt.get("mae_in_sample"),
                                  trained=irt.get("trained"),
                                  screens=irt.get("screens")),
                "gnn": c.get("retrain_gnn"),
                "played": {"campaigns": trial.get("campaigns"),
                           "sett_per_campaign": (trial.get("settlements") or {}).get("mean"),
                           "sett_total": (trial.get("settlements") or {}).get("total"),
                           "grew": (trial.get("settlements") or {}).get("campaigns_that_gained"),
                           "measured": (trial.get("settlements") or {}).get("campaigns_measured"),
                           "lord_per_campaign": (trial.get("lord_level") or {}).get("mean"),
                           "turns_per_campaign": trial.get("turns_per_campaign"),
                           "running": trial.get("running")}})
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out


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


def _parse_strategies(argv):
    if "--strategies" not in argv:
        return None
    try:
        raw = argv[argv.index("--strategies") + 1].strip()
    except IndexError:
        raise SystemExit("--strategies needs a value: name=weight[,name=weight...]")
    if not raw or raw.startswith("--"):
        raise SystemExit("--strategies needs a value: name=weight[,name=weight...]")
    mix = {}
    for part in raw.split(","):
        name, sep, w = part.partition("=")
        name, w = name.strip(), w.strip()
        if not sep or not name or not w:
            raise SystemExit("--strategies expects name=weight[,name=weight...], got %r" % part)
        if name in mix:
            raise SystemExit("--strategies names %r twice" % name)
        try:
            mix[name] = float(w)
        except ValueError:
            raise SystemExit("--strategies weight for %r is not a number: %r" % (name, w))
    try:
        return P.normalize_strategies(mix)
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
    sys.path.insert(0, _HERE)
    if "--backfill-trials" in sys.argv:
        return backfill_trials(recompute="--recompute" in sys.argv)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    turns = _parse_turns(sys.argv[2]) if len(sys.argv) > 2 else 20
    import backends as B
    if "--factions" not in sys.argv:
        raise SystemExit("usage: session.py <campaigns> <turns|min-max> --factions "
                         "all|<key,key,...> [--retrain] [--model %s] [--nn-KEY VALUE ...]\n"
                         "                  [--strategies a=x,b=y[,c=z]] [--ruleset <name>]\n"
                         "  --model     -- which ranker to play on (default %s):\n%s\n"
                         "  --nn-KEY V  -- backend hyperparameter, e.g. --nn-bottleneck 64\n"
                         "  --strategies -- per-decision sampling mix over %s\n"
                         "                 (default greedy_catboost=0.8,random=0.2; weights "
                         "normalized)\n"
                         "  --ruleset   -- rule file name under <TWDATA>/rules\\<name>.json "
                         "(required when 'ruleset' is in the mix)\n"
                         "  'marwil_gnn' -- MARWIL/AWR on the graph encoder "
                         "(<TWDATA>/models\\mapgraph); untrained -> gnn_random_fallback\n"
                         "  --epsilon E -- legacy sugar for greedy_catboost=1-E,random=E\n"
                         % ("|".join(B.names()), B.DEFAULT,
                            "\n".join("                 %-10s %s" % (k, B.label(k))
                                      for k in B.names()),
                            ",".join(ST.NAMES))
                         + "\n"
                         "  all         -- sample from every playable start ON THE MAP --campaign\n"
                         "                 selects, read from launcher/startable_factions.json\n"
                         "                 (harvested from the game's own frontend, keyed by map:\n"
                         "                 Immortal Empires 104 starts, Realm of Chaos 25). A map\n"
                         "                 with no harvest raises rather than borrowing another's\n"
                         "  no-cutscene -- every start EXCEPT the 14 that open on an intro\n"
                         "                 movie (launcher/cutscene_starts.py, measured from\n"
                         "                 the launcher's own cinematic-key counts). Those 14\n"
                         "                 take 68-119s to reach a playable HUD against 17-30s\n"
                         "                 for the rest.\n"
                         "  <key,...>   -- game faction keys, e.g. wh2_main_hef_nagarythe")
    arg = sys.argv[sys.argv.index("--factions") + 1].strip()
    _spec = (sys.argv[sys.argv.index("--campaign") + 1].strip()
             if "--campaign" in sys.argv else "Immortal Empires")
    _names = sorted(normalize_campaigns(_spec))
    keys = {}
    for _name in _names:
        if arg == "all":
            sys.path.insert(0, common.LAUNCHER)
            import bus_launcher
            keys[_name] = bus_launcher.BusLauncher().startable_factions(_name)
            print("--factions all on %s: %d startable starts" % (_name, len(keys[_name])))
        elif arg == "no-cutscene":
            sys.path.insert(0, common.LAUNCHER)
            import bus_launcher
            import cutscene_starts
            _map = bus_launcher.BusLauncher.CAMPAIGN_KEYS.get(_name, _name)
            r = cutscene_starts.classify(campaign_map=_map)
            keys[_name] = [x["faction"] for x in r["clean"]]
            if not keys[_name]:
                raise SystemExit(
                    "--factions no-cutscene found no classified starts on %s. It is derived "
                    "from the launcher's own logs under %s; with none on disk there is "
                    "nothing to filter, and silently falling back to every start would hide "
                    "that." % (_map, common.TWDATA))
            print("--factions no-cutscene on %s: %d clean starts, excluding %d with a "
                  "cutscene and %d unclassified"
                  % (_map, len(keys[_name]), len(r["cutscene"]), len(r["unknown"])))
        else:
            explicit = [k.strip() for k in arg.split(",") if k.strip()]
            if len(_names) > 1:
                sys.path.insert(0, common.LAUNCHER)
                import bus_launcher
                roster = set(bus_launcher.BusLauncher().startable_factions(_name))
                absent = [k for k in explicit if k not in roster]
                if absent:
                    raise SystemExit(
                        "--factions names %s, which are not startable on %s. A campaign mix "
                        "draws that map for some campaigns, and those launches would fail "
                        "one by one." % (", ".join(absent), _name))
            keys[_name] = explicit
    if not keys or not all(keys.values()):
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
    if cold and ("--retrain" in sys.argv or every):
        raise SystemExit("--cold cannot be combined with --retrain/--retrain-every: a cold run "
                         "deliberately ignores the fitted model, so retraining it is wasted work")
    backend = (sys.argv[sys.argv.index("--model") + 1].strip()
               if "--model" in sys.argv else B.DEFAULT)
    B.resolve(backend)
    backend_cfg = B.parse_cfg(sys.argv)
    epsilon = None
    if "--epsilon" in sys.argv:
        epsilon = float(sys.argv[sys.argv.index("--epsilon") + 1])
        if not 0.0 <= epsilon <= 1.0:
            raise SystemExit("--epsilon must be in [0, 1]")
    strategies = _parse_strategies(sys.argv)
    if epsilon is not None and strategies is not None:
        raise SystemExit("--epsilon is legacy sugar for --strategies; give one, not both")
    ruleset = _parse_ruleset(sys.argv)
    presave_radius = None
    if "--presave-radius" in sys.argv:
        presave_radius = float(sys.argv[sys.argv.index("--presave-radius") + 1])
    campaign = "Immortal Empires"
    if "--campaign" in sys.argv:
        i = sys.argv.index("--campaign")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--campaign needs a value: a friendly name "
                             "('Immortal Empires', 'Realm of Chaos', 'Prologue') or a raw "
                             "campaign key such as wh3_main_chaos")
        campaign = sys.argv[i + 1]
    r = run_campaigns(n, turns, plan=keys, retrain="--retrain" in sys.argv,
                      retrain_every=every, cold=cold, backend=backend,
                      backend_cfg=backend_cfg, epsilon=epsilon, strategies=strategies,
                      ruleset=ruleset, campaign=campaign, retrain_first=retrain_first,
                      presave_radius=presave_radius)
    return 0 if r["totals"]["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
