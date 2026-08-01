# HANDOFF — tw_stack autonomous advisor (written 2026-07-30)

Single source of truth. Replaces `HANDOFF_diplomacy_and_open_work.md` and
`HANDOFF_2026-07-30_all_starts_run.md` — both deleted.

---

# 0. YOUR ASSIGNMENT

**Keep the 100x40 run alive, and improve it while it runs.** Concretely, on a loop:

1. **Run it.** A 100-campaign x 40-turn sweep sampling all 104 playable starts. If the session
   process is dead, relaunch it (§2). Do not wait to be asked.
2. **Monitor it.** Watch the session log for campaign outcomes, crashes, and the specific tells in
   §5. Arm a `Monitor` on the log — use `tail -n 0 -F` so you do not get replayed old lines and
   raise false alarms.
3. **Diagnose every failure properly.** The operator's standing instruction: *every crash and every
   campaign of 2 turns or less needs an explanation — the REASON it failed, not a description that
   it failed.* Evidence lives in the session log, the run dir's `errors.log` / `events.jsonl` /
   `*.jsonl`, the harness's own `shots/no_hud_*.png` and `stuck_*.png`, and the game's Lua log
   captured under `<run dir>/logs/script_log_*.tail`.
4. **Fix what you find, then restart.** See §1 — you have the authority.
5. **Implement what is left** (§6) between failures. Do not sit idle waiting for the run.

**You are expected to act autonomously.** Do not ask permission for the ordinary loop of
diagnose → fix → restart → verify.

---

# 1. YOUR AUTHORITY — you may stop and restart the game

You **may and should**:

- **Kill the session process** when you have a fix that needs loading. Python modules are imported
  once at session start, so *no code change takes effect until you restart the session.*
- **Kill Warhammer3.** Required before a mod install. The next session cold-starts it anyway
  (`previous campaign ended ... -- killing the game rather than asking it to quit`).
- **Rebuild and install the mod** whenever `bus/mod/*.lua` changes:
  ```
  cd D:\tw_stack
  $env:PYTHONPATH="D:/tw_stack/launcher"
  & D:\totalwar_runner\.venv\Scripts\python.exe bus/pack_multi.py build
  & D:\totalwar_runner\.venv\Scripts\python.exe bus/pack_multi.py install   # GAME MUST BE CLOSED
  ```
  `install` prints the mod-log outcome — verify **`twcontrol() executed successfully`**, because
  "loaded successfully" is not "ran".
- **Wipe/archive the store** when data is contaminated. Stop `manager.py` and `ui.py` first, move
  `D:\twdata\runs\human\*` to `D:\twdata\runs\archive_prewipe_<stamp>`, restart both.
  ⚠ **Archive `D:	wdatamodelsglobal` at the same time.** A wipe that leaves the trained model
  behind means the next campaign is steered by a model fitted on the data you just deleted. This
  happened today.

The one thing you must **never** do is start `Warhammer3.exe` yourself outside the session — that is
what caused duplicate games. The session owns the launch.

---

# 2. RUNNING IT

```
D:\totalwar_runner\.venv\Scripts\python.exe -u advisor/session.py 100 40 --factions all --retrain
```

Launch it detached with stdout → a timestamped log under `D:	wdataogsdvisor/`, stderr → the matching
`.err`, and write the log path to `D:	wdataogsdvisor/CURRENT_SESSION_LOG.txt` (the health-check reads
that pointer).

- `--factions all` reads `launcher/startable_factions.json` — **104 factions / 24 cultures**,
  harvested from the game's own frontend by cycling every race. It is data, not a hand-typed table.
  `startable_factions()` **raises** if it is missing; there is no default and no quiet narrowing.
  Re-harvest with `BusLauncher().harvest_startable_factions()` at campaign-select after new DLC.
- `--factions` is **mandatory**. Session refuses to start without it, by design: an earlier version
  defaulted to Nagarythe and a "multi-faction" run played one faction 100 times unnoticed.
- Also required: `manager/manager.py` (the recorder — sole sqlite writer) and `advisor_ui/ui.py 8779`.

**Healthy = 1 Warhammer3 + 3 python.** Any extra python that is not manager/ui/session is an orphan
(old scraper, stray probe) — kill it. Orphans polling the bus have killed campaigns.

