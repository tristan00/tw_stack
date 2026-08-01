from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

HUD_MISS_BUDGET = 12          # 12 x 5.0s
HUD_MISS_PAUSE = 5.0
_last_beat_turn = [None]      # last turn that beat the watchdog
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import journal                                             # noqa: E402
import policy as P                                         # noqa: E402

sys.path.insert(0, os.path.join(r"D:\tw_stack", "launcher"))
import trace as TR                                         # noqa: E402
from watchdog import Watchdog                              # noqa: E402


class CampaignLost(RuntimeError):
    """The faction was destroyed."""


class GameStuck(RuntimeError):
    """The watchdog saw no progress."""


class TurnResult(dict):
    pass


def run_campaign(run_dir, executor, pol=None, turns=3, log=print,
                 stuck_seconds=None, on_stuck=None):
    """Play `turns` turns. Returns the per-turn report rows (also written to loop_report.jsonl)."""
    pol = pol or P.Policy()
    TR.set_run_dir(run_dir)
    report_path = os.path.join(run_dir, "loop_report.jsonl")
    stuck = {"fired": False, "reason": None, "detail": None, "shot": None}
    try:
        executor.disable_ui_hotkeys()
    except Exception as e:
        log("!! could not disable the UI-hide hotkeys: %s" % repr(e)[:120])
    import interrupt_model as IM
    import interrupts as I
    ranker = IM.InterruptRanker()
    # installed whether or not a model is fitted; the ranker draws uniformly when cold
    I.set_chooser(lambda screen, options, campaign: ranker.choose(screen, options, campaign))
    if ranker.ready:
        log("interrupt policy: trained(%d rows, screens=%s)"
            % ((ranker.meta or {}).get("rows", 0),
               ",".join((ranker.meta or {}).get("screens") or [])))
    else:
        log("interrupt policy: cold_random (advisor-hosted; no interrupt model fitted yet)")

    def _stuck(reason, detail):
        stuck.update(fired=True, reason=reason, detail=detail)
        stuck["shot"] = executor.screenshot("stuck_%s_%d" % (reason, int(time.time())))
        log("!! STUCK (%s) after %ss -- screenshot: %s"
            % (reason, detail.get("idle_s"), stuck["shot"]))
        _append(report_path, {"kind": "stuck", "reason": reason, "detail": detail,
                              "screenshot": stuck["shot"], "ts": time.time()})
        if on_stuck:
            on_stuck(reason, detail, stuck["shot"])

    from watchdog import STUCK_SECONDS
    wd = Watchdog(lambda: journal.request_hash(run_dir), _stuck,
                  stuck_seconds=stuck_seconds or STUCK_SECONDS,
                  log=lambda m: log("   [watchdog] %s" % m)).start()
    rows = []
    try:
        for t in range(turns):
            if stuck["fired"]:
                if executor.defeated_probe() is True:
                    raise CampaignLost("watchdog fired but the faction is DEAD -- defeat, "
                                       "not a stall (%s)" % stuck["reason"])
                raise GameStuck("%s: %s" % (stuck["reason"], stuck["detail"]))
            row = _run_turn(run_dir, executor, pol, wd, stuck, log)
            rows.append(row)
            _append(report_path, row)
            log("== turn %s done: %d actions (%d confirmed), ended by %s =="
                % (row["turn"], row["actions"], row["confirmed"], row["ended_by"]))
            _turn_trail(run_dir, executor, row, len(rows), log)
            if row["ended_by"] == "defeated":
                raise CampaignLost("faction destroyed at turn %s after %d turns played"
                                   % (row["turn"], len(rows)))
            if row["ended_by"] == "no_campaign_ui":
                raise GameStuck("no_campaign_ui: hud_campaign absent, nothing clickable "
                                "(turn %s, %d actions, %d confirmed)"
                                % (row["turn"], row["actions"], row["confirmed"]))
            if row["ended_by"] == "stuck":
                if executor.defeated_probe() is True:
                    raise CampaignLost("watchdog fired but the faction is DEAD -- defeat, "
                                       "not a stall (%s)" % stuck["reason"])
                raise GameStuck("%s: %s" % (stuck["reason"], stuck["detail"]))
    finally:
        wd.stop()
        # a raise inside a handler must not eat the interrupt decisions buffered before it
        _drain_interrupts(run_dir, log)
    return rows


SHOT_EVERY_TURNS = 5


