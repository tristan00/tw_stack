# tw_stack bloat catalogue

Read-only audit, 2026-09-06. Every candidate for deletion in the checkout and under `D:\twdata`, with what it costs today and what is lost if it goes. Nothing was changed. Premise throughout: one game process plays one campaign, and `runctl.py up` is the only path that matters.

Basis: repo-wide reference counts (AST + grep over py/lua/ts/tsx/sql/ps1/md), pyflakes, byte comparison of assets, git history, the recorder's live `errors.log`, a read-only Postgres size query, and directory walks of `D:\twdata`.

| Headline | Figure |
|---|---|
| Code deletable with no change to how a run plays (high confidence) | ~9,500 lines |
| More lines recoverable by collapsing copy-pasted blocks | ~800 lines |
| Finished-migration output and unreferenced design docs still tracked | ~34,000 lines, 16 MB of PNG |
| On disk under twdata that no process ever reads back | ~120 GB |
| Written on 3 Sept alone into three write-only stores | 56 GB |

Contents: A. splitter chain · B. recorder streams · C. orphan modules · D. dead under the configured run · E. copy-paste · F. API output nobody renders · G. dashboard dead code · H. docs and instruction bloat · I. disk · J. checked and clean · K. suggested order

---

## A. The campaign splitter chain

The 519-line splitter is the visible end of a mechanism that is wired in but structurally cannot do anything. The whole chain is about 610 lines and one regex pass over every chunk of the game log.

| Piece | Where | Lines | What actually happens | Conf. |
|---|---|---|---|---|
| Offline run splitter (segment, detect, reconcile, main) | `campaigns/splitter.py:32-55, 106-519` | 443 | Reachable only from `python splitter.py <run-dir>`. Splits a run directory holding several campaigns. Moot: one game process = one campaign. | high |
| `CampaignTracker` + `scan_state_rows` | `campaigns/splitter.py:22-29, 58-103`; `campaigns/__init__.py` | 84 | The two "live" imports. The recorder calls `observe()` and discards the answer. | high |
| Recorder plumbing | `manager/manager.py:15-21, 35, 52-53, 162-176` | ~45 | `observe_state` computes `started_new` then returns `False` unconditionally (line 176). `Ctx.swap` is the no-op lambda from line 35; nothing ever passes a real `swap`. `_swap_lock`, `swap_count`, `campaign_index` are assigned and never read. | high |
| Log-tail swap slicing | `logs/logs_stream.py:14-22, 33-57, 64-66, 110-112` | ~40 | Regex-scans every script-log chunk for faction/turn rows to find a swap point that can never fire. Behaviourally identical to the plain 4-line append. | high |
| **Total** | | **~610** | Lost: an offline tool to split a multi-campaign run directory. Real campaign identity already comes from the game snapshot (`store2.campaign_key` on `campaign_uuid`). | |

## B. Recorder streams nobody reads

Every `runctl up` starts the recorder with `--dev`, which turns on five side streams. Their output is read by one unwired debugging script and nothing else. One of them has never run successfully.

