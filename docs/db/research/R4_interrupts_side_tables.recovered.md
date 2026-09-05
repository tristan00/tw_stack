# LANE R4 — Interrupts and the side tables (interrupts, diplomacy_events, postmortems, ucb_picks/ucb_pick_rows, rpc_requests/rpc_responses)

Method: every file listed for the lane was read at HEAD; every DB number below came from `pg.connect(autocommit=True, readonly=True)` with `statement_timeout` set, on 2026-09-04 ~21:16 local. Samples use `interrupt_id % k` filters. Where a fact could not be established it is marked UNKNOWN. The scratchpad report file was not written (the session is in plan mode, which forbids writes outside the plan file); this message is the report.

Corpus at measurement time: `interrupts` 43,392 rows / 19 MB; `diplomacy_events` 74,145 / 38 MB; `postmortems` 5,020 / 15 MB; `ucb_picks` 3,853 / 0.9 MB; `ucb_pick_rows` 497,037 / 87 MB; `rpc_requests` 1,239 / 5.8 MB; `rpc_responses` 325 / 96 kB; `decisions` 206,907; `campaigns` 4,827; `blobs` 949,090 / 2,388 MB.

---

## A. INTERRUPT LIFECYCLE

### A.1 Game panel → Lua detection
- The Lua controller does not "detect" interrupt panels by itself. It exposes three primitives the Python side polls: `roots` (visible children of the UI root, `bus/mod/twcontrol.lua:528-545`), `tree <root> <depth> <nodes>` (BFS dump of a subtree with id/visible/state/text/tooltip/context per node, `twcontrol.lua:215-251`; node shape from `describe` at `134-151`, `context` from `GetContextObjectId` over the CCO types at `155-170`), and `click <path>` (`SimulateLClick`, with `roots_before/after`, `state_before/after`, `changed`, `refused="target_not_visible"`, `twcontrol.lua:268-299`).
- The Lua side also appends passive event rows to the bus out-file that the Python interrupt code waits on: `panel opened/closed` (`PanelOpenedCampaign`/`PanelClosedCampaign`, `twcontrol.lua:1580-1604`), `dilemma_issued` (`DilemmaIssuedEvent`, `1502-1507`), `dilemma_choice_made` (`1508-1514`), `incident_occured` (`1515-1529`), `battle_completed` with `autoresolved` (`1531-1536`), `diplo_event` for 21 event names (`1539-1557`), `agent_action` (`1559-1578`), `faction_destroyed`/`turn_start` (`1457-1491`). None of these rows is persisted by this lane's code (the `TWSTATE` stream in `logs/events_stream.py:19-35` persists a different feed into `app.game_event`; it is not joined to `interrupts` anywhere).
- Python-side panel classification (`launcher/interrupts.py`):
  - `pending(bus, roots)` → set of {`popup`, `battle`, `diplomacy`} from visible roots: `battle` if any of `BATTLE_ROOTS=("popup_pre_battle","popup_battle_results","settlement_captured")` (`interrupts.py:17`, `552-553`); `diplomacy` if a root contains "diplo" and is not a HUD root (`504-506`, `554-555`); `popup` if `hud_campaign` is absent or any root is outside `nav.BASE_ROOTS`/`BENIGN_PANELS`/`DIPLOMACY_HUD_ROOTS` (`550-558`). `nav.BASE_ROOTS` at `launcher/nav.py:476-482`, `BENIGN_PANELS` at `64-68`, `DIPLOMACY_HUD_ROOTS=("diplomacy_dropdown","diplomacy_attitude_tooltip")` at `interrupts.py:235`.
  - Engine blocking state is read with `nav.engine_pending` (`CcoCampaignRoot.PendingActionContext` → `"IsActive|ActionType"`, `nav.py:76-97`); `pending_blocks` treats anything `true|…` except `PENDING_ATTACK` as blocking (`nav.py:100-104`, `ORDER_PENDINGS` at `74`). `PROTECTED_SURFACES=("popup_pre_battle","popup_battle_results","settlement_captured","appoint_new_general")` (`nav.py:72-73`) are never dismissed by `close_popups` (`nav.py:373-378`, `398-402`).

### A.2 Entry points into `interrupts.resolve`
`resolve(bus, max_rounds=4)` (`interrupts.py:1755-1917`) is the single dispatcher. It is called from:
- `Executor.resolve_interrupts` (`launcher/executor.py:65-69`), used by the advisor loop at turn open (`advisor/loop.py:406`), HUD recovery (`470`), after locomotion actions (`658`), after every action (`667`), and in `Executor.settle_between_turns` each poll round between `turn_start` rows (`executor.py:231`; the wait loop at `191-246` blocks on `turn_start|faction_destroyed|panel|dilemma_issued` rows, `202-204`).
- `cco_actions._execute_confirmed` mid-confirm, when a `dilemma_issued` row arrives during an action's confirm poll (`launcher/cco_actions.py:231-244`, result stored in `rec["mid_confirm_interrupts"]`).
- `cco_actions._endturn_confirm` when the turn did not advance (`cco_actions.py:1480-1486`); `_await_pending_attack` calls `resolve_battle`/`cancel_declare_war_panel` directly (`1341-1368`).
- `interrupts.claim_screen` (`interrupts.py:1920-1957`) wraps `resolve` and is called from `click_actions.clear_screen` before any click action (`launcher/click_actions.py:127`), `cco_actions._clear_end_turn_blockers` (`1400`) and the hero-action close guard (`1190`).
- `loop._run_turn` also calls `I.colonize_blocker` after a colonize (`loop.py:655`).

Dispatch order inside one `resolve` round (`interrupts.py:1765-1916`): stuck-signature check → `nav.census_roots`/`dump_screen` (dev only) → `pending()` kinds → `resolve_battle` (`1803`) → `answer_ally_attacked` (`1808`) → `answer_incoming_proposal` (`1815`) → `answer_diplomacy` (`1822`) → `choose_dilemma` (`1827`) → `acknowledge_war_declared` (`1833`) → pending-unclaimed grace/report (`1839-1853`) → `nav.close_popups` (`1855`) → `cancel_declare_war` (`1862`) → undismissable/transient handling with `_dismiss_script_overlay` and `UnhandledScreen` raises (`1866-1915`).

### A.3 Kinds, screens and how options are built
Every kind ends in `_record_choice(kind, root, options, chosen, extra, executed, confirmed, refusal, latency_ms)` (`interrupts.py:1574-1589`). Observed kinds in the DB and their code paths:

