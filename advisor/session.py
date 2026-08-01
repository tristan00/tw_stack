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

import interrupt_model as IM                               # noqa: E402
import journal                                             # noqa: E402
import loop as L                                           # noqa: E402
import model as M                                          # noqa: E402
import policy as P                                         # noqa: E402

RUNS_ROOT = "D:/twdata/runs/human"


def _pick_plan(plan, rng):
    """`plan` is a faction KEY, or a list of faction keys to sample one from per campaign."""
    if isinstance(plan, (list, tuple, set)):
        choices = sorted(plan)
        if not choices:
            raise RuntimeError("no faction keys to sample from")
        return rng.choice(choices)
    if not str(plan or "").strip():
        raise RuntimeError("no faction key given -- session will not pick one for you")
    return plan


def _tail_jsonl(path, n):
    """Last n parsed rows of a .jsonl, best effort. Never raises."""
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
    """The case file for adjudicating a campaign ending without having watched the game:
    the state trajectory, the recent battles, the terminal signal, and a plausibility
    verdict (advisory only -- it labels nothing, it argues)."""
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
    # most campaigns live their whole life on 1 settlement -- only CHANGE is evidence,
    # never the absolute level
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
    if outcome == "defeated":
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
    """Append one record per campaign to postmortems.jsonl, on any outcome. Never raises."""
    rec = {"ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
           "campaign": entry.get("index"), "faction": entry.get("plan"),
           "policy": entry.get("policy"), "outcome": entry.get("outcome"),
           "error": entry.get("error"), "seconds": round(time.time() - entry.get("started", 0), 1),
           "turns_played": entry.get("turns_played"), "actions": entry.get("actions"),
           "confirmed": entry.get("confirmed"), "ended_by": entry.get("ended_by"),
           "run_dir": entry.get("run_dir")}
    try:
        rec["wh3_running"] = bool(ex.turn_number() is not None)
    except Exception as e:
        rec["wh3_running"] = None
        rec["wh3_probe_error"] = repr(e)[:120]
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
        # run_campaigns sets these only on the success path
        if rec.get("turns_played") is None:
            turn_rows = [r for r in _tail_jsonl(os.path.join(rd, "loop_report.jsonl"), 500)
                         if r.get("kind") == "turn"]
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


def run_campaigns(n=3, turns=20, plan="nagarythe", campaign="Immortal Empires",
                  log=print, runs_root=RUNS_ROOT, retrain=False, seed=None):
    """Play `n` campaigns of up to `turns` turns each. Returns the session report.

    retrain=True refits the model from everything collected so far before each campaign.
    """
    from bus import Bus
    from executor import Executor

    import random
    rng = random.Random(seed)
    ex = Executor(Bus())
    report = {"started": time.time(), "requested": {"campaigns": n, "turns": turns, "plan": plan},
              "campaigns": []}
    if isinstance(plan, (list, tuple, set)):
        log("sampling the start per campaign from: %s" % ", ".join(sorted(plan)))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(runs_root, "session_%s.json" % stamp)

    hard_restart_next, prev_outcome = True, "session start"

    for i in range(n):
        this_plan = _pick_plan(plan, rng)
        log("\n" + "=" * 78)
        log("CAMPAIGN %d/%d  (up to %d turns, faction=%s)" % (i + 1, n, turns, this_plan))
        log("=" * 78)
        entry = {"index": i + 1, "started": time.time(), "plan": this_plan}
        # kill before retraining, not after
        if hard_restart_next:
            log("previous campaign ended %s -- killing the game now, before retraining"
                % prev_outcome)
            ex.kill_game()
        if retrain:
            try:
                t0 = time.time()
                rep = M.train()
                entry["retrain"] = dict(rep, seconds=round(time.time() - t0, 1))
                log("retrained before run %d: %s" % (i + 1, json.dumps(rep)[:220]))
                irep = IM.train()
                entry["retrain_interrupt"] = irep
                log("   interrupt model: %s" % json.dumps(irep)[:200])
                if not rep.get("trained"):
                    log("!! retrain %d DID NOT FIT (rows=%s need=%s) -- this campaign will NOT be "
                        "played by a freshly trained model" % (i + 1, rep.get("rows"), rep.get("need")))
            except Exception as e:
                entry["retrain"] = {"error": repr(e)[:250]}
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
            run_dir = journal.current_run_dir(timeout=180.0)   # the recorder rotated; follow it
            entry["run_dir"] = run_dir
            ex.shots_dir = os.path.join(run_dir, "shots")
            log("run dir: %s" % run_dir)

            ranker = M.Ranker()                               # re-read from disk: picks up a retrain
            pol = P.Policy(ranker)                            # fresh caps/blacklists per campaign
            entry["policy"] = ("trained(%d rows)" % (ranker.meta or {}).get("rows", 0)
                               if ranker.ready else "cold_random")
            log("policy: %s" % entry["policy"])
            rows = L.run_campaign(run_dir, ex, pol, turns=turns, log=log)
            entry.update(outcome="completed", turns_played=len(rows),
                         actions=sum(r["actions"] for r in rows),
                         confirmed=sum(r["confirmed"] for r in rows),
                         ended_by=[r["ended_by"] for r in rows])
        except L.CampaignLost as e:
            entry.update(outcome="defeated", error=str(e)[:300])
            log("== campaign %d LOST (faction destroyed): %s" % (i + 1, str(e)[:200]))
        except L.GameStuck as e:
            entry.update(outcome="stuck", error=str(e)[:300])
            log("!! campaign %d abandoned (stuck): %s" % (i + 1, str(e)[:200]))
        except Exception as e:                                # never let one campaign kill the run
            entry.update(outcome="error", error=repr(e)[:300])
            log("!! campaign %d failed: %s" % (i + 1, repr(e)[:200]))
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
        log("campaign %d -> %s in %.0fs" % (i + 1, entry["outcome"], entry["seconds"]))
        _write(out_path, report)

    report["seconds"] = round(time.time() - report["started"], 1)
    report["totals"] = _totals(report)
    _write(out_path, report)
    log("\n" + "=" * 78)
    log("SESSION DONE in %.0fs -> %s" % (report["seconds"], out_path))
    for k, v in report["totals"].items():
        log("  %-22s %s" % (k, v))
    return report


def _totals(report):
    done = [c for c in report["campaigns"] if c.get("outcome") == "completed"]
    return {"campaigns": len(report["campaigns"]),
            "completed": len(done),
            "stuck": sum(1 for c in report["campaigns"] if c.get("outcome") == "stuck"),
            "defeated": sum(1 for c in report["campaigns"] if c.get("outcome") == "defeated"),
            "errored": sum(1 for c in report["campaigns"] if c.get("outcome") == "error"),
            "turns_played": sum(c.get("turns_played", 0) for c in report["campaigns"]),
            "actions": sum(c.get("actions", 0) for c in report["campaigns"]),
            "confirmed": sum(c.get("confirmed", 0) for c in report["campaigns"]),
            "run_dirs": [c.get("run_dir") for c in report["campaigns"] if c.get("run_dir")]}


def _write(path, report):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    except OSError as e:
        sys.stderr.write("session: could not write the report -> %s\n" % repr(e)[:90])


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    if "--factions" not in sys.argv:
        raise SystemExit("usage: session.py <campaigns> <turns> --factions all|<key,key,...> "
                         "[--retrain]\n"
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
    r = run_campaigns(n, turns, plan=keys, retrain="--retrain" in sys.argv)
    return 0 if r["totals"]["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