| Stream | Code | Lines | Writes | Read by | Lost if cut | Conf. |
|---|---|---|---|---|---|---|
| UI component recorder | `ui-capture/ui_component_recorder.py`, `ui_capture_stream.py`, `errors.py` | 1,168 | `logs/dev/ui_components.jsonl` (200 MB live) | `debugging/timeline.py` only | Panel-tree captures for the dev timeline. No corpus table, dashboard page, or model reads them. | high |
| Actions stream + CCO sweep | `ui-capture/actions_stream.py`, `cco_queries.py` | 335 | `actions_stream.jsonl` | timeline.py only | Per-turn CCO sweep of every settlement and lord over the live bus. Costs bus budget: 50 `actions-stream` bus timeouts in the current errors.log. Lines 117-146 read `actions_requests.jsonl`, which nothing writes. | high |
| Events stream | `logs/events_stream.py` | 113 | `events_stream.jsonl` | nothing | **Broken.** Calls `_offsets(con)` and `_save_offset`, neither defined anywhere. Raises `NameError` on every recorder start (51 occurrences in errors.log); the recorder's guard swallows it. Nothing is lost because nothing has ever been produced. | high |
| Input stream | `input/input_stream.py` + README | 132 | nothing | never started | Needs `--input`, which runctl never passes. | high |
| Periodic screenshots | `shots/shots_stream.py` + README | 89 | `stream/shots/*.jpg` every 60 s (1,335 files, 0.9 GB) | nothing | A JPEG a minute for eyeballing a stuck game. No reader, 3-day retention. | med |
| Script-log tailer | `logs/logs_stream.py` + README | 150 | `logs/dev/logs/*.tail` (34 GB live) | actions_stream (above), splitter (dead) | The only surviving copy of the game's own script log: `session._rotate_logs` deletes the originals after 10 minutes "because the tail keeps the content". Cut both together or neither. No pipeline step reads the log. | med |
| Lua state scrape | `bus/mod/twstate.lua:65-875` | ~700 | 27 record kinds into the script log, every turn | events_stream (broken), actions_stream (three fields) | Nothing at runtime. The per-campaign script log shrinks from ~260 MB. Decisions are collected through `twcontrol` eval calls, not this dump. | high |
| UI-tree screen dumps | `launcher/nav.py:120-145`, `cco_actions.py:1008-1025` | ~40 | `runs/human/screens/*.json` (30,896 files, 4.3 GB) | timeline.py only | Dev-mode dumps of interrupt and pre-dismiss panels. Useful when hand-debugging a new screen; a switch, not a default. | med |
| **Total** | | **~2,700** | | | Plus the debugging timeline itself (section C), which is these streams' only consumer. | |

## C. Orphan modules

Nothing imports these and no launcher spawns them. Each was verified by a repo-wide search for the module name and every top-level symbol, excluding `.venv` and `node_modules`.