def _turn_trail(run_dir, executor, row, turn_index, log):
    """One turn_trail.jsonl row per turn, plus a screenshot on turn 1, every SHOT_EVERY_TURNS, and
    on any abnormal ending. Never raises."""
    rec = {"ts": time.time(), "turn": row.get("turn"), "turn_index": turn_index,
           "actions": row.get("actions"), "confirmed": row.get("confirmed"),
           "ended_by": row.get("ended_by")}
    for name, fn in (("roots", executor.visible_roots), ("ui_state", executor.ui_state)):
        try:
            rec[name] = fn()
        except Exception as e:
            rec[name] = None
            rec[name + "_error"] = repr(e)[:100]
    abnormal = row.get("ended_by") not in ("end_turn_chosen", "action_cap")
    if abnormal or turn_index == 1 or turn_index % SHOT_EVERY_TURNS == 0:
        try:
            rec["screenshot"] = executor.screenshot(
                "t%s_%s_%d" % (row.get("turn"), row.get("ended_by"), int(time.time())))
        except Exception as e:
            rec["screenshot_error"] = repr(e)[:100]
    try:
        _append(os.path.join(run_dir, "turn_trail.jsonl"), rec)
    except Exception as e:
        log("   !! turn trail not written: %s" % repr(e)[:120])
    if abnormal:
        log("   turn trail: ended_by=%s roots=%s ui=%s shot=%s"
            % (row.get("ended_by"), ",".join(str(r) for r in (rec.get("roots") or []))[:140],
               rec.get("ui_state"), rec.get("screenshot")))


def _drain_interrupts(run_dir, log):
    """Hand every interrupt-screen decision the launcher buffered to the recorder."""
    try:
        import interrupts as I
        recs = I.drain_interrupt_records()
    except Exception as e:
        log("   !! could not drain interrupt records: %s" % repr(e)[:120])
        return
    for r in recs:
        try:
            journal.log_interrupt(run_dir, r)
        except Exception as e:
            log("   !! interrupt record not persisted: %s" % repr(e)[:120])
    if recs:
        log("   recorded %d interrupt decision(s): %s"
            % (len(recs), ", ".join("%s->%s" % (x.get("kind"), x.get("chosen")) for x in recs)))