⚠ **`D:\tw_stack` IS a git repo** (some tooling reports otherwise). Use `git diff` / `git log` — it
makes "what changed?" enormously cheaper than reading session transcripts.

---

# 3. ARCHITECTURE — the bits that keep biting

- **Three layers:** `cco()` (UI command layer, available at frontend), `cm:` (campaign script API,
  campaign only), and bus `click` (SimulateLClick by component path).
- **The confirmation law: `taken = executed AND confirmed`.** A command reporting success proves
  nothing. Only a post-assert read is evidence. The bus click in particular returns `clicked: true`
  for controls that do nothing.
- **Role split:** the recorder owns ALL bus reads and is the only sqlite writer; the advisor holds no
  bus; the launcher owns execution.
- **Null interfaces are TRUTHY in Lua.** `x and x:method()` is not a guard — use `is_null_interface()`.
- **A modal popup pauses the game tick**, the mod runs on that tick, so the bus dies entirely. That
  is the watchdog's case, not something the interrupt layer can see.

---

# 4. FIXED TODAY (do not regress these)

- **Unrecognised interrupt options crash irrecoverably.** `interrupts.UnhandledScreen` derives from
  **`BaseException`** so the broad `except Exception` in `loop.py` and `session.py` ("never let one
  campaign kill the run") cannot swallow it. Recognition sets are **derived** from the pickable
  lists (`KNOWN_RESULTS_CONTROLS = CAPTIVE_OPTIONS | frozenset(ADVANCE_PREFERENCE)`), so a
  recognised-but-unpickable id is unrepresentable — that gap was a fallback wearing the crash's
  clothes. **Never hand-write these sets and never pad them with ids you merely expect to see.**
- **Occupation and captive picks are UNIFORM RANDOM** over what the screen offers, and every option
  set + pick is recorded to `interrupt_decisions`. Fixed preference made every campaign occupy, so
  the data said nothing about sacking or razing. Random now, modelled later once the data supports it.
- **Display furniture is not an option.** `_clickable_controls` returns *every* clickable node, and a
  battle panel is mostly unit cards. `DISPLAY_CONTROLS` + the `unit_` prefix exclude them (measured:
  `unit_0..unit_15, army_button, button_info, button_preview_map, button_surround,
  card_image_holder, icon, selected_frame`). ⚠ **`button_surround` is unverified** — if a battle ever
  resolves without us clicking anything, look there first.
- **`no_campaign_ui` — cause narrowed, mitigation shipped, REAL FIX NOT YET APPLIED (see §6d).**
  Evidence audit: **7 occurrences across 4 logs, every one immediately preceded by
  `attack_settlement` or `attack_army`, never any other action**; two archived cases *recovered on
  their own* once `popup_pre_battle` appeared. It is a transition, not a dead screen —
  `3d_ui_parent` / `ai_attack_targets_parent` / `panel_manager` / `under_advisor_docker` stay loaded.
  The immediate bug was that the miss loop had **no pause**: three "recovery attempts" elapsed in
  under a second. Now `HUD_MISS_BUDGET=12` x `HUD_MISS_PAUSE=5.0` (~60s) with a `hud_transition`
  watchdog beat. ⚠ **I labelled this the "confrontation cinematic". That term has ZERO hits in CA's
  docs, the UI docs, the DB dump and all `_lib` sources — treat the label as unsupported.** The
  mechanism is a cutscene (§6d), which is a better-evidenced and differently-fixable thing.
- **Camera/focus calls are cleared as a current cause.** `click_actions.focus()` is a no-op; every
  surviving `bus.send("focus", ...)` lives in a module the executor never imports; **the mod has no
  `focus` handler at all**. Hardware keypresses are cleared too (`input.ps1` supports arrow keys but
  nothing calls it). ⚠ `click_actions.py:207` carries a DO-NOT-REINTRODUCE warning about
  `cm:set_camera_position` on every `prepare()` — that genuinely wedged the game. Leave it dead.
- **Move completion (likely the CTD).** `cm:move_to` is asynchronous; `_move_confirm` returned as
  soon as the position changed *at all* — i.e. mid-path — so the next order went into a character the
  engine was still animating. Fits both CTDs on record. `_await_standstill` now polls until position
  is unchanged twice running (bounded 4.8s) and warns if it never settles.
- **Heroes can move** (was missing, not excluded — `_move_offers` is now shared with lords).
- **Own-settlement garrison counts** — `setts` emits `units`. We knew enemy garrison strength and
  nothing about our own, so the model could not learn that leaving home undefended loses campaigns.
- `model.py` dead code removed (`H`, `ranges["delta"]`, `ranges["maxturn"]`); `executor.py` encoding
  repaired; `_MOVE_ATTEMPTS` seeds moves per attempt so a stuck lord gets fresh candidates.

---

# 5. WHAT TO WATCH FOR

| Log line | Meaning | Action |
|---|---|---|
| `hud_campaign missing (n/12)` with n climbing past 3, then the loop resumes | **The HUD fix working.** This is the proof that was still outstanding. | Note it; §4 item confirmed. |
| `still moving after ...` (`cm_actions`) | A character never settled in 4.8s; the next order lands mid-path. | If CTDs continue, raise `_SETTLE_POLLS`. |
| `WH3 process gone while awaiting seq N` | Genuine game CTD. Harness fails fast (correct). | Correlate with the 2–3 preceding actions. Check `<run>/logs/script_log_*.tail` for a Lua error. |
| `UnhandledScreen: ... UNHANDLED option(s) [...]` | A screen offered something unknown. **Working as designed.** | Add the named string to the right list, restart. Never guess siblings. |
| `retrain N DID NOT FIT (rows=X need=40)` | Normal until 40 labelled rows exist. | ⚠ `rows` must **RISE** run over run. If it stalls, investigate labelling. |
| `skipped_unlabelled` ≈ all rows | The forward-only target leaves the last turn unlabelled, so a campaign dying at turn 1 contributes **nothing**. | Fix the crash, not the label. |

**Never** open `decisions.sqlite` of a live run, and **never** run bus queries against a live run —
an ad-hoc faction enumeration over the bus killed a campaign (the recorder's next eval timed out at
the adjacent seq). Screenshots via `launcher/ps/capture.ps1` are OS-level and always safe.

---

# 6. WHAT IS LEFT TO IMPLEMENT

### 6a. Diplomacy — IN THE ACTION SPACE as of 2026-07-30. Not yet observed executing live.
`executor.py` imports `diplomacy_actions`, so `diplomacy` is one of the 18 registered action types,
and `collect.diplomacy_offers()` puts it in front of the policy every turn. **Both halves are
required** -- registering only makes it executable; an action nothing OFFERS is never ranked and
never chosen, and the offer half is the one that was missing for a long time.

Offer shape: `<faction>:<term>` or `<faction>:<a>+<b>`, params `{faction, terms[], standing}`.
Per target: declare_war + 7 singles + 21 pairs = 29; `DIPLO_MAX_TARGETS = 8` → ≤232 offers/turn.
Cost is bounded by `max_per_entity_turn: 1` and `retryable: False` — at most ONE panel walk
(~15-20s) per turn however many diplomacy offers rank high.

⚠ The target list is a SUPERSET of the truly deal-able set (relationship flag set, or standing off
zero, capped at 8). Per the operator this is fine: **"we don't care that much if they accept or
refuse — they need to be able to ask."** A faction the panel has no row for raises inside
`select_faction` and is recorded as a refusal, which is data, not a fault.

**Still unobserved:** no `diplomacy` line has yet appeared in a live log, so the click walk has
never actually run end-to-end. Watch for `diplomacy: <faction> [...] -> staged=... sent=...` and for
`DiplomacyError`. That is the first thing to confirm.

**Scope the operator asked for:** execute standard offers and declare war. **Skip** gifts/payments
and settlement trades. Terms: `nonaggression_pact`, `trade_agreement`, `defensive_alliance`,
`soft_access`, `military_alliance`, `vassal`, `confederation` — **singles or pairs at most**. The
advisor proposes target-faction x term-combo as candidate actions, with diplomacy features on the row.

**Settled — do not re-derive:** no cco/cm command offers or accepts a deal (confirmed by a 20-agent
adversarial review of CA's shipped cco reference; `OpenDiplomacyWith` takes no args and only opens
the panel). Execution is a click walk. `force_*` stays banned — including `force_declare_war`, which
needs no AI acceptance but whose `invite_*_allies` flags can suppress ally involvement in a way a
human player cannot.

**The measured walk:**
```
1. open      hud_campaign|faction_buttons_docker|button_group_management|button_diplomacy
2. select    faction_row_entry_<faction_key>   -> HARDWARE DOUBLE-CLICK (SimulateLClick will NOT open it)
3. add term  diplomatic_option_<term> under
             diplomacy_dropdown|offers_panel|diplomacy_hud_offers_panel|panel_diplomacy|
             offers_list_panel|list_possible_actions
4. read      label_deal_success_chance   (signed float, e.g. -6.4)
5. send      button_send, ONLY when active
```
**Constraints learned live:** `offer_button_*` are **invisible templates** — clicking them is a
silent no-op that looks exactly like a refusal (hours were lost to this). The option set is
**state-dependent** — adding a term changes which options exist. All options share `y=994` and the x
ordering shifts, so **re-read the tree and resolve by id immediately before every click; never cache
a position** — a stale coordinate could hit `diplomatic_option_declare_war`. `inactive` means the
game forbids that combination. **An inactive `button_send` is the AI refusing — a legitimate recorded
outcome, never a harness failure and never retried.**

Measured (Rakarth, Neutral/-10): empty −46.5 · +Trade −6.4 · +Trade+Military Alliance −53.3.

**Post-asserts (all verified on the cm faction interface):** `at_war_with`, `allied_with`,
`trade_agreement_with`, `is_vassal_of`, `diplomatic_standing_with`.

**⚠ THE UNRESOLVED BLOCKER — the target set is "can make deals with", NOT "discovered".** You can see
a faction and be unable to deal with it. The panel listed **16** `faction_row_entry_*` rows while
`diplomatic_standing_with` answers for **533** factions (returning `0.0` for never-met, so standing
cannot distinguish unmet from met-and-neutral). `has_met` / `has_encountered` / `is_known_to` /
`has_discovered` do **not** exist. Options, in order: (1) find a real accessor — try
`CcoCampaignFaction` diplomacy-availability properties and `CcoCampaignDiplomacyManager`
(`NegotiationForFaction` exists); **enumerate the cco reference, do not guess names — three guessed
accessors failed**; (2) read the set from the panel once per turn (implemented as
`diplomacy.dealable_factions`, costs a panel open, and the list is scrollable so rows may be
virtualised); (3) lazy upper bound. Whichever you pick, the recorded offer set must be exactly what a
human could have proposed at that moment, or the model learns to propose impossible deals.

### 6b. Option sets — occupation SOLVED, captives STRUCTURALLY BROKEN

**THE SOURCE OF TRUTH IS `Shazbot/WH3-Dump`** — a per-patch dump of WH3's `db/`, `text/db/`, `ui/`
and `script/`. Pinned commit `61d4e117f669ea93d6da1077d1524c332d5e74c9`, "8.1 patch", 2026-07-13.
Use it for any future string question; it beats the wiki (402/403 behind anti-bot) and beats play.
- `db/culture_settlement_occupation_options_tables/data__.tsv` — 372 rows, 56 labels
- `db/campaign_post_battle_captive_options_tables/data__.tsv` — 95 rows, 55 labels
- `db/captive_option_outcomes_tables` — the outcome enum
Label = the part before `||` in `tooltip` (`Name||Description`).

**DONE: `OCCUPY_OPTIONS` now holds all 55 labels from that table** (verified: the five originally
measured live appear in it byte-for-byte). Facts worth knowing: **"Occupy" is not universal** — Wood
Elves' occupy button reads **`Seeding`**; **Beastmen have no occupy, sack or loot at all** (raze
variants + `Raise Herdstone`); **Khorne has no `Raze`** (renamed to `Blood for the Blood God` /
`Skulls for the Skull Throne`); Norsca altars use an **en-dash U+2013**; Warriors of Chaos **can**
occupy in WH3 (the WH1 horde rule is dead — do not code for it).

