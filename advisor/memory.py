from __future__ import annotations

import json
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


_PB_ATTRIB_SQL = (
    "SELECT t.decision_id, i.chosen, bp.z, a.action_type, a.action_key, a.params,"
    " CASE WHEN a.action_type='attack_settlement' THEN"
    " COALESCE((SELECT r->>'province'"
    " FROM jsonb_array_elements(bw.z::jsonb->'regions') r"
    " WHERE r->>'region'=a.action_key LIMIT 1), a.action_key)"
    " ELSE"
    " (SELECT h->>'province'"
    " FROM jsonb_array_elements(bw.z::jsonb->'hostiles') h"
    " WHERE h->>'cqi'=(a.params::jsonb->>'target_cqi') LIMIT 1)"
    " END"
    " FROM interrupts i"
    " LEFT JOIN blobs bp ON bp.blob_id=i.panel_blob"
    " JOIN LATERAL (SELECT t2.decision_id, t2.action_id, t2.ts FROM taken t2"
    " WHERE t2.campaign_id=i.campaign_id AND t2.ts<=i.ts"
    " AND (t2.refusal IS NULL OR"
    " t2.refusal NOT IN ('awaiting_execution','campaign_died'))"
    " ORDER BY t2.ts DESC LIMIT 1) t ON TRUE"
    " JOIN actions a ON a.action_id=t.action_id"
    " JOIN decisions d2 ON d2.decision_id=t.decision_id"
    " LEFT JOIN blobs bw ON bw.blob_id=d2.world_blob"
    " WHERE i.kind='pre_battle' AND i.counted=1"
    " AND a.action_type IN ('attack_army','attack_settlement')"
    " AND i.ts - t.ts <= %s")


def prebattle_attributions(con, camps=None):
    out = {}
    sql, args = _PB_ATTRIB_SQL, [PB_WINDOW_S]
    if camps is not None:
        sql += " AND i.campaign_id = ANY(%s)"
        args.append(sorted(camps))
    for did, chosen, pz, at, akey, params, zone in con.execute(sql, tuple(args)):
        try:
            panel = json.loads(pz or "{}")
        except ValueError:
            panel = {}
        try:
            p = json.loads(params or "{}")
        except ValueError:
            p = {}
        out[int(did)] = {
            "chosen": chosen, "action_type": at, "key": akey, "params": p,
            "zone": str(zone) if zone is not None else None,
            "result": (panel.get("result") or {}).get("state"),
            "casualties": (panel.get("casualties") or {}).get("text")}
    return out


def replay_stamps(store, want):
    with store.snapshot_read():
        return _replay_stamps(store, want)


def _replay_stamps(store, want):
    want = {int(d) for d in (want or ())}
    if not want:
        return {}
    camps = sorted(r[0] for r in store.con.execute(
        "SELECT DISTINCT campaign_id FROM decisions WHERE decision_id = ANY(%s)",
        (sorted(want),)))
    pb = prebattle_attributions(store.con, camps)
    mems = {}
    out = {}
    cur = store.con.cursor(name="memory_replay_stream")
    cur.itersize = 500
    try:
        cur.execute(
            "SELECT t.decision_id, d.turn, d.campaign_id,"
            " a.context_kind, a.context_id, a.action_type, t.counted,"
            " (be.z::jsonb)->'pending_recruit_keys',"
            " (be.z::jsonb)->>'x', (be.z::jsonb)->>'y',"
            " (be.z::jsonb)->'pending_queue'"
            " FROM taken t"
            " JOIN decisions d ON d.decision_id=t.decision_id"
            " LEFT JOIN actions a ON a.action_id=t.action_id"
            " LEFT JOIN entities e ON e.decision_id=t.decision_id"
            " AND e.entity_seq=t.entity_seq"
            " LEFT JOIN blobs be ON be.blob_id=e.features_blob"
            " WHERE (t.refusal IS NULL OR"
            " t.refusal NOT IN ('awaiting_execution','campaign_died'))"
            " AND d.campaign_id = ANY(%s)"
            " ORDER BY t.decision_id", (camps,))
        for did, turn, camp, ck, cid, at, counted, pend, sx, sy, pq in cur:
            mem = mems.get(camp)
            if mem is None:
                mem = mems[camp] = CampaignMemory()
            mem.begin_turn(turn)
            if did in want:
                out[did] = mem.stamp({})
            state = None
            if ck in ("lord", "hero"):
                state = {"pending_recruit_keys": pend or [],
                         "x": float(sx) if sx is not None else None,
                         "y": float(sy) if sy is not None else None,
                         "pending_queue": pq if isinstance(pq, list) else None}
            mem.note_pick(ck, cid, at, state, bool(counted))
            hit = pb.get(did)
            if hit is not None:
                mem.note_prebattle(ck, cid, hit["action_type"], hit["key"],
                                   hit["params"], None, hit["chosen"],
                                   hit["result"], hit["casualties"],
                                   zone=hit["zone"])
    finally:
        cur.close()
    return out
