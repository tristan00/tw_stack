from __future__ import annotations

import time

PB_ATTACK_TYPES = ("attack_army", "attack_settlement")
RECRUIT_TYPES = ("recruit_unit", "raise_dead", "recruit_ror", "recruit_blessed",
                 "recruit_imperial")
PB_CHOICES = ("autoresolve", "retreat", "surround", "continue_siege",
              "sally_forth", "maintain_blockade", "demand_surrender")
PB_RESULTS = ("victory", "defeat")
PB_WINDOW_S = 120.0
MEM_LIMIT = 60


def choice_name(chosen):
    c = str(chosen or "")
    if c.startswith("button_"):
        c = c[len("button_"):]
    return c or "none"


def choice_index(name):
    try:
        return float(PB_CHOICES.index(str(name)) + 1)
    except ValueError:
        return 0.0


def result_index(name):
    try:
        return float(PB_RESULTS.index(str(name)) + 1)
    except ValueError:
        return 0.0


def target_zone(atype, key, params, world):
    w = world or {}
    if atype == "attack_settlement":
        for r in w.get("regions") or []:
            if str(r.get("region")) == str(key):
                return str(r.get("province") or key)
        return str(key)
    cqi = (params or {}).get("target_cqi")
    if cqi is None:
        return None
    for h in w.get("hostiles") or []:
        if str(h.get("cqi")) == str(cqi):
            prov = h.get("province")
            return str(prov) if prov else None
    return None


class CampaignMemory:

    def __init__(self):
        self.acts = 0
        self.turn = 0
        self.prebattle = []
        self.recruit_counts = {}
        self.queues = {}
        self.queue_seen = {}
        self.queue_stall = {}
        self.moved = {}
        self.recent = []

    def begin_turn(self, turn):
        try:
            t = int(float(turn or 0))
        except (TypeError, ValueError):
            t = 0
        if t != self.turn:
            self.turn = t
            self.recruit_counts = {}

    def stamp(self, campaign):
        campaign["mem_acts"] = self.acts
        campaign["prebattle_mem"] = [dict(e) for e in self.prebattle]
        campaign["recruit_counts_turn"] = dict(self.recruit_counts)
        campaign["queue_ages"] = {cqi: [[k, max(0, self.turn - t0)] for k, t0 in q]
                                  for cqi, q in self.queues.items()}
        campaign["queue_stall"] = {cqi: list(f) for cqi, f in self.queue_stall.items()}
        campaign["last_move_turn"] = {cqi: v[0] for cqi, v in self.moved.items()}
        return campaign

    def observe_entity(self, ck, cid, state):
        if ck not in ("lord", "hero") or not state:
            return
        cqi = str(cid)
        x, y = state.get("x"), state.get("y")
        if x is not None and y is not None:
            prev = self.moved.get(cqi)
            if prev is None or prev[1] != x or prev[2] != y:
                self.moved[cqi] = [self.turn, x, y]
        pend = [str(k) for k in (state.get("pending_recruit_keys") or [])]
        pool = list(self.queues.get(cqi) or [])
        merged = []
        for k in pend:
            hit = next((i for i, e in enumerate(pool) if e[0] == k), None)
            merged.append([k, self.turn] if hit is None else pool.pop(hit))
        if merged:
            self.queues[cqi] = merged
        else:
            self.queues.pop(cqi, None)
        pq = state.get("pending_queue")
        if isinstance(pq, list):
            rows = [[str((q or {}).get("key")), (q or {}).get("turns_left")] for q in pq]
            prev = self.queue_seen.get(cqi)
            if rows and prev is not None and prev[0] < self.turn:
                spool = [list(e) for e in prev[1]]
                flags = []
                for k, t in rows:
                    hit = next((i for i, e in enumerate(spool) if e[0] == k), None)
                    if hit is None:
                        flags.append(0.0)
                        continue
                    pt = spool.pop(hit)[1]
                    flags.append(1.0 if (t is not None and pt is not None and t >= pt)
                                 else 0.0)
                self.queue_stall[cqi] = flags
            elif not rows:
                self.queue_stall.pop(cqi, None)
            self.queue_seen[cqi] = [self.turn, rows]

    def note_pick(self, ck, cid, atype, state, counted):
        if not atype or atype == "noop":
            return
        self.acts += 1
        self.observe_entity(ck, cid, state)
        if counted and atype in RECRUIT_TYPES:
            cqi = str(cid)
            self.recruit_counts[cqi] = self.recruit_counts.get(cqi, 0) + 1

    def note_prebattle(self, ck, cid, atype, key, params, world, choice,
                       result=None, casualties=None, zone=None):
        if atype not in PB_ATTACK_TYPES:
            return
        p = params or {}
        self.prebattle.append({
            "x": p.get("x"), "y": p.get("y"),
            "zone": zone if zone is not None else target_zone(atype, key, p, world),
            "choice": choice_name(choice),
            "result": str(result or "none"),
            "casualties": str(casualties or "none"),
            "cqi": str(cid), "act": self.acts, "turn": self.turn})
        del self.prebattle[:-MEM_LIMIT]

    def note_exec(self, pick, world, ts=None):
        self.recent.append((ts if ts is not None else time.time(), dict(pick),
                            world or {}))
        del self.recent[:-8]

    def feed_interrupts(self, recs):
        for r in recs or []:
            if r.get("kind") != "pre_battle" or not r.get("counted"):
                continue
            rts = r.get("ts") or 0.0
            hit = None
            for ts, pick, world in reversed(self.recent):
                if ts <= rts and rts - ts <= PB_WINDOW_S:
                    hit = (pick, world)
                    break
                if ts <= rts:
                    break
            if hit is None:
                continue
            pick, world = hit
            if pick.get("action_type") not in PB_ATTACK_TYPES:
                continue
            panel = r.get("panel") or {}
            self.note_prebattle(pick.get("context_kind"), pick.get("context_id"),
                                pick.get("action_type"), pick.get("key"),
                                pick.get("params") or {}, world, r.get("chosen"),
                                (panel.get("result") or {}).get("state"),
                                (panel.get("casualties") or {}).get("text"))


