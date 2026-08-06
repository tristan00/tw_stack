from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import interrupt_model as IM
import journal
import loop as L
import model as M
import policy as P
from interrupts import UnhandledScreen

RUNS_ROOT = "D:/twdata/runs/human"


MAX_LAUNCH_FAILURES = 3
LOG_ARCHIVE = r"D:\twdata\archive\script_logs"
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


BUS_FILES = (r"D:\totalwar_runner\data\commands.txt",
             r"D:\totalwar_runner\data\twcontrol.jsonl")


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
        con = sqlite3.connect("file:%s/decisions.sqlite?mode=ro" % str(rd).replace("\\", "/"),
                              uri=True, timeout=5.0)
        camp = con.execute("SELECT campaign_id FROM decision_points"
                           " ORDER BY decision_id DESC LIMIT 1").fetchone()
        camp = camp[0] if camp else None
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
        verdict = ("abandoned_on_the_growth_bar at turn %s: %s"
                   % (g.get("turn"),
                      "; ".join("%s %s->%s" % (m.get("label"), m.get("then"), m.get("now"))
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
    path = os.path.join(runs_root, "postmortems.jsonl")
    try:
        os.makedirs(runs_root, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        log("   post-mortem -> %s (roots=%s, shot=%s)"
            % (path, ",".join(str(r) for r in (rec.get("roots") or []))[:120],
               rec.get("screenshot")))
    except Exception as e:
        log("   !! post-mortem NOT written: %s" % repr(e)[:160])


NO_MODEL_DIR = r"D:\twdata\models\__cold_start__"


def run_campaigns(n=3, turns=20, plan="nagarythe", campaign="Immortal Empires",
                  log=print, runs_root=RUNS_ROOT, retrain=False, retrain_every=0, seed=None,
                  cold=False, backend=None, backend_cfg=None):
    from bus import Bus
    from executor import Executor

    import backends as B
    import random
    rng = random.Random(seed)
    ex = Executor(Bus())
    backend = backend or B.DEFAULT
    backend_cfg = backend_cfg or {}
    MB = B.resolve(backend)
    log("model backend: %s -- %s%s"
        % (backend, B.label(backend),
           ("  cfg=%s" % json.dumps(backend_cfg)) if backend_cfg else ""))
    report = {"started": time.time(), "requested": {"campaigns": n, "turns": turns, "plan": plan,
                                                    "backend": backend,
                                                    "backend_cfg": backend_cfg},
              "campaigns": []}
    if isinstance(plan, (list, tuple, set)):
        log("sampling the start per campaign from: %s" % ", ".join(sorted(plan)))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(runs_root, "session_%s.json" % stamp)
    report["session"] = out_path
    report["trials"] = []

    hard_restart_next, prev_outcome = True, "session start"
    launch_failures = 0
    stretch, generation, trained = [], 0, None

    for i in range(n):
        this_plan = _pick_plan(plan, rng)
        this_turns = rng.randint(turns[0], turns[1]) if isinstance(turns, (tuple, list)) \
            else int(turns)
        log("\n" + "=" * 78)
        log("CAMPAIGN %d/%d  (up to %d turns, faction=%s)" % (i + 1, n, this_turns, this_plan))
        log("=" * 78)
        entry = {"index": i + 1, "started": time.time(), "plan": this_plan,
                 "max_turns": this_turns}
        if hard_restart_next:
            log("previous campaign ended %s -- killing the game now" % prev_outcome)
            ex.kill_game()
        log("   log rotation: %s" % _rotate_logs(log))
        entry["bus_files"] = _bus_sizes()
        log("   bus: %s" % ", ".join("%s %.1fMB" % (k, v)
                                     for k, v in sorted(entry["bus_files"].items())))
        if (retrain and i == 0) or (retrain_every and i and i % retrain_every == 0):
            _flush_generation(stretch, backend, backend_cfg, generation, report, trained, log)
            stretch = []
            generation += 1
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
                if not rep.get("trained"):
                    log("!! retrain %d DID NOT FIT (rows=%s need=%s) -- this campaign will NOT be "
                        "played by a freshly trained model" % (i + 1, rep.get("rows"), rep.get("need")))
            except Exception as e:
                entry["retrain"] = {"error": repr(e)[:250]}
                trained = entry["retrain"]
                log("!! retrain before run %d failed (continuing on the previous model): %s"
                    % (i + 1, repr(e)[:180]))
        try:
            if hard_restart_next:
                log("previous campaign ended %s -- killing the game rather than asking it to quit"
                    % prev_outcome)
                state = ex.hard_restart(plan=this_plan, campaign=campaign)
            else:
                state = ex.ensure_campaign(plan=this_plan, campaign=campaign, fresh=True)
            entry["start_state"] = state
            run_dir = journal.current_run_dir(timeout=180.0)
            entry["run_dir"] = run_dir
            ex.shots_dir = os.path.join(run_dir, "shots")
            log("run dir: %s" % run_dir)

            ranker = MB.Ranker(B.NO_MODEL_DIR) if cold else MB.Ranker()
            pol = P.Policy(ranker)
            entry["backend"] = backend
            entry["policy"] = ("cold_random(forced)" if cold else
                               "trained(%d rows)" % (ranker.meta or {}).get("rows", 0)
                               if ranker.ready else "cold_random")
            log("policy: %s" % entry["policy"])
            rows = L.run_campaign(run_dir, ex, pol, turns=this_turns, log=log, cold=cold)
            entry.update(outcome="completed", turns_played=len(rows),
                         actions=sum(r["actions"] for r in rows),
                         confirmed=sum(r["confirmed"] for r in rows),
                         ended_by=[r["ended_by"] for r in rows], **_growth_stats(rows))
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

    _flush_generation(stretch, backend, backend_cfg, generation, report, trained, log)
    report["seconds"] = round(time.time() - report["started"], 1)
    report["totals"] = _totals(report)
    _write(out_path, report)
    log("\n" + "=" * 78)
    log("SESSION DONE in %.0fs -> %s" % (report["seconds"], out_path))
    for k, v in report["totals"].items():
        log("  %-22s %s" % (k, v))
    return report


METRICS_DIR = r"D:\twdata\metrics"
EXPERIMENTS = os.path.join(METRICS_DIR, "experiments.jsonl")


def _growth_stats(rows):
    out = {}
    for part in ("settlements", "lord_level"):
        vals = [(r.get("state") or {}).get(part) for r in rows]
        vals = [float(v) for v in vals if v is not None]
        if not vals:
            continue
        out[part + "_start"] = vals[0]
        out[part + "_peak"] = max(vals)
        out[part + "_gained"] = max(vals) - vals[0]
    return out


def _gain_stats(stretch, part):
    measured = [c for c in stretch if c.get(part + "_gained") is not None]
    if not measured:
        return {"total": None, "mean": None, "per_turn": None, "campaigns_measured": 0,
                "campaigns_that_gained": None, "hist": {}}
    vals = [float(c[part + "_gained"]) for c in measured]
    turns = sum(int(c.get("turns_played") or 0) for c in measured)
    return {"total": round(sum(vals), 2),
            "mean": round(sum(vals) / len(vals), 3),
            "per_turn": round(sum(vals) / turns, 4) if turns else None,
            "campaigns_measured": len(measured),
            "campaigns_that_gained": sum(1 for v in vals if v >= 1),
            "hist": {str(int(v)): vals.count(v) for v in sorted(set(vals))}}


def _flush_generation(stretch, backend, backend_cfg, gen_n, report, trained, log):
    if not stretch:
        return None
    first, last = stretch[0], stretch[-1]
    secs = sum(float(c.get("seconds") or 0.0) for c in stretch)
    outcomes, policies = {}, {}
    for c in stretch:
        outcomes[c.get("outcome")] = outcomes.get(c.get("outcome"), 0) + 1
        policies[c.get("policy")] = policies.get(c.get("policy"), 0) + 1
    session = str(report.get("session") or "")
    trial = os.path.basename(session).replace("session_", "").replace(".json", "")
    row = {"trial": "%s-g%d" % (trial or "unstamped", gen_n),
           "ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "session": session or None,
           "generation": gen_n,
           "started": first.get("started"),
           "campaign_index": [first.get("index"), last.get("index")],
           "campaigns": len(stretch),
           "backend": backend, "backend_cfg": backend_cfg,
           "feature_version": _feature_version(),
           "corpus_at_train": report.get("_corpus"),
           "fit": trained,
           "policies": policies,
           "settlements": _gain_stats(stretch, "settlements"),
           "lord_level": _gain_stats(stretch, "lord_level"),
           "outcomes": outcomes,
           "turns_total": sum(int(c.get("turns_played") or 0) for c in stretch),
           "turns_per_campaign": round(float(sum(int(c.get("turns_played") or 0)
                                                 for c in stretch)) / len(stretch), 2),
           "campaigns_per_hour": (round(len(stretch) / (secs / 3600.0), 2) if secs else None),
           "campaign_hours": round(secs / 3600.0, 2),
           "run_dirs": sorted({c["run_dir"] for c in stretch if c.get("run_dir")})}
    recovered = sum(1 for c in stretch if c.get("growth_source") == "run_dir")
    if recovered:
        row["growth_recovered"] = recovered
    if report.get("backfilled"):
        row["backfilled"] = True
        row["backfilled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if report.get("stopped_short"):
        row["stopped_short"] = True
    _write_trial(row, log)
    report.setdefault("trials", []).append(row)
    return row


def _write_trial(row, log):
    try:
        os.makedirs(METRICS_DIR, exist_ok=True)
        with open(EXPERIMENTS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError as e:
        raise RuntimeError("trial %s NOT written to %s -- refusing to run unrecorded "
                           "experiments: %s" % (row.get("trial"), EXPERIMENTS, repr(e)[:120]))
    s, l = row["settlements"], row["lord_level"]
    num = lambda v, f="%+.2f": "unmeasured" if v is None else f % v
    log("   trial %s logged: %d campaigns (%s), settlements %s total / %s mean / %s campaigns"
        " grew, lord level %s total, %.2f turns per campaign -> %s"
        % (row["trial"], row["campaigns"],
           ", ".join(sorted(str(p) for p in row["policies"])) or "?",
           num(s["total"]), num(s["mean"], "%.3f"), num(s["campaigns_that_gained"], "%d"),
           num(l["total"]), row["turns_per_campaign"], EXPERIMENTS))


def _campaign_blocks(run_dir):
    rows = []
    path = os.path.join(run_dir, "loop_report.jsonl")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("kind") == "turn":
                    rows.append(o)
    except OSError:
        return []
    blocks, cur, prev_id, prev_turn = [], [], None, None
    for r in rows:
        tg, st = r.get("target") or {}, r.get("state") or {}
        ident = tg.get("campaign_uuid") or tg.get("campaign_id") or st.get("faction")
        turn = float(r.get("turn") or 0)
        if cur and ((ident is not None and prev_id is not None and ident != prev_id)
                    or turn <= (prev_turn or 0)):
            blocks.append(cur)
            cur = []
        cur.append(r)
        prev_id = ident if ident is not None else prev_id
        prev_turn = turn
    if cur:
        blocks.append(cur)
    return [{"faction": ((b[0].get("target") or {}).get("campaign_id")
                         or (b[0].get("state") or {}).get("faction")),
             "rows": b} for b in blocks]


def _recover_growth(campaigns, log):
    missing = [c for c in campaigns
               if c.get("settlements_gained") is None and c.get("run_dir")]
    by_dir, filled = {}, 0
    for c in missing:
        by_dir.setdefault(c["run_dir"], None)
    for rd in by_dir:
        by_dir[rd] = _campaign_blocks(rd)
    for rd, blocks in by_dir.items():
        pending = [c for c in missing if c.get("run_dir") == rd]
        cursor = 0
        for c in pending:
            while cursor < len(blocks) and blocks[cursor]["faction"] != c.get("plan"):
                cursor += 1
            if cursor >= len(blocks):
                break
            stats = _growth_stats(blocks[cursor]["rows"])
            if stats:
                c.update(stats)
                c.setdefault("turns_played", len(blocks[cursor]["rows"]))
                c["growth_source"] = "run_dir"
                filled += 1
            cursor += 1
    if filled:
        log("   recovered growth for %d campaigns from their run dirs" % filled)
    return filled


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


LIVE_SESSION_S = 1800


def _still_running(path, rep):
    if rep.get("totals"):
        return False
    try:
        return (time.time() - os.path.getmtime(path)) < LIVE_SESSION_S
    except OSError:
        return False


def backfill(runs_root=RUNS_ROOT, log=print, include_live=False):
    import glob
    seen = set()
    try:
        with open(EXPERIMENTS, encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add(json.loads(line).get("trial"))
                except ValueError:
                    continue
    except OSError:
        pass
    written, skipped = [], 0
    for path in sorted(glob.glob(os.path.join(runs_root, "session_*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                rep = json.load(fh)
        except (OSError, ValueError) as e:
            log("!! %s unreadable, no trials recovered: %s" % (os.path.basename(path), repr(e)[:90]))
            continue
        campaigns = rep.get("campaigns") or []
        if not campaigns:
            continue
        _recover_growth(campaigns, log)
        req = rep.get("requested") or {}
        stretches = _stretches(campaigns)
        cut_short = None
        if _still_running(path, rep) and stretches:
            if include_live:
                cut_short = stretches[-1][0]
                log("   %s looks live -- recording generation %d anyway as stopped_short"
                    % (os.path.basename(path), cut_short))
            else:
                gen, stretch = stretches.pop()
                log("   %s is LIVE -- generation %d (%d campaigns so far) left for the session to "
                    "record when it finishes" % (os.path.basename(path), gen, len(stretch)))
        for gen, stretch in stretches:
            stamp = os.path.basename(path).replace("session_", "").replace(".json", "")
            if "%s-g%d" % (stamp, gen) in seen:
                skipped += 1
                continue
            trained = stretch[0].get("retrain")
            corpus = ({k: trained.get(k) for k in ("rows", "runs", "campaigns", "n_decisions")}
                      if trained and not trained.get("error") else None)
            shadow = {"session": path, "_corpus": corpus, "backfilled": True,
                      "stopped_short": gen == cut_short}
            row = _flush_generation(stretch, req.get("backend") or stretch[0].get("backend"),
                                    req.get("backend_cfg") or {}, gen, shadow, trained, log)
            if row:
                written.append(row)
    log("backfill: %d trials written, %d already in the ledger -> %s"
        % (len(written), skipped, EXPERIMENTS))
    return written


def _feature_version():
    import hashlib
    for d in (r"D:\twdata\models\nn_global", r"D:\twdata\models\global"):
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
    return dict(_growth_stats(rows),
                turns_played=len(rows),
                actions=sum(int(r.get("actions") or 0) for r in rows),
                confirmed=sum(int(r.get("confirmed") or 0) for r in rows),
                ended_by=[r.get("ended_by") for r in rows])


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
    if "--backfill" in sys.argv:
        return 0 if backfill(include_live="--include-live" in sys.argv) else 1
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    turns = _parse_turns(sys.argv[2]) if len(sys.argv) > 2 else 20
    import backends as B
    if "--factions" not in sys.argv:
        raise SystemExit("usage: session.py <campaigns> <turns|min-max> --factions "
                         "all|<key,key,...> [--retrain] [--model %s] [--nn-KEY VALUE ...]\n"
                         "       session.py --backfill   -- write the trial rows for sessions "
                         "that finished before the ledger recorded any\n"
                         "  --model     -- which ranker to play on (default %s):\n%s\n"
                         "  --nn-KEY V  -- backend hyperparameter, e.g. --nn-bottleneck 64\n"
                         % ("|".join(B.names()), B.DEFAULT,
                            "\n".join("                 %-10s %s" % (k, B.label(k))
                                      for k in B.names()))
                         + "\n"
                         "  all         -- sample from EVERY playable start in the installed game,\n"
                         "                 read from launcher/startable_factions.json (harvested\n"
                         "                 from the game's own frontend: 104 factions, 24 cultures)\n"
                         "  <key,...>   -- game faction keys, e.g. wh2_main_hef_nagarythe")
    arg = sys.argv[sys.argv.index("--factions") + 1].strip()
    if arg == "all":
        sys.path.insert(0, r"D:\tw_stack\launcher")
        import bus_launcher
        keys = bus_launcher.BusLauncher().startable_factions()
    else:
        keys = [k.strip() for k in arg.split(",") if k.strip()]
    if not keys:
        raise SystemExit("--factions given but empty")
    every = 0
    if "--retrain-every" in sys.argv:
        every = int(sys.argv[sys.argv.index("--retrain-every") + 1])
        if every < 0:
            raise SystemExit("--retrain-every must be >= 0")
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
    r = run_campaigns(n, turns, plan=keys, retrain="--retrain" in sys.argv,
                      retrain_every=every, cold=cold, backend=backend,
                      backend_cfg=backend_cfg)
    return 0 if r["totals"]["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
