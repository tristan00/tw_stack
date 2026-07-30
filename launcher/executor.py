r"""executor.py -- the LAUNCHER's interface to the advisor loop.

The loop never holds a bus: it asks the recorder for data and calls THIS to make things happen.
Everything here is execution-side -- issuing the action, proving it landed, clearing whatever the
game puts on screen afterwards, and capturing evidence when a run has to be abandoned.

    execute(pick)     -> the ActionRecord (cco_actions.execute_confirmed: snapshot -> gates ->
                         execute -> poll-confirm). `counted` is the ONLY field meaning "it happened".
    resolve_battles() -> autoresolve + post-battle options, if a battle screen is up
    screenshot(name)  -> PNG evidence (used when the watchdog declares the run stuck)
    clear_popups()    -> drain event popups between actions

The registry it dispatches through spans all three execution layers -- cco commands, cm script-API
orders, and the few UI-component clicks that neither layer exposes -- but the loop does not care
which: it passes a pick and gets back proof or a refusal reason.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import cco_actions as CCO                                   # noqa: E402  engine + registry
import cm_actions                                           # noqa: E402  registers cm executors
import click_actions                                        # noqa: E402  registers click executors
import interrupts                                           # noqa: E402
import nav                                                  # noqa: E402

PS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ps")

# the context kind an action's entity is addressed by -- province offers act on a region key, lord
# offers on a character cqi, campaign offers on the faction
_ENTITY_KIND = {"lord": "lord", "province": "settlement", "campaign": "campaign"}


class Executor:
    def __init__(self, bus, shots_dir=None):
        self.bus = bus
        self.shots_dir = shots_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "logs", "v7_shots")
        self.executed = 0
        self.counted = 0

    # ------------------------------------------------------------------ actions
    def execute(self, pick):
        """Run one advisor pick through the confirmed-action engine. Never raises: a failure comes
        back as an ActionRecord with `counted` False and a refusal class, because "the action did
        not happen" is training data, not an exception."""
        ctx = {"context_kind": _ENTITY_KIND.get(pick.get("context_kind"), pick.get("context_kind")),
               "entity_id": str(pick.get("context_id"))}
        run = {"action_type": pick.get("action_type"), "key": pick.get("key"),
               "params": pick.get("params") or {}, "policy": pick.get("policy")}
        try:
            rec = CCO.execute_confirmed(self.bus, ctx, run)
        except Exception as e:
            rec = {"action_type": run["action_type"], "key": run["key"], "executed": False,
                   "confirmed": False, "counted": False, "refusal": "executor_raised",
                   "confirm": {"error": repr(e)[:200]}, "policy": run["policy"]}
        rec["context_kind"] = pick.get("context_kind")
        rec["context_id"] = str(pick.get("context_id"))
        self.executed += 1
        self.counted += 1 if rec.get("counted") else 0
        return rec

    # ------------------------------------------------------------------ screen hygiene
    def resolve_interrupts(self):
        """Clear anything the game put on screen that the advisor did not ask for -- a battle, an
        incoming diplomacy proposal, event popups. Returns the steps taken ([] = clean screen)."""
        return interrupts.resolve(self.bus)

    def settle_between_turns(self, timeout=420.0, poll=4.0, turn_before=None):
        """Ride out the AI turns after end_turn.

        This is where defensive battles and incoming diplomacy proposals appear, and where the old
        code would have stalled: end_turn's own confirm only watches the turn number, which does not
        move while a battle popup is waiting for us. So poll, clear whatever shows up, and stop as
        soon as the turn actually advances.

        Returns {"turn": <new turn or None>, "steps": [...], "waited_s": float}. A None turn means
        the turn never advanced inside `timeout` -- reported, never silently accepted.
        """
        t0 = time.time()
        steps = []
        while time.time() - t0 < timeout:
            s = self.resolve_interrupts()
            if s:
                steps.extend(s)
            t = self.turn_number()
            if t is not None and (turn_before is None or t > turn_before):
                return {"turn": t, "steps": steps, "waited_s": round(time.time() - t0, 1)}
            time.sleep(poll)
        sys.stderr.write("executor: turn did not advance within %ss (steps=%s)\n" % (timeout, steps))
        return {"turn": None, "steps": steps, "waited_s": round(time.time() - t0, 1)}

    def turn_number(self):
        """Execution-side turn read (the loop gets its turn from the recorder; this one exists so
        settle_between_turns can tell 'the AI is still playing' from 'we are stuck')."""
        try:
            r = self.bus.send("eval", "return cm:model():turn_number()", timeout=10.0) or {}
        except Exception:
            return None
        if r.get("error"):
            return None
        try:
            return int(float(r.get("result")))
        except (TypeError, ValueError):
            return None

    def clear_popups(self):
        try:
            return len(nav.close_popups(self.bus))
        except Exception as e:
            sys.stderr.write("executor: close_popups -> %s\n" % repr(e)[:90])
            return 0

    def visible_roots(self):
        try:
            return nav.visible_roots(self.bus)
        except Exception:
            return []

    # ------------------------------------------------------------------ game lifecycle
    # The advisor drives these; nobody sits in the loop. A stuck run does not need a human to go
    # get a fresh campaign, and a cold start does not need one to launch the game.
    def start_game(self, plan="nagarythe", campaign="Immortal Empires", boot_timeout=240):
        """Cold start: spawn WH3 and drive the frontend to a playable campaign."""
        import bus_launcher
        bl = bus_launcher.BusLauncher()
        started = bl.launch(plan_name=plan, campaign=campaign, boot_timeout=boot_timeout)
        self.bus = bl.bus or self.bus
        return started

    def at_main_menu(self):
        return "main" in self.visible_roots()

    def ensure_campaign(self, plan="nagarythe", campaign="Immortal Empires", fresh=False):
        """Guarantee a playable campaign, from whatever state the game is in.

        in a campaign + fresh=False -> nothing to do
        in a campaign + fresh=True  -> quit to menu and start a new one
        at the main menu            -> start one
        Lets the advisor own the whole flow: no human decides when a campaign is needed.
        """
        if self.at_main_menu():
            import bus_launcher
            bl = bus_launcher.BusLauncher()
            bl.bus = self.bus
            started = bl.drive_frontend(plan_name=plan, campaign=campaign)
            self.bus = bl.bus or self.bus
            return started
        if self.turn_number() is None:
            raise RuntimeError("game is neither at the main menu nor in a readable campaign "
                               "(roots=%s)" % self.visible_roots())
        return self.new_campaign(plan, campaign) if fresh else {"already_in_campaign": True,
                                                                "turn": self.turn_number()}

    def new_campaign(self, plan="nagarythe", campaign="Immortal Empires"):
        """Abandon the current campaign and start a FRESH one WITHOUT respawning the process.

        This is the recovery path for a stuck run and the way the loop gets clean state on demand.
        Quitting to the menu rather than killing the game keeps it to seconds instead of the
        several minutes a cold boot costs.
        """
        import bus_launcher
        bl = bus_launcher.BusLauncher()
        bl.bus = self.bus
        started = bl.restart_campaign(plan_name=plan, campaign=campaign)
        self.bus = bl.bus or self.bus
        return started

    # ------------------------------------------------------------------ evidence
    def screenshot(self, name):
        """Capture the game window to <shots_dir>/<name>.png. Returns the path, or None.

        Deliberately a bare subprocess call on the capture helper rather than the v6 ScreenBridge:
        this is called when the run is ALREADY suspected stuck, so it must not depend on the bus or
        on any state the freeze might have poisoned.
        """
        path = os.path.join(self.shots_dir, "%s.png" % name)
        try:
            os.makedirs(self.shots_dir, exist_ok=True)
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", os.path.join(PS_DIR, "capture.ps1"), path],
                           capture_output=True, text=True, timeout=45)
        except Exception as e:
            sys.stderr.write("executor: screenshot failed -> %s\n" % repr(e)[:120])
            return None
        return path if os.path.exists(path) else None


if __name__ == "__main__":
    from bus import Bus
    import json
    ex = Executor(Bus())
    print("registry:", sorted(CCO.REGISTRY))
    print("roots:", ex.visible_roots())
    print("turn:", ex.turn_number())
    print("interrupts pending:", sorted(interrupts.pending(ex.bus)))
    print("interrupt steps:", ex.resolve_interrupts())
    print("noop:", json.dumps({k: v for k, v in ex.execute(
        {"context_kind": "campaign", "context_id": "x", "action_type": "noop",
         "key": "noop"}).items() if k in ("executed", "confirmed", "counted", "refusal")}))