**RESOLVED — `CAPTIVE_OPTIONS` IS COMPLETE. Two agents disagreed; the second one is right.**
A second agent wrote its own PFH5 pack reader and re-derived everything from the installed build's
bytes. `db/captive_option_outcomes_tables` has 7 rows but a `col3` that collapses them to exactly
three: `enslave` / `kill` / `release`. All 95 rows of `campaign_post_battle_captive_options_tables`
map into those three, and the engine builds the id in C++ from the literal prefix
`button_captive_option_` (`Warhammer3.exe` @0x38E3B4F). CA's prologue enumerates the same three
literally. **There is no fourth suffix, so keying on ids is correct** — and keying on TEXT would be
actively wrong, because labels are NOT unique: `Enslave` is both an enslave option (Dark Elves) and a
kill option (Skaven); `Dominate Captives` is both kill (Dechala) and enslave (Vampire Counts).
The 59 distinct labels are display text only. Ignore the paragraph below; kept for the reasoning.

**~~NOT DONE — `CAPTIVE_OPTIONS` IS CONCEPTUALLY WRONG AND WILL BREAK.~~ (superseded, see above)**
`release` / `enslave` / `kill` are **not button labels** — they are values of the `captive_outcome`
enum, which has seven members (`enslave`, `enslave_replenishment_only`, `enslave_slaves_only`,
`kill`, `negotiate`, `none`, `release`). The **on-screen text differs per race**: 55 distinct
strings. A Greenskin player never sees "enslave", they see **`Eat Captives`**. Slaanesh's is
**`Entice Captives`** (outcome `kill`, not release). Chaos Dwarf labour is `Transport Captives`.
Ogres get `Process Meat`.