| Module | Lines | What it is | Lost if cut | Conf. |
|---|---|---|---|---|
| `advisor/mapgraph/optimize_greedy.py` | 358 | Optuna search for the graph model | Hyper-parameter search; winners are already hand-copied into `train.CFG`. Only the untracked bench scripts import it. | high |
| `advisor/mapgraph/invariants.py` | 252 | Self-check that greps its sibling sources with ast/inspect | A design-rule linter run by hand. The only remaining consumer of the dead half of `net.py`. | high |
| `advisor/optimize_catboost.py` | 229 | Optuna search for the CatBoost arm | Winners live in `base_model.CB_PARAMS`. | high |
| `advisor/mapgraph/wl.py` | 180 | Weisfeiler-Lehman expressiveness diagnostic | One-off analysis. | high |
| `advisor/mapgraph/optuna_table.py` | 138 | Pretty-printer for the optuna studies above | Orphan serving orphans. | high |
| `advisor/mapgraph/corpus.py` | 130 | Graph cache fingerprinting | No cache is consulted anywhere. The 8.7 GB `models/mapgraph_corpus` shard cache on disk is its leftover. | high |
| `advisor/mapgraph/query_bench.py` | 121 | EXPLAIN ANALYZE harness | SQL plan benchmark. | high |
| `advisor/mapgraph/test_input.py` | 114 | unittest for the new array path (untracked) | The only test in the repo; no runner collects it. | med |
| `advisor/replay_options.py` | 81 | Regenerates stored offers to spot non-regenerable ones | Corpus integrity spot-check. | high |
| `advisor/mapgraph/graph_bench.py` | 54 | Graph-walk timing | Only an untracked bench script imports it. | high |
| `decisions/cco_audit.py` | 514 | Static lint of Lua CCO routes against the catalogue TSV | Ad-hoc check, referenced only from docs/db. | high |
| `decisions/cycle_audit.py` | 93 | Three assertion queries over corpus.taken | Manual audit. | high |
| `decisions/probe_state.py` | 86 | Boots the game for three hard-coded factions to eyeball three fields | Nothing references it, including docs. | high |
| `debugging/timeline.py` + README | 584 | Merges every run stream into one timestamped file | The post-hoc "what happened in what order" tool, and the sole reader of section B's output and of trace.jsonl. Its README still says it reads `decisions.sqlite`. Keep only if the streams it reads are kept. | med |
| `launcher/cutscene_starts.py`, `verify_cco_commands.py` | 223 | Two `__main__` scripts | Zero importers. | high |
| `launcher/hw_input.py`; `executor.send_hotkey`, `game_is_foreground` | 138 | Raw SendInput / keybd_event paths | hw_input is used only by bake.py, and only its `click`. The executor pair has zero callers. | high |
| `launcher/bus_launcher.py` dead members; `nav.py` dead helpers | ~137 | `harvest_startable_factions`, `restart_campaign`, `quit_to_main_menu`, `tree`; `hover`, `find_rect`, `capital_region`, `ui_to_screen` | Frontend navigation the presave boot path no longer uses. | high |
| `bus/mod/twcontrol.lua` dead handlers | ~140 | `autoresolve`, `apiprobe`, `savegame` ×3, `move_leader`, `clickidx`, `move`, `end_turn`, `children`, `hover`/`unhover` | No Python sends them. `move` and `end_turn` are shadowed by eval snippets that do the same job. | high |
| `bus/bus_stats.py` reporting CLI, `install_tracker`, `install_guard` | ~99 | Bus call statistics | The `ops.bus_call_stat` table has no reader (idx_scan 0). Live cost too: `SuppressionGuard` is on by default and fabricates `found:false` replies after three misses. | med |
| `bench/live.py`, `legacy.json`, `live_day248.json`; `bench/gnn_*` (untracked); 4 result dirs + 4 logs | 1,207 | Benchmarks and this week's GNN studies | Generated output inside the checkout, which the README forbids. `migrate/verify.py` (428) is live only through bench/live.py; ~85 lines of it query schemas dropped on 5 Sept. | high |
| `ui/check_client.py` | 50 | Python wrapper around `npm run check` | Nothing. | high |
| Small dead symbols | ~230 | `net.py:304-356` (Head, Net, listwise_nll, from_cfg: 55); `features.MODEL_COLUMNS` allowlist + always-true flag (80); decisions dead functions (64: `unconsumed`, `_parse_ref_regions`, `taken_map`, `hydrate.records`, `last_request_id`…); launcher dead names (15); `policy.EPSILON/BETA`, duplicate `MAX_ACTIONS_PER_ENTITY`, `HERO_AGENT_TYPES` defined twice; `launcher/errors.py` and `bus/errors.py` identical | Nothing. `MODEL_ARM` in the UI still maps gnn to the retired `marwil_gnn`. | high |
| `launcher/ps/capture.ps1:89-133` | 46 | Per-pixel luma/saturation stats after each screenshot | Both callers discard stdout. Screenshots get faster; one is taken before every end turn. | high |
| **Total** | **~5,300** | | | |

## D. Dead under the configured run

`run_config.RUN` plays `ucb=1.0`, `retrain_every=0`, no `--cold`, no `--width`. These paths in `advisor/session.py` cannot execute with that configuration.

> The README says `python runctl.py up` runs "the configured run: run_config.RUN". It does not: runctl never imports run_config and marks five flags required, so the bare command exits with an argparse error. Only the dashboard's launch controls read RUN. Decide which of the two is the truth before deleting anything in this section.

