from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common

sys.path.insert(0, common.BUS)
from bus import Bus, OUT_PATH
from errors import TWError

GAME_DIR = common.GAME_DIR
EXE = os.path.join(GAME_DIR, "Warhammer3.exe")
PACK_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "bus", "dist", "tw.pack")
PACK_DST = os.path.join(GAME_DIR, "data", "tw.pack")
ROSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startable_factions.json")

P_LIST_PARENT = ("campaign_select_new|right_holder|tab_campaign|campaign_holder|"
                 "campaign_button_holder|list_parent")
P_CONTINUE = "custom_loading_screen|bottom_parent|button_continue"
HUD_GRACE = 25.0


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
        dst = common.ARCHIVE_BUS
        os.makedirs(dst, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        last = None
        for attempt in range(3):
            moved = []
            try:
                with _bus._ProcLock(_bus.CMD_PATH + ".lock"):
                    for p in (_bus.CMD_PATH, _bus.OUT_PATH, _bus.SEND_LOG_PATH):
                        if os.path.exists(p) and os.path.getsize(p) > 0:
                            shutil.move(p, os.path.join(
                                dst, "%s_%s" % (stamp, os.path.basename(p))))
                            moved.append(os.path.basename(p))
                        open(p, "a", encoding="utf-8").close()
                    bad = [p for p in (_bus.CMD_PATH, _bus.OUT_PATH, _bus.SEND_LOG_PATH)
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

    def rotate_out_log(self):
        """Archive the mod's output log and start the next campaign on a fresh file.

        Only the OUT and SEND logs, never commands.txt: the mod tracks last_seq against
        the command file, so truncating that mid-life would silently drop commands.

        Safe with the game up because the mod opens/appends/closes per record rather than
        holding the file, and because readers take a fresh offset per send -- truncating
        between commands is fine, truncating during a wait is what would hang.
        """
        import bus as _bus
        dst = common.ARCHIVE_BUS
        os.makedirs(dst, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        moved, freed = [], 0
        try:
            with _bus._ProcLock(_bus.CMD_PATH + ".lock"):
                for p in (_bus.OUT_PATH, _bus.SEND_LOG_PATH):
                    try:
                        if os.path.exists(p) and os.path.getsize(p) > 0:
                            freed += os.path.getsize(p)
                            shutil.move(p, os.path.join(
                                dst, "%s_%s" % (stamp, os.path.basename(p))))
                            moved.append(os.path.basename(p))
                        open(p, "a", encoding="utf-8").close()
                    except OSError as e:
                        _log("out-log rotation skipped %s -> %s"
                             % (os.path.basename(p), repr(e)[:60]))
        except Exception as e:
            _log("out-log rotation failed -> %s" % repr(e)[:80])
            return None
        if moved:
            _log("out log rotated -> %s (%s, %.1fMB freed)"
                 % (dst, ", ".join(moved), freed / 1e6))
        return moved

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
            time.sleep(0.25)
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
                time.sleep(0.4)
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
            time.sleep(0.4)
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
        import bus as _bus
        t0 = time.time()
        did_continue = False
        last_skip = 0.0
        last_continue_probe = 0.0
        last_alive_probe = time.time()
        cinematic_keys = 0
        hud_since = None
        while time.time() - t0 < timeout:
            roots = self._roots_safe()
            vis = {k.get("id") for k in roots if k.get("visible")}
            if "hud_campaign" in vis:
                if hud_since is None:
                    hud_since = time.time()
                blocked = vis & {"cinematic_bars"}
                waited = time.time() - hud_since
                if not blocked or waited >= HUD_GRACE:
                    _log("interactive HUD reached in %.1fs (%d cinematic keys%s)"
                         % (time.time() - t0, cinematic_keys,
                            "" if not blocked
                            else ", gave up waiting on %s after %.0fs"
                                 % (",".join(sorted(blocked)), waited)))
                    return True
            else:
                hud_since = None
            if time.time() - last_alive_probe > 5.0:
                last_alive_probe = time.time()
                if not _bus._game_alive():
                    raise TWError("the game PROCESS IS GONE %.1fs into the campaign load -- it "
                                  "crashed rather than stalled (%d cinematic keys sent). Not "
                                  "waiting out the remaining %.0fs of the HUD timeout."
                                  % (time.time() - t0, cinematic_keys,
                                     timeout - (time.time() - t0)))
            skip_roots = [k.get("id") for k in roots
                          if k.get("id") in ("campaign_space_bar_options", "black_fade")
                          and k.get("visible")]
            now = time.time()
            if now - last_skip > 2.0:
                last_skip = now
                try:
                    self.bus.send("eval", "cm:skip_all_campaign_cutscenes()", timeout=10)
                except TWError:
                    pass
            if skip_roots:
                key = "space" if "campaign_space_bar_options" in skip_roots else "escape"
                try:
                    self.bus.send("key", "@root %s" % key, timeout=10)
                    cinematic_keys += 1
                except TWError:
                    pass
            elif not did_continue and now - last_continue_probe > 1.0:
                last_continue_probe = now
                res = self.find(P_CONTINUE).get("result") or {}
                if res.get("found") and res.get("visible"):
                    self.click(P_CONTINUE, settle=0.6)
                    did_continue = True
                    _log("dismissed loading-screen Continue")
            time.sleep(0.3)
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
                _log("back at the main menu via cm:quit() (%.1fs)" % (time.time() - t0))
                return True
            time.sleep(0.4)
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
        t0 = time.time()
        r = self._send("eval", lua, timeout=30)
        if not str((r or {}).get("result", "")).startswith("ok=true"):
            raise TWError("StartCampaign did not dispatch: %s" % r)
        started = self.wait_for({"started"}, load_timeout)
        if not started:
            raise TWError("campaign did not load ('started' never logged) within %ds" % load_timeout)
        t_started = time.time()
        _log("campaign 'started' after %.1fs" % (t_started - t0))
        if not self.advance_to_hud():
            raise TWError("reached 'started' but never got the interactive HUD (loading/cinematic stuck)")
        _log("CAMPAIGN PLAYABLE: %s / %s -- load %.1fs + hud %.1fs = %.1fs"
             % (ckey, faction, t_started - t0, time.time() - t_started, time.time() - t0))
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

    def startable_factions(self, campaign_map):
        """Every start playable on ONE campaign map."""
        import json
        key = self.CAMPAIGN_KEYS.get(campaign_map, campaign_map)
        if not key:
            raise TWError("startable_factions needs a campaign map: one of %s"
                          % ", ".join(sorted(self.CAMPAIGN_KEYS.values())))
        if not os.path.exists(ROSTER_PATH):
            raise TWError("no startable-faction roster at %s -- regenerate it with "
                          "harvest_startable_factions() at campaign-select" % ROSTER_PATH)
        with open(ROSTER_PATH, encoding="utf-8-sig") as fh:
            data = json.load(fh) or {}
        maps = data.get("maps") or {}
        if key not in maps:
            raise TWError(
                "no harvested roster for campaign map %r in %s -- it holds %s. Harvest it "
                "with harvest_startable_factions(%r) while that map's campaign-select "
                "screen is showing. Refusing to substitute another map's list: most of it "
                "would not be startable and the launches would fail one by one."
                % (key, ROSTER_PATH, ", ".join(sorted(maps)) or "no maps", key))
        keys = (maps[key] or {}).get("factions") or []
        if not keys:
            raise TWError("the roster for %s in %s lists no factions" % (key, ROSTER_PATH))
        return list(keys)

    def harvest_startable_factions(self, campaign_map, timeout=30.0, poll_s=1.0,
                                   run_s=300.0, log=None):
        """Collect a map's whole roster while someone cycles the race at campaign-select."""
        import json
        import re
        import time
        import interrupts
        if self.bus is None:
            self.bus = Bus()
        key = self.CAMPAIGN_KEYS.get(campaign_map, campaign_map)
        say = log or _log
        reply = self.bus.send("roots", timeout=timeout)
        if not reply or reply.get("error") or (reply.get("kids") is None):
            raise TWError("harvest: the bus did not return a root list (%r)" % (reply,))

        def sample():
            race, campaign, keys = None, None, []
            for n in interrupts._tree(self.bus, "campaign_select_new", 20, 20000):
                i, path = str(n.get("id") or ""), str(n.get("path") or "")
                text = str(n.get("text") or "").strip()
                if i == "race_name" and text:
                    race = text
                elif i == "selected_campaign_text" and text:
                    campaign = text
                if i.startswith("CcoFrontendFactionLeader") and "lord_select_list" in path:
                    k = re.sub(r"\d+$", "", i[len("CcoFrontendFactionLeader"):])
                    if k and k not in keys:
                        keys.append(k)
            return race, campaign, keys

        races, t0, quiet = {}, time.time(), 0
        say("harvest %s: cycle the race at campaign-select; polling every %.1fs"
            % (key, poll_s))
        while time.time() - t0 < run_s:
            try:
                race, on_screen, keys = sample()
            except Exception as e:
                say("harvest: sample failed (%s) -- continuing" % repr(e)[:80])
                time.sleep(poll_s)
                continue
            if on_screen and key and key != self.CAMPAIGN_KEYS.get(on_screen, None) \
                    and on_screen.lower().replace("the ", "") not in \
                    str(campaign_map).lower().replace("the ", ""):
                time.sleep(poll_s)
                continue
            if race and keys and races.get(race) != keys:
                races[race] = keys
                quiet = 0
                say("harvest: %-22s %2d lords" % (race, len(keys)))
            else:
                quiet += 1
            time.sleep(poll_s)

        out = sorted({k for v in races.values() for k in v})
        if not out:
            raise TWError("harvest saw no lord entries. Is the campaign-select lord list "
                          "showing for %s?" % key)
        say("harvest %s: %d races, %d factions" % (key, len(races), len(out)))
        return {"campaign_map": key,
                "races": {r.title(): v for r, v in sorted(races.items())},
                "factions": out}


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: bus_launcher.py <faction_key>   "
                         "(list them with BusLauncher().startable_factions())")
    BusLauncher().launch(sys.argv[1])


if __name__ == "__main__":
    main()
