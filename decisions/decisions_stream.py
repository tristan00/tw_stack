from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import common

sys.path.insert(0, common.BUS)

import collect
import gameref
import journal
from store import DecisionStore

POLL = 0.1

PRUNE_EVERY = 600


def _tag_campaign(campaign_key, ctx):
    eid = os.environ.get("TW_EXPERIMENT_ID")
    sid = os.environ.get("TW_SEGMENT_ID")
    if not (eid or sid):
        return
    try:
        from decisions import workspace
        workspace.tag_campaign(campaign_key,
                               experiment_id=int(eid) if eid else None,
                               segment_id=int(sid) if sid else None)
    except Exception as e:
        ctx.on_error("tag_campaign", e)


def _sync_reference(bus, snap, ctx):
    camp = None
    chars = []
    for e in snap.get("entities") or ():
        if e["context_kind"] == "campaign":
            camp = e["state"]
        elif e["context_kind"] in ("lord", "hero"):
            st = e.get("state") or {}
            if st.get("subtype"):
                chars.append((e["context_id"], st["subtype"]))
    if camp is None:
        return
    faction = camp.get("faction")
    subtypes = {s for _, s in chars}
    try:
        want_tech = not gameref.have_faction_tech(faction)
        want_subtypes = sorted(subtypes - gameref.have_subtypes(subtypes))
        want_regions = gameref.region_count() == 0
    except Exception as e:
        ctx.on_error("gameref probe", e)
        return
    if not (want_tech or want_subtypes or want_regions):
        return
    t0 = time.time()
    try:
        ref = collect.game_reference(
            bus, characters=[c for c in chars if c[1] in set(want_subtypes)],
            with_regions=want_regions)
        if not want_tech:
            ref["tech"] = ref["tech_effect"] = ref["tech_parent"] = []
        n = gameref.write(ref, faction)
    except Exception as e:
        ctx.on_error("gameref collect", e)
        return
    ctx.emit({"kind": "gameref", "faction": faction, "ms": int((time.time() - t0) * 1000),
              "rows": n})