| Path | Where | Lines | Lost if cut | Conf. |
|---|---|---|---|---|
| `--backfill-trials` and three helpers | `session.py:952-977, 1114-1148, 1253-1254` | 63 | A one-off ledger backfill nothing launches. | high |
| Hand-rolled argv parse | `session.py:1250-1349` | ~100 | Re-validates by `sys.argv.index` what runctl's argparse already validated. The rejection of `--epsilon`, `--retrain`, `--model`, `--backend`, `--nn-*` (11 lines) and a 35-line usage tutorial live here. `--cfg` is not rejected despite the README. | med |
| `--width` selector | `session.py:94-115, 459-469, 1334-1338` | 35 | Excluded by `--ucb`, which RUN always sets. | high |
| `--cold` branches | `session.py:351-352, 392-395, 430-436, 563, 597, 606, 1320-1326` | 28 | The model-free start mode. | med |
| Retrain window | `session.py:507-559` | 53 | The documented `--retrain-every` feature. Dead today, but a feature, not residue. Keep unless retraining is truly retired. | low |
| Ending narrator | `session.py:197-323` | 127 | Live, but pure diagnostics: two ad-hoc queries and English verdicts like "SUSPICIOUS: no supporting evidence". Heuristic prose in the hot path. | med |
| Unreachable random fallback | `policy.py:100, 151-158` | 9 | Trainable arms raise `ModelUnavailable` first; the random arm never returns None. | high |
| `runctl session` and `status` subcommands | `runctl.py:475-493, 503-550, 562-564, 610-612` | 25 | Undocumented; `session` duplicates `up` minus the services. | med |
| **Total** | | **~260 (~440 with the retrain window and narrator)** | | |

## E. Copy-paste worth collapsing

Not deletable, but each is one function written several times. Savings are net of the shared version.

| Pattern | Where | Copies | Net saving | Note |
|---|---|---|---|---|
| Choose → click → confirm-closed → record → clear memo | `launcher/interrupts.py:448-529, 930-977, 1230-1303, 1517-1544` vs the generic `_drive_decision` at 1121 | 7 | ~150 | Plus three more handlers for the declare-war confirm (~40). `diplomacy.approve_declare` clicks the same button that `interrupts.py:1268` cancels: two handlers race for one screen. |
| Wait-until-predicate primitive | `nav.py:305, 324`; `interrupts.py:161, 299, 313, 899, 1008`; `click_actions.py:64`; `diplomacy.py:32` | 9 | ~90 | All funnel into `common.waitlog`, so the timing logs survive. |
| Wrappers of `bus.send("click")` and `bus.send("roots")`; treasury reader | interrupts, click_actions, nav, diplomacy, bus_launcher, executor | 6 + 6 + 3 | ~110 | Five distinct mechanisms exist for pressing a button; only the bus click runs in the live path. |
| Same SELECT projection, join chain, per-key aggregate | `advisor_api/queries.py` (18 sites of the `corpus.taken` join; `decisions_page` vs `decision_detail`; `by_character` pasted for traits and skills; three copies of the `_FACT_CAMPS` aggregate) | many | ~228 | Nothing behavioural. |
| Eight 4-line catalog routes; six identical 404 raises | `advisor_api/app.py:312-357, 534-802` | 8 + 6 | ~42 | The UI already builds `/api/${family}` generically. |
| Recruit / cancel / mercenary panel scaffolding | `launcher/click_actions.py:450-468, 553-570, 691-711` | 3 | ~32 | The three `_precheck` functions are byte-identical. |
| Session argv parse vs runctl argparse | `session.py:1250-1349` vs `runctl.py:503-550` | 2 | ~100 | Counted in section D. |
| Small pairs | `cm_actions.colonize` = `attack_settlement` (7); `hydrate.py:43-55` = `canon.py:129-153` (13); `useMeasure` in `charts.tsx:9-20` = `startcharts.tsx:6-17` (12); `greedy_net.from_cfg` = `net.from_cfg` (14); `_character_count` retry loop with bound 1 and an "after 3 tries" message (10, a bug) | | ~56 | |
| Feature vocabularies in three places | `features.py` ↔ `mapgraph/schema.py` ↔ `options.py` (action types, diplomacy terms, gift tiers, race-of, recruit types, lord memory, KNN) | 2-3 | 250-350 | Structural: the CatBoost and graph stacks read the same records into two vocabularies. A unification decision, not a deletion. |
| **Total** | | | **~800 (+300 structural)** | |

