r"""bus_launcher -- launch WH3 and drive the FRONTEND to a loaded campaign over the command bus.

NO PIXELS. Every step navigates by UI-component path via the mod's find/click commands (the mod
arms the bus in the FrontEnd environment: it logs `frontend_armed` and exposes find_uicomponent).
All paths below were discovered LIVE on WH3 v8.1.1 (2026-07-27) by enumerating the real menu tree,
and are addressed by SEMANTIC keys (button ids, culture keys, faction keys) -- so they are robust to
resolution and layout, unlike the retired absolute-pixel-fraction launcher.

Flow:  spawn -> frontend_armed -> Campaign -> New -> [pick campaign card by label] -> LORD
        -> [Change Race -> pick culture] -> [pick lord by faction key] -> Start -> `started`.

    python bus_launcher.py [plan]        # plan defaults to "nagarythe"; "empire_default" = accept default lord
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bus"))
from bus import Bus, OUT_PATH                       # noqa: E402
from errors import TWError                          # noqa: E402

GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
EXE = os.path.join(GAME_DIR, "Warhammer3.exe")
PACK_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "tw.pack")
PACK_DST = os.path.join(GAME_DIR, "data", "tw.pack")

# ---- frontend navigation paths (discovered live) -----------------------------------------
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
P_CONTINUE = "custom_loading_screen|bottom_parent|button_continue"   # loading-screen dismiss (post-load)

# Named plans: culture_key selects the race tile, faction_key selects the lord (matched as a
# SUBSTRING of the CcoFrontendFactionLeader<faction_key><n> id, so the volatile numeric suffix is
# ignored). lord=None accepts the race's default lord. Extend freely -- keys are the game's own.
PLANS = {
    "nagarythe":       {"culture": "wh2_main_hef_high_elves", "faction": "wh2_main_hef_nagarythe",
                        "name": "High Elves / Alith Anar (Nagarythe)"},
    "cathay_zhao_ming": {"culture": "wh3_main_cth_cathay", "faction": "wh3_main_cth_the_western_provinces",
                        "name": "Grand Cathay / Zhao Ming"},
    "cathay_miao_ying": {"culture": "wh3_main_cth_cathay", "faction": "wh3_main_cth_the_northern_provinces",
                        "name": "Grand Cathay / Miao Ying"},
    "slaanesh_masque": {"culture": "wh3_main_sla_slaanesh", "faction": "sla_masque",
                        "name": "Slaanesh / The Masque"},
    "beastmen_taurox": {"culture": "wh_dlc03_bst_beastmen", "faction": "taurox",
                        "name": "Beastmen / Taurox"},
    "empire_default":  {"culture": None, "faction": None, "name": "The Empire / default lord"},
}


def _log(msg):
    print("[launch] %s" % msg, flush=True)


class BusLauncher:
    def __init__(self):
        self.bus = None

    # ---- process / pack ------------------------------------------------------------------
    def ensure_pack(self):
        if os.path.isfile(PACK_DST):
            _log("mod pack present (untouched): %s" % PACK_DST)
            return
        if not os.path.isfile(PACK_SRC):
            raise TWError("mod pack missing: %s" % PACK_SRC)
        shutil.copy2(PACK_SRC, PACK_DST)
        _log("installed mod pack -> %s" % PACK_DST)

    def spawn(self):
        if not os.path.isfile(EXE):
            raise TWError("WH3 exe not found: %s" % EXE)
        flags = 0
        for n in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= getattr(subprocess, n, 0)
        subprocess.Popen([EXE], cwd=GAME_DIR, creationflags=flags, close_fds=True)
        _log("spawned %s" % EXE)

    def wait_for(self, kinds, timeout):
        """Wait for a mod record whose 'cmd' is in `kinds`, appended to OUT_PATH after now."""
        start = os.path.getsize(OUT_PATH) if os.path.exists(OUT_PATH) else 0
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                with open(OUT_PATH, "rb") as f:
                    f.seek(start)
                    data = f.read()
            except OSError:
                data = b""  # intentional: OUT_PATH not readable yet -> retry next poll (expected during boot)
            for line in data.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # intentional: skip partial/non-JSON result line, per-poll parse (no per-line log)
                if obj.get("cmd") in kinds:
                    return obj
            time.sleep(1.0)
        return None

    # ---- bus helpers ---------------------------------------------------------------------
    def _send(self, channel, payload="", timeout=15, tries=3):
        """Send with a few retries: the mod can drop the very first command right after arming,
        and a transient timeout must not abort a whole launch."""
        last = None
        for _ in range(tries):
            try:
                return self.bus.send(channel, payload, timeout=timeout)
            except TWError as e:
                last = e
                time.sleep(1.0)
        raise last

    def _wait_bus_ready(self, timeout=40):
        """After frontend_armed the mod needs a moment before it answers reliably; probe with
        `roots` until a round-trip succeeds so the first real navigation command is never dropped."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                self.bus.send("roots", "", timeout=8)
                return True
            except TWError:
                time.sleep(1.0)  # intentional: bus not ready yet -> retry (expected right after arming)
        return False

    def _roots_safe(self):
        try:
            return self.bus.send("roots", "", timeout=12).get("kids", [])
        except TWError:
            return []  # intentional: bus not ready -> empty roots, polled by caller (expected; not logged)

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
        """Wait until `root_id` is a visible top-level root."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            for k in self._roots_safe():
                if k.get("id") == root_id and k.get("visible"):
                    return True
            time.sleep(1.0)
        return False

    # ---- selection by label / key (robust to volatile numeric suffixes) ------------------
    def _child_path_matching(self, container, substrings, leaf):
        """Return the path <container>|<child>|<leaf> for the first direct child of `container`
        whose id contains ALL of `substrings`, else None. Uses a shallow tree walk."""
        t = self.tree(container, depth=1, nodes=200)
        for nd in (t.get("nodes") or []):
            p = nd.get("path", "")
            if p == container:
                continue
            cid = nd.get("id") or ""
            # only direct children (path == container|<cid>)
            if p.count("|") != container.count("|") + 1:
                continue
            if all(s in cid for s in substrings):
                return p + "|" + leaf
        return None

    def _campaign_card(self, label):
        """Find the campaign card whose button_txt equals `label` (e.g. 'Immortal Empires')."""
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
        """After 'started', reach the INTERACTIVE HUD: dismiss the loading-screen Continue button,
        then skip the intro cinematic until hud_campaign is visible. Returns True on success.
        (Discovered live: 'started' fires when the model loads, but the game then parks on the
        loading screen's Continue and plays an intro cinematic before the HUD is interactive.)"""
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
                pass  # intentional: cutscene-skip eval is best-effort, polled each pass (expected; not logged)
            for k in self._roots_safe():
                if k.get("id") == "hud_campaign" and k.get("visible"):
                    _log("interactive HUD reached (hud_campaign visible)")
                    return True
            time.sleep(2.0)
        return False

    # ---- the launch sequence -------------------------------------------------------------
    def launch(self, plan_name="nagarythe", campaign="Immortal Empires", boot_timeout=240,
               load_timeout=360):
        plan = PLANS.get(plan_name)
        if plan is None:
            raise TWError("unknown plan %r (have %s)" % (plan_name, list(PLANS)))
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

        self.click(P_CAMPAIGN)
        _log("clicked Campaign")
        self.click(P_NEW)
        _log("clicked New")
        if not self.wait_root("campaign_select_new", 30):
            raise TWError("campaign_select_new never appeared after New")

        card = self._campaign_card(campaign)
        if not card:
            raise TWError("campaign card %r not found" % campaign)
        self.click(card)
        _log("selected campaign: %s" % campaign)

        self.click(P_LORD)
        _log("opened LORD tab")

        if plan.get("culture"):
            self.click(P_CHANGE_RACE)
            race = self._child_path_matching(P_CULTURE_LIST, ["CcoCultureRecord", plan["culture"]],
                                             "race_button")
            if not race:
                raise TWError("race not found for culture %s" % plan["culture"])
            self.click(race)
            _log("picked race: %s" % plan["culture"])

        if plan.get("faction"):
            lord = self._child_path_matching(P_LORD_LIST,
                                             ["CcoFrontendFactionLeader", plan["faction"]],
                                             "lord_button")
            if not lord:
                raise TWError("lord not found for faction %s" % plan["faction"])
            self.click(lord)
            leader = self.text_of(P_LEADER_NAME)
            faction = self.text_of(P_FACTION_NAME)
            _log("picked lord: %s / %s" % (leader, faction))

        self.click(P_START, settle=0.5)
        _log("clicked Start Campaign -- loading (up to %ds) ..." % load_timeout)
        started = self.wait_for({"started"}, load_timeout)
        if not started:
            raise TWError("campaign did not load ('started' never logged) within %ds" % load_timeout)
        _log("campaign model loaded ('started'); advancing to the interactive HUD ...")
        if not self.advance_to_hud():
            raise TWError("reached 'started' but never got the interactive HUD (loading/cinematic stuck)")
        _log("CAMPAIGN PLAYABLE (interactive HUD): %s" % json.dumps(started)[:160])
        return started


def main():
    plan = sys.argv[1] if len(sys.argv) > 1 else "nagarythe"
    BusLauncher().launch(plan)


if __name__ == "__main__":
    main()
