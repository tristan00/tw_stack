from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bus"))
from bus import Bus, OUT_PATH
from errors import TWError

GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
EXE = os.path.join(GAME_DIR, "Warhammer3.exe")
PACK_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "bus", "dist", "tw.pack")
PACK_DST = os.path.join(GAME_DIR, "data", "tw.pack")
ROSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startable_factions.json")

P_CAMPAIGN = "main|left_holder|buttons_holder|buttons_list|campaign|button_campaign"
P_NEW = ("main|left_holder|buttons_holder|buttons_list|campaign_frame|campaign_buttons|"
         "start_campaign_new|button_start_campaign_new")
P_LIST_PARENT = ("campaign_select_new|right_holder|tab_campaign|campaign_holder|"
                 "campaign_button_holder|list_parent")
P_LORD = ("campaign_select_new|side_panel_holder|side_holder|button_list|lord_select_holder|"
          "button_select_lord")
P_CHANGE_RACE = ("campaign_select_new|right_holder|tab_lord|lord_details_panel|faction_holder|"
                 "button_select_race")
P_CULTURE_LIST = P_CHANGE_RACE + "|popup_menu|content|culture_list"
P_LORD_LIST = ("campaign_select_new|right_holder|tab_lord|lord_details_panel|lord_select_list|"
               "list|list_clip|list_box")
P_LEADER_NAME = ("campaign_select_new|right_holder|tab_lord|lord_details_panel|"
                 "name_and_icon_holder|name_clip|name_holder|dy_faction_leader")
P_FACTION_NAME = ("campaign_select_new|right_holder|tab_lord|lord_details_panel|"
                  "name_and_icon_holder|name_clip|name_holder|label_faction_name")
P_START = "campaign_select_new|button_start_parent|button_start_campaign"
P_CONTINUE = "custom_loading_screen|bottom_parent|button_continue"


def _log(msg):
    print("[launch] %s" % msg, flush=True)