_EMPTY_PB_OPT = {
    "opt_last_prebattle_choice_at_loc": "none",
    "opt_actions_since_prebattle_at_loc": None,
    "opt_last_prebattle_result_at_loc": "none",
    "opt_last_prebattle_casualties_at_loc": "none",
    "opt_last_prebattle_choice_in_region": "none",
    "opt_actions_since_prebattle_in_region": None,
    "opt_last_prebattle_result_in_region": "none",
    "opt_last_prebattle_casualties_in_region": "none",
    "opt_last_prebattle_same_lord": None,
}


def prebattle_option_feats(campaign, atype, key, params, world, self_cqi):
    out = dict(_EMPTY_PB_OPT)
    if atype not in PB_ATTACK_TYPES:
        return out
    mem = (campaign or {}).get("prebattle_mem") or []
    if not mem:
        return out
    acts = (campaign or {}).get("mem_acts") or 0
    p = params or {}
    tx, ty = p.get("x"), p.get("y")
    zone = target_zone(atype, key, p, world or {})
    loc = reg = None
    for e in reversed(mem):
        if loc is None and tx is not None and e.get("x") == tx and e.get("y") == ty:
            loc = e
        if reg is None and zone is not None and e.get("zone") == zone:
            reg = e
        if loc is not None and reg is not None:
            break
    if loc is not None:
        out["opt_last_prebattle_choice_at_loc"] = loc["choice"]
        out["opt_actions_since_prebattle_at_loc"] = float(max(0, acts - loc["act"]))
        out["opt_last_prebattle_result_at_loc"] = loc["result"]
        out["opt_last_prebattle_casualties_at_loc"] = loc["casualties"]
    if reg is not None:
        out["opt_last_prebattle_choice_in_region"] = reg["choice"]
        out["opt_actions_since_prebattle_in_region"] = float(max(0, acts - reg["act"]))
        out["opt_last_prebattle_result_in_region"] = reg["result"]
        out["opt_last_prebattle_casualties_in_region"] = reg["casualties"]
    src = loc if loc is not None else reg
    if src is not None:
        out["opt_last_prebattle_same_lord"] = (
            1.0 if str(src.get("cqi")) == str(self_cqi or "") else 0.0)
    return out


def _log(msg):
    import sys
    sys.stderr.write("%.3f  memory %s\n" % (time.time(), msg))


def _enum_ids(con, domain, keys):
    return {k: i for k, i in con.execute(
        "SELECT key, enum_id FROM dict.enum WHERE domain = %s AND key = ANY(%s)",
        (domain, list(keys)))}


_PB_ATTRIB_SQL = (
    "SELECT t.decision_id, ty.key, a.action_key, i.chosen,"
    " ib.result_state, ib.casualties_text"
    " FROM corpus.interrupt i"
    " JOIN corpus.snapshot s ON s.snapshot_id = i.interrupt_id"
    " JOIN LATERAL (SELECT t2.decision_id, t2.action_id, t2.ts FROM corpus.taken t2"
    " WHERE t2.campaign_id = s.campaign_id AND t2.ts <= s.ts"
    " AND (t2.refusal_id IS NULL OR t2.refusal_id != ALL(%(skip)s))"
    " ORDER BY t2.ts DESC LIMIT 1) t ON TRUE"
    " JOIN dict.action a ON a.action_id = t.action_id"
    " JOIN dict.action_type ty ON ty.id = a.action_type_id"
    " LEFT JOIN corpus.interrupt_battle_panel ib ON ib.interrupt_id = i.interrupt_id"
    " WHERE i.kind_id = %(kind)s AND i.counted"
    " AND ty.key IN ('attack_army','attack_settlement')"
    " AND s.ts - t.ts <= %(win)s"
    " ORDER BY i.interrupt_id")


