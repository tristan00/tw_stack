from __future__ import annotations

"""Inference: one forward pass per decision, scores for every offer.

Drop-in for `mapgraph.rank.Ranker` -- same three entry points the policy uses
(`ready`, `score_elig(offers, record)`, `pick(elig, record)` + `last_impact`).

One forward pass builds the whole decision graph, action nodes included, and every
offer's score is read off its own node. Offers are matched back to nodes by the same
`(context_kind, context_id, action_type, key)` tuple `strategies.offer_key` uses.

Reported number is `q` centred within the decision, `q - mean(q)`.

Not `q - v`, which is what the predecessor reported and what I wrote first: `q` is an unnormalised
softmax logit whose absolute shift is arbitrary (softmax is shift-invariant), and `v` is
a z-scored outcome prediction. Subtracting one from the other is apples-oranges -- it
produced numbers like -12.4 that mean nothing. Centring removes the arbitrary shift and
gives "how much this candidate is preferred over the average candidate in its own choice
set", which is what a per-offer number should say. Ordering is identical either way.
"""

import json
import os
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import build as B

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

THREADS_INFER = 2


def offer_key(r):
    return (r.get("context_kind"), str(r.get("context_id")),
            r.get("action_type"), str(r.get("key")))


class Ranker:

    def __init__(self, model_dir=S.MODEL_DIR):
        self.model_dir = model_dir
        self.ready = False
        self.meta = None
        self.net = None
        self.last_impact = None
        self.last_value = None
        self.misses = 0
        self._warned = False
        meta_path = os.path.join(model_dir, "meta.json")
        if not os.path.exists(meta_path):
            return
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
            if meta.get("schema_hash") != S.schema_hash():
                sys.stderr.write(
                    "mapgraph.rank: meta schema hash %s != code %s -- trained on a "
                    "different graph; unready until retrain\n"
                    % (str(meta.get("schema_hash"))[:12], S.schema_hash()[:12]))
                self.meta = meta
                return
            import torch
            torch.set_num_threads(THREADS_INFER)
            try:
                from advisor.mapgraph import net as N
            except ImportError:
                import net as N
            cfg = meta.get("cfg") or {}
            net = N.Net(cfg.get("hidden", N.HIDDEN),
                        cfg.get("entity_layers", N.ENTITY_LAYERS),
                        cfg.get("action_rounds", N.ACTION_ROUNDS))
            net.encoder.load_state_dict(
                torch.load(os.path.join(model_dir, "encoder.pt"), map_location="cpu"))
            net.head.load_state_dict(
                torch.load(os.path.join(model_dir, "head.pt"), map_location="cpu"))
            net.eval()
            self.net, self.meta, self.ready = net, meta, True
        except Exception as e:
            sys.stderr.write("mapgraph.rank: load failed -> %s -- unready, gnn draws "
                             "fall back to random\n" % repr(e)[:160])

    def score_elig(self, offers, record):
        if not self.ready:
            raise RuntimeError("mapgraph.rank: score_elig on an unready Ranker")
        import torch
        try:
            from advisor.mapgraph import net as N
        except ImportError:
            import net as N
        g = B.build_graph(record)
        if g is None:
            raise ValueError("mapgraph.rank: record produced no graph")
        if not g.action_nodes:
            raise ValueError("mapgraph.rank: record produced no action nodes")
        data = N.to_data(g)
        with torch.no_grad():
            out = self.net(data)
            q = out["q"].tolist()
            self.last_value = float(out["v"][0])
        # centre within the decision: the softmax logit's absolute shift is arbitrary
        v = sum(q) / float(len(q))
        by_key = {}
        for k, s in zip(g.action_keys, q):
            by_key[k] = s
        floor = min(q) - 1.0
        miss = 0
        scores = []
        for r in offers:
            s = by_key.get(offer_key(r))
            if s is None:
                miss += 1
                s = floor
            scores.append(s - v)
        if miss:
            self.misses += miss
            if not self._warned:
                self._warned = True
                sys.stderr.write(
                    "mapgraph.rank: %d/%d offers had no action node this decision "
                    "(scored at floor). The builder is not covering every offer type.\n"
                    % (miss, len(offers)))
        return scores

    def pick(self, elig, record):
        self.last_impact = None
        if not elig:
            return None
        impact = self.score_elig(elig, record)
        self.last_impact = list(impact)
        return elig[max(range(len(elig)), key=lambda i: impact[i])]


def _smoke(run_dir=common.RUN_DIR):
    sys.path.insert(0, common.DECISIONS)
    from store import DecisionStore
    r = Ranker()
    print("ranker ready:", r.ready, "dir:", r.model_dir)
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
            print("smoke: builder OK; model not trained yet -- nothing to score")
            return
        sc = r.score_elig(offers, rec)
        pairs = sorted(zip(sc, range(len(offers))), reverse=True)
        print("scored %d offers, misses=%d" % (len(sc), r.misses))
        for v, i in pairs[:8]:
            o = offers[i]
            print("  %+.4f  %-18s %-20s %s"
                  % (v, o["context_kind"], o["action_type"], str(o["key"])[:44]))
        return
    raise SystemExit("smoke: no buildable decision found")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        _smoke(sys.argv[2] if len(sys.argv) > 2 else common.RUN_DIR)
    else:
        raise SystemExit("usage: rank.py smoke [run_dir]")