| kind | root | builder | options dict per key | panel | extra keys passed | DB rows |
|---|---|---|---|---|---|---|
| `pre_battle` | `popup_pre_battle` | `resolve_prebattle` `598-667` | `_options_of` `1662-1674` → `{context, text}` (text always null in samples) | `prebattle_forecast` + `combatant_identity` `562-595` → `{result:{state,text}, casualties:{state,text}, ally_cqi, enemy_cqi, region?, n_ally_armies, n_enemy_armies}` | `panel`, `tree` (30-deep/40000-node dump `612`), `controls` | 20,236 |
| `battle_results` | `popup_battle_results` | `handle_results` `714-790` | `_options_of` over captive fates + `ADVANCE_PREFERENCE` `747-749` | `battle_facts_from` `1602-1659` → `{outcome, result_flag, rows[], resources{}, rewards[], armies{}, faction_names{}, settlement_captured, dismiss_visible, ally_cqi…}` (None → no panel) | `panel` | 13,552 |
| `occupation` | `settlement_captured` | `occupy` `793-820` | `options_from_nodes` `395-412` keyed by lower-cased `dy_option` text → `{context:None, text}` | `occupation_panel` `823-833` → `{region?, name?}` (empty dict → not stored) | `panel` | 6,755 |
| `dilemma` | `events` | `choose_dilemma` `1442-1532`, `_dilemma_options` `1701-1727`, `_with_identity` `1739-1746` | `{context, text, dilemma_id, option_id, payload[], subtree[nodes]}` `1508-1512` | none | `root_context`(=dilemma_id), `dilemma_id`, `tree` | 1,456 |
| `diplomacy_proposal` / `diplomacy_notice` | `diplomacy_dropdown` | `answer_incoming_proposal` `1282-1353` → `_drive_decision` `1111-1143` (or `_settle_answered` `1146-1173`) | `_options_of` + `{dilemma_id, option_id, payload:[], answer}` `1343-1348` | `_diplo_panel` `1052-1089` → `{strength_ranks[], reliability[], settlements, attitude, attitude_label, race, terms[], amounts[], sections[], demands[], offers[], treaties[], amount_demanded, amount_offered}` | `tree`, `panel`, `proposer`, `speech`, `attitude`, `answer`, (`settled_shell`) | 714 / 1 |
| `war_declared` | `diplomacy_dropdown` (via `_acknowledge_war_on_proposal` `1218-1237`) or any events root with "declared war on you" text (`acknowledge_war_declared` `867-905`) | `{context:None, text:label, dilemma_id, option_id, payload:[], subtree:[]}` | `_diplo_panel` (proposal path only) | `tree`, `root_context`, `panel` | 579 (all root `diplomacy_dropdown`) |
| `event_ack` | `events` | `choose_dilemma` ack branch `1470-1498` | `{context:None, text:label, dilemma_id:root, option_id, payload:[], subtree:[]}` | none | `tree`, `root_context`, `dilemma_id` | 74 |
| `declare_war_cancel` | `move_options` (`nav.DECLARE_WAR_ROOT` `nav.py:197`) or `diplomacy_dropdown` | `_cancel_declare_root` `419-445`, `cancel_declare_war_panel` `448-471`, `_cancel_declare_on_proposal` `1240-1268` | single `button_cancel_declare`/cancel control | `_diplo_panel` on the proposal path only | `tree`, `root_context`, (`panel`) | 21 (all `move_options`) |
| `ally_attacked` | `ally_attacked` | `answer_ally_attacked` `1386-1403` | `_options_of` + `answer` (`join`/`decline`) `1399-1401` | none | `tree`, `variant` | 4 |
| `diplomacy` | any "diplo" root not a HUD root | `answer_diplomacy` `908-968` | `_options_of` + `{answer, dilemma_id, option_id}` | `_diplo_panel` | `answer`, `tree`, `panel` | 0 |

Two record paths never call the chooser: `battle_results` with no captive fates (advance via `ADVANCE_PREFERENCE`, `735`) and `_cancel_declare_on_proposal` (`1256-1259`). Both still record `policy=_LAST_POLICY[0]` and merge `_LAST_SCORES[0]` from the previous `_choose` (`1579-1583`, `1589`).

### A.4 Decision (scoring) on the Python side
- `_choose(screen, options, campaign, panel, meta, live)` (`interrupts.py:114-147`) requires an installed chooser (`set_chooser`, `97`) and a live re-reader; refuses a pick not in the offered set (`131-134`) and raises `PhantomOption` if the pick is absent from the live panel (`135-142`). It then stores `_LAST_POLICY`/`_LAST_SCORES` (`143-144`).
- The chooser is installed by the advisor at `advisor/loop.py:207-210`: it wraps `IM.InterruptRanker.choose(screen, options, campaign_with_prev_actions_and_counts, panel, record, meta)`; `campaign` is `_CAMPAIGN[0]` set by `I.set_snapshot(record["campaign"], record)` after each decision snapshot (`loop.py:516`) and reset to `None` at campaign start (`206`); `record["world"]` is the last decision's world (`interrupt_model.py:307`).
- `InterruptRanker` (`advisor/interrupt_model.py:243-331`): loads `D:/twdata/models/interrupt/model.cbm` + `meta.json` (`MODEL_INTERRUPT`, `common.py:227`); `choose` draws a strategy from the interrupt mix (`_draw` `285-293`; allowed arms `random`,`greedy_catboost` only — `arms.py:10`, normalised by `advisor/policy.py:56-75`); `greedy_catboost` uses `score()` (`272-283`) which builds one feature row per option via `_row` (`97-131`) and returns **rank shares** (`_ranks`, `advisor/base_model.py:171-188`), not raw predictions; exploitation is gated per screen by `screen_rows >= MIN_ROWS(5)` (`295-301`), otherwise policy `greedy_catboost_random_fallback` (`320-324`). The returned `rich={opt:{"exploit":rank}}` is what `_record_choice` merges into `options_json` (`interrupts.py:1581-1583`).
- Sticky memo: `_sticky_choice` (`985-994`) keeps a pick per root for `_ANSWER_TTL=180s`, up to `_ANSWER_TRIES=2` (`976-978`); used by `pre_battle` (`614`) and `_drive_decision` (`1113`).