def _army_targets(con, pairs):
    if not pairs:
        return {}
    return {(sid, cqi): (x, y, prov) for sid, cqi, x, y, prov in con.execute(
        "SELECT w.snapshot_id, w.cqi, w.x, w.y, dp.key FROM corpus.world_hostile w"
        " LEFT JOIN dict.province dp ON dp.id = w.province_id"
        " JOIN unnest(%s::bigint[], %s::int[]) AS u(sid, cqi)"
        " ON u.sid = w.snapshot_id AND u.cqi = w.cqi",
        ([p[0] for p in pairs], [p[1] for p in pairs]))}


def _settlement_targets(con, pairs):
    if not pairs:
        return {}, {}
    sids = [p[0] for p in pairs]
    keys = [p[1] for p in pairs]
    coords = {}
    for sid, rkey, x, y in con.execute(
            "SELECT wh.snapshot_id, dr.key, wh.x, wh.y FROM corpus.world_hostile wh"
            " JOIN dict.enum k ON k.enum_id = wh.kind_id AND k.domain = 'hostile_kind'"
            " AND k.key = 'settlement'"
            " JOIN dict.region dr ON dr.id = wh.region_id"
            " JOIN unnest(%s::bigint[], %s::text[]) AS u(sid, rkey)"
            " ON u.sid = wh.snapshot_id AND u.rkey = dr.key", (sids, keys)):
        coords.setdefault((sid, rkey), (x, y))
    for table in ("settlement_set_member", "ruin_set_member"):
        col = "settlement_set_id" if table.startswith("settlement") else "ruin_set_id"
        for sid, rkey, x, y in con.execute(
                "SELECT sw.snapshot_id, dr.key, sm.x, sm.y FROM corpus.snapshot_world sw"
                " JOIN corpus.%s sm ON sm.set_id = sw.%s"
                " JOIN dict.region dr ON dr.id = sm.region_id"
                " JOIN unnest(%%s::bigint[], %%s::text[]) AS u(sid, rkey)"
                " ON u.sid = sw.snapshot_id AND u.rkey = dr.key" % (table, col),
                (sids, keys)):
            coords.setdefault((sid, rkey), (x, y))
    zones = {(sid, rkey): prov for sid, rkey, prov in con.execute(
        "SELECT sw.snapshot_id, dr.key, dp.key FROM corpus.snapshot_world sw"
        " JOIN corpus.region_set_member rm ON rm.set_id = sw.region_set_id"
        " JOIN dict.region dr ON dr.id = rm.region_id"
        " LEFT JOIN dict.province dp ON dp.id = rm.province_id"
        " JOIN unnest(%s::bigint[], %s::text[]) AS u(sid, rkey)"
        " ON u.sid = sw.snapshot_id AND u.rkey = dr.key", (sids, keys))}
    return coords, zones


def prebattle_attributions(con, camps=None):
    t0 = time.time()
    skip = list(_enum_ids(con, "refusal",
                          ["awaiting_execution", "campaign_died"]).values())
    kind = _enum_ids(con, "interrupt_kind", ["pre_battle"])["pre_battle"]
    sql = _PB_ATTRIB_SQL
    params = {"skip": skip, "kind": kind, "win": PB_WINDOW_S}
    if camps is not None:
        sql += " AND s.campaign_id = ANY(%(camps)s)"
        params["camps"] = sorted(camps)
    rows = con.execute(sql, params).fetchall()
    army_pairs, sett_pairs = [], []
    for did, at, akey, chosen, result, casualties in rows:
        if at == "attack_army" and str(akey).startswith("cqi:"):
            army_pairs.append((did, int(str(akey).split(":", 1)[1])))
        elif at == "attack_settlement":
            sett_pairs.append((did, str(akey)))
    army = _army_targets(con, army_pairs)
    coords, zones = _settlement_targets(con, sett_pairs)
    out = {}
    for did, at, akey, chosen, result, casualties in rows:
        p, zone = {}, None
        if at == "attack_army" and str(akey).startswith("cqi:"):
            cqi = int(str(akey).split(":", 1)[1])
            hit = army.get((did, cqi))
            p = {"target_cqi": cqi}
            if hit:
                p["x"], p["y"] = hit[0], hit[1]
                zone = hit[2]
        else:
            rkey = str(akey)
            hit = coords.get((did, rkey))
            if hit:
                p = {"x": hit[0], "y": hit[1]}
            zone = zones.get((did, rkey)) or rkey
        out[int(did)] = {
            "chosen": chosen, "action_type": at, "key": akey, "params": p,
            "zone": str(zone) if zone is not None else None,
            "result": result, "casualties": casualties}
    _log("prebattle_attributions exit %.1f ms n=%d"
         % ((time.time() - t0) * 1000, len(out)))
    return out