def run(ctx):
    from bus import Bus
    bus = Bus()
    store, cur_dir, after_id = None, None, 0
    seq = 0
    ticks = 0
    last_uuid = [None]

    def campaign_changed(snap_camp):
        u = (snap_camp or {}).get("campaign_uuid")
        if not u or u == last_uuid[0]:
            return False
        _tag_campaign(u, ctx)
        if last_uuid[0] is not None:
            ctx.emit({"kind": "decisions_status", "status": "campaign_changed",
                      "from": last_uuid[0], "to": u, "same_dir": True})
        last_uuid[0] = u
        return False
    counts = {"snapshot": 0, "turn": 0, "hash": 0, "pick": 0,
              "verification": 0, "error": 0}
    t_loop = time.time()
    sys.stderr.write("decisions_stream: poll loop starting (tick %.2fs)\n" % POLL)
    while ctx.is_running():
        try:
            out_dir = ctx.out_dir
            if out_dir != cur_dir:
                if store is not None:
                    store.close()
                store = DecisionStore(out_dir)
                cv = os.environ.get("TW_CODE_VERSION")
                if cv:
                    store.register_collector(
                        cv, git_sha=(cv.rsplit("+g", 1)[1].split(".")[0]
                                     if "+g" in cv else None),
                        note="code_version")
                stale = store.finalize_stale_awaiting()
                cur_dir, after_id, seq = out_dir, journal.last_request_id(out_dir), 0
                ctx.emit({"kind": "decisions_status", "status": "store_open",
                          "db": journal.pg.dsn(), "stale_finalized": stale,
                          "code_version": cv})
            rows, after_id = journal.read_requests(out_dir, after_id)
            for row in rows:
                kind, rid = row.get("kind"), row.get("req_id")
                try:
                    if kind == "snapshot":
                        t0 = time.time()
                        pickup_lag_ms = int((t0 - (row.get("ts") or t0)) * 1000)
                        snap = collect.snapshot(bus, active=row.get("active"))
                        t1 = time.time()
                        _sync_reference(bus, snap, ctx)
                        if campaign_changed(snap.get("campaign")):
                            seq = 0
                        did = store.write_decision(snap, decision_seq=seq)
                        t2 = time.time()
                        seq += 1
                        counts["snapshot"] += 1
                        journal.respond(out_dir, rid, decision_id=did,
                                        collect_ms=int((t1 - t0) * 1000),
                                        store_ms=int((t2 - t1) * 1000),
                                        pickup_lag_ms=pickup_lag_ms)
                        ctx.emit({"kind": "decisions_point", "decision_id": did,
                                  "entities": len(snap["entities"]),
                                  "turn": snap["campaign"].get("turn"),
                                  "ms": int((time.time() - t0) * 1000),
                                  "profile": snap.get("profile")})
                    elif kind == "turn":
                        cs = collect.campaign_state(bus)
                        campaign_changed(cs)
                        counts["turn"] += 1
                        journal.respond(out_dir, rid, turn=cs.get("turn"),
                                        campaign_uuid=cs.get("campaign_uuid"))
                    elif kind == "hash":
                        h = collect.state_hash(bus)
                        counts["hash"] += 1
                        journal.respond(out_dir, rid, hash=h["hash"], roots=h["roots"])
                    elif kind == "interrupt":
                        cs = collect.campaign_state(bus)
                        try:
                            ws = collect.world_state(bus)
                        except Exception as e:
                            ws = None
                            sys.stderr.write("decisions_stream: world for interrupt -> %s\n"
                                             % repr(e)[:90])
                        store.write_interrupt(dict(row, campaign=cs, world=ws))
                        counts["interrupt"] = counts.get("interrupt", 0) + 1
                        ctx.emit({"kind": "decisions_interrupt", "screen": row.get("kind_screen")
                                  or row.get("screen"), "chosen": row.get("chosen"),
                                  "turn": cs.get("turn")})
                    elif kind == "diplomacy":
                        store.write_diplomacy_event(row)
                        counts["diplomacy"] = counts.get("diplomacy", 0) + 1
                    elif kind == "ucb_pick":
                        store.write_ucb_pick(row)
                        counts["ucb_pick"] = counts.get("ucb_pick", 0) + 1
                    elif kind == "postmortem":
                        store.write_postmortem(row)
                        counts["postmortem"] = counts.get("postmortem", 0) + 1
                        ctx.emit({"kind": "decisions_postmortem",
                                  "campaign": row.get("campaign_key"),
                                  "outcome": row.get("outcome")})
                    elif kind == "options":
                        did = row.get("decision_id")
                        n = store.attach_options(did, row.get("options"))
                        counts["options"] = counts.get("options", 0) + n
                        ctx.emit({"kind": "decisions_options", "decision_id": did,
                                  "options": n})
                    elif kind == "pick":
                        did = row.get("decision_id")
                        store.attach_scores(did, row.get("scores"))
                        store.attach_timings(did, row.get("timings"))
                        pick = row.get("pick") or {}
                        store.attach_taken(did, dict(pick, executed=False, confirmed=False,
                                                     counted=False, refusal="awaiting_execution"),
                                           policy=pick.get("policy"))
                        counts["pick"] += 1
                        ctx.emit({"kind": "decisions_pick", "decision_id": did,
                                  "context": pick.get("context_kind"),
                                  "action": pick.get("action_type"), "key": pick.get("key")})
                    elif kind == "verification":
                        did = row.get("decision_id")
                        res = row.get("result") or {}
                        store.attach_taken(did, res, policy=res.get("policy"))
                        counts["verification"] += 1
                        ctx.emit({"kind": "decisions_verify", "decision_id": did,
                                  "action": res.get("action_type"), "key": res.get("key"),
                                  "counted": bool(res.get("counted")),
                                  "refusal": res.get("refusal")})
                    else:
                        raise ValueError("unknown request kind %r" % kind)
                except Exception as e:
                    counts["error"] += 1
                    ctx.on_error("decisions-%s" % kind, e)
                    _m = repr(e)
                    ctx.emit({"kind": "decisions_error", "req_kind": kind,
                              "err": _m if len(_m) <= 700 else _m[:200] + " ...<<cut>>... " + _m[-500:]})
                    if rid:
                        journal.respond(out_dir, rid, error=(lambda _m: _m if len(_m) <= 700 else
                                                 _m[:200] + " ...<<cut>>... " + _m[-500:])(repr(e)))
            ticks += 1
            if ticks % PRUNE_EVERY == 0 and after_id:
                gone_a, gone_b = journal.prune(out_dir, after_id)
                if gone_a or gone_b:
                    ctx.emit({"kind": "decisions_status", "status": "pruned",
                              "requests": gone_a, "responses": gone_b})
        except Exception as e:
            ctx.on_error("decisions-stream", e)
            common.wait("decisions_error_backoff", 5.0, repr(e)[:80])
            continue
        journal.wait_requests(out_dir, POLL)
    common.waitlog("decisions_poll", time.time() - t_loop, True, "stopped")
    if store is not None:
        ctx.emit({"kind": "decisions_status", "status": "closing", **counts})
        store.close()