### A.5 Execution and confirmation
- Clicks go through `_click` (`325-352`): bus `click`, `_sample_ui_hiding` (`304-322`), settle loop on roots change or `until` predicate. Confirmation per kind: `pre_battle` waits for `battle_completed`/`panel battle_results` rows then `popup_battle_results` root (`_results_appeared` `670-694`) or for the `panel closed pre_battle` row for siege/retreat picks (`642-650`); `battle_results` = fates gone or roots changed (`761-775`); `occupation` = root gone (`811`); `dilemma` = `_dilemma_answered` (no clickable `dilemma_list` node, `1730-1736`) or root gone (`1518-1522`); proposals/ally = `_await_root_gone` up to 3 s (`997-1006`); `war_declared`/`event_ack`/`declare_war_cancel` = `_root_gone` 3×0.4 s (`270-281`).
- `refusal` ∈ {None, `execute_failed`, `command_silently_refused`, `confirm_unreadable_bus_failure`} from `_refusal` (`262-267`); `counted = executed and confirmed` (`1578`).
- Unhandled screens: `_report_unhandled` (`165-206`) appends a JSON line with roots/trees/screenshot to `common.UNHANDLED_LOG` (`D:/twdata/runs/human/unhandled_screens.jsonl`, `common.py:208`) and the caller raises `UnhandledScreen` (a `BaseException`, `224-225`), which ends the campaign as outcome `unhandled_screen` in `advisor/session.py:647-651`. File measured: 255 lines / 5.95 MB; screens: `transient_overlay_persisted` 214, `pending_surface` 27, `stuck_unchanged` 6, `battle_results` 5, `dilemma` 3.

### A.6 Record → bus → recorder → DB
1. `_record_choice` appends `dict(extra, kind, root, options(with scores), chosen, chosen_context, executed, confirmed, counted, refusal, latency_ms, policy, ts=time.time())` to the in-process `_INTERRUPT_LOG` (`interrupts.py:1584-1589`).
2. The advisor drains it with `drain_interrupt_records` (`1592-1595`) via `loop._drain_interrupts` (`loop.py:366-386`) at three moments only: turn open after the opening `resolve` (`407`), after each executed action (`664`), and in `run_campaign`'s `finally` after `executor.kill_game()` (`320-322`). Each record goes to `journal.log_interrupt` (`decisions/journal.py:204-207`), which renames `kind`→`screen` and calls `_ask(run_dir, "interrupt", body)` with `req_id=None` (`77-82`): `INSERT INTO rpc_requests(req_id,kind,ts,payload)` with `ts=time.time()` at ask time and the whole record (tree included) JSON-dumped, then `pg_notify('rpc_requests')`.
3. The recorder (`decisions/decisions_stream.py:116-118`) reads with `journal.read_requests` (`98-111`), which **overwrites** `body["ts"]` with the request's `ts` and `body["kind"]` with the rpc kind (`108`). For `kind == "interrupt"` it calls `collect.campaign_state(bus)` and `collect.world_state(bus)` **at processing time** (`decisions_stream.py:151-159`) and `store.write_interrupt(dict(row, campaign=cs, world=ws))`.
4. `DecisionStore.write_interrupt` (`decisions/store.py:482-507`) inserts `(ts, campaign_id, turn, kind=row["screen"], root, root_context, n_options=len(options), options_json, chosen, chosen_context, executed, confirmed, counted, refusal, latency_ms, campaign_blob, world_blob, panel_blob, policy)`; `campaign_id` resolves through `_campaign_id(campaign_key(faction, campaign_uuid), faction)` (`495-497`, `148-186`), creating a `campaigns` row with NULL map/selector/difficulty/leader if the key is new; `turn = int(campaign_state.turn)` (`498`); blobs are sha-deduped text (`_blob`, `110-127`). `tree`, `controls`, `answer`, `proposer`, `speech`, `attitude`, `variant`, `dilemma_id`, `settled_shell` in the record are **not** stored (the view even carries `NULL AS tree_json`, `decisions/pg_schema.py:245`).

