# Night report — 2026-08-02 (sessions S1 01:08, S2 03:49, S3 5x40 15:24)

Three sessions, ~34 game launches, 437 played turns, 33 campaigns with outcomes. All claims
below were produced by a 6-agent evidence sweep and re-checked by an adversarial verifier;
where the verifier corrected a number, the corrected number is printed and the correction noted.
Nothing in this report has been "fixed" — every proposal is unimplemented and awaits your call.
Sessions: S1 = 40x40 (died at campaign 10, Nakai screen), S2 = 40x40 (killed at your order,
19 campaigns), S3 = the 5x40 timing run (completed). Y = yesterday's 40x40 baseline.

---

## 0. TL;DR

- **The bus corruption is fixed in practice.** 34 launches, 34 clean rotations, campaign starts
  at `last_seq=5` (vs 8,780 yesterday), and **2 campaign-killing environment failures all night
  vs 27 of 40 yesterday**. The one bus-timeout kill (S2 c14) did not cascade — the next launch
  was clean. Current bus window: 12,507 commands → 12,507 matched replies, zero mangled.
- **The advisor accepted its first treaties.** First-ever incoming proposal accepted at
  **01:31:00** (turn 9, "The Architect of Fate" / Apostles of Change, cold-random). 23 incoming
  proposals decided tonight: **15 accepts / 8 declines**, all executed+confirmed; S3's three
  accepts were **model-chosen** (screen went hot at 17 recorded rows, past the MIN_ROWS=5 gate).
  The full cold-start → data → trained-model pipeline you specified closed within 24 hours.
- **Two 40-turn completions tonight** — vilitch (S2 c19, 15:14) and norsca (S3 c5, 17:46).
  Correction: these are the **3rd and 4th ever**, not the first — Azazel and Exiles of Khorne
  completed 40 turns yesterday morning (sessions 021457/054235). Caveat that matters more:
  both tonight's completions ended **settlement-less** — "completed" currently certifies
  40 turns of survival, not a healthy campaign.
- **Full timing decomposition delivered** (§4): collect is 13 serial bus calls on a structural
  ~100ms floor (mod poll 0.1s + client poll 0.05s); the diplomacy enumeration runs once per
  turn at ~2.9s (25.2% of ALL collect time); batching + halving it would cut s/move median
  −47% but s/turn only −9% because **63.7% of wall time is the end-turn cycle** (median 51.5s
  of AI turns per player turn) that no harness optimization touches.
- **One open regression flag (§5): S3's recruit execution collapsed** (recruit_unit 4%,
  recruit_lord 2% confirmed vs 46%/18% in S2, uniform across factions including Empire).
  S3 is the first session on the 12:27–12:36 poll/timing commits — correlation stated,
  mechanism unmeasured, decision yours.
- **Rotation works but disk growth is NOT contained**: 215 GB archived (185 GB was the one-time
  backlog), game dir held at 2 files — but the run-dir `.tail` stream duplicates every archived
  script-log byte (byte-identical copies), ~50 GB/day. Disk fell ~119 GiB in 24h.

---

## 1. The night at a glance

| | Y (08-01) | S1 | S2 | S3 (5x40) |
|---|---|---|---|---|
| campaigns launched | 40 | 10 | 20 | 5 |
| completed / defeated / stuck / errored | 0 / 8 / 5 / **27** | 0 / 2 / 6 / 1† | **1** / 16 / 1 / 1 | **1** / 4 / 0 / 0 |
| turns played | 147 | 58 | 301 | 78 |
| executed actions per turn | 3.67 | 5.24 | 5.74 | 2.95 |
| confirmed per turn | 2.57 | 2.95 | 3.72 | **0.99** |
| survival median (turns) | 7.5 | 6.5 | **12.0** | 9.0 |
| policy rows at start | 4,098 | 4,732 | 5,183 | 7,784 |

† S1's 10th campaign crashed the session (§6) and is absent from its JSON.
Retrain progression: main model 4,098 → 7,784 rows; interrupt model 781 → 1,747 rows and
gained `diplomacy_proposal` as its 6th screen at S2 (in-sample MAE crept 0.0321 → 0.0365 as
rows grew; sd_local rose while sd_global fell — worth watching, not alarming at this n).

## 2. Environment: the corruption fix held

