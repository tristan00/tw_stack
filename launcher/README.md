# launcher

Launch WH3 and drive the frontend (pixel/vision, **no bus**) to a **loaded campaign**, then stop.
Its only job is to bring a campaign up so the live capture repos can be tested; no engine, no
in-campaign actions. Default campaign: Nagarythe / Alith Anar (`RACE_PLANS["nagarythe"]`).

Ported from the monolith's `twapi/launcher.py`, **truncated**: `launch_new` stops at the mod's
`started` signal (campaign loaded); the `api.Campaign` faction-guard + advance-to-map tail was
dropped (that was the only engine dependency).

## API / CLI
- `reach_campaign(race=None, faction=None) -> {"reached", "race", "plan", "seconds"}`
- `python launcher.py [race]` — race in `RACE_PLANS`: `nagarythe` (default) | `cathay` |
  `cathay_miao_ying` | `beastmen_taurox` | `vmp_barrow_legion` | `empire` | `norsca` | `greenskins` | `dwarfs`

## Deps (flattened into this repo)
`screen.py` (ScreenBridge + `ps/*.ps1` vision/click), `errors.py`, `config.py`, `ref/main_menu.png`,
`dist/tw.pack`. `CMD_PATH`/`OUT_PATH` are inlined and **must match the mod in `tw.pack`** (currently
the monolith's `data/` dir; move them when the `bus` repo rebuilds the mod).

## Test (LIVE — launches the game)
`python launcher.py` — a cold start should reach a loaded campaign and print `REACHED` (the mod's
`started` signal fired). The fragile part is pixel-driving the frontend (screen resolution / golden
images / lord-pick coords); a failure to reach the campaign is the launcher's own failure to
surface. Which faction actually loaded is verified downstream (`record` → `runs`), not here.