def replay_stamps(store, want):
    with store.snapshot_read():
        return _replay_stamps(store, want)


_REPLAY_SQL = (
    "SELECT t.decision_id, s.turn, s.campaign_id, ek.key, ch.cqi, dr.key, df.key,"
    " ty.key, t.counted, cs.pending_recruit_unit_ids, cs.x, cs.y,"
    " cs.pending_queue_set_id"
    " FROM corpus.taken t"
    " JOIN corpus.snapshot s ON s.snapshot_id = t.decision_id"
    " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
    " JOIN dict.faction df ON df.id = c.faction_id"
    " JOIN dict.action a ON a.action_id = t.action_id"
    " JOIN dict.action_type ty ON ty.id = a.action_type_id"
    " LEFT JOIN corpus.snapshot_entity se ON se.snapshot_id = t.decision_id"
    " AND se.entity_seq = t.entity_seq"
    " LEFT JOIN dict.enum ek ON ek.enum_id = se.kind_id"
    " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
    " LEFT JOIN dict.region dr ON dr.id = se.region_id"
    " LEFT JOIN corpus.char_state cs ON cs.snapshot_id = t.decision_id"
    " AND cs.entity_seq = t.entity_seq"
    " WHERE (t.refusal_id IS NULL OR t.refusal_id != ALL(%s))"
    " AND s.campaign_id = ANY(%s) ORDER BY t.decision_id")


def _queue_members(con, set_ids):
    want = sorted({s for s in set_ids if s is not None})
    if not want:
        return {}
    out = {}
    for sid, key, left in con.execute(
            "SELECT m.set_id, du.key, m.turns_left"
            " FROM corpus.pending_queue_set_member m"
            " JOIN dict.unit du ON du.id = m.unit_id"
            " WHERE m.set_id = ANY(%s) ORDER BY m.set_id, m.ord", (want,)):
        out.setdefault(sid, []).append({"key": key, "turns_left": left})
    return out


def _replay_stamps(store, want):
    t0 = time.time()
    want = {int(d) for d in (want or ())}
    if not want:
        return {}
    con = store.con
    camps = sorted(r[0] for r in con.execute(
        "SELECT DISTINCT campaign_id FROM corpus.snapshot WHERE snapshot_id = ANY(%s)",
        (sorted(want),)))
    pb = prebattle_attributions(con, camps)
    skip = list(_enum_ids(con, "refusal",
                          ["awaiting_execution", "campaign_died"]).values())
    rows = con.execute(_REPLAY_SQL, (skip, camps)).fetchall()
    queues = _queue_members(con, [r[12] for r in rows])
    unit_ids = sorted({u for r in rows for u in (r[9] or [])})
    unit_keys = {i: k for i, k in con.execute(
        "SELECT id, key FROM dict.unit WHERE id = ANY(%s)", (unit_ids,))} \
        if unit_ids else {}
    mems, out = {}, {}
    for (did, turn, camp, kind, cqi, region, faction, at, counted, pend, sx, sy,
         qset) in rows:
        mem = mems.get(camp)
        if mem is None:
            mem = mems[camp] = CampaignMemory()
        mem.begin_turn(turn)
        if did in want:
            out[did] = mem.stamp({})
        state = None
        if kind in ("lord", "hero"):
            state = {"pending_recruit_keys": [unit_keys.get(u) for u in (pend or [])],
                     "x": float(sx) if sx is not None else None,
                     "y": float(sy) if sy is not None else None,
                     "pending_queue": queues.get(qset) if qset is not None else None}
        if kind in ("lord", "hero"):
            cid = str(cqi)
        elif kind == "province":
            cid = region
        else:
            kind, cid = "campaign", faction
        mem.note_pick(kind, cid, at, state, bool(counted))
        hit = pb.get(did)
        if hit is not None:
            mem.note_prebattle(kind, cid, hit["action_type"], hit["key"],
                               hit["params"], None, hit["chosen"],
                               hit["result"], hit["casualties"], zone=hit["zone"])
    _log("replay_stamps exit %.1f ms decisions=%d stamped=%d"
         % ((time.time() - t0) * 1000, len(rows), len(out)))
    return out