## F. API output nobody renders

`advisor_api/` has no dead endpoint, function, or model class: all 56 routes are called from the dashboard. The bloat is values computed and shipped that no page reads, and a generated type file that has drifted.

| Item | Where | Lines | Lost if cut | Conf. |
|---|---|---|---|---|
| Campaign verdict (three regexes, model, route call) | `queries.py:3539-3576`, `models.py:984-1008`, `app.py:541` | ~47 | `CampaignDetail.tsx` never reads `data.verdict`. | high |
| UCB tiles, pool, cursor paging | `queries.py:5356-5396`, `app.py:462-477` | ~45 | The selector page reads only picks/window/dropped_out/next_out and never sends a cursor. | high |
| Campaigns headline / suspicious / unjoined / growth_coverage | `queries.py:701-711, 208-211`, `app.py:500-521` | ~37 | Unread by `Campaigns.tsx`. | high |
| Unread row fields, constant-valued stats, never-set fields, pure alias wrappers | `queries.py:799-806, 909-923, 4618-4675, 1245, 4120, 4200, 4755`; `models.py:1293-1361` | ~60 | `same_top` is always `Rate(n=0)`, `ambiguous` always 0, so the UI branch gated on it is unreachable. | high |
| Stale generated schema | `ui/src/api/schema.d.ts:126, 535, 586, 824, 841, 858` + 6 orphan types | ~220 | Declares six endpoints the API no longer serves. Regenerating (`npm run gen:api`) breaks six aliases in `ui/src/lib/api.ts` that point at vanished types. | high |
| Inert launch-form field | `models.py:1544`, `app.py:755`, `proc.py:142-166` | 4 | **Behaviour.** The Infra page collects `ruleset`, sends it, and `proc.launch` drops it: `runctl.start_session` has no such parameter. A silent no-op. | high |
| Fallbacks | `queries.py:742-749` (`_pool` swallows everything and blanks the start pool), `1150-1169` (three default layers in `reward_weights`), `app.py:814-817` (a dead DB makes the event stream go silent); 132 sites of `_i(x, 0) or 0` | ~70 | Loud failures instead of empty pages. | med |
| **Total** | | **~215 Python + ~220 generated TS** | | |

## G. Dashboard dead code

| Item | Where | Size | Lost if cut | Conf. |
|---|---|---|---|---|
| Eight unreachable chart components | `ui/src/components/startcharts.tsx:111-124, 252-916` | 679 of 1,107 lines | The selector charts the README still describes (pick lanes, ranking bars, expected-vs-realised scatter). `Selector.tsx` now renders two tables. README prose, screenshot, and code disagree about this page. | high |
| Unused npm dependencies | `@radix-ui/react-select`, `react-slot`, `react-tabs`, `react-tooltip`, `class-variance-authority` | 5 of 14 | Nothing: absent from the built bundle. ~4 MB of node_modules. | high |
| Dead exports | `primitives.tsx:348-355` `MODEL_ARM`; `lib/api.ts` 11 aliases; export keyword on 8 file-local symbols | ~30 | Nothing. | high |
| Committed build | `ui/dist` (677 KB) | keep | In sync with ui/src (same commit, same mtime) and the tradeoff is argued in .gitignore. Twenty-five older bundles sit in git history (~15 MB of the 32 MB .git). | clean |

## H. Docs and instruction bloat

Agents.md is 15 lines and every rule in it is honoured: zero comments and zero docstrings across 43,000 lines of Python. The instruction weight is elsewhere: a finished migration's paperwork, a README that describes files that do not exist, package READMEs that describe tests that do not exist, and explanatory prose that moved from comments into runtime strings.

