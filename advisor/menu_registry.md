# Master Menu Registry (Part A of the plan)

The complete list of player **decision menus** the game can present, derived from authoritative sources —
NOT from what happened to appear in our runs. This is the checklist the extraction spec must cover.

Derivation (Part A of the plan):
- **A.2 resolving-event set** — extracted from the mod's 435-entry `EVENTS` table + `ctx()` accessors
  (`D:/totalwar_runner/mod/twstate.lua:916-1276`). ✅ done (this file).
- **A.1 panel roster** — the `ScriptEvent*PanelOpened` / `*ButtonClicked` events below are the game's own
  panel-open triggers; cross-check vs `ui_component_recorder.py` PANELS + observed `PanelOpenedCampaign`. ◻ partial.
- **A.3 option-set DB tables** + **A.4 faction coverage** — pending the game-DB extraction (next step). ◻ open.

Status legend: **seen** = appeared in valid data; **evt-only** = resolving event known, not yet observed;
**panel** = a faction-mechanic panel event exists but option-set/chosen not yet proven.

## Core decisions (most/all factions)
| menu | panel/trigger event | chosen — event.field | option source | status |
|---|---|---|---|---|
| research | `ScriptEventTechnologyPanelOpened` | `ResearchStarted.tech` | `technologies` DB + tech scrape | seen |
| recruit unit | army/recruitment scrape | `_recruitable` click (`clicks.csv` lens=recruitment → unit key) + `RecruitmentItemIssuedByPlayer` (unit null) | recruitment scrape / `land_units` | seen |
| recruit lord | `ScriptEventRecruitLordPanelOpened` | `CharacterRecruited` / lord click | available-lords pool | evt-only |
| building | construction scrape | `BuildingConstructionIssuedByPlayer` (build_key null → `BuildingCompleted.building`) | `building_levels`/`chains` + slot | seen |
| character skills | skills scrape | `CharacterSkillPointAllocated.skill` (drop ±0.1s of `CharacterRankUp`) | `character_skill_nodes` | seen |
| **force traditions** | army/force scrape | `CharacterMilitaryForceTraditionPointAllocated` | force-tradition tree DB | evt-only (was missing) |
| occupation | settlement-captured | `CharacterPerformsSettlementOccupationDecision.occupation_decision` (code) / `GarrisonOccupiedEvent` | occupy/loot/sack/raze/colonise (positional) | seen |
| post-battle captives | captives scrape | `CharacterPostBattleCaptureOption.captive_outcome_key` | kill/enslave/release/ransom | seen |
| pre-battle | `ScriptEventPreBattlePanelOpened{Field,MinorSettlement,AmbushPlayerDefender}` | triangulate `BattleBeingFought.autoresolved` / `CharacterWithdrewFromBattle` / `CharacterCompletedBattle` | fight/autoresolve/retreat | seen |
| dilemma | `DilemmaIssuedEvent` | `DilemmaChoiceMadeEvent.choice`/`choice_key` | `cdir_events_dilemma_choice_detail` DB | seen |
| army stance | army scrape (no panel event) | `ForceAdoptsStance.stance` (code; by `mf_cqi`) | stance DB table | seen (stance code only) |
| diplomacy | `ScriptEventDiplomacyPanelOpened` / `ScriptEventPlayerOpensDiplomacyPanel` | `DiplomacyManipulationExecutedEvent`, `FactionLeaderSignsPeaceTreaty`/`DeclaresWar`, `TradeRouteEstablished`, `DiplomaticOfferRejected` | deal-term UI (un-attributable in v6) | seen (coarse) |
| edict / commandment | (province UI) | `FactionLeaderIssuesEdict.initiative_key` + `.edict_province` | province edicts DB | evt-only |
| agent action | (agent target UI) | `CharacterCharacterTargetAction`/`CharacterGarrisonTargetAction` (`ability`/`agent_action_key` + `mr_*` tiers) | agent abilities DB | evt-only |
| item / ancillary equip | equipment scrape | `CharacterArmoryItemEquipped.item_variant` / `CharacterAncillaryGained.ancillary` (by `char_cqi`) | equipment pool (opaque cqis) | seen (chosen; pool identity open) |
| unit upgrade / effect | unit scrape | `UnitEffectPurchased` / `UnitUpgraded` | unit upgrades / RoR DB | evt-only |
| minister / governor | (appointment UI) | `CharacterCandidateBecomesMinister` / `ProvinceGovernorAppointed` / `GovernorAssignedCharacterEvent` | candidate chars | evt-only |
| mission accept | (mission UI) | `ScriptEventPlayerAcceptsMission` / `MissionIssued` | accept/decline | evt-only |
| rites | rites scrape | `RitualStartedEvent.ritual_rkey` (STANDARD_RITUAL; `performing_faction`==player) | `rituals` DB | seen |
| great-game rituals | great_game_rituals scrape | `RitualStartedEvent.ritual_rkey` (GREAT_GAME_*) | `rituals` DB | seen |

