r"""loop.py -- the v7 advisor game loop.

THE LOOP (as specified): after every move, re-derive the WHOLE faction's possible actions, score
them in ONE ranking, execute the top one, and repeat until the action limit is reached or end_turn
is the chosen decision.

    while True:
        decision_id, record = recorder.request_snapshot()     # STREAM 2, read at this instant
        pick, ranked        = policy.choose(record)
        recorder.log_pick(decision_id, pick, ranked)          # STREAM 3 + the ranking behind it
        if pick is end_turn: execute it, write the reward row, next turn
        if pick is noop:     retire that entity, continue
        result = launcher.execute(pick)                       # gates -> execute -> confirm
        recorder.log_verification(decision_id, result)        # STREAM 4
        launcher.resolve_battles()

There is NO cycling through lords and then provinces: every entity's options compete in the same
ranking, so a province's building can outrank a lord's attack. Entities leave the pool when the
ranking picks their noop or they hit the per-entity action cap, which shrinks each subsequent sweep.

BOUNDARIES THIS FILE KEEPS
  * It never opens a bus. Game state arrives as DB records from the recorder; actions go out
    through the launcher's Executor. That is the whole reason the recorder exists.
  * Every executed action gets a record in ALL FOUR streams: the sweep writes the offers (2), the
    pick writes the choice (3), the launcher's ActionRecord writes the verification (4), and each
    turn writes one reward row (1). `verify_streams` at the end asserts exactly that.
  * A watchdog thread polls the recorder's liveness digest; if the game stops changing (or the bus
    stops answering) for STUCK_SECONDS, it screenshots and ends the run rather than hanging.
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))

import journal                                             # noqa: E402  recorder request API
import policy as P                                         # noqa: E402
from watchdog import Watchdog                              # noqa: E402


class GameStuck(RuntimeError):
    """The watchdog saw no progress. The run is over; the caller starts a fresh one."""


class TurnResult(dict):
    pass


def run_campaign(run_dir, executor, pol=None, turns=3, log=print,
                 stuck_seconds=None, on_stuck=None):
    """Play `turns` turns. Returns the per-turn report rows (also written to loop_report.jsonl)."""
    pol = pol or P.Policy()
    report_path = os.path.join(run_dir, "loop_report.jsonl")
    stuck = {"fired": False, "reason": None, "detail": None, "shot": None}

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
                raise GameStuck("%s: %s" % (stuck["reason"], stuck["detail"]))
            row = _run_turn(run_dir, executor, pol, wd, stuck, log)
            rows.append(row)
            _append(report_path, row)
            log("== turn %s done: %d actions (%d confirmed), ended by %s =="
                % (row["turn"], row["actions"], row["confirmed"], row["ended_by"]))
            if row["ended_by"] == "stuck":
                raise GameStuck("%s: %s" % (stuck["reason"], stuck["detail"]))
    finally:
        wd.stop()
    return rows


def _run_turn(run_dir, executor, pol, wd, stuck, log):
    pol.new_turn()
    turn = journal.request_turn(run_dir)
    log("== TURN %s ==" % turn)
    opening = executor.resolve_interrupts()          # a battle/diplomacy screen may still be up
    if opening:
        log("   opening interrupts: %s" % ", ".join(str(s) for s in opening))
    actions, confirmed, ended_by, picks = 0, 0, "action_cap", []
    active = None                                   # None = every entity is in play
    while actions < pol.max_actions_per_turn:
        if stuck["fired"]:
            ended_by = "stuck"
            break
        decision_id, record = journal.request_snapshot(run_dir, active=active)
        t_scored0 = time.time()
        pick, ranked = pol.choose(record, actions_taken=actions)
        t_scored = time.time()
        # the per-action phase breakdown, in ms: how long the recorder spent reading the game, how
        # long the round trip added on top, how long scoring took, then execute+confirm. Recorded so
        # a slow session can be READ rather than guessed at.
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
            active = _active_from(record, pol)
            continue

        result = executor.execute(pick)
        journal.log_verification(run_dir, decision_id, result)
        actions += 1
        ok = bool(result.get("counted"))
        confirmed += 1 if ok else 0
        pol.note_result(pick, ok)
        if ok:
            wd.beat("confirmed:%s" % pick["action_type"])
        log("   %-16s %-34s %s%s"
            % (pick["action_type"], str(pick["key"])[:34],
               "OK" if ok else "FAIL", "" if ok else " (%s)" % result.get("refusal")))

        if pick["action_type"] == "end_turn":
            ended_by = "end_turn_chosen" if ok else "end_turn_failed"
            break
        steps = executor.resolve_interrupts()
        if steps:
            log("   interrupts: %s" % ", ".join(str(s) for s in steps))
            wd.beat("interrupts")
        active = _active_from(record, pol)
    else:
        ended_by = "action_cap"
        _force_end_turn(run_dir, executor, None, None, log)

    # STREAM 1 BEFORE the AI plays: the reward row must describe the turn WE just played, sampled at
    # the same point in every turn cycle, not after other factions have moved our numbers.
    target = None
    if ended_by != "stuck":
        target = journal.request_target(run_dir)

    # ride out the AI turns -- defensive battles and incoming diplomacy land here, and the turn
    # number does not advance until they are cleared
    settle = executor.settle_between_turns(turn_before=turn)
    if settle["steps"]:
        log("   inter-turn: %s (%.0fs)" % (", ".join(str(s) for s in settle["steps"]),
                                           settle["waited_s"]))
    if settle["turn"] is None and ended_by not in ("stuck",):
        log("   !! turn never advanced after %.0fs -- the watchdog decides from here"
            % settle["waited_s"])
    return TurnResult({"kind": "turn", "turn": turn, "actions": actions, "confirmed": confirmed,
                       "ended_by": ended_by, "picks": picks, "target": target,
                       "inter_turn": settle, "ts": time.time()})


def _active_from(record, pol):
    """The entities still in play, from the sweep we just took minus everything the policy retired.
    Shrinking the sweep is what keeps re-deriving the whole faction affordable after every move."""
    lords, regions, camp = [], [], True
    for e in record.get("entities") or []:
        k = (e["context_kind"], str(e["context_id"]))
        if k in pol.retired:
            if e["context_kind"] == "campaign":
                camp = False
            continue
        if e["context_kind"] == "lord":
            lords.append(e["context_id"])
        elif e["context_kind"] == "province":
            regions.append(e["context_id"])
    return {"lords": lords, "regions": regions, "campaign": camp}


def _noop_record(pick):
    """A noop is a real, trivially-confirmed action: "the model saw N options and chose to stop"
    is the label we want, not an absent row."""
    return {"context_kind": pick["context_kind"], "context_id": pick["context_id"],
            "action_type": "noop", "key": "noop", "executed": True, "confirmed": True,
            "counted": True, "refusal": None, "policy": pick.get("policy"),
            "confirm": {"signal": "none", "before": {}, "after": {}, "latency_ms": 0}}


def _force_end_turn(run_dir, executor, decision_id, ranked, log):
    """The turn hit the cap (or ran out of eligible actions) without the model choosing end_turn.
    Ending it is the LOOP's decision, not the model's, so it is tagged that way and never trains as
    if the ranking had picked it."""
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


# ------------------------------------------------------------------ the four-stream assertion
def verify_streams(run_dir):
    """Assert the contract: EVERY action has a record in all four streams.

    stream 1  a reward row for every turn that ran
    stream 2  offers for every decision point
    stream 3  a chosen action for every decision point that was acted on
    stream 4  executed/confirmed/counted evidence on every one of those
    """
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
        out["orphan_picks"] = out["awaiting_execution"]      # picks never verified -> must be 0
        return out
    finally:
        s.close()