| Item | Where | Size | Lost if cut | Conf. |
|---|---|---|---|---|
| Migration check output | `migrate/*.json`, `v9_ab.out` (21 files) | 28,460 lines · 375 KB | Recorded stdout of check stages that all PASS in `validation_20260905_203346.md`. Nothing reads them. Keep the validation report (the cutover evidence) and verify.py if bench stays. | high |
| Database design and research docs | `docs/db/design` (15), `docs/db/research` (12), BRIEF, JUDGE_B, README | 5,674 lines · 766 KB | The migration is finished (validation passed, legacy schemas dropped in `d52385f`, tooling removed in `23b40b6`). Zero inbound references from outside docs/db. Design 03a-03c duplicate `sql/03_tables.sql` column for column; 10 documents a `migrate/run.py` that was deleted; README points at a plan file that was removed. Lost: the rationale and measurements behind a schema that is now live. 01 and 02 are the only two without a live counterpart. | high |
| Dead DDL in the live bootstrap | `sql/03_tables.sql:1088-1118` (schema `migrate`), `:90-93` (`dict.occupation_option`); `sql/04_ref_views.sql:200-206` | 42 lines | Every fresh bootstrap recreates three empty transient tables the design said to drop after cutover. 88 of 92 tables and views have a reader. | high |
| Orphan dashboard screenshots | `docs/ui/*.png`: 50 of 59 | 7.0 MB of 16.1 MB | Only nine are embedded by the README. The regeneration script it names, `ui_docshots.py`, does not exist, so these cannot be remade. `experiment-ledger.png` is the same tab as `models-training.png`. | high |
| Duplicate screenshot set on disk | `docs/ui-archive-v0.1.53/` (ignored) | 8.8 MB | Byte-identical to docs/ui, all 59 files. Its README cites a commit that is not in this repo. | high |
| README.md stale references | lines 31, 48-49, 58-59, 67, 111-128, 138, 165, 194-198 | 11 claims | `CLAUDE.md` (twice), `ARCHITECTURE.md`, `ui_docshots.py`, `advisor/backends.py` do not exist. `--campaign` is not a flag; the worked example omits two required flags; `--cfg` is not rejected; run_config keys `ruleset` and campaign-map do not exist; the `ruleset` arm is retired; the selector charts described are gone. | high |
| Package READMEs | `bus/README.md`, `launcher/BUS_LAUNCHER.md`, `manager/`, `input/`, `shots/`, `logs/`, `ui-capture/`, `debugging/` READMEs | ~300 lines, ~half wrong | Five name `test_*.py` files that do not exist; four cite a `record.py` monolith that does not exist. BUS_LAUNCHER documents a `PLANS` table and a six-step frontend click flow that are not in the code; bus/README names an `act` channel, a `bus.call_stats` table (real name `ops.bus_call_stat`) and a `--db` flag, none real. debugging/README says the timeline reads sqlite. | high |
| Tool configuration | `.cursor/permissions.json`; `.claude/settings.local.json`; `requirements.txt` | small | permissions.json whitelists `check.py` and pytest, neither exists. settings.local.json has 12 allow entries, 7 near-identical kill-python variants, one probing for `decisions.sqlite`. requirements.txt lists optuna twice with different floors, pins playwright and lupa which nothing imports, and points at `advisor/tune_catboost.py`, which does not exist. | high |
| Prose in runtime strings | `interrupts.py` (149 lines inside stderr/raise strings), `app.py` `_scope` narratives on ~50 routes, `session.py:1266-1302` tutorials, bus_launcher one log line per 0.3 s poll | ~500 lines | This is where the docstrings went. Shipped to the client or the log instead of a comment. Removable only by policy. | med |
| Local clutter | `.playwright-mcp` (0.5 MB, 64 console logs), root `amb.png` / `ledger.png` / `diplomacy-new.png` (0.3 MB), `.pytest_cache` (from a suite that no longer exists), `__pycache__` (7 MB incl. .pyc for 14 deleted modules) | ~8 MB | Nothing. All ignored except .pytest_cache and bench/. | high |

