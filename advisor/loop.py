from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

HUD_MISS_BUDGET = 12
HUD_MISS_PAUSE = 5.0
POST_ATTACK_ROW_WAIT = 22.0
POST_ATTACK_HUD_TRIES = 3
ATTACK_LOCOMOTION_CAP = 10.0
LOCOMOTION_ACTIONS = frozenset(("attack_army", "attack_settlement", "colonize"))
POST_ATTACK_HUD_PAUSE = 0.6
_last_beat_turn = [None]
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import features as F
import journal
import policy as P

sys.path.insert(0, os.path.join(r"D:\tw_stack", "launcher"))
import trace as TR
from watchdog import Watchdog


class CampaignLost(RuntimeError):
    pass


class CampaignStagnant(RuntimeError):
    pass


class GameStuck(RuntimeError):
    pass


GROWTH_WINDOW = 4
GROWTH_LORD_WINDOW = 3
GROWTH_MIN_GAIN = 1
GROWTH_FIRST_CHECK_TURN = 4

GROWTH_METRICS = (("settlements", "settlements", GROWTH_WINDOW),
                  ("lord_level", "legendary lord level", GROWTH_LORD_WINDOW))


def _await_locomotion(executor, log, run_dir=None, pick=None,
                      cap=ATTACK_LOCOMOTION_CAP, step=0.3):
    t0 = time.time()
    last, polls, capped = None, 0, False
    while time.time() - t0 < cap:
        probe = executor.transition_probe() or {}
        last = probe.get("locomotion_done")
        polls += 1
        if last != "false":
            break
        time.sleep(step)
    else:
        capped = True
        log("   attack: locomotion still running after %.1fs -- proceeding" % cap)
    elapsed = time.time() - t0
    if run_dir:
        _append(os.path.join(run_dir, "locomotion.jsonl"),
                {"ts": time.time(), "action_type": (pick or {}).get("action_type"),
                 "key": (pick or {}).get("key"), "seconds": round(elapsed, 2),
                 "polls": polls, "locomotion_done": last, "hit_cap": capped, "cap": cap})
    return elapsed, last


def _trace_post_attack(executor, log, run_dir, pick, tries=POST_ATTACK_HUD_TRIES,
                       pause=POST_ATTACK_HUD_PAUSE):
    t0 = time.time()
    samples = []
    for i in range(tries):
        roots = executor.visible_roots() or []
        probe = executor.transition_probe() or {}
        samples.append({"t_ms": int((time.time() - t0) * 1000),
                        "hud_root": "hud_campaign" in roots,
                        "popups": [r for r in roots if str(r).startswith("popup_")
                                   or r in ("events", "dilemma_choice", "settlement_captured")],
                        "locomotion_done": probe.get("locomotion_done"),
                        "hud_visible": probe.get("hud_visible"),
                        "pending": probe.get("pending"),
                        "cutscene": probe.get("cutscene")})
        if samples[-1]["popups"] or samples[-1]["hud_root"]:
            break
        if i + 1 < tries:
            time.sleep(pause)
    _append(os.path.join(run_dir, "post_attack_trace.jsonl"),
            {"ts": time.time(), "action_type": pick.get("action_type"), "key": pick.get("key"),
             "samples": samples})
    last = samples[-1]
    if not last["hud_root"] and not last["popups"]:
        log("   post-attack: no HUD and no popup after %dms -- locomotion=%s pending=%s "
            "cutscene=%s hud_visible=%s"
            % (last["t_ms"], last["locomotion_done"], last["pending"], last["cutscene"],
               last["hud_visible"]))
    return last


