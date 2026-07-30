# v7 design — advisor game loop → recorder requirements

The advisor is the brain: it asks the recorder for data, decides, tells the launcher to execute,
and only counts an action that was **programmatically confirmed**. The recorder exists to serve
that loop — so the loop is specified first, and the recorder's API falls out of it.

## 1. Contexts and their action sets (the 16 implemented executors)

| context | entity | actions available for prediction |
|---|---|---|
| **lord** | character cqi | `stance`, `recruit_unit`, `skills`, `items`, `item_unequip`, `attack_army`, `attack_settlement`, `garrison`, `leave_garrison`, **`noop`** |
| **province** | region key (settlement) | `building`, `edict`, `recruit_lord`, *(`recruit_hero` later)*, **`noop`** |
| **campaign** | faction | `research`, `rites`, **`noop`** (`end_turn` is the loop's own act, not a prediction) |

Everything in one prediction per context: attacking competes with changing stance competes with
doing nothing. `noop` is always an option and ends that entity's turn.

## 2. Features (deliberately small for v1)

**Always** (campaign state): `faction`, `turn`, `income`, `settlements` (count), `treasury`.
**Plus, only for the context being decided:**
- lord action → army/lord block: `unit_count`, `ap_pct`, `stance_current`, `rank`,
  `skill_points_available`, `pending_recruits`, `in_settlement`, `is_besieging`,
  `nearest_enemy_army_dist`, `nearest_enemy_settlement_dist`, `nearest_own_settlement_dist`
- province action → province/region block: `region`, `province`, `public_order`, `growth`,
  `num_buildings`, `free_slots`, `province_complete`, `current_edict`
- campaign action → `is_researching`, `researched_count`, `rites_available_count`

**Action features** come from the option itself (key + its own attributes: cost, tier, category …)
via the existing `advisor/reference/features_db.py` DB lookups.

## 3. The loop (pseudocode)

```python
def advisor_campaign_loop(turns):
    for _ in range(turns):
        turn = recorder.turn()                       # cheap, cached from the logs tail

        # ---- LORD PHASE -------------------------------------------------------
        for lord in recorder.entities().lords:
            for step in range(MAX_ACTIONS_PER_ENTITY):        # cap, e.g. 5
                ctx  = recorder.context_features("lord", lord.cqi)     # CALL 2
                acts = recorder.available_actions("lord", lord.cqi)    # CALL 3
                pick = advisor.predict(ctx, acts)                      # incl. noop
                if pick.action_type == "noop":
                    record_decision(ctx, acts, pick, taken=None); break
                rec = launcher.execute_confirmed(lord_ctx, pick)       # engine: gates→exec→confirm
                record_decision(ctx, acts, pick, rec)                  # CALL 4 (taken + confirmed)
                if not rec.counted:
                    blacklist(lord, pick)            # never retry an unconfirmed action twice
                # loop continues -> data is re-requested fresh after every executed action

        # ---- PROVINCE PHASE ---------------------------------------------------
        for region in recorder.entities().regions:
            ... identical inner loop with context="province", entity=region ...

        # ---- CAMPAIGN PHASE ---------------------------------------------------
        ... identical inner loop with context="campaign", entity=faction ...

        # ---- TURN BOUNDARY ----------------------------------------------------
        recorder.write_target_row(turn)               # CALL 1, once per turn, END of turn
        launcher.execute_confirmed(campaign_ctx, {"action_type": "end_turn"})
        handle_interrupts()                           # defensive battles: forced autoresolve
```

Notes that matter:
- **Data is re-requested after every executed action** — an action changes what is available next.
- The advisor never inspects the game directly; it only reads recorder data and calls the launcher.
- An action is training-positive only when `rec.counted` (executed **and** confirmed).

## 4. Recorder requirements (falls out of the loop)

Four calls, exactly as specified:

| # | call | when | returns |
|---|---|---|---|
| 1 | `write_target_row(turn)` | once per turn, at end of turn | persists `campaign_id, turn, income, settlements, num_allies, num_vassals, power_rank` — the value-target inputs |
| 2 | `context_features(kind, entity_id)` | before every prediction | the feature dict from §2 (campaign block + the one context block) |
| 3 | `available_actions(kind, entity_id)` | before every prediction, same instant as (2) | list of `{action_type, key, available, params, features}` — the full option set incl. unavailable ones (with a reason), because "what was offered" is the training signal |
| 4 | `record_decision(ctx, offers, pick, rec)` | after every execution/noop | one decision row: context snapshot + all offers + which was taken + **confirmation evidence** (`executed`, `confirmed`, `counted`, `signal`, before/after) |

### Where the option sets come from (all already proven live)

| action | enumerator |
|---|---|
| stance | cco `MilitaryForceContext.StanceList` (+ faction-legality whitelist) |
| building | cco `BuildingSlotList` → `PossibleUpgradeWithoutConversionsList` + `BuildingRequirementsMet` |
| edict | cco `FactionProvinceManagerContext.InitiativeList` (`click_actions.edict_options`) |
| research | cco `TechnologyManagerContext.TechnologyList` (`NodeKey`, `IsResearched`) |
| rites | cco faction `AvailableRitualList` (+ `CanPerformRitual`) — addressed by index (no key property) |
| skills | cco `SkillList` (`Key`, `Status`, `SkillPointsAvailable`) |
| items | cco faction `AncillaryList` + `CanCharacterEquip` |
| recruit_unit | `units_panel` `<unit>_recruitable` cards (`click_actions.recruitable_units`) |
| recruit_lord | `lord_parent` types + `general_candidate_<n>_` rows (`click_actions.lord_types/lord_candidates`) |
| attack_army / attack_settlement | bus `hostiles` (dist-sorted) + `character_can_reach_*` gate |
| garrison | bus `setts` (nearest own settlement) |

So "available actions" is the union of enumerators that already exist in the executor modules —
the recorder calls them; it does not reinvent them.

## 5. Storage

`<run>/decisions.sqlite` (WAL), written **only** by the recorder stream:
- `target_rows(campaign_id, turn PK, ts, income, settlements, allies, vassals, power_rank)`
- `context_snapshots(snapshot_id PK, ts, turn, context_kind, context_id, features_json)`
- `action_offers(offer_id PK, snapshot_id FK, action_type, action_key, available, gate, params_json)`
- `action_taken(taken_id PK, snapshot_id FK, offer_id FK, action_type, action_key, executed,
  confirmed, counted, confirm_signal, confirm_before_json, confirm_after_json, policy, latency_ms)`

Training reads only `counted = 1` rows as positives; every offer in the same snapshot is a
negative. Unconfirmed executions are kept for debugging and excluded from training.