Yesterday: 27/40 campaigns burned on seq corruption. Tonight: **1** "did not load" (S1 c8) and
**1** genuine bus-timeout kill (S2 c14, seq 166953) across ~34 launches, no cascade, breaker
never needed. Raw "bus timeout" .err lines are actually *higher* tonight (263 vs 117) — the
verifier confirmed every remaining one is the **faction-death signature**: paired watchdog
digest failures at ~35s/71s idle while the game sits on a defeat screen, exactly one pair per
defeat (S1 2/2, S2 16/16, S3 4/4). That noise class is a proposal candidate (§9.5), not a fault.
Two loose ends, both flagged [likely]: S2 c7 started with `last_seq=122446` (session-global
counter carry-over — same fingerprint class as the corruption night; harmless this time), and
the S2 session client logged **zero** seq reseeds across 19 rotations while the manager reseeded
3× — the reseed fires on next alloc, but S2's pattern deserves one look if a c14-style kill recurs.

## 3. Diplomacy: first accepted treaties, and the dataset's first harvest

**Decisions (incoming):** 23 `diplomacy_proposal` screens answered — 15 accept / 8 decline,
23/23 executed+confirmed+counted. S1+S2's 24 were cold-random (per-screen gate, correct); S3's
3 were **model** picks (accept 0.5594 vs decline 0.5449 — nearly indifferent, as expected at
17 training rows). `diplomacy_notice` and `ally_attacked` **never fired live** — the
ally_attacked handler remains untested in the wild.

**Dataset (`diplomacy.jsonl`):** 2,114 rows across the two run dirs — 1,885 pair checkpoints
(130 distinct factions, <1% null pair reads, zero永-null factions), 173 outgoing deal rows,
23 incoming, 33 campaign_end. Field fill is good except: incoming rows carry no parsed `terms`
(the tree has them; the builder can extract) and `treaty_before` is outgoing-only by design.

**Outgoing is still 0-for-everything and now measured:** 173 attempts, `panel.sent` **0/173**.
failed_at: deal_selection 91, ai_would_refuse 76, faction_selection 6. The engine's own success
chance on the ai_would_refuse rows is **always negative** (medians −39.9 / −49.2; best −0.7) —
the advisor is proposing deals the AI prices as terrible, and each attempt costs ~29s including
a hard-coded 20s confirm timeout (26 × 20s = **520s of pure timeout** in S3 alone). §9.3.

## 4. Where the time goes — full granularity (5x40, 527 decision points)

**Wall split: 36.3% move cycles (2,982s) / 63.7% end-turn cycles (5,223s).** An end-turn cycle
medians 56.8s of which ~51.5s is AI turns + inter-turn interrupts — the irreducible-by-harness part.

**Move cycle decomposition** (median / p90 / share of move-cycle wall):

| component | med | p90 | share |
|---|---|---|---|
| collect (13 serial bus calls) | 1,873ms | 4,967ms | **34.3%** |
| execute (typed, §4b) | 4,519ms | 8,584ms | 27.2% |
| confirm | 202ms | 20,003ms | 22.7% (the p90 IS the diplomacy 20s timeout) |
| score | 264ms | 372ms | 4.0% |
| verify-snapshot | 203ms | — | 3.7% |
| pickup lag (post-fix) | 53ms | 514ms | 2.4% |
| housekeep + unattributed | ~240ms | — | 5.1% |
| store / trace / gates | ~0 | — | <1% |

**Collect internals — full sub-stage table** (527 snapshots; med/p90 ms; floor% = share of
samples inside the 90–115ms bus-call floor; the floor is structural: mod poll 0.1s
`twcontrol.lua:23` + client read poll 0.05s `bus.py:31`, hard min 50ms observed):