def _growth_verdict(hist, turn):
    t = int(turn or 0)
    if (hist.get(t) or {}).get("ll_wounded"):
        return True, {"reason": "legendary_lord_wounded", "turn": t, "grew": [],
                      "evaluable": [], "min_gain": GROWTH_MIN_GAIN, "metrics": {}}
    if t < GROWTH_FIRST_CHECK_TURN:
        return False, {"reason": "before_first_check", "turn": t,
                       "first_check_turn": GROWTH_FIRST_CHECK_TURN}
    detail = {"turn": t, "min_gain": GROWTH_MIN_GAIN, "metrics": {}}
    grew, evaluable = [], []
    first_turn = min(hist) if hist else t
    for key, label, window in GROWTH_METRICS:
        then_turn = max(t - window, first_turn)
        now = (hist.get(t) or {}).get(key)
        then = (hist.get(then_turn) or {}).get(key) if then_turn < t else None
        m = {"label": label, "window": t - then_turn, "now": now, "then": then,
             "then_turn": then_turn}
        if now is None or then is None:
            m["gain"] = None
        else:
            m["gain"] = now - then
            evaluable.append(key)
            if now - then >= GROWTH_MIN_GAIN:
                grew.append(key)
        detail["metrics"][key] = m
    detail["grew"] = grew
    detail["evaluable"] = evaluable
    if not evaluable:
        detail["reason"] = "no_metric_evaluable"
        return False, detail
    detail["reason"] = "growing" if grew else "stagnant"
    return not grew, detail


def _growth_line(detail):
    if detail.get("reason") == "legendary_lord_wounded":
        return "legendary lord WOUNDED -- automatic fail"
    return " | ".join(
        "%s %s over %d turns (%s -> %s)"
        % (m["label"], "?" if m["gain"] is None else "%+d" % m["gain"],
           m["window"], m["then"], m["now"])
        for m in detail.get("metrics", {}).values())


class TurnResult(dict):
    pass


NO_MODEL_DIR = r"D:\twdata\models\__cold_start__"


def _verify_action_catalogues(log):
    try:
        sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor", "reference"))
        import collect as C
        import features_db as DB
        mapped = DB.verify_hero_action_mappings(C.HERO_ACTIONS)
        dead = sorted(k for k, v in mapped.items() if not v)
        log("hero actions: %d/%d executable%s"
            % (len(mapped) - len(dead), len(mapped),
               (" -- DEAD: " + ", ".join(dead)) if dead else ""))
    except Exception as e:
        log("!! could not verify the hero-action catalogue: %s" % repr(e)[:160])