## I. Disk: logs, archives, screenshots, backups

The checkout is 40 MB. `D:\twdata` is where the bytes are, and most of them are written by the run and never read back. Retention runs (last sweep about seven hours before this audit) but its three-day windows on stores that grow 10-25 GB a day leave a steady state near 100 GB.

> On 3 September the run wrote 24.8 GB of script-log tails, 15.8 GB of end-turn screenshots and 15.7 GB of bus archives: 56 GB in one day, none of it read by any process.

| Store | Path | Size | Written by | Read by | Retention | Lost if stopped |
|---|---|---|---|---|---|---|
| Script-log tails | `logs/dev/logs/*.tail` (522 files) | 34.3 GB | logs_stream | dead chain (section B) | 3 d | The only copy of the game's own log after session deletes the original. Debugging record only. Shrinks by itself if the Lua scrape goes. |
| End-turn screenshots | `archive/script_logs/<stamp>/pre_end_turn_*.png`; `runs/human/run/shots` | 20.6 + 2.5 GB | `executor.py:41` before every end turn (~6 MB PNG), moved after 24 h by `session._rotate_logs` | nothing | 3 d | A full-resolution picture of the map before each end turn. Misnamed directory: it holds no logs. |
| Bus archives | `archive/bus/*_commands.txt`, `*_twcontrol.jsonl`, `*_bus_send.jsonl` | 21.5 GB | bus_launcher rotation per boot | nothing | 3 d | Raw command and reply transcripts per campaign. Every decision they contain is already in Postgres. |
| UI-tree dumps | `runs/human/screens/*.json` (30,896 files) | 4.3 GB | `nav.dump_screen` in dev mode | timeline.py | 7 d | Panel dumps for hand-debugging new screens. |
| Launcher trace | `runs/human/run/trace.jsonl` + 6 rolled | 3.6 GB | `launcher/trace.py` | timeline.py; a file-age check on the run page | 3 d | Duplicates the executed/confirmed/counted fields already stored in `corpus.taken`. |
| Dev streams | `logs/dev/ui_components.jsonl`, `events.jsonl`, `actions_stream.jsonl` | 0.34 GB | section B streams | timeline.py | rolled at 512 MB | See section B. |
| Minute screenshots | `stream/shots/*.jpg` (1,335) | 0.9 GB | shots_stream | nothing | 3 d | See section B. |
| Graph shard cache | `models/mapgraph_corpus/<fp>/run/shard_*.pt` | 8.7 GB | corpus.py (orphan) | nothing | none | Nothing: the module that knows this directory is not imported anywhere. |
| Model backups | `models_backup_20260826`, `_20260830_221418`, `_20260904_1215` | 25.6 GB | manual copies | — | none | Three snapshots of models that are 15-50 MB each plus the shard cache. Owner's call; the shard cache is what makes them large. |
| Old GNN corpus | `cache/gnn3_corpus.pt` (10 Aug) | 2.8 GB | the previous model stack | nothing | none | Nothing. |
| Migration backups | `backup/tw_stack_20260904_032121.dump`, `deblob*`, `blobs_final`, `starts`; `D:\pg17\dump` | 3.8 + 1.6 GB | the 4 Sept migration | — | none | The pre-cutover database. The design's own inventory scheduled it for deletion seven days after cutover (5 Sept). |
| Old Postgres cluster | `C:\Program Files\PostgreSQL\17\data`, service `postgresql-x64-17`, still running | 28.2 GB | pre-migration | — | none | The C: cluster the migration superseded; the D: cluster on 55433 holds only the new schemas (6.3 GB). Same seven-day gate. C: has 38 GB free. |
| Scratch and demos | `scratch`, `repo_archive`, `agent_demo_*` (9 dirs), `oldlogs`, `bus_archive*`, `panel_capture`, `db_rebuild` | ~2.6 GB | July and August sessions | — | none | Nothing current; newest file 4 Sept in db_rebuild, the rest July-August. |
| Service and session logs | `logs/services`, `logs/advisor`, `logs/launcher` | 0.9 GB | runctl, session | run page, harness, timeline | 7 d | **Keep.** These are the logs that are read. |
| **Unread total** | | **~120 GB** | | | | Of ~135 GB under twdata, excluding reference (0.9 GB), presaves (0.6 GB), the live database, and the read logs. |