## Faction-mechanic decisions (each a distinct menu; option-sets pending DB)
| mechanic / menu | panel/trigger event | faction(s) | chosen signal |
|---|---|---|---|
| Dwarf forge (runes/items) | `ScriptEventDwarfForgePanelOpened` | Dwarfs | `component` click + `PooledResourceChanged` (oathgold) |
| Grom's cauldron (cooking) | `ScriptEventGromsCauldronPanelOpened` | Greenskins (Grom) | `FactionCookedDish.dish_recipe` |
| Ikit's workshop | `ScriptEventIkitWorkshopPanelOpened` | Skaven (Ikit) | `component` click |
| Forbidden workshop | `ScriptEventForbiddenWorkshopButtonClicked` | Chaos Dwarfs | `component` click |
| Shadowy dealings | `ScriptEventShadowyDealingsPanelOpened` | Dark Elves | `component` click |
| Athel Tamarha | `ScriptEventAthelTamarhaPanelOpened` | Alith Anar (HEF) | `component` click |
| Elector counts | `ScriptEventElectorCountButtonClicked` | Empire | `component` / `ScriptEventElectorAppointed` |
| Bretonnia vows | `ScriptEventBretonnianVowsButtonClicked` | Bretonnia | vow-completed events |
| Worldroots | `ScriptEventWorldrootsButtonClicked` | Wood Elves | `component` + `TeleportationNetworkMove*` |
| Wulfhart's hunters | `ScriptEventWulfhartsHuntersButtonClicked` | Wulfhart (Empire) | `component` click |
| Gifts of Chaos / DotGP | `ScriptEventDotGPButtonClicked` | Daemons/Be'lakor | `ScriptEventDotGPGodCompleted` |
| Ogre contracts / camps | `ScriptEventOgreContractsIssued` | Ogre Kingdoms | contract-issued + caravans |
| Cathay compass | (WoM compass UI) | Cathay | `WoMCompassUserDirectionSelectedEvent` (+ `component`) |
| Caravans | (caravan UI) | Cathay/Ogres | `CaravanRecruited`/`Spawned` (`caravan_dest`/`value`) |
| Nurgle plagues (crafting) | (plague UI) | Nurgle | `AgentPlagueDataCreatedEvent` |
| Tzeentch grimoires / changing | (grimoire UI) | Tzeentch | `PooledResourceChanged.pr_factor_key`=changing_of_the_ways |
| Khorne skulls / bloodletting | (Khorne UI) | Khorne | `StreakEffectLevelsEntered.streak_effect` |
| Prophecy of Sotek / sacrifices | (sacrifice UI) | Lizardmen (Tehenhauin) | `ScriptEventSacrificeTier{1-5}Unlocked`, `PoSStage{1-3}Completed` |
| Ice Court | (Ice Court UI) | Kislev | `ScriptEventIceCourtExpired` + `component` |
| Beastmen herdstones | `ScriptEventBloodgroundsHerdstoneCreated` | Beastmen | `component` |
| Changeling forms | (form UI) | Changeling | `ScriptEventChangelingGainsForm` |
| Raise force (dark) | `ScriptEventRaiseForceButtonClicked` | (various) | `component` |
| Rite of Ruin | (ritual UI) | Slaanesh/Chaos | `ScriptEventRitualofRuinPerformed` |
| generic button mechanics | `ScriptEvent*ButtonClicked` / `*PanelOpened` | per-faction | the `component` (context.string) captures ANY button-driven choice — the generalizing signal |

## Required STATE / value dimensions (not decisions, but the advisor needs them)
The advisor's value metric = normalized mean of {income, settlements, power_score, **vassals**}. These are
state quantities the extraction must ALSO capture, tracked separately from the decision menus:
- **income / settlements / power_score** — from the per-turn `faction` TWSTATE dump (income, regions, and
  Σ force strength deduped by `mf_cqi`). Captured now.
- **vassals (count of the player's subject factions)** — THE key missing piece from the failed advisor.
  The `diplo` dump is human-only and computes `is_vassal_of(f)` = player-AS-vassal (wrong direction), so
  it is always empty. **v7 fix:** add the reverse `"has_as_vassal": o:is_vassal_of(f)` in the existing
  per-faction diplo loop (`twstate.lua` ~700); vassal count = diplo rows where that's true. Must be
  verified live, same as the menus.

## Key insight from the source (drives Part B/D)
- The `component` (context.string) field on `Component*ClickUp` **captures the clicked component id for
  any button-driven mechanic** (`twstate.lua:1115-1120`) — so faction UI mechanics that fire no scripted
  event are still capturable as a `component` string. This + the `ScriptEvent*PanelOpened` events means the
  registry is derivable and future-proof.
- Recruitment's chosen is the **click stream** (`clicks.csv` lens=recruitment → unit key), not the issue
  event (unit null) — confirmed in `twstate.lua:1236-1246`.
- Many chosen-side keys the `ctx()` block probes are **null on this patch** (build_key, recruit_unit_key,
  occupation_decision_key, ancillary_key, ritual_key, captive_option, counterparty faction) — the working
  fields are the sibling ones noted per menu above.

## Next (to finish Part A + B)
1. Extract the game DB → resolve every option-set table (A.3) + features, and map mechanics→factions (A.4).
2. Cross-check the panel roster (A.1) against `ui.pack` + observed `PanelOpenedCampaign` components.
3. Then per menu, prove the extraction recipe (Part B) and only then write `MENU_EXTRACTION.md`.