def run_campaign(run_dir, executor, pol=None, turns=3, log=print,
                 stuck_seconds=None, on_stuck=None, cold=False, on_turn=None):
    pol = pol or P.Policy()
    TR.set_run_dir(run_dir)
    import diplo_stream as DS
    DS.reset(run_dir)
    report_path = os.path.join(run_dir, "loop_report.jsonl")
    stuck = {"fired": False, "reason": None, "detail": None, "shot": None}
    try:
        executor.disable_ui_hotkeys()
    except Exception as e:
        log("!! could not disable the UI-hide hotkeys: %s" % repr(e)[:120])
    executor.mark_campaign_start()
    import interrupt_model as IM
    import interrupts as I
    _verify_action_catalogues(log)
    ranker = IM.InterruptRanker(NO_MODEL_DIR) if cold else IM.InterruptRanker()
    act_hist = []
    act_counts = {}
    I.reset_answers()
    I.set_chooser(lambda screen, options, campaign, panel=None, world=None, meta=None:
                  ranker.choose(screen, options, F.stamp_action_counts(
                      F.stamp_prev_actions(dict(campaign or {}), act_hist), act_counts),
                      panel, world, meta))
    if ranker.ready:
        log("interrupt policy: trained(%d rows, screens=%s)"
            % ((ranker.meta or {}).get("rows", 0),
               ",".join((ranker.meta or {}).get("screens") or [])))
    elif cold:
        log("interrupt policy: cold_random (FORCED -- cold start, the fitted model is ignored)")
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
    growth_hist = {}
    diplo_epoch = [0]

    def _gate_would_fail(state, turn_no):
        hist = dict(growth_hist)
        hist[int(turn_no or 0)] = dict(state or {})
        done, _v = _growth_verdict(hist, turn_no)
        return done

    def _carry(exc):
        if getattr(exc, "rows", None) is None:
            exc.rows = list(rows)
        return exc

    log("growth bar: survives on %s, first checked at turn %d"
        % (" OR ".join("+%d %s over %d turns" % (GROWTH_MIN_GAIN, label, window)
                       for _, label, window in GROWTH_METRICS),
           GROWTH_FIRST_CHECK_TURN))
    try:
        for t in range(turns):
            if stuck["fired"]:
                if executor.defeated_row_seen() or executor.defeated_probe() is True:
                    raise CampaignLost("watchdog fired but the faction is DEAD -- defeat, "
                                       "not a stall (%s)" % stuck["reason"])
                raise GameStuck("%s: %s" % (stuck["reason"], stuck["detail"]))
            row = _run_turn(run_dir, executor, pol, wd, stuck, log, diplo_epoch,
                            act_hist, act_counts, pre_settle=_gate_would_fail)
            rows.append(row)
            _append(report_path, row)
            log("== turn %s done: %d actions (%d confirmed), ended by %s =="
                % (row["turn"], row["actions"], row["confirmed"], row["ended_by"]))
            _turn_trail(run_dir, executor, row, len(rows), log)
            if on_turn is not None:
                try:
                    on_turn(rows)
                except Exception as e:
                    log("!! on_turn hook failed: %s" % repr(e)[:140])
            if row["ended_by"] == "defeated":
                raise CampaignLost("faction destroyed at turn %s after %d turns played"
                                   % (row["turn"], len(rows)))
            if row["ended_by"] == "no_campaign_ui":
                raise GameStuck("no_campaign_ui: hud_campaign absent, nothing clickable "
                                "(turn %s, %d actions, %d confirmed)"
                                % (row["turn"], row["actions"], row["confirmed"]))
            if row["ended_by"] == "stuck":
                if executor.defeated_row_seen() or executor.defeated_probe() is True:
                    raise CampaignLost("watchdog fired but the faction is DEAD -- defeat, "
                                       "not a stall (%s)" % stuck["reason"])
                raise GameStuck("%s: %s" % (stuck["reason"], stuck["detail"]))
            growth_hist[int(row["turn"] or 0)] = dict(row.get("state") or {})
            done, verdict = _growth_verdict(growth_hist, row["turn"])
            _append(report_path, dict(verdict, kind="growth_check", ts=time.time()))
            if verdict["reason"] == "no_metric_evaluable":
                log("   !! growth bar NOT evaluated at turn %s -- neither settlements nor lord "
                    "level is readable over its window; the campaign runs on" % row["turn"])
            elif verdict["reason"] != "before_first_check":
                log("   growth: %s -> %s" % (_growth_line(verdict),
                                             ("kept by " + ",".join(verdict["grew"]))
                                             if verdict["grew"] else "NO GROWTH"))
            if done:
                stop = CampaignStagnant(
                    "no growth at turn %s (%s), +%d required on either -- after %d turns played"
                    % (verdict["turn"], _growth_line(verdict), GROWTH_MIN_GAIN, len(rows)))
                stop.rows = list(rows)
                stop.verdict = verdict
                raise stop
    except (CampaignLost, CampaignStagnant, GameStuck) as e:
        raise _carry(e)
    finally:
        wd.stop()
        _drain_interrupts(run_dir, log)
        try:
            DS.emit("campaign_end", turns_played=len(rows),
                    ended_by=(rows[-1]["ended_by"] if rows else None),
                    tracked=DS.tracked())
        except Exception as e:
            log("   diplo_stream campaign_end -> %s" % repr(e)[:80])
    return rows


SHOT_EVERY_TURNS = 5


def _turn_trail(run_dir, executor, row, turn_index, log):
    rec = {"ts": time.time(), "turn": row.get("turn"), "turn_index": turn_index,
           "actions": row.get("actions"), "confirmed": row.get("confirmed"),
           "ended_by": row.get("ended_by")}
    if row.get("ended_by") == "defeated":
        rec.update(roots=None, ui_state=None, probes_skipped="defeat_modal_tick_paused")
    else:
        for name, fn in (("roots", executor.visible_roots), ("ui_state", executor.ui_state)):
            try:
                rec[name] = fn()
            except Exception as e:
                rec[name] = None
                rec[name + "_error"] = repr(e)[:100]
    abnormal = row.get("ended_by") not in ("end_turn_chosen", "action_cap")
    if (abnormal or turn_index == 1 or turn_index % SHOT_EVERY_TURNS == 0) \
            and row.get("ended_by") != "defeated":
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


_DIPLO_SCREENS = frozenset(("diplomacy_proposal", "diplomacy_notice", "ally_attacked",
                            "diplomacy", "diplomacy_hud"))


