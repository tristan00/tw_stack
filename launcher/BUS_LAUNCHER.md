# bus_launcher — launch WH3 to a playable campaign over the command bus (NO pixels)

The current, preferred launcher. It navigates the WH3 frontend by **UI-component paths over the
command bus** (the mod arms the bus in the FrontEnd environment and exposes `find`/`click`). Every
path is addressed by a **semantic key** (button id, culture key, faction key), so it is robust to
resolution and layout — the reason for retiring the old absolute-pixel-fraction launcher
(`launcher.py`, kept for reference).

All paths were discovered live on WH3 v8.1.1 (2026-07-27) and the whole flow was validated
end-to-end to a playable Nagarythe campaign.

## The presave path (what runs actually use)

Sessions with `--presave-radius` never touch the frontend: `load_save(save_file)` spawns
`Warhammer3.exe game_startup_mode campaign_load <save>`, waits for the mod's `started`
(boot budget 90s; observed ~25–31s), probes the bus, and advances to the HUD (budget 60s;
observed ~0.1s on presave loads). The frontend-navigation flow below remains for fresh
campaigns and the bake tools. The caller guarantees no game instance is running — the
previous campaign's kill confirms process death before returning, and `spawn` refuses to
boot over a live mod.

## Run
```
python bus_launcher.py [plan]      # plan defaults to "nagarythe"
```
Plans (see `PLANS` in the source): `nagarythe`, `cathay_zhao_ming`, `cathay_miao_ying`,
`slaanesh_masque`, `beastmen_taurox`, `empire_default`. Each is a `{culture_key, faction_key}`
pair; the launcher picks the race tile by culture key and the lord by faction-key **substring**
(so the volatile numeric suffix on `CcoFrontendFactionLeader<key><n>` is ignored). Add plans
freely — the keys are the game's own.

## Flow (what it does)
1. install the mod pack if missing (never overwrites a working installed pack), spawn `Warhammer3.exe`
2. wait for the mod's `frontend_armed`, then **probe the bus until it answers** (avoids the
   arm-time race where the first command is dropped)
3. `Campaign` → `New` → select the **Immortal Empires** card (matched by its `button_txt` label)
4. `LORD` → `Change Race` → pick the culture tile → pick the lord (by faction-key substring)
5. `Start Campaign` → wait for the mod's `started` (model loaded)
6. **advance to the playable HUD**: dismiss the loading-screen `Continue`, skip the intro
   cinematic, poll until `hud_campaign` is visible

Returns the `started` record once the interactive HUD is up. Transient bus timeouts are retried;
a genuinely missing component or an un-advanceable load raises `TWError`.

## Notes / limits
- Requires **Steam running** (for auth) and the mod pack (`dist/tw.pack`).
- The bus paths (`<TWDATA>/bus/commands.txt`, `<TWDATA>/bus/twcontrol.jsonl`) are baked into the
  compiled `tw.pack` by `pack_multi.py` — pack and Python must agree.
- It does **not** kill an already-running game; `spawn` refuses to boot over a live WH3 instance
  (bus rotation guard). In a run, the previous campaign's confirmed kill provides the clean slate.
- It performs **no gameplay actions** — it only navigates menus and reads state (the mod's own
  `start()` skips intro cutscenes; the launcher only clicks UI and polls).
