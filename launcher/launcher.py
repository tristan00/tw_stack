from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

from errors import TWError
from screen import ScreenBridge

CMD_PATH = "D:/totalwar_runner/data/commands.txt"
OUT_PATH = "D:/totalwar_runner/data/twcontrol.jsonl"


try:
    import config as _config
    _GAME_DIR = _config.GAME_DIR
    _REPO = _config.REPO
except Exception as e:
    _GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
    _REPO = os.path.dirname(os.path.abspath(__file__))
    sys.stderr.write("launcher: config import failed, using hardcoded paths -> %s\n" % repr(e)[:80])

DEFAULT_FACTION = "wh2_main_hef_nagarythe"


class GameManager:

    GAME_DIR = _GAME_DIR
    REPO = _REPO
    EXE = os.path.join(_GAME_DIR, "Warhammer3.exe")
    PACK_NAME = "tw.pack"
    PACK_SRC = os.path.join(os.path.dirname(_REPO), "bus", "dist", PACK_NAME)
    PACK_DST = os.path.join(_GAME_DIR, "data", PACK_NAME)
    PS_DIR = os.path.join(_REPO, "ps")
    SHOTS_DIR = r"D:\twdata\logs\launcher\launch_shots"
    REF_MENU = os.path.join(_REPO, "ref", "main_menu.png")
    SCRATCH_DIR = r"D:\twdata\scratch\launcher"

    SCREEN_W = 2560
    SCREEN_H = 1440

    T_WINDOW = 180
    T_MENU_FLOOR = 20
    T_MENU_STABLE = 60
    T_SCREEN = 20
    T_ANIMATED = 4
    T_ACCORDION = 14
    T_TRANSITION = 35
    T_LOAD = 300
    T_MAP = 300

    STABLE_THRESH = 5.0
    CHANGE_THRESH = 10.0
    MENU_MATCH_THRESH = 20.0
    LEFTCOL_FR = 0.18
    LEFTCOL_THRESH = 1.2

    MENU_CAMPAIGN = (0.070, 0.472)
    MENU_NEW = (0.0525, 0.507)
    IE_TILE = (
        (0.735, 0.471), (0.720, 0.450), (0.760, 0.500), (0.700, 0.420),
    )
    GRAND_CATHAY = (0.388, 0.326)
    ZHAO_MING = (0.298, 0.180)
    MIAO_YING = (0.251, 0.180)
    TAUROX = (0.251, 0.286)
    LORD_PANEL_REGION = (0.02, 0.38, 0.165, 0.502)
    LORD_CHANGE_THRESH = 5.0
    START_CAMPAIGN = (0.499, 0.954)
    BEASTMEN = (0.210, 0.186)
    EMPIRE = (0.210, 0.326)
    DWARFS = (0.566, 0.256)
    GREENSKINS = (0.566, 0.326)
    NORSCA = (0.388, 0.466)
    VAMPIRE_COUNTS = (0.210, 0.685)
    KEMMLER = (0.297, 0.184)
    HIGH_ELVES = (0.210, 0.396)
    ALITH_ANAR = (0.251, 0.286)
    SLAANESH = (0.566, 0.542)
    THE_MASQUE = (0.345, 0.175)

    RACE_PLANS = {
        "cathay": {
            "tiles": (GRAND_CATHAY,
                      (GRAND_CATHAY[0], GRAND_CATHAY[1] - 0.026),
                      (GRAND_CATHAY[0] - 0.013, GRAND_CATHAY[1])),
            "lord": ZHAO_MING,
            "guard": "western_provinces",
            "name": "Grand Cathay / Zhao Ming",
        },
        "cathay_miao_ying": {
            "tiles": (GRAND_CATHAY,
                      (GRAND_CATHAY[0], GRAND_CATHAY[1] - 0.026),
                      (GRAND_CATHAY[0] - 0.013, GRAND_CATHAY[1])),
            "lord": MIAO_YING,
            "guard": "northern_provinces",
            "name": "Grand Cathay / Miao Ying",
        },
        "beastmen": {
            "tiles": (BEASTMEN,
                      (BEASTMEN[0], BEASTMEN[1] - 0.026),
                      (BEASTMEN[0] - 0.013, BEASTMEN[1]),
                      (BEASTMEN[0] + 0.013, BEASTMEN[1])),
            "lord": None,
            "guard": "bst",
            "name": "Beastmen (horde)",
        },
        "beastmen_taurox": {
            "tiles": (BEASTMEN,
                      (BEASTMEN[0], BEASTMEN[1] - 0.026),
                      (BEASTMEN[0] - 0.013, BEASTMEN[1]),
                      (BEASTMEN[0] + 0.013, BEASTMEN[1])),
            "lord": TAUROX,
            "lord_change": True,
            "guard": "taurox",
            "name": "Beastmen / Taurox",
        },
        "empire": {
            "tiles": (EMPIRE,
                      (EMPIRE[0], EMPIRE[1] - 0.026),
                      (EMPIRE[0] - 0.013, EMPIRE[1]),
                      (EMPIRE[0] + 0.013, EMPIRE[1])),
            "lord": None,
            "guard": "emp",
            "name": "The Empire / Karl Franz",
        },
        "norsca": {
            "tiles": (NORSCA,
                      (NORSCA[0], NORSCA[1] - 0.026),
                      (NORSCA[0] - 0.013, NORSCA[1]),
                      (NORSCA[0] + 0.013, NORSCA[1])),
            "lord": None,
            "guard": "nor",
            "name": "Norsca",
        },
        "greenskins": {
            "tiles": (GREENSKINS,
                      (GREENSKINS[0], GREENSKINS[1] - 0.026),
                      (GREENSKINS[0] - 0.013, GREENSKINS[1]),
                      (GREENSKINS[0] + 0.013, GREENSKINS[1])),
            "lord": None,
            "guard": "grn",
            "name": "Greenskins",
        },
        "dwarfs": {
            "tiles": (DWARFS,
                      (DWARFS[0], DWARFS[1] - 0.026),
                      (DWARFS[0] - 0.013, DWARFS[1]),
                      (DWARFS[0] + 0.013, DWARFS[1])),
            "lord": None,
            "guard": "dwf",
            "name": "Dwarfs",
        },
        "vmp_barrow_legion": {
            "tiles": (VAMPIRE_COUNTS,
                      (VAMPIRE_COUNTS[0], VAMPIRE_COUNTS[1] - 0.020),
                      (VAMPIRE_COUNTS[0] - 0.013, VAMPIRE_COUNTS[1]),
                      (VAMPIRE_COUNTS[0] + 0.013, VAMPIRE_COUNTS[1])),
            "lord": KEMMLER,
            "lord_change": True,
            "guard": "barrow_legion",
            "name": "Vampire Counts / Heinrich Kemmler (Barrow Legion)",
        },
        "nagarythe": {
            "tiles": (HIGH_ELVES,
                      (HIGH_ELVES[0], HIGH_ELVES[1] - 0.026),
                      (HIGH_ELVES[0] - 0.013, HIGH_ELVES[1]),
                      (HIGH_ELVES[0] + 0.013, HIGH_ELVES[1])),
            "lord": ALITH_ANAR,
            "lord_change": True,
            "guard": "nagarythe",
            "name": "High Elves / Alith Anar (Nagarythe)",
        },
        "slaanesh_masque": {
            "tiles": (SLAANESH,
                      (SLAANESH[0], SLAANESH[1] - 0.026),
                      (SLAANESH[0] - 0.013, SLAANESH[1]),
                      (SLAANESH[0] + 0.013, SLAANESH[1])),
            "lord": THE_MASQUE,
            "lord_change": True,
            "guard": "masque",
            "name": "Slaanesh / The Masque",
        },
    }

    _FACTION_HINT_RACE = (
        ("northern_provinces", "cathay_miao_ying"),
        ("taurox", "beastmen_taurox"),
        ("barrow_legion", "vmp_barrow_legion"),
        ("nagarythe", "nagarythe"), ("alith_anar", "nagarythe"),
        ("masque", "slaanesh_masque"),
        ("bst", "beastmen"), ("emp", "empire"), ("nor", "norsca"),
        ("grn", "greenskins"), ("dwf", "dwarfs"),
    )

    def __init__(self) -> None:
        self._t0 = None
        self._screen = ScreenBridge(log=self._log)

    @property
    def _client(self):
        return self._screen._client

    @_client.setter
    def _client(self, value):
        self._screen._client = value

    @property
    def _stats(self):
        return self._screen._stats

    @_stats.setter
    def _stats(self, value):
        self._screen._stats = value

    def _log(self, msg: str) -> None:
        t = 0.0 if self._t0 is None else (time.time() - self._t0)
        print("[startup +%5.1fs] %s" % (t, msg), flush=True)

    def _run_ps(self, script: str, *args, **kw) -> subprocess.CompletedProcess | None:
        return self._screen._run_ps(script, *args, **kw)

    def _client_rect(self):
        return self._screen._client_rect()

    def _map_frac(self, fx: float, fy: float):
        return self._screen._map_frac(fx, fy)

    def _restore(self):
        return self._screen._restore()

    def screenshot(self, name: str) -> str:
        path = os.path.join(self.SHOTS_DIR, name + ".png")
        return self._screenshot_to(path)

    def _screenshot_to(self, path: str) -> str:
        return self._screen._screenshot_to(path)

    def _frame_ok(self) -> bool:
        return self._screen._frame_ok()

    def _valid_shot(self, name: str, tries: int = 6, settle: float = 1.2) -> tuple[str, bool]:
        path = self.screenshot(name)
        for i in range(tries):
            if self._frame_ok():
                return path, True
            self._log("capture %s: rejected non-game/screensaver frame #%d (stats=%s); "
                      "restoring + retrying" % (name, i, self._stats))
            self._restore()
            time.sleep(settle)
            path = self.screenshot(name)
        return path, self._frame_ok()

    def _menu_match(self, path: str) -> float | None:
        if not os.path.isfile(self.REF_MENU):
            return None
        return self._img_diff(self.REF_MENU, path)

    def _scratch_shot(self, label: str) -> str:
        return self._screenshot_to(os.path.join(self.SCRATCH_DIR, label + ".png"))

    def _fail_fast(self, label: str, msg: str):
        self._restore()
        path = self._scratch_shot(label)
        self._log("FAIL-FAST @ %s: %s (frame: %s)" % (label, msg, path))
        raise TWError("%s (labelled frame: %s)" % (msg, path))

    def _img_diff(self, path_a: str, path_b: str,
                  region: tuple[float, float, float, float] | None = None) -> float | None:
        args = [path_a, path_b]
        if region is not None:
            args += [region[0], region[1], region[2], region[3]]
        r = self._run_ps("imgdiff.ps1", *args, timeout=30)
        if r is None:
            return None
        val = None
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                val = float(line)
            except ValueError:
                continue
        if val is None or val < 0:
            return None
        return val

    def _wait_stable(self, timeout: float, min_wait: float = 0.0, label: str = "screen",
                     save_as: str | None = None) -> str:
        self._restore()
        last = self.screenshot("_stable_0")
        last_ok = self._frame_ok()
        start = time.time()
        deadline = start + timeout
        toggle = 1
        result = last
        while time.time() < deadline:
            time.sleep(2.0)
            self._restore()
            cur = self.screenshot("_stable_%d" % toggle)
            toggle ^= 1
            cur_ok = self._frame_ok()
            elapsed = time.time() - start
            d = self._img_diff(last, cur)
            if cur_ok:
                result = cur
            if (cur_ok and last_ok and d is not None and d < self.STABLE_THRESH
                    and elapsed >= min_wait):
                self._log("%s settled (diff=%.2f, +%.0fs)" % (label, d, elapsed))
                break
            if not cur_ok:
                self._log("%s: skipped non-game frame (stats=%s)" % (label, self._stats))
            last = cur
            last_ok = cur_ok
        else:
            self._log("%s settle-wait exhausted (proceeding)" % label)
        if save_as:
            dst = os.path.join(self.SHOTS_DIR, save_as + ".png")
            try:
                shutil.copy2(result, dst)
                result = dst
            except OSError as exc:
                self._log("WARN could not copy settled frame to %s: %s" % (dst, exc))
        return result

    def _await_transition(self, baseline_path: str, timeout: float, label: str = "screen",
                          region: tuple[float, float, float, float] | None = None,
                          thresh: float | None = None) -> str | None:
        thr = self.CHANGE_THRESH if thresh is None else thresh
        deadline = time.time() + timeout
        toggle = 0
        while time.time() < deadline:
            self._restore()
            cur = self.screenshot("_await_%d" % (toggle & 1))
            toggle += 1
            if not self._frame_ok():
                self._log("%s: ignored non-game frame (stats=%s)" % (label, self._stats))
                time.sleep(1.5)
                continue
            d = self._img_diff(baseline_path, cur, region=region)
            if d is not None and d > thr:
                self._log("%s: transition detected (diff=%.2f > %.2f)" % (label, d, thr))
                return cur
            time.sleep(1.5)
        return None

    def _try_candidates(self, candidates: tuple[tuple[float, float], ...], baseline: str,
                        per_timeout: float, total_timeout: float, label: str,
                        region: tuple[float, float, float, float] | None = None,
                        thresh: float | None = None,
                        before_tag: str | None = None) -> str | None:
        deadline = time.time() + total_timeout
        for i, (fx, fy) in enumerate(candidates):
            if time.time() >= deadline:
                break
            self._restore()
            if before_tag:
                self.screenshot("%s_%d_before" % (before_tag, i))
            self._log("frontend: click %s #%d @ frac(%.3f,%.3f)" % (label, i, fx, fy))
            budget = min(per_timeout, max(1.0, deadline - time.time()))
            settled = self._await_transition(baseline, budget, label, region=region,
                                              thresh=thresh)
            if settled:
                return settled
        return None

    def ensure_prefs(self) -> None:
        if not os.path.isfile(self.EXE):
            raise TWError("WH3 executable not found: %s" % self.EXE)
        try:
            need_copy = True
            if os.path.isfile(self.PACK_DST) and os.path.isfile(self.PACK_SRC):
                need_copy = (
                    os.path.getsize(self.PACK_DST) != os.path.getsize(self.PACK_SRC)
                    or os.path.getmtime(self.PACK_DST) < os.path.getmtime(self.PACK_SRC))
            if need_copy:
                if not os.path.isfile(self.PACK_SRC):
                    raise TWError("mod pack missing: %s (run pack_multi.py build)"
                                  % self.PACK_SRC)
                shutil.copy2(self.PACK_SRC, self.PACK_DST)
                self._log("installed mod pack -> %s" % self.PACK_DST)
            else:
                self._log("mod pack present -> %s" % self.PACK_DST)
        except OSError as exc:
            raise TWError("cannot install mod pack to %s: %s (close the game first?)"
                          % (self.PACK_DST, exc))
        return None

    def _process_window_up(self) -> bool:
        cmd = ["powershell", "-NoProfile", "-Command",
               "$p=Get-Process Warhammer3 -ErrorAction SilentlyContinue | "
               "Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; "
               "if ($p) { 'up' } else { 'down' }"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            return r.returncode == 0 and "up" in (r.stdout or "")
        except Exception as exc:
            self._log("WARN _game_up process check failed: %s" % repr(exc)[:80])
            return False

    def _out_size(self) -> int:
        try:
            return os.path.getsize(OUT_PATH)
        except OSError:
            return 0

    def _mod_started_since(self, offset: int) -> bool:
        try:
            with open(OUT_PATH, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return False
        for raw in data.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("cmd") == "started":
                return True
        return False

    def _reset_command_file(self) -> None:
        try:
            with open(CMD_PATH, "w", encoding="utf-8") as f:
                f.write("")
            self._log("cleared command file %s" % CMD_PATH)
        except OSError as exc:
            self._log("WARN could not clear %s: %s" % (CMD_PATH, exc))

    def _ensure_singleton(self) -> None:
        import tempfile
        import msvcrt
        if getattr(GameManager, "_launch_lock_fh", None) is None:
            lp = os.path.join(tempfile.gettempdir(), "tw_launcher.lock")
            try:
                fh = open(lp, "a+")
                fh.seek(0)
                if not fh.read(1):
                    fh.write("x"); fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                GameManager._launch_lock_fh = fh
            except OSError:
                raise TWError("another launcher already holds the launch lock (%s) -- "
                              "refusing to spawn a 2nd game" % lp)
        try:
            r = subprocess.run(["taskkill", "/F", "/IM", "Warhammer3.exe"],
                               capture_output=True, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            if "SUCCESS" in (r.stdout or ""):
                self._log("singleton: killed pre-existing Warhammer3.exe before spawn")
                time.sleep(4)
        except Exception as exc:
            self._log("singleton: taskkill skipped -> %r" % repr(exc)[:80])

    def _enforce_settings(self) -> None:
        prefs = os.path.join(os.environ.get("APPDATA", ""), "The Creative Assembly",
                             "Warhammer3", "scripts", "preferences.script.txt")
        profile = {
            "gfx_unit_size": "2",
            "gfx_vsync": "true",
            "gfx_aa": "0", "gfx_distortion": "false", "gfx_sharpening": "false",
            "gfx_resolution_scale": "0.5",
            "gfx_cloth_simulation": "false", "gfx_blood_effects": "false",
            "gfx_blood_enable_dismemberment": "false",
            "incremental_autosave_enabled": "false",
        }
        try:
            if not os.path.isfile(prefs):
                self._log("settings: prefs file absent (%s) -- skipping" % prefs)
                return
            with open(prefs, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            changed = []
            for i, ln in enumerate(lines):
                st = ln.strip()
                if not st or " " not in st:
                    continue
                key = st.split(None, 1)[0]
                newv = profile.get(key)
                if newv is None:
                    continue
                rest = st.split(None, 1)[1]
                semi = rest.find(";")
                comment = rest[semi:] if semi >= 0 else ""
                cur = (rest[:semi] if semi >= 0 else rest).strip()
                if cur != newv:
                    changed.append("%s:%s>%s" % (key, cur, newv))
                    lines[i] = "%s %s%s%s" % (key, newv, (" " if comment else ""), comment)
            if changed:
                with open(prefs, "w", encoding="utf-8", newline="") as f:
                    f.write("\r\n".join(lines) + "\r\n")
                self._log("settings: fast profile enforced (%s)" % ", ".join(changed[:8]))
            else:
                self._log("settings: fast profile already applied")
        except Exception as exc:
            self._log("settings: enforcement skipped -> %r" % repr(exc)[:80])

    def _spawn_game(self) -> None:
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= getattr(subprocess, name, 0)
        try:
            subprocess.Popen([self.EXE], cwd=self.GAME_DIR, creationflags=flags,
                             close_fds=True)
        except Exception as exc:
            raise TWError("failed to launch %s: %s" % (self.EXE, exc))
        self._log("spawned %s" % self.EXE)

    def _wait_window(self, timeout: float) -> float | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process_window_up():
                return time.time()
            time.sleep(2.0)
        return None

    def _wait_menu(self) -> bool:
        ready = self._wait_window(self.T_WINDOW)
        if ready is None:
            raise TWError("game window never appeared within %ds (exe launch failed? is "
                          "Steam running?)" % self.T_WINDOW)
        self._log("game booted (window+RAM ready)")
        while (time.time() - ready) < self.T_MENU_FLOOR:
            time.sleep(2.0)
        have_ref = os.path.isfile(self.REF_MENU)
        deadline = time.time() + self.T_MENU_STABLE
        prev = None
        settled = None
        toggle = 0
        while time.time() < deadline:
            self._restore()
            cur = self.screenshot("_menu_%d" % (toggle & 1))
            toggle += 1
            ok = self._frame_ok()
            match = self._menu_match(cur) if ok else None
            is_menu = ok and (not have_ref or (match is not None and match <= self.MENU_MATCH_THRESH))
            if is_menu:
                if prev is not None:
                    d = self._img_diff(prev, cur)
                    if d is not None and d < self.STABLE_THRESH:
                        settled = cur
                        self._log("main menu confirmed (golden match=%s, stable diff=%.2f)"
                                  % (("%.2f" % match) if match is not None else "n/a", d))
                        break
                prev = cur
                self._log("main-menu candidate (frame_ok=True match=%s); confirming stability"
                          % (("%.2f" % match) if match is not None else "n/a"))
            else:
                prev = None
                self._log("main-menu: awaiting real menu (frame_ok=%s match=%s stats=%s)"
                          % (ok, ("%.2f" % match) if match is not None else None, self._stats))
            time.sleep(2.0)
        if settled is None:
            self._fail_fast("menu_never_settled",
                            "main menu never rendered a real (golden-matching) frame within %ds "
                            "-- capture is likely grabbing a screensaver/overlay; see the labelled "
                            "frame" % self.T_MENU_STABLE)
        dst = os.path.join(self.SHOTS_DIR, "01_main_menu.png")
        try:
            shutil.copy2(settled, dst)
        except OSError as exc:
            self._log("WARN could not copy main-menu frame to %s: %s" % (dst, exc))
        self._log("main menu ready (rendered+stable; see D:\\twdata\\logs\\launcher\\launch_shots\\01_main_menu.png)")
        return True

    def _open_accordion(self, base_menu: str) -> bool:
        cfx, cfy = self.MENU_CAMPAIGN
        region = (0, 0, self.LEFTCOL_FR, 1)
        for attempt, dx in enumerate((0.0, 0.014, -0.012)):
            self._restore()
            self._log("frontend: click Campaign (accordion, ONCE) #%d @ frac(%.3f,%.3f)"
                      % (attempt, cfx + dx, cfy))
            time.sleep(2.5)
            self._restore()
            cur, ok = self._valid_shot("_acc_%d" % (attempt & 1))
            if not ok:
                self._log("accordion: post-click frame is not real game content (stats=%s); "
                          "re-clicking Campaign" % self._stats)
                continue
            d = self._img_diff(base_menu, cur, region=region)
            self._log("accordion left-col diff=%s (open if > %.2f)"
                      % (d, self.LEFTCOL_THRESH))
            if d is not None and d > self.LEFTCOL_THRESH:
                return True
        return False

    def _drive_frontend(self, plan: dict | None = None) -> str:
        if plan is None:
            plan = self.RACE_PLANS["nagarythe"]
        base_fac = self._drive_to_race_grid()
        return self._drive_race_and_lord(plan, base_fac)

    def _drive_to_race_grid(self) -> str:
        self._restore()
        self._client = None
        base_menu = self._scratch_shot("menu_baseline")
        for _ in range(6):
            if self._frame_ok() and (self._menu_match(base_menu) or 0) <= self.MENU_MATCH_THRESH:
                break
            self._log("menu_baseline: rejected non-menu frame (stats=%s); retrying" % self._stats)
            self._restore()
            time.sleep(1.2)
            base_menu = self._scratch_shot("menu_baseline")
        else:
            self._fail_fast("menu_baseline",
                            "could not capture a real main-menu baseline (capture keeps grabbing "
                            "a screensaver/overlay) -- see the labelled frame")
        self._client_rect()
        self._log("live client rect = %r (clicks mapped through this, not 2560x1440)"
                  % (self._client,))

        if not self._open_accordion(base_menu):
            self._fail_fast("after_campaign",
                            "accordion 'Campaign' never opened (left-col crop diff stayed "
                            "below %.2f after 3 single clicks -- the Campaign row was missed)"
                            % self.LEFTCOL_THRESH)
        after_campaign = self._scratch_shot("after_campaign")
        self._log("accordion open (New/Load visible); see after_campaign.png")

        settled = self._try_candidates(
            (self.MENU_NEW, self.MENU_NEW), after_campaign, per_timeout=18,
            total_timeout=self.T_TRANSITION, label="New->campaign-type", before_tag=None)
        if not settled:
            self._fail_fast("after_new",
                            "'New' never advanced to the campaign-type screen within %ds"
                            % self.T_TRANSITION)
        self._scratch_shot("after_new")
        base_type = self._wait_stable(self.T_SCREEN, min_wait=2.0, label="campaign-type",
                                      save_as="13_campaign_type")
        self._scratch_shot("campaign_type")
        self._log("reached campaign-type screen; see campaign_type.png")

        settled = self._try_candidates(
            self.IE_TILE, base_type, per_timeout=7, total_timeout=self.T_TRANSITION,
            label="ImmortalEmpires->faction-select", before_tag="20_ie")
        if not settled:
            self._fail_fast("faction_select",
                            "Immortal Empires card never advanced to faction select within "
                            "%ds -- recalibrate IE_TILE from campaign_type.png"
                            % self.T_TRANSITION)
        base_fac = self._wait_stable(self.T_SCREEN, min_wait=2.0, label="faction-select",
                                     save_as="30_faction_select")
        self._scratch_shot("faction_select")
        self._log("reached faction-select (Change Race grid); see faction_select.png")
        return base_fac

    def _drive_race_and_lord(self, plan: dict, base_fac: str) -> str:
        race = self._try_candidates(
            plan["tiles"], base_fac, per_timeout=8, total_timeout=self.T_TRANSITION,
            label="%s tile->lord-details" % plan["name"], before_tag="31_race")
        if not race:
            self._fail_fast("after_race",
                            "%s race tile never advanced to the lord screen within %ds -- "
                            "recalibrate the tile from faction_select.png"
                            % (plan["name"], self.T_TRANSITION))
        self._wait_stable(self.T_ANIMATED, min_wait=1.0, label="lord-details",
                          save_as="32_lord")
        self._scratch_shot("after_race")
        self._log("reached %s lord screen; see after_race.png" % plan["name"])

        lord = plan.get("lord")
        if lord is not None:
            self._restore()
            base_lord = self.screenshot("33_lord_before")
            lfx, lfy = lord
            if plan.get("lord_change"):
                picked = self._try_candidates(
                    (lord, (lfx - 0.010, lfy), (lfx + 0.010, lfy), (lfx, lfy - 0.015)),
                    base_lord, per_timeout=8, total_timeout=self.T_TRANSITION,
                    label="%s lord portrait" % plan["name"],
                    region=self.LORD_PANEL_REGION, thresh=self.LORD_CHANGE_THRESH)
                if not picked:
                    self._fail_fast(
                        "after_lord",
                        "%s lord portrait click never flipped the LORD panel (region diff "
                        "stayed under %.2f within %ds) -- recalibrate the portrait frac from "
                        "after_race.png" % (plan["name"], self.LORD_CHANGE_THRESH,
                                            self.T_TRANSITION))
            else:
                self._log("frontend: click lord portrait @ frac(%.3f,%.3f)" % (lfx, lfy))
                time.sleep(2.5)
        else:
            self._log("frontend: accepting DEFAULT lord for %s (lord=None)" % plan["name"])
        base_pre_start = self._wait_stable(self.T_ANIMATED, min_wait=1.0, label="pre-start",
                                           save_as="34_pre_start")
        self._scratch_shot("after_lord")

        self._restore()
        self.screenshot("40_start_before")
        loading = self._try_candidates(
            (self.START_CAMPAIGN,
             (self.START_CAMPAIGN[0] - 0.012, self.START_CAMPAIGN[1] + 0.008),
             (self.START_CAMPAIGN[0] + 0.012, self.START_CAMPAIGN[1] - 0.008)),
            base_pre_start, per_timeout=12, total_timeout=self.T_TRANSITION,
            label="Start->loading", before_tag=None)
        self._scratch_shot("after_start")
        if not loading:
            self._fail_fast("after_start",
                            "Start Campaign never began loading within %ds -- recalibrate "
                            "START_CAMPAIGN / verify the %s race+lord picks from after_race/"
                            "after_lord.png" % (self.T_TRANSITION, plan["name"]))
        self._scratch_shot("loading")
        self._log("loading screen confirmed; awaiting mod 'started' record")
        return loading

    def _bus_turn(self, bus) -> int:
        try:
            r = bus.send("eval", "return cm:model():turn_number()", timeout=8)
            return int(r.get("result"))
        except (TWError, TypeError, ValueError):
            return -1

    def _hud_ready(self, bus, path: str) -> bool:
        try:
            res = bus.send("find", path, timeout=8).get("result") or {}
        except TWError:
            return False
        if not res.get("found"):
            return False
        if res.get("visible") is False:
            return False
        if res.get("state") == "inactive":
            return False
        return True

    def _advance_to_map(self, campaign) -> None:
        bus = campaign.bus
        deadline = time.time() + self.T_MAP

        turn = -1
        while time.time() < deadline:
            turn = self._bus_turn(bus)
            if turn >= 1:
                break
            time.sleep(2.0)
        self._log("_advance_to_map: model up (turn=%d)" % turn)

        dismiss = (
            "custom_loading_screen|bottom_parent|button_continue",
            "events|button_set|accept_holder|button_accept",
            "advice_interface|text_parent|advice_text_panel|maximised_button_docker|button_close",
            "under_advisor_docker|scripted_tour_controls|button_close",
        )
        end_turn = "hud_campaign|faction_buttons_docker|end_turn_docker|button_end_turn"
        stable = 0
        while time.time() < deadline:
            campaign._handle_cutscene()
            for path in dismiss:
                try:
                    bus.send("click", path, timeout=6)
                except TWError:
                    pass
            if self._hud_ready(bus, end_turn):
                stable += 1
                if stable >= 2:
                    self._scratch_shot("map")
                    self._log("_advance_to_map: HUD ready (end-turn visible) -> playable; "
                              "see map.png")
                    return
            else:
                stable = 0
            time.sleep(2.0)
        self._fail_fast("zz_no_map",
                        "campaign HUD never became playable within %ds (turn=%d)"
                        % (self.T_MAP, turn))

    def _advance_past_continue(self) -> None:
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "bus"))
        try:
            from bus import Bus as _Bus
            bus = _Bus()
        except Exception as exc:
            self._log("WARN _advance_past_continue: no bus (%s) -- leaving screen as-is" % repr(exc)[:60])
            return
        dismiss = (
            "custom_loading_screen|bottom_parent|button_continue",
            "events|button_set|accept_holder|button_accept",
            "advice_interface|text_parent|advice_text_panel|maximised_button_docker|button_close",
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                roots = bus.send("roots", "", timeout=6) or {}
            except Exception:
                time.sleep(1.5); continue
            vis = [k.get("id") for k in (roots.get("kids") or []) if k.get("visible")]
            if "custom_loading_screen" not in vis and "hud_campaign" in vis:
                self._log("_advance_past_continue: loading screen cleared -> on the campaign HUD")
                return
            for p in dismiss:
                try:
                    bus.send("click", p, timeout=6)
                except Exception:
                    pass
            time.sleep(1.5)
        self._log("WARN _advance_past_continue: loading screen still up after 60s (roots=%s)" % vis)

    def launch_new(self, faction: str | None = None, race: str | None = None):
        if race is None:
            race = "nagarythe"
            if faction:
                fl = str(faction).lower()
                for sub, r in self._FACTION_HINT_RACE:
                    if sub in fl:
                        race = r
                        break
        plan = self.RACE_PLANS.get(race) or self.RACE_PLANS["nagarythe"]
        self._t0 = time.time()
        self._log("launch() begin (race=%s -> %s; faction hint=%s)"
                  % (race, plan["name"], faction))

        self._ensure_singleton()
        self._enforce_settings()
        self.ensure_prefs()
        self._restore()
        self._reset_command_file()
        out_offset = self._out_size()
        self._spawn_game()
        self._wait_menu()
        self._drive_frontend(plan)
        deadline = time.time() + self.T_LOAD
        while time.time() < deadline:
            if self._mod_started_since(out_offset):
                break
            time.sleep(1.0)
        else:
            self._fail_fast("zz_no_mod_started",
                            "loading screen appeared but the mod never started within %ds -- "
                            "the wrong campaign/faction may have launched; verify the race+lord "
                            "picks from after_race.png / after_lord.png" % self.T_LOAD)
        self._log("mod started (campaign loaded)")
        loaded = self._verify_loaded_faction(plan)
        self._advance_past_continue()
        self._log("launch_total=%.1f" % (time.time() - self._t0))
        return {"reached": True, "race": race, "plan": plan["name"], "faction": loaded,
                "seconds": round(time.time() - self._t0, 1)}

    def _verify_loaded_faction(self, plan):
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "bus"))
        try:
            from bus import Bus as _Bus
        except Exception as exc:
            self._fail_fast("zz_bus_import", "post-load guard: cannot import command bus: %s" % exc)
        bus = _Bus()
        guard = (plan or {}).get("guard")

        faction = None
        for _ in range(20):
            try:
                r = bus.send("eval", "cm:get_local_faction_name(true)", timeout=6) or {}
            except Exception:
                time.sleep(1.0); continue
            faction = r.get("result")
            if faction:
                break
            time.sleep(1.0)
        if not faction:
            self._fail_fast("zz_faction_unverified",
                            "post-load guard: the command bus never returned a faction name -- "
                            "cannot verify the loaded campaign.")
        if guard and guard not in str(faction):
            self._fail_fast("zz_wrong_campaign",
                            "WRONG CAMPAIGN: loaded faction %r does not match requested plan %r "
                            "(guard %r). The frontend lord/faction pick failed and the grid DEFAULT "
                            "loaded instead. See after_race.png / after_lord.png." % (faction, (plan or {}).get("name"), guard))

        cap = None
        for _ in range(12):
            try:
                cap = bus.send("focus", "xy 0 0", timeout=6) or {}
                break
            except Exception as exc:
                self._log("post-load guard: focus probe not ready (%s), retrying" % repr(exc)[:60])
                time.sleep(1.0)
        if cap is not None and str(cap.get("error")) == "unknown command":
            self._fail_fast("zz_stale_mod",
                            "STALE MOD: the loaded pack has no 'focus' handler (bus returned "
                            "'unknown command'). The deployed dist/tw.pack predates the required "
                            "mod handlers -- rebuild it with pack_multi.py before launching.")
        if cap is None:
            self._log("WARN post-load guard: bus never answered the 'focus' probe -- mod handlers "
                      "UNVERIFIED (faction verified, proceeding)")
        else:
            self._log("post-load guard OK: faction=%s (guard=%r), mod handlers present" % (faction, guard))
        return faction


def reach_campaign(race: str | None = None, faction: str | None = None, attempts: int = 3) -> dict:
    import subprocess
    last = None
    for i in range(max(1, attempts)):
        try:
            return GameManager().launch_new(race=race, faction=faction)
        except TWError as exc:
            last = exc
            print("launch attempt %d/%d failed: %s" % (i + 1, attempts, exc))
            if i + 1 < attempts:
                subprocess.run(["taskkill", "/F", "/IM", "Warhammer3.exe"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(6)
    raise last


def launch_lord(faction_key: str, culture_key: str | None = None) -> dict:
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "bus"))
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from bus import Bus
    import nav

    def _hw(bus, match):
        tr = bus.send("tree", "campaign_select_new 12 6000", timeout=20) or {}
        n = next((x for x in (tr.get("nodes") or [])
                  if x.get("visible") and x.get("x") is not None and match in str(x.get("id", ""))), None)
        if not n:
            return None
        nav.bus_input(bus, "launcher.py:campaign_select(%s)" % match, n.get("path"))
        time.sleep(1.3)
        return n.get("id")

    gm = GameManager()
    gm._t0 = time.time()
    gm.ensure_prefs()
    gm._restore()
    gm._reset_command_file()
    out_offset = gm._out_size()
    gm._spawn_game()
    gm._wait_menu()
    gm._drive_to_race_grid()
    bus = Bus()

    cults = []
    for _ in range(6):
        _hw(bus, "change_race")
        time.sleep(1.0)
        tr = bus.send("tree", "campaign_select_new 12 6000", timeout=20) or {}
        cults = [str(x.get("id", ""))[len("CcoCultureRecord"):] for x in (tr.get("nodes") or [])
                 if str(x.get("id", "")).startswith("CcoCultureRecord") and x.get("visible") and x.get("x") is not None]
        if cults:
            break
    if not cults:
        gm._fail_fast("zz_no_grid", "Change Race never opened the culture grid for %s" % faction_key)
    if not culture_key:
        toks = faction_key.split("_")
        sub = toks[2] if len(toks) > 2 else ""
        culture_key = next((c for c in cults if sub and ("_%s_" % sub) in ("_" + c + "_")), None)
    if not culture_key or not _hw(bus, "CcoCultureRecord" + culture_key):
        gm._fail_fast("zz_no_culture", "could not select culture for %s (culture=%s; grid had %d tiles)"
                      % (faction_key, culture_key, len(cults)))
    if not _hw(bus, "CcoFrontendFactionLeader" + faction_key):
        gm._fail_fast("zz_no_lord", "lord %s not found under culture %s" % (faction_key, culture_key))

    tr = bus.send("tree", "campaign_select_new 12 6000", timeout=20) or {}
    start = next((x for x in (tr.get("nodes") or [])
                  if "start" in str(x.get("id", "")).lower() and "button" in str(x.get("id", "")).lower()
                  and x.get("visible") and x.get("x") is not None), None)
    if start:
        nav.bus_input(bus, "launcher.py:campaign_start", start.get("path"))
    deadline = time.time() + gm.T_LOAD
    while time.time() < deadline:
        if gm._mod_started_since(out_offset):
            break
        time.sleep(1.0)
    else:
        gm._fail_fast("zz_no_mod_started_lord", "loading appeared but the mod never started for %s" % faction_key)
    loaded = gm._verify_loaded_faction({"guard": faction_key, "name": faction_key})
    return {"reached": True, "faction": loaded, "requested": faction_key, "culture": culture_key,
            "seconds": round(time.time() - gm._t0, 1)}


if __name__ == "__main__":
    import sys
    _args = sys.argv[1:]
    try:
        if _args and _args[0] == "--lord":
            _r = launch_lord(_args[1], culture_key=(_args[2] if len(_args) > 2 else None))
        else:
            _r = reach_campaign(race=(_args[0] if _args else None))
        print("REACHED:", _r)
    except TWError as e:
        print("FAILED:", e)
        sys.exit(1)