### A.7 Readers
- Training: `interrupt_model.gather` → `DecisionStore.interrupt_rows()` (`store.py:739-765`) — see H.
- In-process memory: `loop._drain_interrupts` feeds the same records to `CampaignMemory.feed_interrupts` (`loop.py:381-385`; `advisor/memory.py:153-178` uses `kind=="pre_battle"`, `counted`, `ts`, `panel.result.state`, `panel.casualties.text`, `chosen`).
- API: `queries.menus` (`advisor_api/queries.py:3765-3809`, route `/api/decisions/menus` `app.py:676-679`; last `MENUS_ROWS=60` rows from `interrupt_decisions`, per-option `exploit`/`gnn`), `queries.matrix(kind="interrupt")` (`3399-3407`, route `/api/campaigns/matrix` `app.py:505-509`), `queries.correlations` interrupt tile (`4579-4581`), `_interrupt_coverage` (`3746-3762`), `db.py:80` watermark `MAX(interrupt_id)`.
- Session post-mortem evidence: `session._ending_evidence` reads `interrupt_decisions` for the last 6 battle-kind rows (`advisor/session.py:219-224`).
- Analytics: `analytics/health_extract.interrupt_stats` (`76-91`; joins the latest decision's `version_id` per interrupt by `d.ts <= i.ts`). `analytics/state_facts.py` does not read interrupts. `decisions/dilemma_audit.py:16-50` audits dilemma option identity. `debugging/timeline.py:263-270` lists interrupts by ts window.

---

## B. DATA MODEL AS-IS

### B.1 `interrupts` row (DDL `decisions/pg_schema.py:97-106`; index `ix_interrupts_campaign(campaign_id, interrupt_id)` `159` only)
`interrupt_id` identity; `ts` double (= rpc ask time, see C.1); `campaign_id` FK; `turn` int; `kind` text NOT NULL; `root`; `root_context`; `n_options`; `options_json` text; `chosen`; `chosen_context`; `executed/confirmed/counted` smallint nullable; `refusal`; `latency_ms`; `campaign_blob/world_blob/panel_blob` FK blobs; `policy`. View `interrupt_decisions` (`238-251`) joins `campaigns.campaign_key` and the three blob texts.

Column facts measured over all 43,392 rows:
- `executed`/`confirmed`/`counted` NULL only on 3 `dilemma` rows (`confirm_unreadable_bus_failure`); `chosen` never NULL; `chosen` always one of the `options_json` keys (0 mismatches in an 8,679-row sample).
- `chosen_context` is NULL on **all** rows (every option builder writes `context: None` or the node has no CCO context).
- `root_context` NULL on `pre_battle`, `battle_results`, `occupation`, `diplomacy_proposal`, `diplomacy_notice`, `ally_attacked` (their `extra` never sets it); set on `dilemma` (= shared dilemma record prefix, e.g. `CcoCdirEventsDilemmaChoiceDetailRecordwh2_main_dilemma_loyalty_def_price_of_treachery_1`; matches every option's `dilemma_id` in 467/467 sampled rows), `war_declared`/`declare_war_cancel`/`event_ack` (= root name).
- `campaign_blob` never NULL; `world_blob` NULL on 6 rows (5 `pre_battle`, 1 `battle_results`; `world_state` raised, `decisions_stream.py:154-158`); `panel_blob` NULL on all `dilemma` (1,456), `event_ack` (74), `declare_war_cancel` (21), `ally_attacked` (4), on 5,730/6,755 `occupation` (empty `occupation_panel` dict is dropped by `{"panel": panel} if panel else None`, `807`/`817`; all rows in the first four id-quintiles are NULL, 277/1,302 in the newest), 538/13,552 `battle_results` (`battle_facts_from` returned None), 36/20,236 `pre_battle`.
- `policy`: `greedy_catboost` 31,444; `random` 11,834; `marwil_gnn` 103 (an arm no longer in `arms.INTERRUPT_NAMES`); `greedy_catboost_random_fallback` 11; never NULL.
- `latency_ms` never NULL; p50 by kind: occupation 545, pre_battle 744, battle_results 786, dilemma 793, diplomacy_proposal 1,414, war_declared 2,557; max 22,773 (dilemma).
- `n_options`: pre_battle avg 2.36/max 3; battle_results 1.59/3; occupation 3.39/5; dilemma 2.50/9; proposal 2.00/2; the rest 1 (ally_attacked 1.25/2).
- `options_json` length: avg 145 B pre_battle, 108 battle_results, 219 occupation, 305 proposal; **dilemma avg 14,412 B, max 38,900 B**; total 27.2 MB of which dilemma 21.0 MB (77%); inside dilemma rows the per-option `subtree` node dumps account for essentially all bytes (re-serialised subtree = 100.8% of the compact stored size on a 467-row sample; `payload` 1.2%).

### B.2 `options_json` shape per kind (from code, confirmed on 3 sampled rows per kind)
- `pre_battle`: `{button_autoresolve|button_retreat|button_surround|button_continue_siege: {context:null, text:null, exploit:float}}`; panel `{ally_cqi, enemy_cqi, casualties:{state,text}, result:{state,text}, n_ally_armies, n_enemy_armies, region?}` (sample of 5,036: `result.state` 5,035, `casualties.state` 5,035, `region` 475, `ally_cqi` 844).
- `battle_results`: fates `{button_captive_option_kill|enslave|release|enslave_replenishment_only|enslave_slaves_only: {context:null,text:null,exploit}}` when a captive choice exists; otherwise `{button_accept|button_dismiss: {context:null, text:null, score:null}}` (8,654 rows carry `score:null`, 1,135 carry neither key); panel `{ally_cqi, armies:{allies[],enemy[]}, faction_names{}, n_*_armies, outcome, resources{ui-path: text}, result_flag, rewards[{text,label}], rows[{row_id,…dy_* cells}], settlement_captured}`.
- `occupation`: keys are the lower-cased option texts (`colonise`, `do nothing`, `occupy`, `sack`, `loot & occupy`, `raze (elven heritage)`, `skulls for the skull throne`, …) → `{context:null, text:<same>, exploit}`; panel `{name, region}` when present.
- `dilemma`: keys `Cco…Record<dilemma><FIRST|SECOND|THIRD|FOURTH|FIFTH|…>` → `{context:null, text:label, dilemma_id, option_id:'FIRST'…, payload:[strings], subtree:[12–16 node dicts × 14 keys], exploit}`.
- `diplomacy_proposal`: `{button_accept:{answer:'accept',…}, button_cancel:{answer:'decline',…}}` each with `context:null, text:null, dilemma_id:'diplomacy_dropdown', option_id, payload:[], exploit`; `diplomacy_notice`: single `button_accept` with `answer:'acknowledge'`; panel = `_diplo_panel` dict (attitude as string, e.g. `'-40'`).
- `war_declared`: `{button_ok_war_declared:{context:null, text:'button_ok_war_declared', dilemma_id:'diplomacy_dropdown', option_id, payload:[], subtree:[], exploit}}` + `_diplo_panel`.
- `event_ack`: `{text_button|button_accept:{…, text:'Continue'|'button_accept', dilemma_id:'events', subtree:[]}}`.
- `declare_war_cancel`: `{button_cancel_declare:{text:'Cancel Move', dilemma_id:'move_options', …}}`.
- `ally_attacked`: `{button_join_defender|button_join_aggressor|decline_button:{answer:'join'|'decline', context:null, text:null}}`.

### B.3 Blobs
- `campaign_blob` = `collect.campaign_state` (`decisions/collect.py:243-273`): 20 scalar keys (`faction, turn, income, settlements, treasury, is_researching, faction_cqi, campaign_uuid, armies, lord_level, allies, vassals, power_rank, _eval_ms, ll_wounded, game_version, defeated, difficulty, leader`) — sampled n≈400 B. It lacks the decision snapshot's `resources`, `effect_bundles`, `campaign_map`, `presave_radius`, `selector`, `hero_type_counts`, `read_failures` (`collect.py:1683-1689`, `1835`, `1863`).
- `world_blob` = `collect.world_state` (`416-428`): `{armies, settlements, hostiles, enemy_agents, ruins, regions}` — sampled ~7 kB; lacks the decision world's `war_graph`, `relations`, `diplo_schema`, `diplo_unseen`, `diplo_hostile_rows`, `citizenry`, `stationed` and the per-army `ap` fields (`1702-1722`).
- Sharing: **0** interrupt `campaign_blob` ids and **0** `world_blob` ids are used by any `decisions` row (different key sets → different sha). Distinct blobs: 30,015 campaign (12.3 MB), 19,041 world (174.9 MB), 12,404 panel (9.4 MB). Because the recorder samples the world once per processing moment, 34,578 rows share their `world_blob` with another interrupt row (drain batches), and 24,789 rows share a `panel_blob`.

### B.4 Relation to decisions
- No FK or column links an interrupt to a decision; there is no `decision_id`/`decision_seq`; interrupts are not part of the `decisions` sequence and are not entities/offers/taken.
- Same `campaigns` row when the uuid matches; 166 rows (78 campaigns) sit on campaign rows that have no decision at all (see C.5).
- Ordering: `ts` is the drain/ask time (C.1). Joining each interrupt to the nearest earlier/later decision of the same campaign (sample `id%3=0`, 14,463 rows): gap to previous decision p50 7–11 s (war_declared 24 s), gap to next decision p50 1.3–1.9 s; 12,393 of 43,220 joinable rows have `ts` earlier than the first decision of their recorded `turn` (`turn` = previous decision's turn + 1 in 1,707/6,675 pre_battle, 1,508/4,558 battle_results, 362/467 dilemma, 250/251 diplomacy_proposal, 176/192 war_declared samples) — i.e. inter-turn (AI-phase) interrupts are stamped with the **next** player turn and that turn's campaign state, because they are drained at the next turn open (`loop.py:407`) after `journal.request_turn` (`397`). No row differs from its preceding decision's turn by more than 1; 172 rows have a `turn` that no decision of the campaign has.
- Density: 0.21 interrupts per decision; 4,495 of 4,827 campaigns have interrupts, avg 9.65, max 63.

---

## C. WHAT IS INCORRECT OR LOSSY TODAY (observed facts)

C.1 **`interrupts.ts` is not the choice time.** `_record_choice` stamps `ts=time.time()` (`interrupts.py:1589`), but `journal.read_requests` overwrites the payload `ts` with `rpc_requests.ts` (`journal.py:108`), and `write_interrupt` stores that (`store.py:494`). Records drained together share a timestamp: 20,778 of 43,392 rows are within 0.1 s of the previous row of the same campaign; batch sizes reach 13 (5,931 batches of 2, 5,979 of 3, 517 of 4, … 1 of 13). Example: rows 43266/43267/43268/… (`pre_battle, battle_results, pre_battle, battle_results, dilemma`) all within 0.01 s. The still-present `rpc_requests` rows show the true lag between choice and ask: p50 1.2 s, max 8.5 s (n=54). Consequence: intra-batch order is only recoverable from `interrupt_id`; any time-window join (e.g. `health_extract.interrupt_stats` version join by `d.ts <= i.ts`, `memory.feed_interrupts` `PB_WINDOW_S`) uses the drain time.

C.2 **`turn` and the state blobs are sampled at recorder processing time, not at panel time** (`decisions_stream.py:152-159`). Measured: 12,393/43,220 rows carry the next turn (B.4). Their `campaign_blob` (`income`, `settlements`, `treasury`, …) reflects the post-AI-phase state.

C.3 **The chooser's state is stale or empty for many interrupts.** `_CAMPAIGN[0]`/`_RECORD[0]` are the last decision snapshot (`loop.py:516`) and `None` at campaign start (`206`). Opening interrupts of turn 1 and all inter-turn interrupts are scored against the previous decision's campaign/world, and the first campaign's opening screens against an empty dict (`F.campaign_block({}, {})`). This matches training (H) but is not the state at the panel.

C.4 **`policy` and per-option scores are inherited on chooser-less records.** `battle_results` advance rows (`button_accept`/`button_dismiss`, 9,300 rows) never call `_choose` (`interrupts.py:735`) yet record `policy` = `greedy_catboost` 7,108 / `random` 2,168 / `marwil_gnn` 24 from the previous `_choose` (`1589`) and merge the previous scores → `"score": null` on 8,654 rows (`1581-1583`). Same for `_cancel_declare_on_proposal` (`1259`). API `menus`/`correlations` count these as picks of that arm (`queries.py:3773-3782`, `4579-4581`).

C.5 **End-of-campaign drains are attributed to the next campaign.** The `finally` drain at `loop.py:320-322` runs after `kill_game()`; the recorder answers those requests against the next loaded game, before `bus_launcher.stamp_campaign_uuid` (`launcher/bus_launcher.py:388-400`) so the key is the presave's base uuid. DB: 78 `campaigns` rows with `first_decision_id IS NULL` and `campaign_map/selector/difficulty/leader` NULL, holding 166 interrupts (137 `battle_results`, 23 `war_declared`, 4 `pre_battle`, 2 `dilemma`); 166/166 have a stamped campaign `key LIKE base||'_%'` starting within 10 min after and a postmortem within ±2 min; 120/166 have a decision in the 30 s before. These rows are unreachable by `window_keys`/`campaign_snapshots` and silently dropped from training.

C.6 **Duplicate records for one screen with a false refusal.** `war_declared`: 283/579 rows `command_silently_refused` (`_root_gone` polls 3×0.4 s, `270-281`); 273 of them are followed by another `war_declared` row within 60 s (275 rows have a same-root twin within 5 s). `event_ack`: 50/74 refused. `declare_war_cancel`: 21/21 refused (`move_options` cancel, `_cancel_declare_root` records `confirmed=_root_gone`). Since 2026-09-01: war_declared 25/54 refused, event_ack 7/9, battle_results 200/2,320, pre_battle 7/3,570.

C.7 **`dilemma` rows recorded as refused although the choice landed.** 452/1,456 dilemma rows have `counted=0` (`command_silently_refused`), all dated 2026-08-18..08-31; only 1 is followed by another row for the same `root_context` within 120 s. Since 09-01: 0 refused of 250, and 134/136 counted dilemmas have a `DilemmaChoiceMadeEvent` in `app.game_event` within the window. The 452 old rows therefore carry a chosen option that was executed but is stored as not counted (`interrupt_model.gather` keeps them: it filters only on `chosen`, `interrupt_model.py:153`).

C.8 **Options with no label/identity.** `pre_battle`, `battle_results`, `diplomacy_proposal`, `ally_attacked` options have `text:null` and `context:null` (`_options_of` finds no text on the button node); `chosen_context` is NULL on 100% of rows, so `isc_option_label`/`isc_dilemma_id`/`isc_option_id` are `"none"` for every non-dilemma screen in training (`interrupt_model.py:102-104`).

C.9 **Dilemma `options_json` bloat.** Each option embeds `subtree` (every node dict under the dilemma record, `interrupts.py:1724-1726`); 21.0 MB of the 27.2 MB column; no reader consumes `subtree` (`interrupt_model._row` reads `dilemma_id/option_id/text/payload` only, `102-106`; API `_options_of` reads `exploit/gnn`, `3754`; `dilemma_audit` reads `dilemma_id/option_id/text`).

C.10 **Panel is missing where the code has it.** `dilemma` passes no `panel` (extra at `1526-1527`), so `_local_block`/`isc_dip_*`/`isc_fc_*` are all `none`/0 for dilemmas; `occupation` drops the panel when `occupation_panel` finds neither region context nor name (5,730 rows); `event_ack`/`declare_war_cancel`/`ally_attacked` never carry a panel.

C.11 **Full UI trees are shipped and discarded.** The `pre_battle` record includes the 30-deep tree (`612`, `663`) and `controls`; `dilemma`/`war_declared`/`event_ack`/`declare_war_cancel`/proposal/ally records include `tree`. They travel through `rpc_requests` (measured: `pre_battle` request payload avg 685 kB, max 970 kB, 531–2,437 nodes, 100% tree; `war_declared` avg 82 kB, 99% tree) and are dropped by `write_interrupt`. `answer`, `proposer`, `speech`, `attitude`, `variant`, `settled_shell`, `controls` are dropped likewise (they survive only in `diplomacy_events` for the proposal kinds).

C.12 **`answer_diplomacy` (kind `diplomacy`, channel `diplomacy_hud`) has never produced a row** (0 interrupts, 0 diplomacy_events) — the proposal root `diplomacy_dropdown` is excluded by `DIPLOMACY_HUD_ROOTS` (`504-506`) and handled by `answer_incoming_proposal` first (`1815`).

C.13 **Legacy arm names in the corpus.** `policy='marwil_gnn'` on 103 rows and `"gnn"` per-option keys on 147 rows predate the current arms; `queries._INTERRUPT_SCORERS` still looks for `marwil_gnn`/`gnn` (`3743`), so `_interrupt_coverage` "compared/agree" counts come only from those legacy rows.

C.14 **`loop.verify_streams` cannot run against Postgres**: it uses `?` placeholders (`loop.py:797-815`); psycopg raises `ProgrammingError: the query has 0 placeholders but 1 parameters were passed` (verified). `session.py:682-694` swallows the error into `entry["streams"]={"error":…}`.

C.15 **`interrupts.turn` is derived from the recorder's `campaign_state.turn` with `or 0`** (`store.py:498`); no row has `turn=0` today, but 172 rows carry a turn no decision of the campaign has.

C.16 Storage facts: interrupt-only world blobs 174.9 MB (19,041 distinct) are read by `interrupt_rows()` (383.8 MB of JSON text with repeats) and used by nothing (H.2); the `interrupt_decisions` view carries a permanent `NULL AS tree_json` column (`pg_schema.py:245`).

---

## D. DIPLOMACY_EVENTS

DDL `pg_schema.py:122-124` (`event_id, ts, campaign_key, turn, kind, payload`), index `ix_diplo_ev(campaign_key, event_id)` (`150`).

Writers (all through `diplo_stream.emit` → `journal.log_diplomacy` → `_ask("diplomacy")` → recorder → `store.write_diplomacy_event`, `decisions_stream.py:164-166`, `store.py:444-455`):
- `emit(kind, **fields)` (`launcher/diplo_stream.py:51-64`) adds `kind, turn=TURN[0], campaign_key=CAMPAIGN[0], ts` (TURN/CAMPAIGN set at `loop.py:398-399` from `request_turn`); drops rows with a warning when `run_dir` is unset (`53-57`).
- `deal/outgoing`: `diplomacy_actions._emit_stream` (`launcher/diplomacy_actions.py:89-96`) after every `diplomacy` action (`99-124`): `{channel:'outgoing', faction, terms[], ok, gift, panel:<diplomacy.propose result>, treaty_before:{at_war,allied,trade,our_master,their_vassal,standing}}`; `panel` is `diplomacy.propose`'s dict (`launcher/diplomacy.py:625-674`: `faction, requested, stage, ok, failed_at, staged, unavailable, gift, success_chance, chance_carried_in, sendable, sent, refused_by_ai, response{answer,accepted,text}, accepted, exited, declared, declare_walk{…}`).
- `deal/diplomacy_proposal|diplomacy_notice|ally_attacked`: `interrupts._emit_deal` (`1092-1108`) from `_drive_decision` (`1140`) and `_settle_answered` (`1169`): `{channel:<kind>, chosen, answer, options[], executed, confirmed, policy, proposer, speech, attitude, variant, facts:{strength_ranks,reliability,settlements}, faction_keys[], pair:{faction_key:{treaty…}} (only when confirmed)}`.
- `deal/diplomacy_hud`: `answer_diplomacy` (`957-967`) — 0 rows.
- `pair_checkpoint`: `diplo_stream.checkpoint` (`88-90`) per tracked faction (`track` cap 128, `31-45`) once per settled turn (`loop.py:698-703`): `{faction, pair:<treaty dict or null>}`.
- `campaign_end`: `loop.py:324-326`: `{turns_played, ended_by, tracked[]}`.

Stored shape facts (measured):
- `kind` is **'diplomacy' on all 74,145 rows**: `read_requests` overwrites `kind` with the rpc kind (`journal.py:108`) and `write_diplomacy_event` stores `row.get("kind")` while stripping it from the payload (`store.py:446-453`); the emit kind (`deal`/`pair_checkpoint`/`campaign_end`) is lost and only inferable from payload shape: pair_checkpoint 49,772 (avg 178 B), deal/outgoing 18,942 (651 B), campaign_end 4,707, deal/diplomacy_proposal 719, deal/ally_attacked 4, deal/diplomacy_notice 1.
- `req_id:null` and `rpc_id:int` leak into every payload (from `read_requests`, not stripped at `store.py:446-447`).
- `campaign_key`/`turn` never NULL; 65 rows reference a `campaign_key` absent from `campaigns`.
- Outgoing deals: 3,510 `declare_war` ok/accepted; 2,565 with no first term (gift-only) accepted; 1,545 `declare_war` failed at `faction_selection`; large `ai_would_refuse` groups (`defensive_alliance` 1,779, `soft_access` 1,029, `peace` 918, …); `vassal` `deal_selection` 733.
- Proposal deals: accept/confirmed 459, decline/confirmed 258, decline/unconfirmed 2; `pair` present on all confirmed rows. Pair checkpoints: avg 11.5 rows and 3.0 distinct factions per campaign, max 143; 48 checkpoints have `pair: null`.

Readers:
- `DecisionStore.diplomacy_events()` (`store.py:457-476`) — no caller in the tree.
- API `queries.diplomacy_tail` (`4881-4911`, route via `app.py:586`): reads `deal_score`/`success_chance`, `outcome`/`result`, `target`/`faction`, `standing`, `terms`/`speech` at payload top level; measured presence: `deal_score` 0, `outcome` 0, `result` 0, `target` 0, `standing` 0 (it is nested in `pair`/`treaty_before`), `success_chance` 0 (nested in `panel`), `faction` 68,714, `terms` 18,942, `speech` 724 → the API's `outcome`, `deal_score`, `standing` and `state` are always null/neutral for the current writers.
- `debugging/timeline.py:298-302` (raw payload prefix).

---

## E. POSTMORTEMS

DDL `pg_schema.py:116-120` (`postmortem_id, campaign_key, ts, run_dir, faction, turn, outcome, defeated, reason, payload`); no index.

Writer: `advisor/session._postmortem` (`278-323`) builds `rec` = `{ts, when, campaign_key=entry.campaign_uuid, campaign=<index int>, faction=<plan>, picked_ts, policy, outcome, error, seconds, turns_played, actions, confirmed, ended_by[], growth, run_dir, code_version, wh3_running, turn_tail (last 6 loop_report rows), errors_tail, trajectory[], recent_battles[] (from interrupt_decisions), defeat_row, campaign_source?, plausibility{verdict,evidence}, game_logs}` (`_ending_evidence` `202-275`) → `journal.log_postmortem` (`223-224`) → recorder `store.write_postmortem` (`decisions_stream.py:170-175`, `store.py:403-427`): columns `turn = turn_at_death or turns_played` (a count of turns played, not a game turn), `defeated = outcome=='defeated'`, `reason = ended_by (str) else json(ended_by) else error`, `payload = whole rec`; then `UPDATE campaigns SET outcome, defeated` and `picked_ts` when `campaign_key` is set (`417-425`).

Measured: outcomes `stagnant` 3,985, `unhandled_screen` 624, `stuck` 248, `defeated` 158, `error` 4, `completed` 1. Payload avg 6–8 kB (80% is `turn_tail`). `campaign_key` NULL on all 624 `unhandled_screen` rows and all 4 `error` rows: `UnhandledScreen` is a `BaseException` (`interrupts.py:224`), `run_campaign` never attaches `rows` to it (`loop.py:311-318`), so `_played(e)` is `{}` (`session.py:1088-1096`) and `campaign_uuid` stays unset; `_postmortem` is called twice for such campaigns (`session.py:648` and `677`) → 496 same-faction `unhandled_screen` pairs within 60 s, and `campaigns.outcome` stays NULL (435 NULL outcomes; 357 campaigns with decisions have no postmortem keyed to them). `reason` for `stagnant` rows is the `ended_by` list (e.g. `["end_turn_chosen",…,"ll_wounded"]`), not the growth verdict (which lives in `payload.plausibility.verdict`, 625 rows `n/a`). `req_id`/`rpc_id`/`kind:'postmortem'` leak into the payload.

Readers: `DecisionStore.postmortems()` (`store.py:429-442`, no caller); `analytics/health_extract.postmortem_stats` (`96-…`: `code_version`, `outcome`, `plausibility.verdict`, `seconds`, `turns_played`); `analytics/state_facts._CampaignEndings` (`363-405`) derives `campaign_endings(campaign_key, ts, faction, outcome, when, error, verdict, suspicious, because)` used by API `outcome_join` (`queries.py:175-190`) — rows with NULL key are skipped (`state_facts.py:387-388`); `debugging/timeline.py:293-297`.

---

## F. UCB_PICKS / UCB_PICK_ROWS

DDL `pg_schema.py:126-143` (`ucb_picks(pick_id, ts, c, total_plays, campaign_map, faction, n, mean, explore, score, tied, blend, entropy, std, adjust)`, `ucb_pick_rows(pick_id, rank, …, chosen, …)` PK `(pick_id, rank)`; index `ix_ucb_picks_ts` `151`).

Writer: `session._ucb_pick` (`advisor/session.py:143-184`) once per campaign start when `--ucb C` is given: scores every presave in the pool with `ucb_stats.score` (`ucb_stats.py:78-83`: `blend + c*sqrt(ln total / n) + adjust`, `inf` under `MIN_PLAYS=2`), draws among ties, and `journal.log_ucb_pick` (`179-183`) with `{ts:picked_ts, c, k, scale, total_plays, tied, chosen:<cell>, rows:[cell+chosen …]}`; recorder `store.write_ucb_pick` (`374-401`) writes the head row and one `ucb_pick_rows` row per pool entry (`inf` → NULL via `_real_or_none`/psycopg float → stored NULL on 311 rows). `k`/`scale` are not stored.

Measured: 3,853 picks (2026-08-21..09-03), **129 rows per pick** (min=avg=max) → 497,037 rows, 23.7 kB per pick; `blend` NULL 500, `adjust` NULL 2,665 (column added later, `142-143`), `score`/`explore` NULL 44; `tied` avg 1.5 max 32; `c` in [2,4]; exactly one `chosen=1` row per pick; 3,657/3,853 picks match a `campaigns.picked_ts` (same map/faction, within 600 s). Volume per decision: one head row + 129 detail rows per campaign vs 43.6 decisions per campaign.

Readers: API `queries.pick_campaigns` (`670-692`, join by `picked_ts` within `PICK_JOIN_S=120`), `ucb_context` (`695-740`), `ucb_pick_counts` (`938-940`), `ucb_pick_series` (`5100-5166`, reads `rank<=2` and all `n` per pick), `ucb_pick_rows` (`5243-5269`), `current` (`211-213`); routes `app.py:151`, `478-480`, `495`. `debugging/timeline.py:305-310`. `session._start_gain_stats` reads `campaign_gains`, not these tables (`119-130`).

---

## G. RPC_REQUESTS / RPC_RESPONSES

Purpose: the advisor↔recorder queue over Postgres (`journal.py`): `_ask` inserts a request and `pg_notify` (`77-82`); the recorder polls `read_requests(after_id)` (`98-111`) after `LISTEN rpc_requests` (`38`) and `wait_requests` (`114-117`, `POLL=0.1`); only kinds created with a `req_id` (`snapshot`, `turn`, `hash`; `_new_id` `70-74`) get an `rpc_responses` row via `respond` (`85-95`) which the advisor awaits in `_await` (`141-166`); error replies also only when `rid` exists (`decisions_stream.py:211-213`). Fire-and-forget kinds: `interrupt`, `options`, `pick`, `verification`, `postmortem`, `ucb_pick`, `diplomacy` (`204-232`).

Retention: `journal.prune` deletes requests with `rpc_id<=after_id AND ts<now-900` and responses `ts<now-900` (`129-138`), invoked every `PRUNE_EVERY=600` recorder ticks (`decisions_stream.py:21`, `214-219`). Measured: 1,239 requests spanning 952 s (2026-09-03 20:28:08..20:44:00 local), `rpc_id` 674,837..676,075 (≈675k requests issued lifetime); 325 responses (snapshot 246, hash 56, turn 23), 0 errors, 0 orphans in either direction. The recorder has not pruned since its last tick (latest request 2026-09-03 20:44; the table is a frozen tail of the last session).

Payload duplication (all measured on the surviving rows): `snapshot` requests carry only `{active}` (139 B); `options` avg 34 kB → stored as `offers` (`store.attach_options`); `pick` avg 38 kB (pick + all scores + timings) → `offer_scores`/`offer_model_scores`/`taken`/`decisions.timings`; `verification` avg 3.6 kB → `taken`; `interrupt` avg **346 kB** (pre_battle 685 kB avg, 970 kB max: 100% tree) → `interrupts` minus tree/controls; `diplomacy` 475 B → `diplomacy_events`; `postmortem` 9.5 kB → `postmortems.payload` verbatim; `ucb_pick` 35.6 kB → `ucb_picks`+`ucb_pick_rows`. So every request payload is a transient duplicate of a durable table, except the discarded tree/controls fields and the true record `ts` (C.1), which exist only here for ≤15 min.

Other readers: `debugging/timeline.py:271-292`.

---

## H. TRAINING USE (`advisor/interrupt_model.py`)

### H.1 Read path and volume
- `gather()` (`134-183`): for the single run DB (`common.run_dbs`, `common.py:198-206`) opens `DecisionStore(readonly=True)` and reads `target_series()` (`store.py:767-777`, view `turn_open`), `window_floor/keys(TRAIN_WINDOW_CAMPAIGNS=1000)` (`532-547`; `common.LOOKBACK_CAMPAIGNS=1000`, `common.py:144`), `action_sequence` (`726-737`), `campaign_snapshots` (`705-724`: every decision's campaign+world JSON ≥ floor), then **`interrupt_rows()`** (`739-765`): one unbounded `SELECT … FROM interrupts LEFT JOIN campaigns LEFT JOIN blobs×3 ORDER BY interrupt_id`, decoding `options_json`, campaign, world and panel JSON for **all 43,392 rows** — measured 27.2 MB options + 17.8 MB campaign + **383.8 MB world** + 14.9 MB panel of text. Rows are then filtered in Python: `chosen` present (`153`), campaign in the 1,000-campaign window (`155`), a preceding decision snapshot by `rts >= t` (`158`), non-null deltas (`161`), non-null target (`177`).
- Measured eligibility now: 12,044 rows with `chosen` in the current window, 12,012 with a preceding decision snapshot, all with a later `turn_open`; `meta.json` (trained 2026-09-04 12:25) reports **11,477 rows over 971 campaigns**; per screen: pre_battle 5,593, battle_results 3,565, occupation 1,687, dilemma 325, diplomacy_proposal 178, war_declared 109, event_ack 18, diplomacy_notice 1, ally_attacked 1. Fit: `val_r2` 0.519, `val_rmse` 1.62, 67 iterations (`fit_es`, `base_model.py:126-151`, params `CB_INTERRUPT_PARAMS` `41-53`).

### H.2 Field matrix — which stored fields the trainer consumes
| stored field | used at | note |
|---|---|---|
| `interrupts.kind` (`screen`) | `_state_row` `isc_screen` (`30`), `screen_rows` gate (`196-198`, `295-301`) | |
| `interrupts.chosen` | filter (`153`); `isc_option` (`100`); `meta[option]` lookup (`101`) | |
| `interrupts.options_json` | `n_options` (`165`) → `isc_n_options` (`31`); per-option `dilemma_id, option_id, text, payload` (`102-106`) | `subtree`, `exploit`, `score`, `context`, `answer` unused |
| `interrupts.ts` | picks the base decision snapshot `rts >= t` (`158`) and past actions `rts > t` (`163`) | drain time (C.1) |
| `interrupts.turn` | `decision_deltas(base, series, turn)` (`160`) | next-turn attribution (C.2) |
| `campaigns.campaign_key` (`campaign_id` in the row) | window filter, snapshots, groups (`155-166`, `182`) | |
| `panel_blob` | `_local_block` (`ally_cqi, enemy_cqi, region, n_ally_armies, n_enemy_armies`, `57-94`), `isc_fc_result/casualties` (`108-109`), `isc_dip_*` (`110-130`) | |
| `campaign_blob`, `world_blob` | **not used** — `base`/`base_world` come from the preceding *decision* (`158-159`, `166`) | loaded and discarded |
| `executed/confirmed/counted/refusal/latency_ms/policy/root/root_context/chosen_context` | not used | refused rows are trained on |
| decisions `campaign_blob`/`world_blob` | `F.campaign_block` (`features.py:122-154`), `stamp_prev_actions` (`164`), `_local_block` world lookups | |
| `turn_open` scalars | target: `gain(future_max − base)` over `settlements, lord_level, allies, vassals` weighted 1/1/1/3 (`base_model.py:20`, `208-226`) | |

Inference (`score`, `272-283`) uses the same `_row` with `campaign` = advisor's last decision campaign stamped with `prev_action_*`/`action_counts` (`loop.py:208-209`), `world` = last decision record's world (`307`), `panel` = the live panel dict, `meta` = the live options dict — consistent with training's use of the preceding decision snapshot, and equally stale (C.3).

### H.3 Consumers of the trained model
`InterruptRanker` in the advisor (`loop.py:197-222`, hard-fails when `greedy_catboost` is in the mix and the model is not loaded, `211-215`); `session._require_models` (`351-378`) and retrain child `advisor/interrupt_model.py` (`525-529`); API model tile `queries._MODEL_DIRS` (`3861-3864`).