The component-id route **cannot be extrapolated**: `button_captive_option_release/_kill/_enslave` are
confirmed real (string comparison in the prologue Lua
`wh3_prologue_kislev_expedition_interventions.lua`), but group `wh3_main_captive_option_kill_daemons`
offers **three simultaneous `kill` options** (`Offer to Khorne`, `Offer to Tzeentch`, `Offer to the
Chaos Gods`) — they cannot all share one id, so the scheme carries a disambiguator nobody has found.

**Therefore: match captive buttons on their ON-SCREEN TEXT, not on a derived id.** That is a real
code change — `_clickable_controls` returns ids only, so `handle_results` needs to read node text for
the captive family the way `occupy()` already reads `dy_option` text. Until then, any Daemon Prince,
Ogre, Greenskin, Skaven or Slaanesh battle with captives will hit `UnhandledScreen`. The full 55-label
table with per-race attribution is in the research agent's report for this session.

### 6d. THE REAL HUD FIX — evidenced, not yet applied. Do this first.

`HUD_MISS_BUDGET` only *waits out* the symptom. The mechanism is now sourced (CA script docs +
`Shazbot/WH3-Dump` `script/_lib/`; verified copies of `lib_campaign_cutscene.lua`,
`lib_campaign_manager.lua`, `default_keys.xml` were pulled to the session scratchpad):