| stage | med | p90 | at floor | notes |
|---|---|---|---|---|
| campaign_offers/diplomacy | 115 | 2,890 | 68% | **full enum once per turn, 78/78 on the turn's FIRST decision: med 2,904 / max 3,041ms; sum 277.8s = 25.2% of ALL collect; flat vs turn# and lord count** |
| lord_offers (total) | 708 | 1,163 | 0% | **~580ms per lord, serial-additive** (608ms @1 lord → 1,160 @2); slowest single lord 809/p90 860ms |
| lord_offers/recruitable | 404 | 603 | 4% | dominant lord sub-stage, 11.2% of collect |
| world_state (4 calls) | 405 | 406 | — | fixed 4 × floor: ruins 101 (87%), hostiles 101 (94%), chars 101 (84%), setts 101 (95%) |
| province_offers | 202 | 203 | 0% | 2 calls |
| hero_offers | 202 | 405 | 0% | |
| lord_offers/ev | 101 | 202 | 72% | stances+skills eval |
| lord_offers/reach | 101 | 202 | 48% | |
| lord_state / hero_state | 101 | 202 | 72–77% | |
| campaign_offers/ev | 101 | 101 | 95% | tech+rites eval |
| settlement_forces | 101 | 101 | 96% | |
| faction_resources | 101 | 101 | 89% | |
| province_state | 101 | 101 | 95% | |
| campaign_state | 69 | 101 | 38% | the only sub-floor stage |
| store write (sqlite) | 1 | — | — | innocent |

Shape of the reduction: 13 serial calls/collect (med; p90 17), leaf-sum/collect = 0.962 (fully
serial). 8+ stages are pure floor — batching them into one eval collapses ~800ms of floors to
~100ms; the two *content*-heavy stages are the diplomacy enum (once per turn) and per-lord
recruitable. `campaign_offers` as a phase = 330.5s total, 84.1% of which is the diplomacy enum.

**Poll fix verified:** pickup lag median 53ms vs the ~372–396ms pre-fix proxy across three prior
runs — ~0.3s saved per decision, ~140s over the run. Execute medians unmoved (as expected).

**Instrumentation defect (mine, measurement-only):** `housekeep_ms` covers only the pre-request
window, but `drain`/`resolve`/`post_attack` parts are timed *outside* it — parts sum 125.4s vs
41.9s counted. The parts themselves are correct; the lump they should reconcile against is
mis-windowed. One-line fix, next batch.

**Headroom model [likely]:** batch the 13 serial calls to one shared floor and halve the
diplomacy enumeration → collect 1,102s → 343s (−69%), **s/move median 3.20 → 1.71 (−47%)**,
s/turn mean 105.2 → 95.5 (**−9.2%** — bounded by the 51.5s AI-turn remainder). Worth doing for
decision throughput; not a campaign-per-hour transformer on its own.

## 4b. Execute + confirm rates by type (S3 vs night, confirm% Y→S1→S2→S3)

diplomacy 28.9s ×0% (0% in every session ever measured) · recruit_unit 6.5s, 54→40→46→**4%** ·
recruit_lord 4.9s, 16→18→18→**2%** · move 4.7s, 74→76→91→90% · stance 1.4s, 67→71→57→36% ·
research 0.6s, 100% everywhere · end_turn 4.3s, 93→88→100→100%.

## 5. OPEN REGRESSION FLAG — S3's recruit collapse

All 114 S3 recruit failures are `execute_failed` with `executed:false`, gates passed, treasury
unchanged — uniform across all 5 campaigns *including Empire* (0/14), which rules out the
known horde/raw-name explanations alone. S3 is the first session launched on the 12:27–12:36
timing commits (`bece4a8`/`cb332ea`/`f12d9cd`); none touch the click path, and S2 already showed
86% `executed:false` among its recruit *failures* — so this is a **correlation with an
unmeasured mechanism** [likely]. Options if you want it chased: A/B one session with the journal
poll reverted; or read the failed attempts' panel states (recorded). Not touched, per your rule.

## 6. The Nakai crash, and the recurring pre-battle weakness

S1 died at 03:46:11: campaign 10 (Nakai) fought an unretreatable battle — three
"retreat clicked but pre-battle still open" rounds, forced autoresolve, victory — then the
results screen offered `button_nakai_temples` (Temples of the Old Ones overview, sitting in
`resources_bar` next to the captive buttons). KNOWN=HANDLED raised, the session died as designed,
the evidence chain was complete (unhandled record + tree dump + screenshot), and the fix was a
one-line DISPLAY_CONTROLS addition (`d6842bc`, 3 minutes later). Two durable observations:
(1) the **retreat-fails-then-autoresolve loop recurs all night** across sessions — pre-battle
handling underweights unretreatable battles (§9.4); (2) S1's five stucks shared the
hud-missing + end_turn-refused signature and then **nearly vanished in S2/S3 (1 in 24 campaigns)
with no code change** — unexplained, worth remembering before anyone "fixes" it.

