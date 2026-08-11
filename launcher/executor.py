from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)
sys.path.insert(0, common.LAUNCHER)

import cco_actions as CCO
import cm_actions
import click_actions
import diplomacy_actions
import interrupts
import nav
import trace

PS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ps")

_ENTITY_KIND = {"lord": "lord", "province": "settlement", "campaign": "campaign"}


class Executor:
    def __init__(self, bus, shots_dir=None):
        self.bus = bus
        self.shots_dir = shots_dir or common.V7_SHOTS_DIR
        self.executed = 0
        self.counted = 0

    def execute(self, pick):
        ctx = {"context_kind": _ENTITY_KIND.get(pick.get("context_kind"), pick.get("context_kind")),
               "entity_id": str(pick.get("context_id"))}
        run = {"action_type": pick.get("action_type"), "key": pick.get("key"),
               "params": pick.get("params") or {}, "policy": pick.get("policy")}
        if run["action_type"] == "end_turn":
            self._end_turn_offset = self.bus.out_offset()
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
                       before=rec.get("before"), after=rec.get("after"),
                       gates=rec.get("prechecks"), execute_error=rec.get("execute_error"),
                       doomed=rec.get("doomed"), timing=rec.get("timing"),
                       params=run["params"], stderr=rec.get("stderr"))
        self.executed += 1
        self.counted += 1 if rec.get("counted") else 0
        return rec

    def resolve_interrupts(self):
        return interrupts.resolve(self.bus)

    def defeated_probe(self):
        return interrupts.defeated_probe(self.bus)

    def mark_campaign_start(self):
        self._campaign_offset = self.bus.out_offset()

    def defeated_row_seen(self):
        import json
        off = getattr(self, "_campaign_offset", None)
        if off is None:
            return False
        try:
            size = os.path.getsize(self.bus.out_path)
            start = max(off, size - 1_000_000)
            with open(self.bus.out_path, "rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return False
        for line in data.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line or '"faction_destroyed"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("cmd") == "faction_destroyed" and row.get("is_us"):
                sys.stderr.write("executor: faction_destroyed row seen (event=%s turn=%s)\n"
                                 % (row.get("event"), row.get("turn")))
                return True
        return False

    def settle_between_turns(self, timeout=420.0, poll=4.0, turn_before=None, abort=None):
        t0 = time.time()
        steps = []

        def _aborted():
            return abort is not None and abort()

        def _bail():
            sys.stderr.write("executor: settle aborted -- the run was declared stuck\n")
            return {"turn": None, "steps": steps, "waited_s": round(time.time() - t0, 1),
                    "aborted": True}

        off = getattr(self, "_end_turn_offset", None)
        self._end_turn_offset = None
        if off is None:
            off = self.bus.out_offset()

        def _row_wanted(r):
            if r.get("cmd") == "faction_destroyed":
                return bool(r.get("is_us"))
            return turn_before is None or (r.get("turn") or 0) > turn_before

        rounds = 0
        while time.time() - t0 < timeout:
            if _aborted():
                return _bail()
            row, off = self.bus.wait_row(("turn_start", "faction_destroyed"),
                                         timeout=poll, offset=off, pred=_row_wanted)
            if row is not None:
                if row.get("cmd") == "faction_destroyed":
                    sys.stderr.write("executor: settle -- the faction is DEAD (event=%s)\n"
                                     % row.get("event"))
                    return {"turn": None, "steps": steps + ["defeated"],
                            "waited_s": round(time.time() - t0, 1), "defeated": True}
                return {"turn": row.get("turn"), "steps": steps,
                        "waited_s": round(time.time() - t0, 1)}
            if _aborted():
                return _bail()
            s = self.resolve_interrupts()
            if s:
                steps.extend(s)
            rounds += 1
            if rounds % 8 == 0:
                t = self.turn_number()
                if t is not None and (turn_before is None or t > turn_before):
                    return {"turn": t, "steps": steps, "waited_s": round(time.time() - t0, 1)}
                if interrupts.defeated_probe(self.bus) is True:
                    sys.stderr.write("executor: settle -- the faction is DEAD (probe)\n")
                    return {"turn": None, "steps": steps + ["defeated"],
                            "waited_s": round(time.time() - t0, 1), "defeated": True}
        sys.stderr.write("executor: turn did not advance within %ss (steps=%s)\n" % (timeout, steps))
        return {"turn": None, "steps": steps, "waited_s": round(time.time() - t0, 1)}

    def turn_number(self):
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
        try:
            return interrupts.defeat_screen(self.bus, roots)
        except Exception as e:
            sys.stderr.write("executor: defeat_screen -> %s\n" % repr(e)[:90])
            return None

    def campaign_ui_alive(self):
        roots = self.visible_roots()
        if not roots:
            return None
        return "hud_campaign" in roots

    _LUA_TRANSITION = (
        "local function g(c,p) local ok,v=pcall(function() return c:Call(p) end) "
        "if ok and v~=nil then return tostring(v) end return 'nil' end "
        "local function b(f) local ok,v=pcall(f) if not ok then return 'nil' end return tostring(v) end "
        "local r=cco('CcoCampaignRoot','') "
        "local pa=nil pcall(function() pa=r:Call('PendingActionContext') end) "
        "return g(r,'IsLocomotionComplete')..'|'..g(r,'IsHUDVisible')..'|'.."
        "(pa and (g(pa,'IsActive')..'~'..g(pa,'ActionType')..'~'"
        "..g(pa,'ShouldBlockAllInteractions')) or 'none')..'|'.."
        "b(function() return cm:is_any_cutscene_running() end)")

    def transition_probe(self):
        raw = self._eval(self._LUA_TRANSITION, timeout=10.0)
        if not raw:
            return None
        p = str(raw).split("|")
        if len(p) < 4:
            return None
        return {"locomotion_done": p[0], "hud_visible": p[1], "pending": p[2], "cutscene": p[3]}

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
        "s('ui_hiding',function() cm:enable_ui_hiding(false) end) "
        "r[#r+1]='hiding_now='..tostring(cm:is_ui_hiding_enabled()) "
        "return table.concat(r,',')")
    _LUA_NO_UI_HOTKEY = (
        "local r={} "
        "local function s(n,f) local ok=pcall(f) r[#r+1]=n..'='..tostring(ok) end "
        "s('toggle_ui',function() cm:disable_shortcut('root','toggle_ui',true) end) "
        "s('toggle_ui_borders',function() cm:disable_shortcut('root','toggle_ui_with_borders',true) end) "
        "s('ui_hiding',function() cm:enable_ui_hiding(false) end) "
        "r[#r+1]='hiding_now='..tostring(cm:is_ui_hiding_enabled()) "
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
        raw = self._eval(self._LUA_UI_STATE)
        if not raw:
            return None
        p = str(raw).split("|")
        if len(p) < 3:
            return None
        f = lambda v: True if v == "true" else (False if v == "false" else None)
        return {"cutscene": f(p[0]), "cinematic_ui": f(p[1]), "ui_hiding": f(p[2])}

    def force_ui_restore(self):
        shown = None
        try:
            shown = self.bus.send("show", "hud_campaign", timeout=20.0)
        except Exception as e:
            sys.stderr.write("executor: show hud_campaign -> %s\n" % repr(e)[:120])
        out = self._eval(self._LUA_UI_RESTORE, timeout=20.0)
        sys.stderr.write("executor: force_ui_restore -> show=%s | %s\n" % (shown, out))
        return {"show": shown, "script": out}

    def disable_ui_hotkeys(self):
        out = self._eval(self._LUA_NO_UI_HOTKEY)
        sys.stderr.write("executor: disable_ui_hotkeys -> %s\n" % (out,))
        return out

    def start_game(self, plan, campaign="Immortal Empires", boot_timeout=90):
        import bus_launcher
        bl = bus_launcher.BusLauncher()
        started = bl.launch(plan, campaign=campaign, boot_timeout=boot_timeout)
        self.bus = bl.bus or self.bus
        return started

    def kill_game(self):
        import subprocess
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-Process -Name Warhammer3 -ErrorAction SilentlyContinue "
                            "| Stop-Process -Force"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            sys.stderr.write("executor: kill_game -> %s\n" % repr(e)[:120])

    def game_is_up(self):
        """tasklist, not our own bookkeeping. Fails OPEN (True) so an unreadable process
        table never reads as 'the gpu is free'."""
        import bus as _bus
        return _bus._game_alive()

    def wait_game_down(self, timeout=45.0, poll=1.5, log=None):
        """Block until no Warhammer3 process remains.

        kill_game() only issues Stop-Process and returns; it does not wait, and it
        swallows its own failures. A trainer that starts while the game is still up
        contends with it for VRAM, which is the whole reason the retrain window takes
        the game down. Kills once more if the first attempt did not take.
        """
        for attempt in (1, 2):
            t0 = time.time()
            while time.time() - t0 < timeout:
                if not self.game_is_up():
                    return True
                time.sleep(poll)
            if attempt == 1:
                if log:
                    log("   game still up %.0fs after kill -- killing again" % timeout)
                self.kill_game()
        return not self.game_is_up()

    def hard_restart(self, plan, campaign="Immortal Empires", boot_timeout=90):
        self.kill_game()
        time.sleep(8)
        return self.start_game(plan=plan, campaign=campaign, boot_timeout=boot_timeout)

    def at_main_menu(self):
        return "main" in self.visible_roots()

    def ensure_campaign(self, plan, campaign="Immortal Empires", fresh=False):
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
        import bus_launcher
        bl = bus_launcher.BusLauncher()
        bl.bus = self.bus
        started = bl.restart_campaign(plan, campaign=campaign)
        self.bus = bl.bus or self.bus
        return started

    def screenshot(self, name):
        path = os.path.join(self.shots_dir, "%s.png" % name)
        try:
            os.makedirs(self.shots_dir, exist_ok=True)
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", os.path.join(PS_DIR, "capture.ps1"), path],
                           capture_output=True, text=True, timeout=45,
                           creationflags=subprocess.CREATE_NO_WINDOW)
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