1. **`*_with_cutscene` camera helpers hide the UI by documented design.** `scroll_camera_with_cutscene`
   et al: *"Cinematic borders will be shown …, **the UI hidden, and interaction with the game
   disabled** while the camera is scrolling."* They construct a real `campaign_cutscene`, not a camera
   tween. **`cm:set_camera_position`, `cm:scroll_camera_with_direction` and `CampaignUI.ZoomToSmooth`
   document no such side effect** — if focus is ever reintroduced, use only those.
2. **`campaign_cutscene:start()` calls `cm:steal_user_input(true)` and `cm:override_ui(...)` locks.**
   That is almost certainly the source of `command_silently_refused` during these windows.
3. **The UI-hide hotkey is `K`, and `ALT+K` is hide-UI-*with-cinematic-borders*** — which matches the
   evidence screenshot exactly. Defined in `text/default_keys.xml` under `<category name="universal">`
   (so it applies on the campaign map), `component="root"`.
4. **The cleanup is deferred and can be LOST.** `campaign_cutscene:release()` restores the UI via
   `cm:callback(..., 0.1)`, and `cm:callback` is *"a timer **synchronised to the campaign model**"*.
   The cutscene library has **zero** battle/loading-screen listeners. So a cutscene running as the
   campaign model stalls (battle transition — i.e. exactly what an attack causes) has **no guaranteed
   path back to `release()`**, and the UI stays hidden indefinitely. That explains why some cases
   recover and some never do.