def _drain_interrupts(run_dir, log, diplo_epoch=None):
    try:
        import interrupts as I
        recs = I.drain_interrupt_records()
    except Exception as e:
        log("   !! could not drain interrupt records: %s" % repr(e)[:120])
        return
    if diplo_epoch is not None and any(str(r.get("kind")) in _DIPLO_SCREENS for r in recs):
        diplo_epoch[0] += 1
    for r in recs:
        try:
            journal.log_interrupt(run_dir, r)
        except Exception as e:
            log("   !! interrupt record not persisted: %s" % repr(e)[:120])
    if recs:
        log("   recorded %d interrupt decision(s): %s"
            % (len(recs), ", ".join("%s->%s" % (x.get("kind"), x.get("chosen")) for x in recs)))


def _run_turn(run_dir, executor, pol, wd, stuck, log, diplo_epoch=None, act_hist=None,
              act_counts=None, pre_settle=None):
    import diplo_stream as DS
    import interrupts as I
    diplo_epoch = diplo_epoch if diplo_epoch is not None else [0]
    act_hist = act_hist if act_hist is not None else []
    act_counts = act_counts if act_counts is not None else {}
    pol.new_turn()
    turn = journal.request_turn(run_dir)
    DS.TURN[0] = turn
    _last_done_ts = [None]
    _hk_parts = [{}]
    log("== TURN %s ==" % turn)
    opening = executor.resolve_interrupts()
    _drain_interrupts(run_dir, log, diplo_epoch)
    if opening:
        log("   opening interrupts: %s" % ", ".join(str(s) for s in opening))
    actions, confirmed, ended_by, picks = 0, 0, "action_cap", []
    moves = 0
    active = None
    no_hud = 0
    last_record = {}
    gate_failed = [False]

    def _check_gate_before_end():
        if pre_settle is None:
            return False
        camp0 = (last_record.get("campaign") or {})
        st = {"settlements": camp0.get("settlements"), "lord_level": camp0.get("lord_level"),
              "ll_wounded": camp0.get("ll_wounded")}
        try:
            failing = bool(pre_settle(st, turn))
        except Exception as e:
            log("   !! pre-end-turn gate check failed: %s" % repr(e)[:120])
            return False
        if failing:
            log("   growth gate: FAILING at end of turn %s -- ending the turn for the scoring "
                "row, then skipping the inter-turn settle" % turn)
        return failing
    while actions < pol.max_actions_per_turn:
        if stuck["fired"]:
            ended_by = "stuck"
            break
        if executor.defeated_row_seen():
            log("   faction_destroyed row seen mid-turn -- defeat, ending the turn now")
            ended_by = "defeated"
            break
        _t = time.time()
        alive = executor.campaign_ui_alive()
        _hk_parts[0]["hud_check"] = _hk_parts[0].get("hud_check", 0) + int((time.time() - _t) * 1000)
        if alive is False:
            no_hud += 1
            roots = executor.visible_roots()
            log("   !! hud_campaign missing (%d/%d) -- roots=%s"
                % (no_hud, HUD_MISS_BUDGET, ",".join(sorted(str(r) for r in roots))[:200]))
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
            if no_hud == 1:
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
        t_housekeep0 = _last_done_ts[0]
        decision_id, record = journal.request_snapshot(run_dir, active=active, diplo_epoch=diplo_epoch[0])
        (record.setdefault("campaign", {}))["act_index"] = actions + 1
        record["campaign"]["move_index"] = moves + 1
        F.stamp_prev_actions(record["campaign"], act_hist)
        F.stamp_action_counts(record["campaign"], act_counts)
        I.set_snapshot(record["campaign"], record.get("world"))
        last_record = record
        if turn is not None and turn != _last_beat_turn[0]:
            _last_beat_turn[0] = turn
            wd.beat("turn_advanced")
        t_scored0 = time.time()
        pick, ranked = pol.choose(record, actions_taken=actions)
        t_scored = time.time()
        TR.advisor(pick, ranked_top=[{k: r.get(k) for k in
                                      ("context_kind", "context_id", "action_type", "key",
                                       "score", "exploit", "explore", "rank")}
                                     for r in (ranked or [])[:10]],
                   turn=turn, decision_id=decision_id, actions_taken=actions,
                   n_offers=len(ranked or []),
                   choice=pol.last_choice, dropped=pol.last_drops)
        t_traced = time.time()
        timing = {"t_request": record.get("_t_request"), "t_received": record.get("_t_received"),
                  "collect_ms": record.get("_collect_ms"),
                  "store_ms": record.get("_store_ms"),
                  "pickup_lag_ms": record.get("_pickup_lag_ms"),
                  "trace_ms": int((t_traced - t_scored) * 1000),
                  "housekeep_ms": (int((record.get("_t_request", 0) - t_housekeep0) * 1000)
                                   if t_housekeep0 and record.get("_t_request") else None),
                  "housekeep_parts": dict(_hk_parts[0]) or None,
                  "roundtrip_ms": int((record.get("_t_received", t_scored0)
                                       - record.get("_t_request", t_scored0)) * 1000),
                  "score_ms": int((t_scored - t_scored0) * 1000),
                  "offers": sum(len(e.get("offers") or []) for e in record.get("entities") or []),
                  "entities": len(record.get("entities") or [])}
        if pick is None:
            log("   nothing eligible -> ending turn")
            ended_by = "no_eligible_actions"
            gate_failed[0] = _check_gate_before_end()
            _force_end_turn(run_dir, executor, decision_id, ranked, log)
            act_hist.append("end_turn")
            del act_hist[:-F.PREV_ACTIONS]
            F.bump_action_counts(act_counts, "end_turn")
            break
        _t = time.time()
        journal.log_pick(run_dir, decision_id, pick, P.scores_for_store(ranked), timings=timing)
        _hk_parts[0] = {"pick_log": int((time.time() - _t) * 1000)}
        picks.append({"action": pick["action_type"], "key": pick["key"],
                      "context": "%s:%s" % (pick["context_kind"], pick["context_id"]),
                      "policy": pick["policy"], "score": pick.get("score")})

        if pick["action_type"] == "noop":
            _t = time.time()
            pol.retire(pick["context_kind"], pick["context_id"])
            journal.log_verification(run_dir, decision_id, _noop_record(pick))
            _hk_parts[0]["verify_log"] = int((time.time() - _t) * 1000)
            log("   %-9s %-24s -> retired" % ("noop", pick["context_id"][:24]))
            wd.beat("noop_retired")
            _t = time.time()
            active = _active_from(record, pol)
            _hk_parts[0]["active_from"] = int((time.time() - _t) * 1000)
            _last_done_ts[0] = time.time()
            continue

        if pick["action_type"] == "end_turn":
            gate_failed[0] = _check_gate_before_end()
        pre_off = executor.bus.out_offset()
        result = executor.execute(pick)
        act_hist.append(pick["action_type"])
        del act_hist[:-F.PREV_ACTIONS]
        F.bump_action_counts(act_counts, pick["action_type"])
        _t = time.time()
        journal.log_verification(run_dir, decision_id, result)
        _hk_parts[0]["verify_log"] = int((time.time() - _t) * 1000)
        actions += 1
        if pick["action_type"] == "move":
            moves += 1
        ok = bool(result.get("counted"))
        confirmed += 1 if ok else 0
        pol.note_result(pick, ok)
        if pick["action_type"] == "diplomacy":
            diplo_epoch[0] += 1
        if ok:
            wd.beat("confirmed:%s" % pick["action_type"])
        log("   %-16s %-34s %s%s"
            % (pick["action_type"], str(pick["key"])[:34],
               "OK" if ok else "FAIL", "" if ok else " (%s)" % result.get("refusal")))

        if pick["action_type"] == "end_turn":
            if ok:
                ended_by = "end_turn_chosen"
                break
            log("   end_turn refused -- retrying rather than settling on a turn that never ended")
            ended_by = "end_turn_failed"
            active = _active_from(record, pol)
            continue
        if pick.get("action_type") in LOCOMOTION_ACTIONS:
            _t = time.time()
            moved_s, loco = _await_locomotion(executor, log, run_dir, pick)
            if moved_s >= 0.5:
                log("   attack: lord moved for %.1fs (locomotion_done=%s) -- timeout starts now"
                    % (moved_s, loco))
            row = executor.bus.wait_row(
                ("panel", "battle_completed", "dilemma_issued"),
                timeout=POST_ATTACK_ROW_WAIT, offset=pre_off,
                pred=lambda r: r.get("cmd") != "panel" or bool(r.get("opened")))
            if row is None:
                try:
                    _trace_post_attack(executor, log, run_dir, pick)
                except Exception as e:
                    log("   !! post-attack trace failed: %s" % repr(e)[:120])
                wd.beat("post_attack_trace")
            pre = executor.resolve_interrupts()
            _hk_parts[0]["post_attack"] = int((time.time() - _t) * 1000)
            if pre:
                log("   post-attack interrupts: %s" % ", ".join(str(s) for s in pre))
                wd.beat("post_attack_interrupt")
        _t = time.time()
        _drain_interrupts(run_dir, log, diplo_epoch)
        _hk_parts[0]["drain"] = int((time.time() - _t) * 1000)
        _t = time.time()
        steps = executor.resolve_interrupts()
        _hk_parts[0]["resolve"] = int((time.time() - _t) * 1000)
        if steps:
            log("   interrupts: %s" % ", ".join(str(s) for s in steps))
            wd.beat("interrupts")
        _t = time.time()
        active = _active_from(record, pol)
        _hk_parts[0]["active_from"] = int((time.time() - _t) * 1000)
        _last_done_ts[0] = time.time()
    else:
        ended_by = "action_cap"
        gate_failed[0] = _check_gate_before_end()
        _force_end_turn(run_dir, executor, None, None, log)

    terminal = ended_by in ("stuck", "no_campaign_ui", "defeated")
    target = None
    doomed = stuck.get("fired") or executor.defeated_row_seen()
    if doomed and not terminal:
        log("   reward row skipped: %s -- the campaign tick is frozen, the read can only "
            "time out" % ("stuck flag up" if stuck.get("fired") else "defeat row seen"))
    if not terminal and not doomed:
        target = journal.request_target(run_dir)

    if terminal:
        settle = {"turn": None, "steps": [], "waited_s": 0.0, "skipped": ended_by}
    elif gate_failed[0]:
        settle = {"turn": None, "steps": [], "waited_s": 0.0, "skipped": "growth_gate_failed"}
        log("   inter-turn settle skipped: the growth gate failed, the campaign ends here")
    else:
        settle = executor.settle_between_turns(turn_before=turn,
                                               abort=lambda: bool(stuck.get("fired")))
    if settle["steps"]:
        log("   inter-turn: %s (%.0fs)" % (", ".join(str(s) for s in settle["steps"]),
                                           settle["waited_s"]))
    if settle.get("defeated"):
        log("   !! faction destroyed during the AI phase -- defeat, not a stall")
        ended_by = "defeated"
    elif settle["turn"] is None and not terminal and not settle.get("skipped"):
        log("   !! turn never advanced after %.0fs -- the watchdog decides from here"
            % settle["waited_s"])
    if (not terminal and not settle.get("defeated") and DS.tracked()
            and settle.get("turn") is not None and not stuck.get("fired")):
        try:
            DS.checkpoint(executor.bus)
        except Exception as e:
            log("   diplo_stream checkpoint -> %s" % repr(e)[:80])
    camp = (last_record.get("campaign") or {})
    state = {"faction": camp.get("faction"), "settlements": camp.get("settlements"),
             "armies": camp.get("armies"), "treasury": camp.get("treasury"),
             "income": camp.get("income"), "lord_level": camp.get("lord_level"),
             "ll_wounded": camp.get("ll_wounded"), "campaign_turn": camp.get("turn")}
    log("   state: turn=%s settlements=%s armies=%s treasury=%s income=%s lord_level=%s"
        % (state["campaign_turn"], state["settlements"], state["armies"],
           state["treasury"], state["income"], state["lord_level"]))
    return TurnResult({"kind": "turn", "turn": turn, "actions": actions, "confirmed": confirmed,
                       "ended_by": ended_by, "picks": picks, "target": target,
                       "state": state, "inter_turn": settle, "ts": time.time()})


def _active_from(record, pol):
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
    return {"context_kind": pick["context_kind"], "context_id": pick["context_id"],
            "action_type": "noop", "key": "noop", "executed": True, "confirmed": True,
            "counted": True, "refusal": None, "policy": pick.get("policy"),
            "confirm": {"signal": "none", "before": {}, "after": {}, "latency_ms": 0}}


def _force_end_turn(run_dir, executor, decision_id, ranked, log):
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
    sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))
    from store import DecisionStore
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
        out["orphan_picks"] = out["awaiting_execution"]
        return out
    finally:
        s.close()
