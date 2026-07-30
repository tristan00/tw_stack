r"""session.py -- run N campaigns for up to M turns each, unattended.

    python advisor_v7/session.py 3 20            # 3 campaigns, up to 20 turns each
                                                 # DEFAULT PLAN: nagarythe (High Elves / Alith Anar)
                                                 # --plan <name> selects another bus_launcher.PLANS
                                                 # entry; omit it to stay on nagarythe.

This is the data-collection driver. With no trained model the policy is cold-start random, which is
the point: a few real campaigns of random-but-VERIFIED actions is the seed dataset the first model
is fitted on.

WHAT IT OWNS
  * starting each campaign (the advisor asks the launcher; no human step)
  * following the recorder: every campaign makes the manager open a FRESH run dir, so the run dir
    is re-resolved after every restart rather than assumed
  * surviving a bad campaign: if one gets stuck or throws, it is abandoned WITH a screenshot and the
    next campaign starts. One wedged campaign must never cost the whole session.
  * a per-campaign and whole-session report, written to <runs_root>/session_<stamp>.json

WHAT IT DOES NOT DO
  It does not judge the campaigns. Every decision point, confirmed or refused, is already in the
  store; whether a run was "good" is the trainer's problem, not the driver's.
"""
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

import journal                                             # noqa: E402
import loop as L                                           # noqa: E402
import model as M                                          # noqa: E402
import policy as P                                         # noqa: E402

RUNS_ROOT = "D:/twdata/runs/human"


def run_campaigns(n=3, turns=20, plan="nagarythe", campaign="Immortal Empires",
                  log=print, runs_root=RUNS_ROOT):
    """Play `n` campaigns of up to `turns` turns each. Returns the session report."""
    from bus import Bus
    from executor import Executor

    ex = Executor(Bus())
    report = {"started": time.time(), "requested": {"campaigns": n, "turns": turns, "plan": plan},
              "campaigns": []}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(runs_root, "session_%s.json" % stamp)

    for i in range(n):
        log("\n" + "=" * 78)
        log("CAMPAIGN %d/%d  (up to %d turns, plan=%s)" % (i + 1, n, turns, plan))
        log("=" * 78)
        entry = {"index": i + 1, "started": time.time(), "plan": plan}
        try:
            # A fresh campaign for every iteration -- including the first, so campaign 1 is not
            # silently whatever happened to be loaded when the session started.
            state = ex.ensure_campaign(plan=plan, campaign=campaign, fresh=True)
            entry["start_state"] = state
            run_dir = journal.current_run_dir(timeout=180.0)   # the recorder rotated; follow it
            entry["run_dir"] = run_dir
            ex.shots_dir = os.path.join(run_dir, "shots")
            log("run dir: %s" % run_dir)

            pol = P.Policy(M.Ranker())                        # fresh caps/blacklists per campaign
            rows = L.run_campaign(run_dir, ex, pol, turns=turns, log=log)
            entry.update(outcome="completed", turns_played=len(rows),
                         actions=sum(r["actions"] for r in rows),
                         confirmed=sum(r["confirmed"] for r in rows),
                         ended_by=[r["ended_by"] for r in rows])
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
    plan = "nagarythe"
    if "--plan" in sys.argv:
        plan = sys.argv[sys.argv.index("--plan") + 1]
    r = run_campaigns(n, turns, plan=plan)
    return 0 if r["totals"]["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
