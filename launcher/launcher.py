"""twapi.launcher -- GameManager: launch / prefs / frontend drive.

Moved text-identically from api.py in R1. The ONE deliberate edit: the
`campaign = Campaign()` construction inside launch_new uses a FUNCTION-LEVEL
`from api import Campaign` -- a documented ONE-WAY runtime edge (api.py is fully
imported long before launch_new runs); deliberately NOT a module-level import, so
twapi.launcher never imports api at load time (no import-time cycle).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

from errors import TWError
from screen import ScreenBridge

# bus command/output files. These MUST match the mod (tw.pack), which is compiled with these exact
# paths, so they are inlined here rather than imported. They currently point at the monolith's data
# dir -- the location the copied tw.pack reads/writes. When the `bus` repo rebuilds the mod with its
# own data dir, update these two constants to match.
CMD_PATH = "D:/totalwar_runner/data/commands.txt"      # client APPENDS command lines here
OUT_PATH = "D:/totalwar_runner/data/twcontrol.jsonl"   # mod APPENDS result JSON (incl. 'started') here

# ======================================================================================
# GameManager — launch / prefs / screenshot  (contract §1)
# ======================================================================================

# Absolute paths for launch. config.py is the one file allowed to hold them; fall back to
# its literal values so import/py_compile succeed even if config isn't importable.
try:
    import config as _config
    _GAME_DIR = _config.GAME_DIR
    _REPO = _config.REPO
except Exception as e:                  # pragma: no cover - fallback mirrors config.py
    _GAME_DIR = r"D:\SteamLibrary\steamapps\common\Total War WARHAMMER III"
    _REPO = os.path.dirname(os.path.abspath(__file__))
    sys.stderr.write("launcher: config import failed, using hardcoded paths -> %s\n" % repr(e)[:80])

# The DEFAULT campaign launch_new boots: High Elves / Alith Anar (the Shadow King) =
# "Nagarythe" on Immortal Empires -- the project's current focus. faction=None AND race=None
# resolve to this (RACE_PLANS["nagarythe"]). Other RACE_PLANS keys select other starts (incl.
# "cathay" = wh3_main_cth_the_western_provinces / Zhao Ming, "cathay_miao_ying" =
# wh3_main_cth_the_northern_provinces, and "beastmen_taurox" = the Slaughterhorn Tribe).
DEFAULT_FACTION = "wh2_main_hef_nagarythe"


class GameManager:
    """Launches WH3, drives the frontend to a live campaign (DEFAULT: High Elves / Alith Anar
    on Immortal Empires; other RACE_PLANS select other starts), waits for the command-bus mod,
    advances to a playable turn-1 map, and returns a Campaign attached to the running bus.
    Constructed with no required args.

    Design constraints honoured here:
      * The command-bus mod (twcontrol.lua) only runs INSIDE a campaign, so the FRONTEND
        has no bus: it is driven by absolute-pixel mouse clicks (ps/input.ps1), which are
        focus-independent, with a screenshot saved before/after each click for calibration.
        observed_ui_paths.json holds only in-campaign paths, so it provides no frontend
        coordinates -- the MENU_CAMPAIGN / IE_TILE / GRAND_CATHAY / ZHAO_MING / START_CAMPAIGN
        fractions below are the calibration surface.
      * Every milestone that HAS a real signal is OUTCOME-gated, not blind-slept. In the busless
        FRONTEND that signal is VISION: a click is confirmed by the screen actually CHANGING away
        from a stable baseline (ps/imgdiff.ps1), and screens are waited on until they finish
        rendering -- not on a fixed clock. In the campaign the signals are stronger: campaign-
        loaded is the mod's {"cmd":"started"} record in twcontrol.jsonl, and map-playable is the
        bus returning cm:model():turn_number()>=1 with the HUD end-turn button found+visible+active.
      * Reads + UI only. No cm mutator / god-mode call anywhere. The mod itself skips intro
        cutscenes on start(); launch only clicks UI and reads state.
    """

    GAME_DIR = _GAME_DIR
    REPO = _REPO
    EXE = os.path.join(_GAME_DIR, "Warhammer3.exe")
    PACK_NAME = "tw.pack"
    PACK_SRC = os.path.join(_REPO, "dist", PACK_NAME)
    PACK_DST = os.path.join(_GAME_DIR, "data", PACK_NAME)     # MOVIE pack: auto-loads here
    PS_DIR = os.path.join(_REPO, "ps")
    SHOTS_DIR = os.path.join(_REPO, "logs", "launch_shots")   # churn (temp diff frames)
    # Golden reference of the REAL WH3 main menu (a trustworthy collapsed-menu capture). The
    # menu-settle gate requires the live frame to MATCH this within MENU_MATCH_THRESH so the
    # baseline every frontend click is measured against can NEVER again be a screensaver frame
    # (attempt 1 accepted a solid-black overlay as the menu baseline -> whole run poisoned).
    REF_MENU = os.path.join(_REPO, "ref", "main_menu.png")
    # Labelled milestone + fail-fast frames land HERE so a downstream iteration can dial in the
    # still-estimated coords from real frames of screens attempt-1 never reached.
    SCRATCH_DIR = ("C:/Users/trist/AppData/Local/Temp/claude/D--totalwar-runner/"
                   "9cd42a34-d986-4a74-b5f7-7ed3e6114774/scratchpad")

    # Fallback render size ONLY (used if the live client rect can't be read). The game runs
    # borderless at ~2560x1440, but clicks are NEVER computed from this constant -- every click
    # is mapped through the LIVE client rect read from capture.ps1 (see _client_rect/_map_frac).
    SCREEN_W = 2560
    SCREEN_H = 1440

    # Timing budgets (seconds). Every post-click screen TRANSITION is FAIL-FAST (short): if the
    # expected diff does not appear in the budget we screenshot + RAISE rather than sit on a
    # stalled menu. The two long waits (T_LOAD/T_MAP) are entered ONLY after a real transition
    # (loading screen confirmed / mod 'started'), i.e. a legitimate load, never a stalled menu.
    T_WINDOW = 180              # max wait for the game window to exist
    T_MENU_FLOOR = 20           # min settle window-ready -> main menu before we look for it
    T_MENU_STABLE = 60          # max wait for the main-menu frame to finish rendering (stable)
    T_SCREEN = 20               # max wait for a frontend sub-screen to settle after a transition
    T_ANIMATED = 4              # settle budget for KNOWN-ANIMATED screens (lord-details, pre-start):
                                # their portrait/particle animation NEVER stops, so a stability settle
                                # can only ever EXHAUST -- the transition was already confirmed, so a
                                # short fixed wait replaces the ~20s each these used to burn.
    T_ACCORDION = 14            # FAIL-FAST: confirm the Campaign accordion opened (left-col crop)
    T_TRANSITION = 35           # FAIL-FAST: one full-frame screen transition after a click
    T_LOAD = 300                # mod 'started' after the loading screen is CONFIRMED (legit load)
    T_MAP = 300                 # campaign HUD playable after mod started (legit map load)

    # ---- screenshot-diff thresholds (see ps/imgdiff.ps1: mean abs per-channel diff, 0..255) --
    # An UNCHANGED animated screen (menu snow/aurora) downscales to a tiny delta; a real screen
    # transition is huge. These sit safely between the two regimes.
    STABLE_THRESH = 5.0         # consecutive frames within this => screen has finished rendering
    CHANGE_THRESH = 10.0        # full-frame frame-vs-baseline above this => the screen changed
    # Menu-settle gate: the live frame must match REF_MENU (golden real main menu) within this.
    # Measured (48x27 downscale, mean abs per-channel): the 4 real main-menu frames all diff
    # 0.0..3.1 vs the golden, while the black / mono-logo / intel-blue screensaver overlays diff
    # 62..78 -- so 20 cleanly accepts a real menu (incl. a hovered item / open accordion) and
    # rejects every screensaver frame with a >40-wide margin.
    MENU_MATCH_THRESH = 20.0
    # Accordion gate: clicking "Campaign" inserts New/Load and shifts Battle/Multiplayer/Options
    # DOWN ~2 rows -- the full-frame diff is blind (~0.46) but a LEFT-18% crop diff is 2.31 when it
    # opens, vs 0.74 for a hovered item and 0.30 for pure snow animation (measured on real frames
    # scratchpad/launch_fail_1.png collapsed vs _2.png expanded). 1.2 sits between hover (0.74) and
    # open (2.31); it is only trusted on a frame that already passed the real-game-content check.
    LEFTCOL_FR = 0.18
    LEFTCOL_THRESH = 1.2

    # ---- Frontend click plan (FRACTIONS of the LIVE client rect) ------------------------------
    # ALL VERIFIED LIVE on WH3 v8.1.1 @ 2560x1440 (client origin 0,0): each fraction below was
    # clicked and confirmed by re-capturing the frame it produced (labelled cal_*.png in
    # SCRATCH_DIR) during the calibration drive that reached a live turn-1 Zhao Ming campaign.
    # The "Campaign" label row centres at fracY 0.472; clicking it ONCE opens an in-place
    # New/Load accordion (New at fracY 0.507) -- it does NOT advance the screen.
    MENU_CAMPAIGN = (0.070, 0.472)    # VERIFIED: "Campaign" label (click EXACTLY once = open)
    MENU_NEW = (0.0525, 0.507)        # VERIFIED: "New" sub-item row (only present once open)
    # "New" -> New-Campaign screen ("Choose a campaign"): three campaign cards. The big right-hand
    # card is Immortal Empires; its centre is frac(0.735,0.471). Extra nearby points are fallbacks
    # for the FAIL-FAST candidate loop (all land inside the same card).
    IE_TILE = (   # New-Campaign screen -> "Immortal Empires" card
        (0.735, 0.471), (0.720, 0.450), (0.760, 0.500), (0.700, 0.420),
    )
    # Immortal Empires card -> a "Change Race" grid modal (The Empire default). Grand Cathay is
    # row 3, middle column; clicking it closes the modal onto the Cathay Lord-Details screen.
    GRAND_CATHAY = (0.388, 0.326)     # VERIFIED: Grand Cathay tile in the race grid
    # Cathay Lord-Details: a portrait grid top-left. Portrait #1 (default) = Miao Ying (Northern
    # Provinces); portrait #2 = Zhao Ming, the Iron Dragon (Western Provinces) -- the one we want.
    ZHAO_MING = (0.298, 0.180)        # VERIFIED: Zhao Ming portrait (#2), confirmed via LORD panel
    # Miao Ying = Cathay portrait #1, one measured column LEFT of the verified Zhao Ming (column
    # spacing 0.047 measured on disc_cathay_lords.png). VERIFIED live 2026-07-21: she is the
    # picker DEFAULT (portrait #1 pre-highlighted, LORD panel reads "Miao Ying, the Storm
    # Dragon" on entry), and clicking this frac raised her name tooltip with the LORD panel
    # unchanged -- an idempotent anchor click, so the plan does NOT diff-gate it (no change to
    # await); the post-load faction guard is the hard verification.
    MIAO_YING = (0.251, 0.180)        # VERIFIED: Miao Ying portrait (#1, the picker default)
    # Taurox = Beastmen picker portrait row 2, col 1 (same measured portrait grid: col-1
    # x=0.251, row-2 y=0.286 on disc_b_bst_lords.png). VERIFIED live 2026-07-21: clicking this
    # frac flipped the LORD panel from Khazrak (the Beastmen default) to "Taurox the Brass
    # Bull / Slaughterhorn Tribe" (disc_c_after_taurox.png); the portrait is NOT DLC-locked.
    # NON-default, so his plan diff-gates the click on LORD_PANEL_REGION (lord_change=True).
    TAUROX = (0.251, 0.286)           # VERIFIED: Taurox portrait (row 2, col 1), NOT the default
    # The left "LORD / <name>" box crop -- static UI that repaints ONLY when the selected lord
    # changes, unlike the full frame (the selected lord's 3D scene ANIMATES: full-frame
    # no-click noise measured 5.44 vs 10.77 on a real selection change -- useless separation,
    # and the same animation is why the lord-details settle-wait routinely exhausts). Region
    # diffs measured live on the Beastmen picker: no-click noise 0.85, Khazrak->Taurox change
    # 14.09 -- so 5.0 sits ~6x above noise with ~3x margin below the real signal.
    LORD_PANEL_REGION = (0.02, 0.38, 0.165, 0.502)
    LORD_CHANGE_THRESH = 5.0
    START_CAMPAIGN = (0.499, 0.954)   # VERIFIED: bottom-CENTRE "Start Campaign" button
    # Beastmen tile in the SAME "Change Race" grid. Beastmen are a PURE HORDE (they build inside the
    # mobile army), so ANY Beastmen lord exercises the horde_details()/force_slot/force_pooled code
    # paths -- Cathay never does. Calibrated live from the real race grid (bst_grid.png): the grid
    # is a 3-col alphabetical list; Beastmen is the TOP-LEFT tile (row 1, col 1). Verified against
    # Grand Cathay (row 3, col 2) sitting at exactly its known frac (0.388, 0.326) in the same frame.
    BEASTMEN = (0.210, 0.186)         # VERIFIED: Beastmen tile (top-left) in the race grid
    # Four more tiles in the SAME 3-col alphabetical "Change Race" grid (bst_grid.png). The grid
    # reads left->right, top->bottom, so column centres are frac x {0.210, 0.388, 0.566} (spacing
    # 0.178, from Beastmen col1 & Grand Cathay col2) and row centres step 0.070 in frac y (from
    # Beastmen row1 0.186 & Grand Cathay row3 0.326). Each tile below is that grid model's cell,
    # cross-checked against its label position in bst_grid.png (both known fracs land dead-on):
    EMPIRE = (0.210, 0.326)           # row 3, col 1 (The Empire -- default lord Karl Franz)
    DWARFS = (0.566, 0.256)           # row 2, col 3 (Dwarfs   -- default lord Thorgrim)
    GREENSKINS = (0.566, 0.326)       # row 3, col 3 (Greenskins -- default lord Grimgor)
    NORSCA = (0.388, 0.466)           # row 5, col 2 (Norsca   -- default lord Wulfrik)
    # Vampire Counts tile in the SAME 3-col alphabetical "Change Race" grid: col 1, BOTTOM row
    # (row 8 -- Vampire Counts is alphabetically 22nd). Empirically located on 30_faction_select.png
    # (frac 0.210,0.685) and corroborated by the proven-live legacy VC coord (0.204,0.684); the tile
    # advanced to the lord screen on the FIRST candidate in discovery (disc_vc_lords, live 2026-07-21).
    VAMPIRE_COUNTS = (0.210, 0.685)   # VERIFIED: Vampire Counts tile (col 1, bottom row)
    # Heinrich Kemmler = VC picker portrait row 1, col 2 (same portrait grid: col-2 x=0.297,
    # row-1 y=0.184 on vc_lord_grid.png). VERIFIED live 2026-07-21: the VC DEFAULT lord is
    # Mannfred von Carstein (portrait #1 pre-highlighted, "The Drakenhof Conclave"); clicking this
    # frac flipped the LORD panel + tooltip to "Heinrich Kemmler / The Barrow Legion" (vc_p2_r1c2.png).
    # NON-default, so his plan diff-gates the click on LORD_PANEL_REGION (lord_change=True).
    KEMMLER = (0.297, 0.184)          # VERIFIED: Heinrich Kemmler portrait (row 1, col 2), NOT default
    # High Elves tile in the SAME 3-col alphabetical "Change Race" grid. HE is alphabetically
    # 10th (index 9) => row 4, col 1: col-1 x=0.210, row centres step 0.070 from Beastmen row1
    # 0.186 so row4 y=0.186+3*0.070=0.396. VERIFIED live 2026-07-24 on A_race_grid.png (the grid
    # label under this tile reads "High Elves") and by click: frac(0.210,0.396) advanced to the
    # High Elves Lord-Details screen (HIGH ELVES header, default lord Tyrion).
    HIGH_ELVES = (0.210, 0.396)       # VERIFIED: High Elves tile (row 4, col 1) in the race grid
    # Alith Anar (the Shadow King = Nagarythe) = HE picker portrait row 2, col 1 -- the SAME
    # portrait-grid cell as Taurox (col-1 x=0.251, row-2 y=0.286). The HE DEFAULT is Tyrion
    # (portrait #1). VERIFIED live 2026-07-24: clicking this frac flipped the LORD panel from
    # "Tyrion / Eataine" to "Alith Anar / Nagarythe" (cell_r2_c0_x0.251.png; tooltip + LORD box
    # both confirm), LORD_PANEL_REGION diff 19.15 (>> LORD_CHANGE_THRESH 5.0). NON-default, so
    # his plan diff-gates the click (lord_change=True).
    ALITH_ANAR = (0.251, 0.286)       # VERIFIED: Alith Anar portrait (row 2, col 1), NOT the default
    # Slaanesh (mono-god) tile in the SAME 3-col alphabetical "Change Race" grid. MEASURED from
    # logs/launch_shots/30_faction_select.png (the full grid, The Empire default): the grid reads
    # Beastmen/Bretonnia/ChaosDwarfs (row1) ... Ogre Kingdoms/Skaven/SLAANESH (row6) ...
    # VampireCounts/WarriorsOfChaos/WoodElves (row8). So Slaanesh = ROW 6, COL 3. Col-3 x=0.566
    # (verified for Greenskins/Dwarfs); the real per-row step is (VC 0.685 - Beastmen 0.186)/7 =
    # 0.0713, so row6 y = 0.186 + 5*0.0713 = 0.542 (the tile centre in that screenshot lands here).
    SLAANESH = (0.566, 0.542)         # MEASURED: Slaanesh tile (row 6, col 3) in the race grid
    # The Masque of Slaanesh portrait -- VERIFIED live 2026-07-25 from the Slaanesh lord grid
    # (slaanesh_lords.png / sla_p3.png). The 3 Slaanesh LLs are portrait #1 N'Kari (DEFAULT), #2
    # Dechala the Denied One, #3 THE MASQUE (The Accursed Troupe). Same portrait grid as the others
    # (row1 y~0.175, col spacing 0.047 -> col3 x~0.345). Clicking this frac flipped the LORD panel to
    # "The Masque of Slaanesh / The Accursed Troupe" (tooltip + LORD box confirm; faction effects
    # read "Has access to the Eternal Dance"). NON-default -> the plan diff-gates it (lord_change).
    THE_MASQUE = (0.345, 0.175)       # VERIFIED: The Masque portrait (row 1, col 3), NOT the default

    # ---- Race plans: the ONLY per-race differences in the frontend path -----------------------
    # The path menu->New->Immortal Empires->[race tile]->[lord]->Start is identical for every race;
    # only the "Change Race" grid tile and the lord portrait differ. Each plan:
    #   tiles:  candidate frac(x,y) points inside the race tile (FAIL-FAST candidate loop)
    #   lord:   frac(x,y) of the legendary-lord portrait to click, or None to ACCEPT THE DEFAULT
    #   lord_change (optional): True => the lord is NOT the picker default, so the click MUST
    #           visibly flip the selection: it is diff-gated on LORD_PANEL_REGION (the static
    #           "LORD / <name>" box) crossing LORD_CHANGE_THRESH, with in-portrait fallback
    #           candidates and a fail-fast -- the same capture+settle+diff idiom as every other
    #           frontend step. Absent/False => the proven blind anchor click (default lords /
    #           Zhao Ming's original behavior, unchanged).
    #   guard:  substring that MUST appear in cm:get_local_faction_name(true) after the load
    #   name:   human label for logs / the wrong-faction fail-fast
    RACE_PLANS = {
        # Grand Cathay / Zhao Ming (the Iron Dragon = Western Provinces) -- the proven default. The
        # DEFAULT Cathay lord is Miao Ying (Northern Provinces, a DIFFERENT start), so Cathay MUST
        # click Zhao Ming (portrait #2).
        "cathay": {
            "tiles": (GRAND_CATHAY,
                      (GRAND_CATHAY[0], GRAND_CATHAY[1] - 0.026),
                      (GRAND_CATHAY[0] - 0.013, GRAND_CATHAY[1])),
            "lord": ZHAO_MING,
            "guard": "western_provinces",
            "name": "Grand Cathay / Zhao Ming",
        },
        # Grand Cathay / Miao Ying (the Storm Dragon = Northern Provinces). She IS the picker
        # DEFAULT (portrait #1 pre-highlighted on entry -- the reason the plain "cathay" plan must
        # click Zhao Ming), so this plan's lord click is an IDEMPOTENT ANCHOR: clicking the already-
        # selected portrait re-selects it (verified live: LORD panel unchanged, her name tooltip
        # raised at this frac), a mis-click into dead space leaves her selected anyway, and the
        # post-load guard (northern_provinces) catches every remaining wrong-lord path. Same race
        # tiles as "cathay" (identical grid cell).
        "cathay_miao_ying": {
            "tiles": (GRAND_CATHAY,
                      (GRAND_CATHAY[0], GRAND_CATHAY[1] - 0.026),
                      (GRAND_CATHAY[0] - 0.013, GRAND_CATHAY[1])),
            "lord": MIAO_YING,
            "guard": "northern_provinces",
            "name": "Grand Cathay / Miao Ying",
        },
        # Beastmen (horde). EVERY Beastmen legendary lord is a horde faction (subculture keys carry
        # "bst"), so we ACCEPT THE DEFAULT lord (lord=None) -- no portrait mis-click risk, and the
        # horde code runs FOR REAL regardless of which Beastmen LL is selected.
        "beastmen": {
            "tiles": (BEASTMEN,
                      (BEASTMEN[0], BEASTMEN[1] - 0.026),
                      (BEASTMEN[0] - 0.013, BEASTMEN[1]),
                      (BEASTMEN[0] + 0.013, BEASTMEN[1])),
            "lord": None,
            "guard": "bst",
            "name": "Beastmen (horde)",
        },
        # Beastmen / Taurox (the Brass Bull = Slaughterhorn Tribe, a Naggaroth start). The
        # Beastmen default is Khazrak, so this plan MUST click Taurox's portrait (row 2, col 1)
        # and -- being a real selection change -- diff-gates it (lord_change=True): the LORD
        # panel crop must cross LORD_CHANGE_THRESH or the launch fail-fasts. Same race tiles as
        # "beastmen". Guard "taurox" matches the faction key regardless of its dlc prefix.
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
        # The four races below ACCEPT THE DEFAULT lord (lord=None) exactly like Beastmen -- no
        # portrait hunt (the mechanically-rich lords are a later job). Each guard is the standard
        # WH3 faction-key subculture code that appears in cm:get_local_faction_name(true):
        # Empire=wh_main_emp_empire, Norsca=wh_dlc08_nor_norsca, Greenskins=wh_main_grn_greenskins,
        # Dwarfs=wh_main_dwf_dwarfs. Tile fallbacks mirror Beastmen's (primary, then up/left/right,
        # all inside the same tile) so a slightly-off primary still lands via the fail-fast loop.
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
        # Vampire Counts / Heinrich Kemmler (The Barrow Legion, a Grey Mountains start). The VC
        # DEFAULT lord is Mannfred von Carstein (The Drakenhof Conclave), so this plan MUST click
        # Kemmler's portrait (row 1, col 2) and -- being a real selection change -- diff-gates it
        # (lord_change=True): the static LORD-panel crop must cross LORD_CHANGE_THRESH or the launch
        # fail-fasts, exactly like beastmen_taurox. Guard "barrow_legion" matches the recorded
        # faction key wh2_dlc11_vmp_the_barrow_legion regardless of its dlc prefix.
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
        # High Elves / Alith Anar (the Shadow King = Nagarythe, the Shadowlands start -- the
        # project's current focus). The HE DEFAULT lord is Tyrion (Eataine), so this plan MUST
        # click Alith Anar's portrait (row 2, col 1) and -- being a real selection change --
        # diff-gates it (lord_change=True): the static LORD-panel crop must cross
        # LORD_CHANGE_THRESH or the launch fail-fasts, exactly like beastmen_taurox. Guard
        # "nagarythe" matches the recorded faction key wh2_main_hef_nagarythe.
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
        # Slaanesh / The Masque of Slaanesh (Seducers of Slaanesh, faction key
        # wh3_dlc27_sla_masque_of_slaanesh). The Slaanesh DEFAULT LL is N'Kari, so this plan MUST
        # click The Masque's portrait and diff-gates it (lord_change=True). Guard "masque" matches
        # the faction key. Race tile = SLAANESH (row 6, col 3), lord portrait = THE_MASQUE.
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

    # faction= backward-compat hints: a substring in the caller's faction string selects a race
    # (checked in order; first match wins), mirroring the original "bst" -> Beastmen mapping.
    # Lord-specific keys are checked BEFORE their generic race substring so e.g. a recorded
    # wh3_main_cth_the_northern_provinces resolves to the Miao Ying start, not plain cathay.
    _FACTION_HINT_RACE = (
        ("northern_provinces", "cathay_miao_ying"),
        ("taurox", "beastmen_taurox"),
        ("barrow_legion", "vmp_barrow_legion"),
        # High Elves / Alith Anar: both the faction key (wh2_main_hef_nagarythe) and the
        # leader subtype (..._alith_anar) resolve to the nagarythe plan, ordered before any
        # generic hef substring.
        ("nagarythe", "nagarythe"), ("alith_anar", "nagarythe"),
        ("masque", "slaanesh_masque"),
        ("bst", "beastmen"), ("emp", "empire"), ("nor", "norsca"),
        ("grn", "greenskins"), ("dwf", "dwarfs"),
    )

    def __init__(self) -> None:
        """Initialise with no required args; creates the ScreenBridge lazily-used later."""
        self._t0 = None
        # PowerShell/vision primitives live on a ScreenBridge (twapi.screen); the delegating
        # methods + the _client/_stats properties below keep this class's surface identical
        # for launcher code and external callers.
        self._screen = ScreenBridge(log=self._log)

    # Live CLIENT rect + last-frame content stats are OWNED by the ScreenBridge; these
    # properties keep the historical `self._client` / `self._stats` attribute paths (read
    # AND write -- launch code resets self._client to force a live re-read) working.
    @property
    def _client(self):
        """Cached live client rect (origin_x, origin_y, w, h) held on the ScreenBridge."""
        return self._screen._client

    @_client.setter
    def _client(self, value):
        self._screen._client = value

    @property
    def _stats(self):
        """Last captured frame's content stats held on the ScreenBridge."""
        return self._screen._stats

    @_stats.setter
    def _stats(self, value):
        self._screen._stats = value

    # ---- logging (mirrors the [startup +Xs] format of the launch logs) ---------------
    def _log(self, msg: str) -> None:
        """Print `msg` with the [startup +Xs] elapsed-time stamp (0.0 before launch_new)."""
        t = 0.0 if self._t0 is None else (time.time() - self._t0)
        print("[startup +%5.1fs] %s" % (t, msg), flush=True)

    # ---- powershell bridges (bodies moved to twapi.screen.ScreenBridge; identical
    # signatures delegate so every caller sees no difference) --------------------------
    def _run_ps(self, script: str, *args, **kw) -> subprocess.CompletedProcess | None:
        """Run one of the ps/*.ps1 helper scripts, returning the CompletedProcess (or None).

        Args:
            script: Script filename inside ps/ (e.g. "imgdiff.ps1").
            *args: Positional arguments passed through to the script.
            **kw: Keyword options for the bridge (e.g. timeout).

        Returns:
            The subprocess.CompletedProcess, or None if the run failed.
        """
        return self._screen._run_ps(script, *args, **kw)

    def _input(self, action: str, a=None, b=None, d=None):
        """input.ps1: mouse (absolute-pixel, focus-independent) or key (needs foreground).

        Args:
            action: input.ps1 action name (e.g. a mouse or key action).
            a: First action argument (typically an absolute x pixel), or None.
            b: Second action argument (typically an absolute y pixel), or None.
            d: Third action argument (action-specific, e.g. a key/delay), or None.

        Returns:
            Whatever ScreenBridge._input returns for the action.
        """
        return self._screen._input(action, a, b, d)

    def _client_rect(self):
        """Live CLIENT rect (origin_x, origin_y, w, h) in SCREEN pixels (ScreenBridge).

        Returns:
            The (origin_x, origin_y, w, h) tuple of the live client rect.
        """
        return self._screen._client_rect()

    def _map_frac(self, fx: float, fy: float):
        """Map a client-fraction (fx,fy) to an absolute SCREEN pixel via the live client rect.

        Args:
            fx: Horizontal client fraction in [0, 1].
            fy: Vertical client fraction in [0, 1].

        Returns:
            The absolute SCREEN pixel (x, y) pair from the ScreenBridge.
        """
        return self._screen._map_frac(fx, fy)

    def _click_frac(self, fx: float, fy: float):
        """Click the absolute SCREEN pixel mapped from client-fraction (fx, fy).

        Args:
            fx: Horizontal client fraction in [0, 1].
            fy: Vertical client fraction in [0, 1].

        Returns:
            Whatever ScreenBridge._click_frac returns.
        """
        return self._screen._click_frac(fx, fy)

    def _restore(self):
        """Un-minimise + foreground the game window (restore.ps1, which also disables the
        screensaver + display-sleep) before UI operations.

        Returns:
            Whatever ScreenBridge._restore returns.
        """
        return self._screen._restore()

    def _nudge(self):
        """Move the cursor (NO click) to a neutral empty spot below the menu (ScreenBridge).

        Returns:
            Whatever ScreenBridge._nudge returns.
        """
        return self._screen._nudge()

    def screenshot(self, name: str) -> str:
        """Capture the game window to logs/launch_shots/<name>.png; return the path.

        Args:
            name: Basename (without extension) for the PNG inside SHOTS_DIR.

        Returns:
            The absolute path of the written PNG.
        """
        path = os.path.join(self.SHOTS_DIR, name + ".png")
        return self._screenshot_to(path)

    def _screenshot_to(self, path: str) -> str:
        """Capture a screenshot to `path` (capture.ps1), caching the live client rect +
        frame stats on the ScreenBridge (surfaced here via the _client/_stats properties).

        Args:
            path: Destination PNG path.

        Returns:
            The destination path.
        """
        return self._screen._screenshot_to(path)

    def _frame_ok(self) -> bool:
        """True if the LAST captured frame's content stats fall inside the real-game-content
        bounds (ScreenBridge).

        Returns:
            True if the last frame is real game content, else False.
        """
        return self._screen._frame_ok()

    def _valid_shot(self, name: str, tries: int = 6, settle: float = 1.2) -> tuple[str, bool]:
        """Capture until the frame passes the real-game-content check, restoring+foregrounding
        between tries so the game repaints over any screensaver overlay (and any synthetic input
        we later send dismisses a running screensaver outright). Returns (path, ok); ok=False if
        no valid frame within `tries` -- the caller fail-fasts on that.

        Args:
            name: Basename for the PNG inside SHOTS_DIR.
            tries: Maximum capture attempts before giving up.
            settle: Seconds slept between attempts.

        Returns:
            (path, ok): path of the last captured frame, and whether it passed
            the real-game-content check.
        """
        path = self.screenshot(name)
        for i in range(tries):
            if self._frame_ok():
                return path, True
            self._log("capture %s: rejected non-game/screensaver frame #%d (stats=%s); "
                      "restoring + retrying" % (name, i, self._stats))
            self._restore()
            self._nudge()                              # dismiss any active screensaver
            time.sleep(settle)
            path = self.screenshot(name)
        return path, self._frame_ok()

    def _menu_match(self, path: str) -> float | None:
        """Full-frame diff of `path` vs the golden main-menu reference (or None if unavailable).

        Args:
            path: PNG path of the live frame to compare.

        Returns:
            Mean abs per-channel diff (0..255) vs REF_MENU, or None if the
            golden reference is missing or the diff could not be computed.
        """
        if not os.path.isfile(self.REF_MENU):
            return None
        return self._img_diff(self.REF_MENU, path)

    def _scratch_shot(self, label: str) -> str:
        """Save a labelled milestone / fail-fast frame into SCRATCH_DIR for downstream coord
        calibration (menu_baseline, after_campaign, campaign_type, faction_select, ...).

        Args:
            label: Basename (without extension) for the PNG inside SCRATCH_DIR.

        Returns:
            The written PNG path.
        """
        return self._screenshot_to(os.path.join(self.SCRATCH_DIR, label + ".png"))

    def _fail_fast(self, label: str, msg: str):
        """Save a labelled frame to SCRATCH_DIR and RAISE immediately -- used at every screen
        transition so a stalled menu aborts in seconds (with a real frame to calibrate from)
        instead of burning the long load/map budgets.

        Args:
            label: Frame label written to SCRATCH_DIR.
            msg: Human-readable failure message.

        Returns:
            Never returns normally.

        Raises:
            TWError: Always, with `msg` and the labelled frame path.
        """
        self._restore()
        path = self._scratch_shot(label)
        self._log("FAIL-FAST @ %s: %s (frame: %s)" % (label, msg, path))
        raise TWError("%s (labelled frame: %s)" % (msg, path))

    # ---- vision: screenshot diffing to OUTCOME-gate the (bus-less) frontend ------------
    def _img_diff(self, path_a: str, path_b: str,
                  region: tuple[float, float, float, float] | None = None) -> float | None:
        """Mean abs per-channel difference (0..255) of two frames via ps/imgdiff.ps1, or None
        if it could not be computed. Used to tell "the screen advanced" from "the click missed
        and we are still on the same animated screen" without trusting a fixed sleep. `region`,
        when given as (fl,ft,fr,fb) fractions, crops the SAME sub-rectangle of both frames first
        (used for the left-column accordion gate the full-frame diff is blind to).

        Args:
            path_a: PNG path of the first frame.
            path_b: PNG path of the second frame.
            region: Optional (fl, ft, fr, fb) crop fractions applied to BOTH
                frames before diffing, or None for full-frame.

        Returns:
            The mean abs per-channel difference (0..255), or None if imgdiff
            failed or reported an unreadable frame.
        """
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
                continue  # intentional: tolerant parse of imgdiff stdout -> take last float, skip non-numeric noise
        if val is None or val < 0:          # imgdiff prints -1 for an unreadable frame
            return None
        return val

    def _wait_stable(self, timeout: float, min_wait: float = 0.0, label: str = "screen",
                     save_as: str | None = None) -> str:
        """Poll screenshots until two consecutive frames are near-identical (STABLE_THRESH),
        i.e. the screen has finished rendering and is only animating. A frame that fails the
        real-game-content check (screensaver overlay) is NEVER treated as settled -- it is
        skipped so the settle baseline can only ever be a genuine game frame. Returns the path
        of the settled frame (copied to logs/launch_shots/<save_as>.png when given). Degrades to
        the last VALID frame on timeout so a broken diff never hangs launch.

        Args:
            timeout: Maximum seconds to wait for the screen to settle.
            min_wait: Minimum elapsed seconds before a stable pair counts.
            label: Human label used in log lines.
            save_as: Optional basename to copy the settled frame to inside
                SHOTS_DIR, or None to skip the copy.

        Returns:
            The path of the settled frame (or the last VALID frame on
            timeout; the SHOTS_DIR copy when `save_as` succeeded).
        """
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
            cur = self.screenshot("_stable_%d" % toggle)   # alternate files: never diff vs self
            toggle ^= 1
            cur_ok = self._frame_ok()
            elapsed = time.time() - start
            d = self._img_diff(last, cur)
            if cur_ok:
                result = cur                               # only ever surface a valid frame
            # Settled only when BOTH frames are real game content AND mutually stable.
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
        """Poll fresh frames until one differs from `baseline_path` by > `thresh` (full-frame
        CHANGE_THRESH by default; or a crop `region` above `thresh`). Returns the changed
        frame's path on success, or None on timeout. This is the FAIL-FAST primitive: callers
        give it a SHORT timeout and abort (screenshot+raise) when it returns None.

        Args:
            baseline_path: PNG path of the pre-click baseline frame.
            timeout: Maximum seconds to poll for the transition.
            label: Human label used in log lines.
            region: Optional (fl, ft, fr, fb) crop fractions for the diff.
            thresh: Diff threshold; None uses CHANGE_THRESH.

        Returns:
            The changed frame's path, or None on timeout.
        """
        thr = self.CHANGE_THRESH if thresh is None else thresh
        deadline = time.time() + timeout
        toggle = 0
        while time.time() < deadline:
            self._restore()
            cur = self.screenshot("_await_%d" % (toggle & 1))
            toggle += 1
            # A flip from the game to a screensaver overlay is a HUGE diff but is NOT a screen
            # transition -- only trust the diff on a frame that is real game content.
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
        """Click each candidate frac(x,y) in turn, waiting a SHORT `per_timeout` after each for
        a transition away from `baseline`; stop as soon as one lands (returns its frame path).
        Bounded by `total_timeout` overall so a screen whose coords are still estimates aborts
        fast rather than looping. Saves a before-shot per attempt. Returns None if none moved.

        Args:
            candidates: Sequence of (fx, fy) client-fraction click points.
            baseline: PNG path of the baseline frame each attempt diffs against.
            per_timeout: Seconds allowed per candidate for the transition.
            total_timeout: Overall seconds budget across all candidates.
            label: Human label used in log lines.
            region: Optional (fl, ft, fr, fb) crop fractions for the diff.
            thresh: Diff threshold; None uses CHANGE_THRESH.
            before_tag: Optional basename prefix for a per-attempt before-shot
                in SHOTS_DIR, or None to skip.

        Returns:
            The transitioned frame's path from the first candidate that
            landed, or None if none produced a transition.
        """
        deadline = time.time() + total_timeout
        for i, (fx, fy) in enumerate(candidates):
            if time.time() >= deadline:
                break
            self._restore()
            if before_tag:
                self.screenshot("%s_%d_before" % (before_tag, i))
            self._log("frontend: click %s #%d @ frac(%.3f,%.3f)" % (label, i, fx, fy))
            self._click_frac(fx, fy)
            budget = min(per_timeout, max(1.0, deadline - time.time()))
            settled = self._await_transition(baseline, budget, label, region=region,
                                              thresh=thresh)
            if settled:
                return settled
        return None

    # ---- prefs: exe present + command-bus mod pack installed -------------------------
    def ensure_prefs(self) -> None:
        """Verify the WH3 executable exists and the command-bus mod pack is installed where
        the game auto-loads it (GAME_DIR\\data, a MOVIE pack). Copies/refreshes it from
        dist\\ if missing or stale. Raises TWError with a clear message on any problem.

        Returns:
            None.

        Raises:
            TWError: If the exe is missing, the source pack is missing, or the
                pack cannot be copied (e.g. the game holds it open).
        """
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

    # ---- process / result-file gating helpers ----------------------------------------
    def _process_window_up(self) -> bool:
        """True once a Warhammer3 process owns a main window (window+RAM ready).

        Returns:
            True if a Warhammer3 process with a main window exists, else False
            (including on any PowerShell failure/timeout).
        """
        cmd = ["powershell", "-NoProfile", "-Command",
               "$p=Get-Process Warhammer3 -ErrorAction SilentlyContinue | "
               "Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; "
               "if ($p) { 'up' } else { 'down' }"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            return r.returncode == 0 and "up" in (r.stdout or "")
        except Exception as exc:                     # noqa: BLE001
            self._log("WARN _game_up process check failed: %s" % repr(exc)[:80])
            return False

    def _out_size(self) -> int:
        """Current byte size of the mod's result log (OUT_PATH), or 0 if unreadable."""
        try:
            return os.path.getsize(OUT_PATH)
        except OSError:
            return 0  # intentional: OUT_PATH not yet created pre-launch -> size 0 (expected; not logged)

    def _mod_started_since(self, offset: int) -> bool:
        """True if a {"cmd":"started"} record was appended to twcontrol.jsonl past `offset`.

        Args:
            offset: Byte offset into OUT_PATH to start scanning from (the
                file size captured before launch).

        Returns:
            True if a {"cmd": "started"} record appears past `offset`, else
            False (including when the file is unreadable).
        """
        try:
            with open(OUT_PATH, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError:
            return False  # intentional: OUT_PATH not yet present -> not started (expected during boot poll)
        for raw in data.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue  # intentional: skip partial/non-JSON result line, per-poll parse (no per-line log)
            if obj.get("cmd") == "started":
                return True
        return False

    def _reset_command_file(self) -> None:
        """Truncate commands.txt before a NEW campaign. A fresh campaign restores the mod's
        last_seq to 0 (verified: the 'started' record logs last_seq=0), so any stale lines
        here would be REPLAYED on the first poll -- clear them so the bus starts clean.

        Returns:
            None. A failed truncate only logs a warning.
        """
        try:
            with open(CMD_PATH, "w", encoding="utf-8") as f:
                f.write("")
            self._log("cleared command file %s" % CMD_PATH)
        except OSError as exc:
            self._log("WARN could not clear %s: %s" % (CMD_PATH, exc))

    # ---- singleton ownership ---------------------------------------------------------
    def _ensure_singleton(self) -> None:
        """SINGLETON GUARD -- the launcher is the SOLE owner of the game process. Called FIRST in a
        launch, before prefs/spawn:
          (1) take a cross-process launch LOCK so two launcher runs can't race into two games;
          (2) KILL any Warhammer3.exe already running, so a launch can NEVER create a duplicate
              instance. Two instances is the bug that made attempt 1 grab the wrong window ('menu
              never settled') AND blocks the mod-pack reinstall (the running game holds the pack open).
        Idempotent; the lock is held for the launcher process lifetime (freed on exit)."""
        import tempfile
        import msvcrt
        # CLASS-level lock (shared across GameManager instances in this process) so reach_campaign's
        # retry -- which builds a NEW GameManager per attempt -- does NOT deadlock on a lock the prior
        # instance still holds. Acquire once per process; concurrent OTHER processes still can't.
        if getattr(GameManager, "_launch_lock_fh", None) is None:
            lp = os.path.join(tempfile.gettempdir(), "tw_launcher.lock")
            try:
                fh = open(lp, "a+")
                fh.seek(0)
                if not fh.read(1):
                    fh.write("x"); fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)   # exclusive, non-blocking
                GameManager._launch_lock_fh = fh
            except OSError:
                raise TWError("another launcher already holds the launch lock (%s) -- "
                              "refusing to spawn a 2nd game" % lp)
        try:
            r = subprocess.run(["taskkill", "/F", "/IM", "Warhammer3.exe"],
                               capture_output=True, text=True)
            if "SUCCESS" in (r.stdout or ""):
                self._log("singleton: killed pre-existing Warhammer3.exe before spawn")
                time.sleep(4)     # let the OS release the window handle + the pack file lock
        except Exception as exc:                     # noqa: BLE001
            self._log("singleton: taskkill skipped -> %r" % repr(exc)[:80])

    # ---- settings enforcement --------------------------------------------------------
    def _enforce_settings(self) -> None:
        """Pin the game to a FAST + CONSISTENT profile before launch so every campaign renders and
        plays identically: low/consistent graphics, intro & launch movies OFF, campaign camera-follow
        OFF (never wait to watch AI/allied moves), and 'Enable context viewer' ON (harmless to our
        pipeline). WH3 stores these in %APPDATA%/The Creative Assembly/Warhammer3/scripts/
        preferences.script.txt (+ a campaign UI-prefs file). Best-effort + NON-FATAL: only
        key-VERIFIED settings are written; unverified ones are logged, so a wrong key can never brick a
        launch. Keys are mapped in _SETTINGS_KEYS (populated as the prefs-file audit confirms each)."""
        prefs = os.path.join(os.environ.get("APPDATA", ""), "The Creative Assembly",
                             "Warhammer3", "scripts", "preferences.script.txt")
        # verified-safe FAST/CONSISTENT keys (audited against the live prefs file, format
        # `key value; # comment #`). Graphics quality knobs are already 0; these are the remaining
        # flair/perf wins. NOT touched (pinned by leaving alone): x_res/y_res, ui_scale (click coords
        # depend on it), gfx_fullscreen (windowed=stable capture), difficulty/AI (gameplay), and
        # write_preferences_at_exit (we re-assert this profile every launch, so a clobber-on-exit is
        # harmless). Camera-follow is NOT a pref -- handled in-game via the bus (see nav camera util).
        # Mostly pure-visual keys. TWO gameplay-relevant keys are PINNED to their correct values on
        # purpose (user directive) so battles play consistently:
        #   gfx_unit_size = 2 (LARGE) -- unit size scales models/unit -> changes battle + AUTORESOLVE
        #                                outcomes; Small once flipped a free-win shrine siege into a loss.
        #   gfx_vsync     = true (ON) -- keep vsync ON for consistent frame pacing.
        # Difficulty / AI keys are still left untouched (they change the campaign itself, not just speed).
        profile = {
            "gfx_unit_size": "2",             # ALWAYS LARGE (gameplay-correct; never Small)
            "gfx_vsync": "true",              # ALWAYS ON
            "gfx_aa": "0", "gfx_distortion": "false", "gfx_sharpening": "false",
            "gfx_resolution_scale": "0.5",    # render 3D at half-res + upscale (pure visual)
            "gfx_cloth_simulation": "false", "gfx_blood_effects": "false",
            "gfx_blood_enable_dismemberment": "false",
            "incremental_autosave_enabled": "false",   # kill per-turn autosave stall (consistent timing)
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
                with open(prefs, "w", encoding="utf-8", newline="") as f:   # CRLF, no BOM
                    f.write("\r\n".join(lines) + "\r\n")
                self._log("settings: fast profile enforced (%s)" % ", ".join(changed[:8]))
            else:
                self._log("settings: fast profile already applied")
        except Exception as exc:                     # noqa: BLE001
            self._log("settings: enforcement skipped -> %r" % repr(exc)[:80])

    # ---- spawn -----------------------------------------------------------------------
    def _spawn_game(self) -> None:
        """Launch Warhammer3.exe directly (bypasses the Steam launcher; the MOVIE pack
        auto-loads from data\\). Steam must be running for auth.

        Returns:
            None.

        Raises:
            TWError: If the process could not be spawned.
        """
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= getattr(subprocess, name, 0)
        try:
            subprocess.Popen([self.EXE], cwd=self.GAME_DIR, creationflags=flags,
                             close_fds=True)
        except Exception as exc:                     # noqa: BLE001
            raise TWError("failed to launch %s: %s" % (self.EXE, exc))
        self._log("spawned %s" % self.EXE)

    # ---- menu gates ------------------------------------------------------------------
    def _wait_window(self, timeout: float) -> float | None:
        """Poll until the game window exists, returning the ready timestamp or None.

        Args:
            timeout: Maximum seconds to wait for the window.

        Returns:
            time.time() at the moment the window was seen, or None on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process_window_up():
                return time.time()
            time.sleep(2.0)
        return None

    def _wait_menu(self) -> bool:
        """Gate on the game window existing, then wait (outcome-gated) for a REAL main menu:
        a frame that (a) passes the real-game-content check AND (b) matches the golden main-menu
        reference within MENU_MATCH_THRESH, confirmed twice in a row. This makes the baseline every
        frontend click is measured against impossible to poison with a screensaver overlay --
        attempt 1's fatal false positive was two identical solid-BLACK frames diffing to 0.00 and
        being accepted as 'menu settled'. There is no bus in the frontend, so vision is the only
        signal. FAIL-FAST if no real menu appears in the (short) budget.

        Returns:
            True once the main menu is confirmed rendered and stable.

        Raises:
            TWError: If the window never appears within T_WINDOW, or (via
                _fail_fast) if no real golden-matching menu frame settles
                within T_MENU_STABLE.
        """
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
            # A real menu = game content AND (if we have the golden ref) matching it closely.
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
                self._nudge()                          # dismiss any active screensaver overlay
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
        self._log("main menu ready (rendered+stable; see logs/launch_shots/01_main_menu.png)")
        return True

    def _open_accordion(self, base_menu: str) -> bool:
        """Click "Campaign" to OPEN the in-place New/Load accordion, confirmed by a LEFT-COLUMN
        crop diff vs the collapsed baseline (the full-frame diff is blind to it). Click EXACTLY
        once per attempt and re-check the ACTUAL open state each time: only click again when it
        is CONFIRMED still collapsed (a real miss) so we never toggle an already-open menu shut.
        Returns True once open. FAIL-FAST caller aborts if this returns False.

        Args:
            base_menu: PNG path of the collapsed-menu baseline frame.

        Returns:
            True once the accordion is confirmed open; False after 3 misses.
        """
        cfx, cfy = self.MENU_CAMPAIGN
        region = (0, 0, self.LEFTCOL_FR, 1)
        for attempt, dx in enumerate((0.0, 0.014, -0.012)):
            self._restore()
            self._log("frontend: click Campaign (accordion, ONCE) #%d @ frac(%.3f,%.3f)"
                      % (attempt, cfx + dx, cfy))
            self._click_frac(cfx + dx, cfy)
            time.sleep(2.5)                          # accordion is instant UI; let it paint
            self._restore()
            # Only trust the left-col diff on a REAL menu frame -- attempt 1's false "accordion
            # open" was a white-on-black WARHAMMER-logo overlay diffed against a black baseline.
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
        """Drive main menu -> (accordion) New -> Immortal Empires -> [race tile] -> [lord]
        -> Start -> loading, for the given race `plan` (defaults to High Elves / Alith Anar, the
        project focus + launch default). Clicks are absolute SCREEN pixels mapped from the LIVE client rect
        (focus-independent; restore.ps1 keeps the window foregrounded). EVERY transition is
        FAIL-FAST. Returns the confirmed loading-screen frame.

        Args:
            plan: A RACE_PLANS entry ({"tiles", "lord", "guard", "name"});
                None uses RACE_PLANS["nagarythe"] (the launch default).

        Returns:
            The confirmed loading-screen frame's PNG path.

        Raises:
            TWError: Via _fail_fast at any stalled screen transition.
        """
        if plan is None:
            plan = self.RACE_PLANS["nagarythe"]
        base_fac = self._drive_to_race_grid()
        return self._drive_race_and_lord(plan, base_fac)

    def _drive_to_race_grid(self) -> str:
        """Menu -> (accordion) New -> Immortal Empires -> the 'Change Race' grid. This whole prefix
        is RACE-INDEPENDENT (identical for every faction), so it is factored out and reused by both
        the launch and the live race-tile calibration. Returns the settled race-grid frame, which
        is the click baseline for the race-tile step. A labelled PNG is saved at each milestone.

        Returns:
            The settled race-grid ("faction select") frame's PNG path.

        Raises:
            TWError: Via _fail_fast on a poisoned menu baseline or any stalled
                transition (accordion / New / Immortal Empires).
        """
        # Prime the LIVE client rect (so every click below is screen-mapped) + save the baseline.
        self._restore()
        self._client = None                          # force a fresh live read on next capture
        # The click baseline MUST be a real menu frame: a screensaver overlay here would poison
        # every downstream diff (exactly attempt 1's root failure). _wait_menu just confirmed the
        # menu, so this normally passes on the first capture; retry a few times to be certain.
        base_menu = self._scratch_shot("menu_baseline")
        for _ in range(6):
            if self._frame_ok() and (self._menu_match(base_menu) or 0) <= self.MENU_MATCH_THRESH:
                break
            self._log("menu_baseline: rejected non-menu frame (stats=%s); retrying" % self._stats)
            self._restore()
            self._nudge()                            # dismiss any active screensaver overlay
            time.sleep(1.2)
            base_menu = self._scratch_shot("menu_baseline")
        else:
            self._fail_fast("menu_baseline",
                            "could not capture a real main-menu baseline (capture keeps grabbing "
                            "a screensaver/overlay) -- see the labelled frame")
        self._client_rect()
        self._log("live client rect = %r (clicks mapped through this, not 2560x1440)"
                  % (self._client,))

        # STEP 1 -- ACCORDION: "Campaign" opens New/Load in place (NOT a screen change).
        if not self._open_accordion(base_menu):
            self._fail_fast("after_campaign",
                            "accordion 'Campaign' never opened (left-col crop diff stayed "
                            "below %.2f after 3 single clicks -- the Campaign row was missed)"
                            % self.LEFTCOL_THRESH)
        after_campaign = self._scratch_shot("after_campaign")
        self._log("accordion open (New/Load visible); see after_campaign.png")

        # STEP 2 -- "New" -> the campaign-type / "Select a Campaign" screen (full-frame change).
        # New always advances (never toggles), so a clean retry at the same row is safe; both
        # attempts stay clear of "Load" (fracY 0.541, well below New's 0.505).
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

        # STEP 3 -- "Immortal Empires" card -> faction/race select (full-frame change). CALIBRATE.
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
        """From the 'Change Race' grid: click the plan's race tile -> Lord-Details, pick the plan's
        lord (or accept the default when plan['lord'] is None), then Start -> loading. `base_fac` is
        the settled race-grid frame used as the tile-click baseline. Returns the loading frame.

        Args:
            plan: A RACE_PLANS entry ({"tiles", "lord", "guard", "name"}).
            base_fac: PNG path of the settled race-grid baseline frame.

        Returns:
            The confirmed loading-screen frame's PNG path.

        Raises:
            TWError: Via _fail_fast if the race tile or Start Campaign never
                produced a transition.
        """
        # STEP 4 -- the race tile in the "Change Race" grid -> Lord-Details. This IS a full-frame
        # change (the darkened modal closes and the lord screen paints), so it is FAIL-FAST gated
        # on a transition away from the race-grid baseline. Verified coord first, nearby fallbacks.
        race = self._try_candidates(
            plan["tiles"], base_fac, per_timeout=8, total_timeout=self.T_TRANSITION,
            label="%s tile->lord-details" % plan["name"], before_tag="31_race")
        if not race:
            self._fail_fast("after_race",
                            "%s race tile never advanced to the lord screen within %ds -- "
                            "recalibrate the tile from faction_select.png"
                            % (plan["name"], self.T_TRANSITION))
        # lord-details ANIMATES forever (portrait + particles) -> a stability settle can only exhaust.
        # The race->lord transition was already confirmed above, so use the short animated budget.
        self._wait_stable(self.T_ANIMATED, min_wait=1.0, label="lord-details",
                          save_as="32_lord")
        self._scratch_shot("after_race")
        self._log("reached %s lord screen; see after_race.png" % plan["name"])

        # STEP 5 -- legendary-lord pick. Some races (Cathay) MUST pick a specific lord because the
        # DEFAULT is a different start (Miao Ying = Northern Provinces); others (Beastmen -- every LL
        # is a horde) ACCEPT THE DEFAULT (plan['lord'] is None -> no portrait click, no mis-click).
        # A wrong pick is caught by launch_new's post-load faction guard (plan['guard']).
        # plan['lord_change']=True additionally VISION-VERIFIES the pick right here: the click is
        # diff-gated on the static LORD-panel crop actually flipping (the full frame is useless --
        # the selected lord's 3D scene animates), with in-portrait fallback candidates; a re-click
        # of an already-selected portrait is idempotent, so the candidate loop is safe.
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
                self._click_frac(lfx, lfy)
                time.sleep(2.5)
        else:
            self._log("frontend: accepting DEFAULT lord for %s (lord=None)" % plan["name"])
        # pre-start also animates (lord idle + banners) -> short animated budget, not a 20s settle.
        base_pre_start = self._wait_stable(self.T_ANIMATED, min_wait=1.0, label="pre-start",
                                           save_as="34_pre_start")
        self._scratch_shot("after_lord")

        # STEP 6 -- "Start Campaign" (bottom-CENTRE) -> loading screen (full-frame change).
        # CALIBRATE; only the real Start advances, so a retry is safe. FAIL-FAST: if no loading
        # screen appears we abort here (with a frame) instead of entering the long load wait.
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

    # ---- advance to a playable map (bus is live from here) ---------------------------
    def _bus_turn(self, bus) -> int:
        """Current cm:model():turn_number() read over the bus, or -1 on any failure.

        Args:
            bus: The live command bus (Campaign.bus).

        Returns:
            The turn number (>=1 once the model is up), or -1 on a bus error
            or an unparseable result.
        """
        try:
            r = bus.send("eval", "return cm:model():turn_number()", timeout=8)
            return int(r.get("result"))
        except (TWError, TypeError, ValueError):
            return -1  # intentional: model not up yet -> -1, polled during map advance (expected; not logged)

    def _hud_ready(self, bus, path: str) -> bool:
        """True when the campaign HUD end-turn button is found, visible and not inactive --
        i.e. no full-screen intro modal is still covering the map.

        Args:
            bus: The live command bus (Campaign.bus).
            path: Direct pipe-path of the HUD end-turn button component.

        Returns:
            True if the component is found, visible, and not inactive; False
            otherwise (including on a bus TWError).
        """
        try:
            res = bus.send("find", path, timeout=8).get("result") or {}
        except TWError:
            return False  # intentional: HUD not ready yet -> not ready, polled (expected; not logged)
        if not res.get("found"):
            return False
        if res.get("visible") is False:
            return False
        if res.get("state") == "inactive":
            return False
        return True

    def _advance_to_map(self, campaign) -> None:
        """From 'mod started' to a playable turn-1 map. The mod skips intro cutscenes on
        start(); here we only READ state and CLICK UI (dismiss the story panel / loading
        continue / first-turn event / advisor), gating on the HUD end-turn button + turn>=1.
        All paths are direct pipe-paths from observed_ui_paths.json (no whole-tree walk).

        Args:
            campaign: The live api.Campaign attached to the running bus.

        Returns:
            None.

        Raises:
            TWError: Via _fail_fast if the HUD never became playable within
                T_MAP seconds.
        """
        bus = campaign.bus
        deadline = time.time() + self.T_MAP

        # (a) campaign MODEL up: turn_number resolves -> save finished loading, bus is live.
        turn = -1
        while time.time() < deadline:
            turn = self._bus_turn(bus)
            if turn >= 1:
                break
            time.sleep(2.0)
        self._log("_advance_to_map: model up (turn=%d)" % turn)

        # (b) dismiss any intro/event modal, then confirm the HUD is interactive.
        dismiss = (
            "custom_loading_screen|bottom_parent|button_continue",
            "events|button_set|accept_holder|button_accept",
            "advice_interface|text_parent|advice_text_panel|maximised_button_docker|button_close",
            "under_advisor_docker|scripted_tour_controls|button_close",
        )
        end_turn = "hud_campaign|faction_buttons_docker|end_turn_docker|button_end_turn"
        stable = 0
        while time.time() < deadline:
            # (P7) a lingering cinematic masks the HUD this loop gates on -> the cutscene
            # handler runs each pass (fast no-op when nothing is playing: one model-read
            # eval; the mod's start() lever already skipped the intros in the normal case).
            campaign._handle_cutscene()
            for path in dismiss:
                try:
                    bus.send("click", path, timeout=6)
                except TWError:
                    pass  # intentional: dismiss button often absent this pass -> click fails, polled (expected)
            if self._hud_ready(bus, end_turn):
                stable += 1
                if stable >= 2:                      # require two consecutive readings
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
        """After 'mod started' the bus/guard already pass, but the game still SITS on the campaign-intro
        loading screen (a big 'Continue' button under custom_loading_screen) -- so the launch would park
        there ~30s+ until something clicks it. Click Continue (+ any intro/advice dismiss) and gate on the
        loading screen closing so the launch ENDS on the playable HUD, not the loading screen. Best-effort
        + bus-timeout tolerant; never fatal (the campaign is already loaded + verified)."""
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "bus"))
        try:
            from bus import Bus as _Bus
            bus = _Bus()
        except Exception as exc:                       # pragma: no cover
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
                time.sleep(1.5); continue              # bus momentarily busy right after load -> retry
            vis = [k.get("id") for k in (roots.get("kids") or []) if k.get("visible")]
            if "custom_loading_screen" not in vis and "hud_campaign" in vis:
                self._log("_advance_past_continue: loading screen cleared -> on the campaign HUD")
                return
            for p in dismiss:
                try:
                    bus.send("click", p, timeout=6)
                except Exception:
                    pass  # intentional: a dismiss target is usually absent this pass (polled; expected)
            time.sleep(1.5)
        self._log("WARN _advance_past_continue: loading screen still up after 60s (roots=%s)" % vis)

    # ---- public entry point ----------------------------------------------------------
    def launch_new(self, faction: str | None = None, race: str | None = None):
        """Boot a fresh Immortal Empires campaign to a playable turn 1 and return a Campaign
        attached to the running command bus. `race` selects the frontend RACE_PLAN
        ("nagarythe" | "cathay" | "cathay_miao_ying" | "beastmen" | "beastmen_taurox" |
        "vmp_barrow_legion" | "empire" | "norsca" | "greenskins" | "dwarfs"); it defaults to
        High Elves / Alith Anar (Nagarythe), the project focus. `faction` is kept for
        backward-compat: a substring in it (nagarythe/alith_anar/northern_provinces/taurox/
        barrow_legion/bst/emp/nor/grn/dwf) maps to the matching race, otherwise Nagarythe.

        Args:
            faction: Backward-compat faction hint; a substring (nagarythe/
                alith_anar/northern_provinces/taurox/barrow_legion/bst/emp/
                nor/grn/dwf) selects the matching race when `race` is None.
            race: RACE_PLANS key; None resolves via `faction` or defaults to
                "nagarythe". An unknown key also falls back to "nagarythe".

        Returns:
            An api.Campaign attached to the running bus, on a playable turn-1
            map with the launched faction verified against the plan's guard.

        Raises:
            TWError: On any launch failure (prefs, window, menu, frontend
                transition, mod never started, wrong faction, or the map
                never becoming playable).
        """
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

        # (0) SINGLETON: sole owner -- lock + kill any existing game BEFORE prefs (a running game
        # holds the mod pack open, blocking the reinstall) and before spawn (no duplicate instance).
        self._ensure_singleton()
        # (0a) enforce fast/consistent game settings (graphics low, movies off, camera-follow off,
        # context viewer on) so every campaign runs identically + fast. Best-effort; never fatal.
        self._enforce_settings()
        # (0b) prefs: exe present + mod pack installed.
        self.ensure_prefs()
        # (0b) disable the screensaver / display-sleep up front (restore.ps1::KeepAwake, persistent
        # for the session) so the wallpaper-slideshow that overlaid the game and poisoned attempt
        # 1's captures never engages during the long window/load waits. Frame validation is the
        # hard guarantee; this just keeps the overlay from appearing in the first place.
        self._restore()
        # (1) launch hygiene + result-log offset for the 'started' gate.
        self._reset_command_file()
        out_offset = self._out_size()
        # (2) spawn the game.
        self._spawn_game()
        # (3) frontend (no bus): wait for the menu, then click the campaign into being.
        self._wait_menu()
        self._drive_frontend(plan)
        # (4) OUTCOME GATE: the command-bus mod appended its 'started' record => loaded. This
        # long wait is only reached AFTER _drive_frontend confirmed the loading screen (a real
        # load in progress), never on a stalled menu -- those fail-fast inside _drive_frontend.
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
        # HARD POST-LOAD GUARDS -- the mod's 'started' record fires for ANY faction, so a failed
        # lord/faction pick (which silently loads the grid's DEFAULT, e.g. The Empire) would sail
        # through as success. Verify the actual loaded state over the command bus and FAIL LOUDLY on
        # a mismatch instead of returning a wrong campaign. (Previously truncated; restored per the
        # requirement that the launch must error out on the wrong campaign / a stale mod.)
        loaded = self._verify_loaded_faction(plan)
        self._advance_past_continue()          # clear the parked campaign-intro 'Continue' loading screen
        self._log("launch_total=%.1f" % (time.time() - self._t0))
        return {"reached": True, "race": race, "plan": plan["name"], "faction": loaded,
                "seconds": round(time.time() - self._t0, 1)}

    def _verify_loaded_faction(self, plan):
        """Query the command bus for the LOADED faction + required mod handlers, and fail_fast on a
        mismatch. Returns the verified faction key. Two guards:
          (1) cm:get_local_faction_name(true) MUST contain plan['guard'] (right campaign);
          (2) the mod MUST answer the 'focus' command (not 'unknown command') -> the deployed pack
              is current, not a stale build missing handlers this stack depends on."""
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "bus"))
        try:
            from bus import Bus as _Bus
        except Exception as exc:                       # pragma: no cover
            self._fail_fast("zz_bus_import", "post-load guard: cannot import command bus: %s" % exc)
        bus = _Bus()
        guard = (plan or {}).get("guard")

        faction = None
        for _ in range(20):                            # the bus can lag a few s right after load
            try:
                r = bus.send("eval", "cm:get_local_faction_name(true)", timeout=6) or {}
            except Exception:                          # bus timeout / not-ready right after load -> retry
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
        for _ in range(12):                            # the bus can be slow/unready right after load
            try:
                cap = bus.send("focus", "xy 0 0", timeout=6) or {}
                break
            except Exception as exc:                   # log each retry (was silent) so a never-answering
                self._log("post-load guard: focus probe not ready (%s), retrying" % repr(exc)[:60])
                time.sleep(1.0)
        if cap is not None and str(cap.get("error")) == "unknown command":
            self._fail_fast("zz_stale_mod",
                            "STALE MOD: the loaded pack has no 'focus' handler (bus returned "
                            "'unknown command'). The deployed dist/tw.pack predates the required "
                            "mod handlers -- rebuild it with pack_multi.py before launching.")
        # BUGFIX: if the probe never answered, cap stays None and the stale-mod check above is SKIPPED --
        # do NOT then falsely log "mod handlers present". The faction (the critical guard) IS verified, so
        # we proceed, but we report the handler check as UNVERIFIED rather than lying about it.
        if cap is None:
            self._log("WARN post-load guard: bus never answered the 'focus' probe -- mod handlers "
                      "UNVERIFIED (faction verified, proceeding)")
        else:
            self._log("post-load guard OK: faction=%s (guard=%r), mod handlers present" % (faction, guard))
        return faction


# ======================================================================================
# module entry point
# ======================================================================================
def reach_campaign(race: str | None = None, faction: str | None = None, attempts: int = 3) -> dict:
    """Launch WH3 and drive the frontend to a LOADED, VERIFIED campaign (default: Nagarythe / Alith
    Anar). Returns {"reached", "race", "plan", "faction", "seconds"} once the post-load guard confirms
    the right faction + a current mod. The frontend lord pick is occasionally flaky (a miss silently
    loads the grid DEFAULT, e.g. The Empire); the post-load guard now CATCHES that, and this retries
    the whole launch (killing the wrong-faction game first) rather than returning a wrong campaign."""
    import subprocess
    last = None
    for i in range(max(1, attempts)):
        try:
            return GameManager().launch_new(race=race, faction=faction)
        except TWError as exc:
            last = exc
            print("launch attempt %d/%d failed: %s" % (i + 1, attempts, exc))
            if i + 1 < attempts:
                subprocess.run(["taskkill", "/F", "/IM", "Warhammer3.exe"], capture_output=True)
                time.sleep(6)
    raise last


def launch_lord(faction_key: str, culture_key: str | None = None) -> dict:
    """Launch WH3 to a fresh campaign as ANY legendary lord, selected BY BUS KEY at the faction-select
    grid -- no hardcoded per-lord pixel coords (unlike RACE_PLANS). The frontend command bus exposes
    the race tiles as `CcoCultureRecord<culture_key>` and the lords as `CcoFrontendFactionLeader<faction_key>`;
    we open Change Race, hardware-click the culture tile, hardware-click the lord, click Start, then run
    the same post-load guard. `culture_key` is auto-derived from the faction's subculture token if omitted.

    Args:
        faction_key: e.g. 'wh2_main_hef_nagarythe' (Alith Anar), 'wh2_main_def_naggarond' (Malekith).
        culture_key: e.g. 'wh2_main_hef_high_elves'; omit to auto-derive.

    Returns: {"reached": True, "faction": <loaded key>, "requested", "culture", "seconds"}.
    Raises TWError on any failure (incl. the guard catching a wrong load).
    """
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
        nav.mouse("move", *nav.ui_to_screen(n["x"] + n["w"] / 2, n["y"] + n["h"] / 2)); time.sleep(0.2)
        nav.mouse("click", *nav.ui_to_screen(n["x"] + n["w"] / 2, n["y"] + n["h"] / 2)); time.sleep(1.3)
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

    # open the race grid: Change Race can need a couple tries to actually render the culture tiles.
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
        nav.mouse("move", *nav.ui_to_screen(start["x"] + start["w"] / 2, start["y"] + start["h"] / 2)); time.sleep(0.2)
        nav.mouse("click", *nav.ui_to_screen(start["x"] + start["w"] / 2, start["y"] + start["h"] / 2))
    else:
        nav.mouse("click", 1280, 1376)   # Start Campaign (bottom-centre) fallback
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
        if _args and _args[0] == "--lord":                 # launch ANY legendary lord by faction key
            _r = launch_lord(_args[1], culture_key=(_args[2] if len(_args) > 2 else None))
        else:                                              # default: RACE_PLANS race (nagarythe default)
            _r = reach_campaign(race=(_args[0] if _args else None))
        print("REACHED:", _r)
    except TWError as e:
        print("FAILED:", e)
        sys.exit(1)