## J. Checked and clean

These looked like bloat candidates and are not. Listed so they are not cut by mistake.

- **analytics/**: all eight rollup tables are queried by the API; the health report runs from the Windows task `\tw_stack health report`. One degenerate dimension: `store2.py:47` hard-codes `collector_sha='legacy:meta1:pq1'`, so the report's per-version grouping is a constant.
- **store.py vs store2.py**: read side used by the advisor vs write side used by the recorder. Complementary, badly named; only `taken_map` and `_kind_id` inside store.py are dead.
- **decisions/collect.py** (1,707): dense, not duplicated. About 570 lines are Lua query constants with paired parsers; one dead function.
- **rowmap / schema_map / dicts / sets / canon**: complementary layers. The one overlap is `hydrate.py:43-55` restating `canon.py:129-153`.
- **advisor/memory, watchdog, arms, ucb_stats, presaves, retention, reference/check**: all imported by the live path. **bake.py** is orphan by import but is the only producer of the presave pool the run requires; it is undocumented in the README.
- **mapgraph/train.py**: not the old model stack. It is the shared walk/collate library that greedy_train imports; misnamed, not dead. The old stack survives only as the ~55-line dead half of net.py.
- **advisor_api**: no dead route, function, or model class. **ui routes**: all 17 mounted. **ui/dist**: in sync. **sql/**: 88 of 92 objects read.
- **Untracked but live**: `advisor/mapgraph/arrays.py`, `source.py`, `projection.py`, `project_offers.py` are imported by tracked code and are not committed. A gap, not clutter.

## K. Suggested order

Ranked by bytes or lines freed per unit of risk. Each tier is independent of the next.

1. **Stop the write-only firehose.** End-turn screenshot before every `end_turn` (`executor.py:41`), the bus archive rotation, and the Lua state scrape. About 50 GB a day at current throughput, zero readers, and the screenshot alone is a 45-second-budget PowerShell call per turn. Then delete the existing archives, the shard cache, the old corpus, the old cluster, and the migration dumps: roughly 105 GB.
2. **Delete the dead chains.** The splitter chain (A), the ui-capture and events streams (B), and the orphan modules (C). About 8,600 lines, no runtime change. The one decision inside this tier: whether to keep `debugging/timeline.py` and the dev streams it reads as an opt-in flag, or drop both.
3. **Retire the migration paperwork.** `migrate/*.json`, `docs/db` except 01, 02 and the validation report, the 42 lines of dead DDL, the duplicate and orphan screenshots. About 34,000 tracked lines and 16 MB of PNG. Historical value only; git keeps it anyway.
4. **Fix the instructions.** Eleven README claims, eight package READMEs, requirements.txt, the two permission files. Small edits, but today the README sends a new reader to three files that do not exist and a launch command that fails.
5. **Collapse the copies.** Section E, about 800 lines, and the session-only paths in section D once the run_config question is settled. This is the only tier that touches live behaviour, so it is last.

Two things surfaced that are bugs rather than bloat and are worth fixing regardless: `logs/events_stream.py` has never run (undefined `_offsets`), and the launch form's `ruleset` field is silently dropped before it reaches the session.
