from __future__ import annotations

"""Blocking screen -> graph.

A dilemma, a pre-battle, an occupation choice and a diplomatic proposal are all the same
shape as a map decision: a world, a candidate set, and one candidate that was taken. So
they get the same graph, built by the same `build.build_graph`, and the options become
action nodes like any other. What is new is only the screen they sit on.

TWO THINGS THIS GETS RIGHT ON PURPOSE, because both were measured and both are silent:

1. The world comes from the last real DECISION snapshot, never from the interrupt's own
   `world_blob`. `decisions_stream` collects an interrupt with `collect.world_state`,
   which on a popped panel returns no `relations`, no `citizenry` and no `war_graph` --
   0 of 347 archived rows carry any of the three, against 100%/89.5%/100% on decision
   snapshots. That is not a thinner graph, it is a WRONG one: with no citizenry every own
   settlement's garrison_units is 0 (the only defensive scalar there is) and our own
   garrison stacks stop being skipped in build.py's character loop, so they are built as
   field armies. The live path already hands us the decision snapshot's world
   (loop.py set_snapshot), so training on anything else would also be train/serve skew.

2. `offers` are stripped from the borrowed record. By the time the advisor has a record
   in hand, options.attach has already hung ~376 map actions off its entities. Leaving
   them in would put them in the candidate set and the listwise softmax would be over the
   wrong universe -- the screen's 3 options plus every move on the map.
"""

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
    """The borrowed decision snapshot, with every offer removed. See note 2 above."""
    ents = []
    for e in record.get("entities") or []:
        e2 = dict(e)
        e2["offers"] = []
        ents.append(e2)
    return {"campaign": record.get("campaign") or {}, "world": record.get("world") or {},
            "entities": ents}


def _num(v, default=0.0):
    """Panel numbers arrive as UI text: '1,250', 'Strength Rank: 12' is already split."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _panel_numbers(panel):
    """The six numeric facts a panel can carry, flat, for one guard.Reader.

    All six come off ONE entity -- the open panel -- so they may be read together and the
    model may relate them. guard.Raw is what forbids combining two ENTITIES; a panel is
    one entity, and its own two strength ranks are two facts about it.
    """
    p = panel or {}
    ranks = [_num(x) for x in (p.get("strength_ranks") or [])]
    return {"attitude": _num(p.get("attitude")),
            "amount_demanded": _num(p.get("amount_demanded")),
            "amount_offered": _num(p.get("amount_offered")),
            "strength_them": ranks[0] if ranks else 0.0,
            "strength_us": ranks[1] if len(ranks) > 1 else 0.0,
            "settlements": _num(p.get("settlements"))}


def _state_of(v):
    """A forecast cell is {'text': ..., 'state': ...}; the state is the closed vocabulary."""
    if isinstance(v, dict):
        return str(v.get("state") or v.get("text") or "").strip()
    return str(v or "").strip()


def panel_facts(screen, panel, options=None, meta=None):
    """String-valued panel facts, as `<screen>.<field>=<value>` catalogue keys.

    Numbers are scalars on the screen node; these are the things that are NAMES -- a
    forecast of 'decisive victory', an attitude of 'hostile'. Identity, not magnitude.
    """
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
    # A captive's fate is named by the control itself: button_captive_option_enslave.
    # reference.captive_options would additionally give the culture-specific RECORD behind
    # that button (via captive_binding); that join is not done here -- the outcome family
    # is what the choice is, and it is already in the id.
    for o in options or ():
        s = str(o)
        if s.startswith("button_captive_option_"):
            add("captive_outcome", s[len("button_captive_option_"):])
    return sorted(set(out))


def _option_key(screen, opt, meta):
    """Stable identity for one option: the screen, the dilemma it belongs to, the choice.

    A dilemma option's id already carries its dilemma
    ('...Recordwh3_main_dilemma_cth_6FOURTH'), but an `occupation` option is the UI text
    'raze' and a pre-battle option is 'button_autoresolve' -- neither means anything
    without the screen, and 'button_accept' means three different things on three panels.
    """
    m = (meta or {}).get(opt) or {}
    did = str(m.get("dilemma_id") or "")
    oid = str(m.get("option_id") or opt)
    return "%s|%s|%s" % (screen, did, oid)


def screen_offers(screen, options, meta=None):
    """The screen's options as offers, in the order they will be scored."""
    atype = S.screen_action_type(screen)          # raises on a screen we do not know
    return [{"action_type": atype, "key": str(o), "params": {}} for o in options]


def build_screen_graph(record, screen, options, meta=None, panel=None):
    """One interrupt decision -> a graph whose action nodes are exactly its options.

    `record` is the borrowed decision snapshot (see module docstring). `options` must
    already be in the order the caller will read scores back in.
    """
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
    # Only the synthetic entity carries offers, so action nodes are the options in order.
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
    """The screen node's six scalars, scaled and clipped, all from the one panel."""
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
    """1.0 on the option that was taken, in action-node order. None if it is not there."""
    opts = [str(o) for o in options]
    if str(chosen) not in opts:
        return None
    return [1.0 if o == str(chosen) else 0.0 for o in opts]


def _smoke(limit=5):
    """Build a graph for the most recent interrupts in the corpus and print the shape."""
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