class BusLauncher:
    def __init__(self):
        self.bus = None
        self.last_startable = []

    def ensure_pack(self):
        import pack_multi
        built = str(pack_multi.build())
        if os.path.abspath(built) != os.path.abspath(PACK_SRC):
            raise TWError("pack_multi built %s but the launcher installs %s" % (built, PACK_SRC))
        _log("mod pack built from %s" % os.path.join(os.path.dirname(PACK_SRC), "..", "mod"))
        if not os.path.isfile(PACK_SRC):
            raise TWError("mod pack missing: %s" % PACK_SRC)
        if os.path.isfile(PACK_DST):
            fresh = (os.path.getsize(PACK_DST) == os.path.getsize(PACK_SRC)
                     and os.path.getmtime(PACK_DST) >= os.path.getmtime(PACK_SRC))
            if fresh:
                _log("mod pack current: %s" % PACK_DST)
                return
        shutil.copy2(PACK_SRC, PACK_DST)
        _log("installed mod pack -> %s" % PACK_DST)

    def _rotate_bus_files(self):
        import bus as _bus
        if _bus._game_alive():
            raise TWError("bus rotation refused: Warhammer3 is still running -- spawning a second "
                          "instance over a live mod is forbidden")
        dst = r"D:\twdata\archive\bus"
        os.makedirs(dst, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        last = None
        for attempt in range(3):
            moved = []
            try:
                with _bus._ProcLock(_bus.CMD_PATH + ".lock"):
                    for p in (_bus.CMD_PATH, _bus.OUT_PATH):
                        if os.path.exists(p) and os.path.getsize(p) > 0:
                            shutil.move(p, os.path.join(
                                dst, "%s_%s" % (stamp, os.path.basename(p))))
                            moved.append(os.path.basename(p))
                        open(p, "a", encoding="utf-8").close()
                    bad = [p for p in (_bus.CMD_PATH, _bus.OUT_PATH)
                           if not os.path.exists(p) or os.path.getsize(p) != 0]
                if not bad:
                    _log("bus files rotated -> %s (%s)"
                         % (dst, ", ".join(moved) or "were already empty"))
                    return
                last = "non-empty after recreate: %s" % bad
            except OSError as e:
                last = repr(e)[:100]
            _log("bus rotation attempt %d failed (%s) -- retrying" % (attempt + 1, last))
            time.sleep(5.0)
        raise TWError("bus rotation FAILED after 3 attempts (%s) -- refusing to boot the game "
                      "on a grown command file (the A/B-proven corruption state)" % last)

    def spawn(self):
        if not os.path.isfile(EXE):
            raise TWError("WH3 exe not found: %s" % EXE)
        self._rotate_bus_files()
        flags = 0
        for n in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= getattr(subprocess, n, 0)
        subprocess.Popen([EXE], cwd=GAME_DIR, creationflags=flags, close_fds=True)
        _log("spawned %s" % EXE)

    def wait_for(self, kinds, timeout):
        start = os.path.getsize(OUT_PATH) if os.path.exists(OUT_PATH) else 0
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                with open(OUT_PATH, "rb") as f:
                    f.seek(start)
                    data = f.read()
            except OSError:
                data = b""
            for line in data.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("cmd") in kinds:
                    return obj
            time.sleep(1.0)
        return None

    def _send(self, channel, payload="", timeout=15, tries=3):
        last = None
        for _ in range(tries):
            try:
                return self.bus.send(channel, payload, timeout=timeout)
            except TWError as e:
                last = e
                time.sleep(1.0)
        raise last

    def _wait_bus_ready(self, timeout=40):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                self.bus.send("roots", "", timeout=8)
                return True
            except TWError:
                time.sleep(1.0)
        return False

    def _roots_safe(self):
        try:
            return self.bus.send("roots", "", timeout=12).get("kids", [])
        except TWError:
            return []

    def click(self, path, settle=1.2):
        r = self._send("click", path, timeout=15)
        if not r.get("found"):
            raise TWError("launch: component not found to click: %s" % path)
        if not r.get("clicked"):
            raise TWError("launch: found but click failed: %s" % path)
        time.sleep(settle)
        return r

    def find(self, path):
        return self._send("find", path, timeout=15)

    def tree(self, path, depth=4, nodes=400):
        return self._send("tree", "%s %d %d" % (path, depth, nodes), timeout=25)

    def text_of(self, path):
        return (self.find(path).get("result") or {}).get("text")

    def wait_root(self, root_id, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            for k in self._roots_safe():
                if k.get("id") == root_id and k.get("visible"):
                    return True
            time.sleep(1.0)
        return False

    def _child_path_matching(self, container, substrings, leaf):
        t = self.tree(container, depth=1, nodes=200)
        for nd in (t.get("nodes") or []):
            p = nd.get("path", "")
            if p == container:
                continue
            cid = nd.get("id") or ""
            if p.count("|") != container.count("|") + 1:
                continue
            if all(s in cid for s in substrings):
                return p + "|" + leaf
        return None

    def _campaign_card(self, label):
        t = self.tree(P_LIST_PARENT, depth=1, nodes=200)
        for nd in (t.get("nodes") or []):
            p = nd.get("path", "")
            if p.count("|") != P_LIST_PARENT.count("|") + 1:
                continue
            entry = p + "|button_campaign_entry"
            if self.text_of(entry + "|button_txt") == label:
                return entry
        return None

    def advance_to_hud(self, timeout=200):
        t0 = time.time()
        did_continue = False
        while time.time() - t0 < timeout:
            if not did_continue:
                res = self.find(P_CONTINUE).get("result") or {}
                if res.get("found") and res.get("visible"):
                    self.click(P_CONTINUE, settle=2.0)
                    did_continue = True
                    _log("dismissed loading-screen Continue")
                    continue
            try:
                self.bus.send("eval", "cm:skip_all_campaign_cutscenes()", timeout=10)
            except TWError:
                pass
            roots = self._roots_safe()
            skip_roots = [k.get("id") for k in roots
                          if k.get("id") in ("campaign_space_bar_options", "black_fade")
                          and k.get("visible")]
            if skip_roots:
                key = "space" if "campaign_space_bar_options" in skip_roots else "escape"
                try:
                    r = self.bus.send("key", "@root %s" % key, timeout=10) or {}
                except TWError as e:
                    r = {"error": repr(e)[:80]}
                _log("cinematic on screen (%s) -> bus key %s (sent=%s changed=%s)"
                     % (",".join(skip_roots), key, r.get("sent"), r.get("changed")))
            for k in roots:
                if k.get("id") == "hud_campaign" and k.get("visible"):
                    _log("interactive HUD reached (hud_campaign visible)")
                    return True
            time.sleep(2.0)
        return False

    CAMPAIGN_KEYS = {"Immortal Empires": "wh3_main_combi",
                     "Realm of Chaos": "wh3_main_chaos",
                     "Prologue": "wh3_main_prologue"}

    def quit_to_main_menu(self, timeout=120):
        if self.bus is None:
            self.bus = Bus()
        r = self._send("eval", "local ok,e=pcall(function() cm:quit() end) "
                               "return 'ok='..tostring(ok)..' err='..tostring(e)", timeout=25)
        if not str((r or {}).get("result", "")).startswith("ok=true"):
            raise TWError("cm:quit() did not dispatch: %s" % r)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if any(k.get("id") == "main" and k.get("visible") for k in self._roots_safe()):
                _log("back at the main menu via cm:quit() (%.0fs)" % (time.time() - t0))
                return True
            time.sleep(2.0)
        raise TWError("cm:quit() dispatched but the main menu never appeared within %ds" % timeout)

    def start_campaign(self, faction, campaign="Immortal Empires", load_timeout=120):
        ckey = self.CAMPAIGN_KEYS.get(campaign, campaign)
        faction = str(faction or "").strip()
        if not faction:
            raise TWError("start_campaign needs a faction key (see startable_factions())")
        lua = ("local ok,e=pcall(function() cco('CcoFrontendRoot',''):Call("
               "'StartCampaign(\"%s\", \"%s\", \"SP_NORMAL\")') end) "
               "return 'ok='..tostring(ok)..' err='..tostring(e)" % (ckey, faction))
        _log("StartCampaign(%s, %s, SP_NORMAL)" % (ckey, faction))
        r = self._send("eval", lua, timeout=30)
        if not str((r or {}).get("result", "")).startswith("ok=true"):
            raise TWError("StartCampaign did not dispatch: %s" % r)
        started = self.wait_for({"started"}, load_timeout)
        if not started:
            raise TWError("campaign did not load ('started' never logged) within %ds" % load_timeout)
        if not self.advance_to_hud():
            raise TWError("reached 'started' but never got the interactive HUD (loading/cinematic stuck)")
        _log("CAMPAIGN PLAYABLE: %s / %s" % (ckey, faction))
        return started

    def restart_campaign(self, faction, campaign="Immortal Empires", load_timeout=120):
        self.quit_to_main_menu()
        return self.start_campaign(faction, campaign, load_timeout)

    def launch(self, faction, campaign="Immortal Empires", boot_timeout=90, load_timeout=120):
        if not str(faction or "").strip():
            raise TWError("launch() needs a faction key -- none given")
        self.ensure_pack()
        self.spawn()
        _log("waiting for frontend_armed ...")
        if not self.wait_for({"frontend_armed", "started"}, boot_timeout):
            raise TWError("frontend never armed within %ds" % boot_timeout)
        _log("frontend armed.")
        self.bus = Bus()
        if not self._wait_bus_ready():
            raise TWError("bus never became ready after frontend_armed")
        _log("bus ready.")
        return self.start_campaign(faction, campaign, load_timeout)

    def startable_factions(self):
        import json
        if not os.path.exists(ROSTER_PATH):
            raise TWError("no startable-faction roster at %s -- regenerate it with "
                          "harvest_startable_factions() at campaign-select" % ROSTER_PATH)
        with open(ROSTER_PATH, encoding="utf-8-sig") as fh:
            keys = (json.load(fh) or {}).get("factions") or []
        if not keys:
            raise TWError("startable-faction roster %s contains no factions" % ROSTER_PATH)
        return list(keys)

    def harvest_startable_factions(self, timeout=30.0):
        import re
        import interrupts
        if self.bus is None:
            self.bus = Bus()
        reply = self.bus.send("roots", timeout=timeout)
        if not reply or reply.get("error") or reply.get("roots") is None:
            raise TWError("startable_factions: the bus did not return a root list (%r)" % (reply,))
        keys, seen = [], set()
        for root in reply["roots"]:
            nodes = interrupts._tree(self.bus, root, 18, 20000)
            for n in nodes:
                m = re.match(r"CcoFrontendFactionLeader:(.+)$", str(n.get("context") or ""))
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    keys.append(m.group(1))
        return keys


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: bus_launcher.py <faction_key>   "
                         "(list them with BusLauncher().startable_factions())")
    BusLauncher().launch(sys.argv[1])


if __name__ == "__main__":
    main()
