from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, r"D:\tw_stack\bus")
sys.path.insert(0, r"D:\tw_stack\launcher")

import cco_actions as CCO                                   # noqa: E402
import cm_actions                                           # noqa: E402  registers cm executors
import click_actions                                        # noqa: E402  registers click executors
import diplomacy_actions                                    # noqa: E402  registers `diplomacy`
import interrupts                                           # noqa: E402
import nav                                                  # noqa: E402
import trace                                                # noqa: E402

PS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ps")

_ENTITY_KIND = {"lord": "lord", "province": "settlement", "campaign": "campaign"}


class Executor:
    def __init__(self, bus, shots_dir=None):
        self.bus = bus
        self.shots_dir = shots_dir or r"D:\twdata\logs\launcher\v7_shots"
        self.executed = 0
        self.counted = 0

    def execute(self, pick):
        """Run one advisor pick through the confirmed-action engine. Never raises; a failure comes
        back as an ActionRecord with `counted` False and a refusal class."""
        ctx = {"context_kind": _ENTITY_KIND.get(pick.get("context_kind"), pick.get("context_kind")),
               "entity_id": str(pick.get("context_id"))}
        run = {"action_type": pick.get("action_type"), "key": pick.get("key"),
               "params": pick.get("params") or {}, "policy": pick.get("policy")}
        trace.launcher("execute_start", action_type=run["action_type"], key=run["key"],
                       context_kind=pick.get("context_kind"), context_id=str(pick.get("context_id")),
                       params=run["params"], policy=run["policy"])
        try:
            rec = CCO.execute_confirmed(self.bus, ctx, run)
        except Exception as e:
            rec = {"action_type": run["action_type"], "key": run["key"], "executed": False,
                   "confirmed": False, "counted": False, "refusal": "executor_raised",
                   "confirm": {"error": repr(e)[:200]}, "policy": run["policy"]}
        rec["context_kind"] = pick.get("context_kind")
        rec["context_id"] = str(pick.get("context_id"))
        trace.launcher("execute_done", action_type=rec.get("action_type"), key=rec.get("key"),
                       executed=rec.get("executed"), confirmed=rec.get("confirmed"),
                       counted=rec.get("counted"), refusal=rec.get("refusal"),
                       gate=rec.get("gate"), confirm=rec.get("confirm"),
                       before=rec.get("before"), after=rec.get("after"))
        self.executed += 1
        self.counted += 1 if rec.get("counted") else 0
        return rec

    def resolve_interrupts(self):
        """Clear anything on screen the advisor did not ask for. Returns the steps taken."""
        return interrupts.resolve(self.bus)

    def defeated_probe(self):
        """Engine-side: is the player faction dead? True/False, None when the probe failed."""
        return interrupts.defeated_probe(self.bus)

    def settle_between_turns(self, timeout=420.0, poll=4.0, turn_before=None, abort=None):
        """Ride out the AI turns after end_turn, clearing interrupts.

        Returns {"turn": <new turn or None>, "steps": [...], "waited_s": float}.
        """
        t0 = time.time()
        steps = []

        def _aborted():
            """Checked between every slow call, not once per iteration.

            resolve_interrupts and turn_number each make bus calls that burn their full timeout
            when the bus is dead, so one iteration can run for tens of seconds. Testing abort only
            at the top let a fired watchdog sit unnoticed for 215s.
            """
            return abort is not None and abort()

        def _bail():
            sys.stderr.write("executor: settle aborted -- the run was declared stuck\n")
            return {"turn": None, "steps": steps, "waited_s": round(time.time() - t0, 1),
                    "aborted": True}

        while time.time() - t0 < timeout:
            if _aborted():
                return _bail()
            s = self.resolve_interrupts()
            if s:
                steps.extend(s)
            if _aborted():
                return _bail()
            t = self.turn_number()
            if t is not None and (turn_before is None or t > turn_before):
                return {"turn": t, "steps": steps, "waited_s": round(time.time() - t0, 1)}
            if interrupts.defeated_probe(self.bus) is True:
                sys.stderr.write("executor: settle -- the faction is DEAD; this turn will never advance\n")
                return {"turn": None, "steps": steps + ["defeated"],
                        "waited_s": round(time.time() - t0, 1), "defeated": True}
            if _aborted():
                return _bail()
            time.sleep(poll)
        sys.stderr.write("executor: turn did not advance within %ss (steps=%s)\n" % (timeout, steps))
        return {"turn": None, "steps": steps, "waited_s": round(time.time() - t0, 1)}

    def turn_number(self):
        """Execution-side turn read, or None if unreadable."""
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

    def defeat_screen(self, roots=None):
        """The end-of-campaign victory/defeat screen's root name, or None."""
        try:
            return interrupts.defeat_screen(self.bus, roots)
        except Exception as e:
            sys.stderr.write("executor: defeat_screen -> %s\n" % repr(e)[:90])
            return None

    def campaign_ui_alive(self):
        """True while the campaign HUD is on screen. None when the bus cannot answer."""
        roots = self.visible_roots()
        if not roots:
            return None
        return "hud_campaign" in roots

    _LUA_UI_STATE = (
        "local function b(f) local ok,v=pcall(f) if not ok then return 'nil' end return tostring(v) end "
        "return b(function() return cm:is_any_cutscene_running() end)..'|'.."
        "b(function() return cm:is_cinematic_ui_enabled() end)..'|'.."
        "b(function() return cm:is_ui_hiding_enabled() end)")
    _LUA_UI_RESTORE = (
        "local r={} "
        "local function s(n,f) local ok=pcall(f) r[#r+1]=n..'='..tostring(ok) end "
        "s('skip',function() cm:skip_all_campaign_cutscenes() end) "
        "s('input',function() cm:steal_user_input(false) end) "
        "s('borders',function() CampaignUI.ToggleCinematicBorders(false) end) "
        "s('ui',function() cm:enable_ui(true) end) "
        "return table.concat(r,',')")
    _LUA_NO_UI_HOTKEY = (
        "local r={} "
        "local function s(n,f) local ok=pcall(f) r[#r+1]=n..'='..tostring(ok) end "
        "s('toggle_ui',function() cm:disable_shortcut('root','toggle_ui',true) end) "
        "s('toggle_ui_borders',function() cm:disable_shortcut('root','toggle_ui_with_borders',true) end) "
        "return table.concat(r,',')")

    def _eval(self, lua, timeout=15.0):
        try:
            r = self.bus.send("eval", lua, timeout=timeout) or {}
        except Exception as e:
            sys.stderr.write("executor: eval failed -> %s\n" % repr(e)[:110])
            return None
        if r.get("error"):
            sys.stderr.write("executor: lua error -> %s\n" % str(r["error"])[:140])
            return None
        return r.get("result")

    def ui_state(self):
        """{cutscene, cinematic_ui, ui_hiding}, or None if unreadable."""
        raw = self._eval(self._LUA_UI_STATE)
        if not raw:
            return None
        p = str(raw).split("|")
        if len(p) < 3:
            return None
        f = lambda v: True if v == "true" else (False if v == "false" else None)   # noqa: E731
        return {"cutscene": f(p[0]), "cinematic_ui": f(p[1]), "ui_hiding": f(p[2])}

    def force_ui_restore(self):
        """Give the UI back. Returns what each step reported."""
        shown = None
        try:
            shown = self.bus.send("show", "hud_campaign", timeout=20.0)
        except Exception as e:
            sys.stderr.write("executor: show hud_campaign -> %s\n" % repr(e)[:120])
        out = self._eval(self._LUA_UI_RESTORE, timeout=20.0)
        sys.stderr.write("executor: force_ui_restore -> show=%s | %s\n" % (shown, out))
        return {"show": shown, "script": out}

    def disable_ui_hotkeys(self):
        """Disable the toggle_ui / toggle_ui_with_borders shortcuts."""
        out = self._eval(self._LUA_NO_UI_HOTKEY)
        sys.stderr.write("executor: disable_ui_hotkeys -> %s\n" % (out,))
        return out

    def start_game(self, plan, campaign="Immortal Empires", boot_timeout=90):
        """Cold start: spawn WH3 and drive the frontend to a playable campaign."""
        import bus_launcher
        bl = bus_launcher.BusLauncher()
        started = bl.launch(plan, campaign=campaign, boot_timeout=boot_timeout)
        self.bus = bl.bus or self.bus
        return started

    def kill_game(self):
        """Kill the Warhammer3 process."""
        import subprocess
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue "
                            "| Stop-Process -Force"],
                           capture_output=True, text=True, timeout=60)
        except Exception as e:
            sys.stderr.write("executor: kill_game -> %s\n" % repr(e)[:120])

    def hard_restart(self, plan, campaign="Immortal Empires", boot_timeout=90):
        """Kill the game process and cold-start a fresh campaign."""
        self.kill_game()
        time.sleep(8)
        return self.start_game(plan=plan, campaign=campaign, boot_timeout=boot_timeout)

    def at_main_menu(self):
        return "main" in self.visible_roots()

    def ensure_campaign(self, plan, campaign="Immortal Empires", fresh=False):
        """Guarantee a playable campaign, from whatever state the game is in.

        in a campaign + fresh=False -> nothing to do
        in a campaign + fresh=True  -> quit to menu and start a new one
        at the main menu            -> start one
        """
        if self.at_main_menu():
            import bus_launcher
            bl = bus_launcher.BusLauncher()
            bl.bus = self.bus
            started = bl.start_campaign(plan, campaign=campaign)
            self.bus = bl.bus or self.bus
            return started
        if self.turn_number() is None:
            raise RuntimeError("game is neither at the main menu nor in a readable campaign "
                               "(roots=%s)" % self.visible_roots())
        return self.new_campaign(plan, campaign) if fresh else {"already_in_campaign": True,
                                                                "turn": self.turn_number()}

    def new_campaign(self, plan, campaign="Immortal Empires"):
        """Abandon the current campaign and start a fresh one without respawning the process."""
        import bus_launcher
        bl = bus_launcher.BusLauncher()
        bl.bus = self.bus
        started = bl.restart_campaign(plan, campaign=campaign)
        self.bus = bl.bus or self.bus
        return started

    def screenshot(self, name):
        """Capture the game window to <shots_dir>/<name>.png. Returns the path, or None."""
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