## 7. The gate and act_index, measured

The gate works exactly as ordered: end_turn is chosen at median decision index 6 (62%/49%/73%
of end_turn turns take it at the *first offered* decision). Executed actions/turn rose 3.67 →
5.24/5.74. Costs, also real: `no_eligible_actions` went from **0% of turns yesterday to 40%/32%/
83%** (S1/S2/S3) — every one exhausting at ≤4 executed actions, ending via the forced path —
and noop share rose 20% → 34%/28%/**50%**. S3's stronger model has almost stopped *choosing*
end_turn (11 chosen vs 65 forced): the gate, not the model, is pacing S3. Survival: S2's median
12.0 vs 7.5 yesterday is the best cohort ever, but the same commit shipped the environment fixes,
so the gate's share of that gain is **not attributable** [proven confound].
`camp_act_index` is confirmed flowing into both trained models' feature lists via the
inject-live/reconstruct-at-train design — it is intentionally not in stored rows.

## 8. Hygiene: rotation works; growth doesn't stop

34/34 bus rotations clean; 29 script-log boundaries archived 2,001 files / **214.98 GiB**
(180.6 GiB was the one-time backlog — verifier-corrected from 184.9); game dir steady-state is
2 files. But: the manager's `.tail` stream writes a **byte-identical copy** of every script log
into the run dir (verified byte-for-byte), so every archived byte exists twice and run-dir logs/
grew ~50 GB/day (old run 49.3 GiB of tails; the 5x40 added 6.8 GiB in 2.4h). Disk: −119 GiB
in 24h, 1.53 TiB free (~13 days at this rate). Stream cost ranking (MB/day, measured):
script-log tails ~52,000 ≫ shots ~2,800–4,200 > decisions.sqlite ~400 ≈ decisions_requests ~380
> screens dumps ~150 > diplomacy.jsonl <1.

## 9. Proposals — ranked, none implemented

1. **Stop the double-storage of script logs** (~50 GB/day): once the splitter has consumed a
   boot's tail, archive-move the tail *or* skip archiving the game-dir original. Needs a
   splitter-aware "consumed" signal — the naive version was the blocker the review caught.
2. **Collect batching + once-per-turn diplomacy enumeration cache-within-turn** (§4 headroom:
   s/move −47%). Fresh-at-decision constraint kept: state reads still happen at decision time;
   the diplomacy candidate SET could be enumerated once per turn (it already is — the question
   is only whether 2.9s can be cheaper).
3. **Outgoing diplomacy triage**: 0/173 sent, always-negative engine chance, 29s + 20s timeout
   per attempt. Cheapest evidence-backed moves: don't walk deals whose engine chance is deeply
   negative (it's readable before send), and drop the confirm timeout for refused deals.
4. **Pre-battle: handle unretreatable battles** (retreat-fail → autoresolve loop recurs nightly;
   it fed the Nakai crash path).
5. **Silence the defeat-screen watchdog noise** (263 timeout lines/night are death-screen
   probes firing into a paused tick — skip digest probes once a defeat root is visible).
6. **Recruit regression investigation** (§5) — before the next long batch.
7. **Report hygiene**: totals block only counts completed campaigns (norsca's 40/143/52 masked
   38 defeat-turns); S2 has no totals block at all (killed mid-c20); housekeep window fix (mine).
8. **ally_attacked live validation** — handler shipped, never fired; it will meet its first
   real call-to-arms eventually and both options carry campaign-scale stakes.

## 10. File index

Sessions: `D:/twdata/logs/advisor/session_{40x40_20260802_010819,40x40_20260802_034911,5x40_20260802_152453}.{log,err}` + JSONs under `D:/twdata/runs/human/`.
Runs: `20260801_110615` (S1+S2, cumulative with yesterday), `20260802_152453` (S3, fresh).
Timing: `decisions_requests.jsonl` (pick timings incl. housekeep_parts), `decisions_stream.jsonl`
(collect profiles). Diplomacy: `diplomacy.jsonl` ×2, screens dumps (1,343 files), interrupt
records. Evidence chain for the crash: `unhandled_screens.jsonl` row 8 + dump 1785667570806 +
shot stuck_unhandled_battle_results_1785667571.png. Sweep transcripts:
`.claude/.../workflows/wf_822efc69-188/`.