def _run_turn(run_dir, executor, pol, wd, stuck, log):
    pol.new_turn()
    turn = journal.request_turn(run_dir)
    log("== TURN %s ==" % turn)
    opening = executor.resolve_interrupts()
    _drain_interrupts(run_dir, log)
    if opening:
        log("   opening interrupts: %s" % ", ".join(str(s) for s in opening))
    actions, confirmed, ended_by, picks = 0, 0, "action_cap", []
    active = None                                   # None = every entity is in play
    no_hud = 0
    last_record = {}                                # newest snapshot, for the end-of-turn line
    while actions < pol.max_actions_per_turn:
        if stuck["fired"]:
            ended_by = "stuck"
            break
        alive = executor.campaign_ui_alive()
        if alive is False:
            no_hud += 1
            roots = executor.visible_roots()
            log("   !! hud_campaign missing (%d/%d) -- roots=%s"
                % (no_hud, HUD_MISS_BUDGET, ",".join(sorted(str(r) for r in roots))[:200]))
            # check before spending any budget, and never try to drive this screen
            lost = executor.defeat_screen(roots)
            if lost:
                log("   !! END-OF-CAMPAIGN SCREEN %r -- the campaign is OVER, not stalled. Ending "
                    "as `defeated` without spending the HUD budget (turn %s, %d actions, roots=%s)"
                    % (lost, turn, actions,
                       ",".join(sorted(str(r) for r in roots))[:200]))
                ended_by = "defeated"
                break
            steps = executor.resolve_interrupts()
            if steps:
                log("   hud recovery: %s" % ", ".join(str(s) for s in steps))
            st = None
            try:
                st = executor.ui_state()
            except Exception as e:
                log("   !! ui_state failed: %s" % repr(e)[:100])
            if st:
                log("   ui state: cutscene=%s cinematic_ui=%s ui_hiding=%s"
                    % (st.get("cutscene"), st.get("cinematic_ui"), st.get("ui_hiding")))
            if no_hud == HUD_MISS_BUDGET // 2:
                try:
                    log("   forcing UI restore: %s" % executor.force_ui_restore())
                except Exception as e:
                    log("   !! force_ui_restore failed: %s" % repr(e)[:100])
            if no_hud < HUD_MISS_BUDGET:
                time.sleep(HUD_MISS_PAUSE)
                continue
            if no_hud >= HUD_MISS_BUDGET:
                shot = None
                try:
                    shot = executor.screenshot("no_hud_t%s_%d" % (turn, int(time.time())))
                except Exception as e:
                    log("   !! HUD-gone screenshot failed: %s" % repr(e)[:120])
                log("   !! campaign HUD is gone after %d recovery attempts, abandoning the turn "
                    "(roots=%s, shot=%s)"
                    % (no_hud, ",".join(sorted(str(r) for r in roots))[:200], shot))
                ended_by = "no_campaign_ui"
                break
            continue
        else:
            no_hud = 0
        decision_id, record = journal.request_snapshot(run_dir, active=active)
        last_record = record
        if turn is not None and turn != _last_beat_turn[0]:
            _last_beat_turn[0] = turn
            wd.beat("turn_advanced")
        t_scored0 = time.time()
        pick, ranked = pol.choose(record, actions_taken=actions)
        TR.advisor(pick, ranked_top=[{k: r.get(k) for k in
                                      ("context_kind", "context_id", "action_type", "key",
                                       "score", "exploit", "explore", "rank")}
                                     for r in (ranked or [])[:10]],
                   turn=turn, decision_id=decision_id, actions_taken=actions,
                   n_offers=len(ranked or []))
        t_scored = time.time()
        timing = {"t_request": record.get("_t_request"), "t_received": record.get("_t_received"),
                  "collect_ms": record.get("_collect_ms"),
                  "roundtrip_ms": int((record.get("_t_received", t_scored0)
                                       - record.get("_t_request", t_scored0)) * 1000),
                  "score_ms": int((t_scored - t_scored0) * 1000),
                  "offers": sum(len(e.get("offers") or []) for e in record.get("entities") or []),
                  "entities": len(record.get("entities") or [])}
        if pick is None:
            log("   nothing eligible -> ending turn")
            ended_by = "no_eligible_actions"
            _force_end_turn(run_dir, executor, decision_id, ranked, log)
            break
        journal.log_pick(run_dir, decision_id, pick, P.scores_for_store(ranked), timings=timing)
        picks.append({"action": pick["action_type"], "key": pick["key"],
                      "context": "%s:%s" % (pick["context_kind"], pick["context_id"]),
                      "policy": pick["policy"], "score": pick.get("score")})

        if pick["action_type"] == "noop":
            pol.retire(pick["context_kind"], pick["context_id"])
            journal.log_verification(run_dir, decision_id, _noop_record(pick))
            log("   %-9s %-24s -> retired" % ("noop", pick["context_id"][:24]))
            wd.beat("noop_retired")
            active = _active_from(record, pol)
            continue

        pre_off = executor.bus.out_offset()
        result = executor.execute(pick)
        journal.log_verification(run_dir, decision_id, result)
        actions += 1
        ok = bool(result.get("counted"))
        confirmed += 1 if ok else 0
        pol.note_result(pick, ok)
        # only a confirmed action beats the watchdog
        if ok:
            wd.beat("confirmed:%s" % pick["action_type"])
        log("   %-16s %-34s %s%s"
            % (pick["action_type"], str(pick["key"])[:34],
               "OK" if ok else "FAIL", "" if ok else " (%s)" % result.get("refusal")))

        if pick["action_type"] == "end_turn":
            if ok:
                ended_by = "end_turn_chosen"
                break
            # do not break on a refused end_turn: retry it, bounded by the per-turn action cap
            log("   end_turn refused -- retrying rather than settling on a turn that never ended")
            ended_by = "end_turn_failed"
            active = _active_from(record, pol)
            continue
        # let a battle screen appear and be driven before the next order goes out; the mod's
        # panel/battle rows end the wait the moment the screen exists (2.5s is the cap)
        if str(pick.get("action_type", "")).startswith("attack"):
            executor.bus.wait_row(("panel", "battle_completed", "dilemma_issued"), timeout=2.5,
                                  offset=pre_off,
                                  pred=lambda r: r.get("cmd") != "panel" or bool(r.get("opened")))
            pre = executor.resolve_interrupts()
            if pre:
                log("   post-attack interrupts: %s" % ", ".join(str(s) for s in pre))
                wd.beat("post_attack_interrupt")
        _drain_interrupts(run_dir, log)
        steps = executor.resolve_interrupts()
        if steps:
            log("   interrupts: %s" % ", ".join(str(s) for s in steps))
            wd.beat("interrupts")
        active = _active_from(record, pol)
    else:
        ended_by = "action_cap"
        _force_end_turn(run_dir, executor, None, None, log)

    # the reward row is sampled before the AI phase, at the same point in every turn
    terminal = ended_by in ("stuck", "no_campaign_ui", "defeated")
    target = None
    if not terminal:
        target = journal.request_target(run_dir)

    if terminal:
        settle = {"turn": None, "steps": [], "waited_s": 0.0, "skipped": ended_by}
    else:
        settle = executor.settle_between_turns(turn_before=turn,
                                               abort=lambda: bool(stuck.get("fired")))
    if settle["steps"]:
        log("   inter-turn: %s (%.0fs)" % (", ".join(str(s) for s in settle["steps"]),
                                           settle["waited_s"]))
    if settle.get("defeated"):
        log("   !! faction destroyed during the AI phase -- defeat, not a stall")
        ended_by = "defeated"
    elif settle["turn"] is None and not terminal:
        log("   !! turn never advanced after %.0fs -- the watchdog decides from here"
            % settle["waited_s"])
    camp = (last_record.get("campaign") or {})
    state = {"faction": camp.get("faction"), "settlements": camp.get("settlements"),
             "armies": camp.get("armies"), "treasury": camp.get("treasury"),
             "income": camp.get("income"), "campaign_turn": camp.get("turn")}
    log("   state: turn=%s settlements=%s armies=%s treasury=%s income=%s"
        % (state["campaign_turn"], state["settlements"], state["armies"],
           state["treasury"], state["income"]))
    return TurnResult({"kind": "turn", "turn": turn, "actions": actions, "confirmed": confirmed,
                       "ended_by": ended_by, "picks": picks, "target": target,
                       "state": state, "inter_turn": settle, "ts": time.time()})


