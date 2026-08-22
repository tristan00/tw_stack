from __future__ import annotations


import json
import os
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

THREADS_INFER = 2
MODEL_DIR = common.MODEL_MAPGRAPH_GREEDY


def offer_key(r):
    return (r.get("context_kind"), str(r.get("context_id")),
            r.get("action_type"), str(r.get("key")))


def _load(model_dir, tag):
    try:
        import torch
        torch.set_num_threads(THREADS_INFER)
        from advisor.mapgraph import greedy_net as GN
        return GN.load(model_dir, tag)
    except Exception as e:
        sys.stderr.write("%s: load failed -> %s -- unready; the model gate refuses "
                         "trainable arms without a usable model\n" % (tag, repr(e)[:160]))
        return None, None


class Ranker:

    def __init__(self, model_dir=MODEL_DIR):
        self.model_dir = model_dir
        self.last_reward = None
        self.misses = 0
        self._warned = False
        self.net, self.meta = _load(model_dir, "mapgraph.greedy_rank")
        self.ready = self.net is not None

    def score_elig(self, offers, record, graph=None):
        if not self.ready:
            raise RuntimeError("mapgraph.greedy_rank: score_elig on an unready Ranker")
        import torch
        from advisor.mapgraph import net as N
        from advisor.mapgraph import greedy_net as GN
        g = graph if graph is not None else B.build_graph(record)
        if g is None:
            raise ValueError("mapgraph.greedy_rank: record produced no graph")
        if not g.action_nodes:
            raise ValueError("mapgraph.greedy_rank: record produced no action nodes")
        data = N.to_data(g)
        with torch.no_grad():
            q = self.net(data)["q"].tolist()
        reward = GN.reward_of(self.meta, q)
        by_key = dict(zip(g.action_keys, reward))
        floor = min(reward) - 1.0
        miss = 0
        out = []
        for r in offers:
            v = by_key.get(offer_key(r))
            if v is None:
                miss += 1
                v = floor
            out.append(v)
        if miss:
            self.misses += miss
            if not self._warned:
                self._warned = True
                sys.stderr.write(
                    "mapgraph.greedy_rank: %d/%d offers had no action node this decision "
                    "(scored at floor). The builder is not covering every offer type.\n"
                    % (miss, len(offers)))
        return out

    def pick(self, elig, record, graph=None):
        self.last_reward = None
        if not elig:
            return None
        reward = self.score_elig(elig, record, graph=graph)
        i = max(range(len(elig)), key=lambda j: reward[j])
        self.last_reward = reward[i]
        return elig[i]


def _smoke(run_dir=common.RUN_DIR):
    sys.path.insert(0, common.DECISIONS)
    from store import DecisionStore
    r = Ranker()
    print("greedy ranker ready:", r.ready, "dir:", r.model_dir)
    if r.meta:
        f = r.meta.get("fit") or {}
        print("  rows %s  val_mse %s  val_r2 %s" % (r.meta.get("rows"), f.get("val_mse"),
                                                    f.get("val_r2")))
    s = DecisionStore(run_dir, readonly=True)
    try:
        with s.snapshot_read():
            recs = s.labelled_decisions()
    finally:
        s.close()
    if not recs:
        raise SystemExit("smoke: no labelled decisions in %s" % run_dir)
    for rec, taken, _ in reversed(recs):
        offers = [dict(o, context_kind=e["context_kind"], context_id=e["context_id"])
                  for e in rec.get("entities") or [] for o in e.get("offers") or []]
        if not offers:
            continue
        g = B.build_graph(rec)
        if g is None:
            continue
        print("graph: %s" % json.dumps(g.counts))
        if not r.ready:
            print("smoke: builder OK; greedy model not trained yet -- nothing to score")
            return
        sc = r.score_elig(offers, rec, graph=g)
        pairs = sorted(zip(sc, range(len(offers))), reverse=True)
        print("scored %d offers, misses=%d" % (len(sc), r.misses))
        for v, i in pairs[:8]:
            o = offers[i]
            print("  %+.4f  %-18s %-20s %s"
                  % (v, o["context_kind"], o["action_type"], str(o["key"])[:44]))
        return
    raise SystemExit("smoke: no buildable decision found")


if __name__ == "__main__":
    common.require_venv()
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        _smoke(sys.argv[2] if len(sys.argv) > 2 else common.RUN_DIR)
    else:
        raise SystemExit("usage: greedy_rank.py smoke [run_dir]")