**Apply, in order:**
- **At campaign start, once: `cm:enable_ui_hiding(false)`.** Implemented as
  `disable_shortcut("root","toggle_ui")` + `toggle_ui_with_borders`, and it **sticks** — `start()`
  records `if not cm:is_ui_hiding_enabled() then self.enable_ui_hiding_on_release = false`, so
  cutscenes will not re-enable `K` behind you. Cheap, and removes the stray-keypress class entirely.
- **Gate orders on `cm:is_any_cutscene_running()` and `cm:is_cinematic_ui_enabled()`** instead of
  inferring from a missing root. Caveat: the former only knows about script-created cutscenes.
- **Replace the blind 60s wait with a bounded wait then FORCED recovery:**
  `cm:skip_all_campaign_cutscenes()` → `CampaignUI.ToggleCinematicBorders(false)` →
  `cm:steal_user_input(false)` → `cm:enable_ui(true)`. This is the piece that turns an unrecoverable
  campaign into a recovered one. (`bus_launcher.py:224` and `twcontrol.lua:834` already call
  `skip_all_campaign_cutscenes` at startup — the call is known-good, just not used mid-campaign.)
- **Never issue a focus/camera call across a battle transition** — the one window where the restore
  callback is lost outright. `click_actions.py:207`'s DO-NOT-REINTRODUCE warning is now explained.

**Diagnostic if you need to know unloaded-vs-hidden:** cache the `hud_campaign` handle while it
exists; when it vanishes from the walk, call `:Visible()` and `:VisibleFromRoot()` on the cached
handle. Valid handle + `Visible()==true` + `VisibleFromRoot()==false` ⇒ **orphaned from the UI root**,
not destroyed. (A whole-UI teardown would take the siblings too, which it does not.)

### 6c. Smaller, still open
- **`script_log` reaches ~78 MB per campaign** — `twstate.lua` dumps every unit, region slot and
  pooled resource every turn. Heavy disk I/O concurrent with the game; a plausible CTD aggravator.
  No verbosity flag exists, so trimming means editing `twstate.lua`, which the recorder depends on.
  The operator has **approved doing this on any restart** — do it deliberately, verify the recorder
  still gets what it needs.
- **`PREBATTLE_PREFERENCE`** is only `button_autoresolve` / `button_continue_siege`. Sieges, ambushes
  and quest battles may need more.
- **`nav.py:234-245` and `nav.py:343`** still contain fallbacks. Remove them.
- **Agent actions** (assassinate/block/wound) have no command path on any layer — click-only, and
  opening the targeting panel is not solved click-free.
- Confirm `wh2_main_def_cult_of_pleasure` is actually Morathi (moot now that all 104 are sampled).

---

# 7. STANDING CONSTRAINTS (operator's, non-negotiable)

- **No fallbacks. Loud, immediate failure.** One code path per operation. If you don't know, crash —
  do not cope, do not guess, do not "handle" it. This is the single most repeated instruction and the
  most repeatedly violated.
- **KNOWN must equal HANDLED.** An option the code cannot both *pick* and *record* must kill the run.
- **The fight-battle option must always be unavailable** — `FORBIDDEN_CLICK_IDS` =
  `button_attack`, `button_spectate`, `button_retreat`. A testing agent dropped into a real-time
  battle is unrecoverable without a human.
- **No god-mode**: no `cm:force_*`, `create_force*`, `spawn_*`, `grant_*`, `Dev*`, no teleport.
- **Delete replaced code.** Be mindful of bloat.
- **Deliverables go in the repo, not the session scratchpad.** A 104-faction roster the operator did
  manual work to harvest was written to a temp dir and nearly thrown away.
- **Subagents**: read-only. No sqlite, no bus, no process control, never launch the game.
- **Develop on knowable game states** — never on a campaign that already ran wrong actions.
- Follow instructions literally; don't expand scope.
