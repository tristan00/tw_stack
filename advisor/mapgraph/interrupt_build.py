from __future__ import annotations


import os
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B
from advisor.mapgraph import guard as G

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

CONTEXT_KIND = "interrupt"


def context_record(record):
    ents = []
    for e in record.get("entities") or []:
        e2 = dict(e)
        e2["offers"] = []
        ents.append(e2)
    return {"campaign": record.get("campaign") or {}, "world": record.get("world") or {},
            "entities": ents}


def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _panel_numbers(panel):
    p = panel or {}
    ranks = [_num(x) for x in (p.get("strength_ranks") or [])]
    return {"attitude": _num(p.get("attitude")),
            "amount_demanded": _num(p.get("amount_demanded")),
            "amount_offered": _num(p.get("amount_offered")),
            "strength_them": ranks[0] if ranks else 0.0,
            "strength_us": ranks[1] if len(ranks) > 1 else 0.0,
            "settlements": _num(p.get("settlements"))}


def _state_of(v):
    if isinstance(v, dict):
        return str(v.get("state") or v.get("text") or "").strip()
    return str(v or "").strip()


def panel_facts(screen, panel, options=None, meta=None):
    p = panel or {}
    out = []

    def add(field, value):
        v = str(value or "").strip()
        if v and v.lower() != "none":
            out.append("%s.%s=%s" % (screen, field, v))

    if screen == "pre_battle":
        add("result", _state_of(p.get("result")))
        add("casualties", _state_of(p.get("casualties")))
    elif screen == "battle_results":
        add("outcome", p.get("outcome"))
        add("result_flag", p.get("result_flag"))
        add("settlement_captured", p.get("settlement_captured"))
    if p.get("attitude_label"):
        add("attitude_label", p.get("attitude_label"))
    if p.get("race"):
        add("race", p.get("race"))
    rel = p.get("reliability") or []
    if rel:
        add("reliability", rel[0])
    for o in options or ():
        s = str(o)
        if s.startswith("button_captive_option_"):
            add("captive_outcome", s[len("button_captive_option_"):])
    return sorted(set(out))


def _option_key(screen, opt, meta):
    m = (meta or {}).get(opt) or {}
    did = str(m.get("dilemma_id") or "")
    oid = str(m.get("option_id") or opt)
    return "%s|%s|%s" % (screen, did, oid)


def screen_offers(screen, options, meta=None):
    atype = S.screen_action_type(screen)
    return [{"action_type": atype, "key": str(o), "params": {}} for o in options]


def build_screen_graph(record, screen, options, meta=None, panel=None):
    opts = [str(o) for o in options]
    if not opts:
        return None
    atype = S.screen_action_type(screen)
    rec = context_record(record)
    rec["entities"].append({"context_kind": CONTEXT_KIND, "context_id": screen,
                            "state": {}, "offers": screen_offers(screen, opts, meta)})
    g = B.build_graph(rec)
    if g is None:
        return None
    if len(g.action_nodes) != len(opts):
        raise ValueError(
            "mapgraph.interrupt_build: %s built %d action nodes for %d options -- the "
            "borrowed record still had offers on it, or an option was dropped"
            % (screen, len(g.action_nodes), len(opts)))

    me = g.player_faction or ""
    si = g.add("scr:%s" % screen, "screen",
               _screen_values(screen, panel), atype=S.atype_index(atype),
               cat=S.cat_global("screen_fact", "screen=%s" % screen))
    g.edge(si, g.id2idx.get("f:" + me), "act_actor")

    for i, opt in zip(g.action_nodes, opts):
        g.edge(i, si, "on_screen")
        g.edge(i, g.cat_node("screen_option", _option_key(screen, opt, meta)), "act_on")
        did = str(((meta or {}).get(opt) or {}).get("dilemma_id") or "")
        if did:
            g.edge(i, g.cat_node("dilemma", did), "of_dilemma")

    for fact in panel_facts(screen, panel, opts, meta):
        g.edge(si, g.cat_node("screen_fact", fact), "screen_fact")
    p = panel or {}
    for side, rel in (("demands", "demanded"), ("offers", "offered"),
                      ("treaties", "in_treaty")):
        for term in p.get(side) or ():
            g.edge(si, g.cat_node("treaty_term", str(term)), rel)

    g.finalize()
    g.counts = dict(g.counts or {}, nodes=len(g.x), edges=len(g.src),
                    actions=len(g.action_nodes), screen=screen,
                    facts=len(panel_facts(screen, panel, opts, meta)))
    return g


def _screen_values(screen, panel):
    nums = _panel_numbers(panel)
    if not any(nums.values()):
        return {}
    rd = G.Reader(nums, "screen:%s" % screen, "panel")
    return {
        "attitude": (rd.num("attitude") / S.ATTITUDE_SCALE).clip(-1.0, 1.0),
        "amount_demanded": (rd.num("amount_demanded") / S.DEAL_GOLD_SCALE).clip(0.0, 2.0),
        "amount_offered": (rd.num("amount_offered") / S.DEAL_GOLD_SCALE).clip(0.0, 2.0),
        "strength_them": (rd.num("strength_them") / S.STRENGTH_RANK_SCALE).clip(0.0, 2.0),
        "strength_us": (rd.num("strength_us") / S.STRENGTH_RANK_SCALE).clip(0.0, 2.0),
        "settlements": (rd.num("settlements") / S.SCREEN_SETTLEMENTS_SCALE).clip(0.0, 5.0),
    }


def taken_mask(g, options, chosen):
    opts = [str(o) for o in options]
    if str(chosen) not in opts:
        return None
    return [1.0 if o == str(chosen) else 0.0 for o in opts]


def _smoke(limit=5):
    import json
    sys.path.insert(0, common.DECISIONS)
    from store import DecisionStore
    from advisor.mapgraph import interrupt_train as IT

    s = DecisionStore(common.RUN_DIR, readonly=True)
    try:
        rows = s.interrupt_rows()
        ctx = IT.context_for(s, rows)
    finally:
        s.close()
    shown = 0
    for r in reversed(rows):
        c = ctx.get(r["interrupt_id"])
        if c is None or len(r.get("options") or {}) < 2:
            continue
        opts = sorted(r["options"])
        g = build_screen_graph(c["record"], r["screen"], opts,
                               meta=r.get("options"), panel=r.get("panel"))
        if g is None:
            print("%-20s -> no graph" % r["screen"])
            continue
        print("%-20s %s  age=%.0fs chained=%d"
              % (r["screen"], json.dumps(g.counts), c["age_s"], c["interrupts_since"]))
        shown += 1
        if shown >= limit:
            break


if __name__ == "__main__":
    common.require_venv()
    _smoke(int(sys.argv[2]) if len(sys.argv) > 2 else 5)