def _active_from(record, pol):
    """The entities still in play: this sweep minus everything the policy retired. The campaign
    context is always kept -- end_turn is a campaign offer."""
    lords, regions = [], []
    for e in record.get("entities") or []:
        k = (e["context_kind"], str(e["context_id"]))
        if k in pol.retired:
            continue
        if e["context_kind"] == "lord":
            lords.append(e["context_id"])
        elif e["context_kind"] == "province":
            regions.append(e["context_id"])
    return {"lords": lords, "regions": regions, "campaign": True}


def _noop_record(pick):
    """The verification record for a noop -- executed and confirmed."""
    return {"context_kind": pick["context_kind"], "context_id": pick["context_id"],
            "action_type": "noop", "key": "noop", "executed": True, "confirmed": True,
            "counted": True, "refusal": None, "policy": pick.get("policy"),
            "confirm": {"signal": "none", "before": {}, "after": {}, "latency_ms": 0}}


def _force_end_turn(run_dir, executor, decision_id, ranked, log):
    """End the turn without the model having picked it; tagged `forced_end_turn`."""
    log("   forcing end_turn (loop decision, not the model's)")
    pick = {"context_kind": "campaign", "context_id": "campaign", "action_type": "end_turn",
            "key": "end_turn", "params": {}, "policy": "forced_end_turn"}
    result = executor.execute(pick)
    if decision_id is not None:
        journal.log_verification(run_dir, decision_id, result)
    log("   forced end_turn -> %s" % ("OK" if result.get("counted") else result.get("refusal")))
    return result


def _append(path, row):
    import json
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def verify_streams(run_dir):
    """Per-stream counts plus the all_*/orphan_picks consistency flags."""
    sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))
    from store import DecisionStore                          # noqa: E402
    s = DecisionStore(run_dir)
    try:
        q = lambda sql: s.con.execute(sql).fetchone()[0]
        out = {
            "turns_with_reward": q("SELECT COUNT(*) FROM target_rows"),
            "decision_points": q("SELECT COUNT(*) FROM decision_points"),
            "points_with_offers": q("SELECT COUNT(DISTINCT decision_id) FROM action_offers"),
            "points_with_taken": q("SELECT COUNT(DISTINCT decision_id) FROM action_taken"),
            "taken_with_evidence": q("SELECT COUNT(*) FROM action_taken WHERE confirm_signal IS NOT NULL"
                                     " OR refusal IS NOT NULL"),
            "counted": q("SELECT COUNT(*) FROM action_taken WHERE counted=1"),
            "awaiting_execution": q("SELECT COUNT(*) FROM action_taken"
                                    " WHERE refusal='awaiting_execution'"),
            "offers": q("SELECT COUNT(*) FROM action_offers"),
            "scored_offers": q("SELECT COUNT(*) FROM action_offers WHERE score IS NOT NULL"),
        }
        out["all_points_have_offers"] = out["decision_points"] == out["points_with_offers"]
        out["all_taken_have_evidence"] = out["points_with_taken"] == out["taken_with_evidence"]
        out["orphan_picks"] = out["awaiting_execution"]      # must be 0
        return out
    finally:
        s.close()
