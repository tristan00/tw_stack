from __future__ import annotations

import json
import os
import sys

try:
    from mapgraph import schema as S
    from mapgraph import build as B
except ImportError:
    import schema as S
    import build as B

THREADS_INFER = 2


class Ranker:

    def __init__(self, model_dir=S.MODEL_DIR):
        self.model_dir = model_dir
        self.ready = False
        self.meta = None
        self.net = None
        self.last_impact = None
        meta_path = os.path.join(model_dir, "meta.json")
        if not os.path.exists(meta_path):
            return
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
            if meta.get("schema_hash") != S.schema_hash():
                sys.stderr.write("mapgraph.rank: meta schema hash %s != code %s -- model was "
                                 "trained on a different graph schema; unready until retrain\n"
                                 % (str(meta.get("schema_hash"))[:12], S.schema_hash()[:12]))
                self.meta = meta
                return
            import torch
            torch.set_num_threads(THREADS_INFER)
            try:
                from mapgraph import net as N
            except ImportError:
                import net as N
            net = N.Net(hidden=int(meta.get("cfg", {}).get("hidden", N.HIDDEN)))
            net.encoder.load_state_dict(torch.load(os.path.join(model_dir, "encoder.pt"),
                                                   map_location="cpu"))
            net.head.load_state_dict(torch.load(os.path.join(model_dir, "head.pt"),
                                                map_location="cpu"))
            net.eval()
            self.net = net
            self.meta = meta
            self.ready = True
        except Exception as e:
            sys.stderr.write("mapgraph.rank: load failed -> %s -- unready, gnn draws fall back "
                             "to random\n" % repr(e)[:160])

    def score_elig(self, elig, record):
        if not self.ready:
            raise RuntimeError("mapgraph.rank: score_elig called on an unready Ranker")
        import torch
        try:
            from mapgraph import net as N
        except ImportError:
            import net as N
        g = B.build_graph(record)
        if g is None:
            raise ValueError("mapgraph.rank: record has no known regions -- no graph to score")
        offers = [B.bind_offer(g, r.get("context_kind"), r.get("context_id"),
                               r.get("action_type"), r.get("key"), r.get("params"),
                               r.get("available", True))
                  for r in elig]
        data = N.to_data(g, offers)
        with torch.no_grad():
            out = self.net(data)
            impact = (out["q"] - out["v"]).tolist()
        return impact

    def pick(self, elig, record):
        self.last_impact = None
        if not elig:
            return None
        impact = self.score_elig(elig, record)
        # keep the per-offer Q-V so the pick can be explained after the fact; without this
        # the gnn's own numbers die here and the ui can only ever show catboost's opinion
        self.last_impact = list(impact)
        best = max(range(len(elig)), key=lambda i: impact[i])
        return elig[best]


def _smoke(run_dir):
    sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor"))
    sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))
    import backends as BK
    import model as M
    import policy as P
    from store import DecisionStore
    s = DecisionStore(run_dir, readonly=True)
    try:
        with s.snapshot_read():
            recs = s.labelled_decisions()
    finally:
        s.close()
    if not recs:
        raise SystemExit("smoke: no labelled decisions in %s" % run_dir)
    rec, taken, _ = recs[-1]
    g = B.build_graph(rec)
    print("build_graph: %s" % (g.counts if g is not None else None))
    pol = P.Policy(M.Ranker(BK.NO_MODEL_DIR), seed=0, strategies={"gnn": 1.0})
    pick, ranked = pol.choose(rec)
    assert pick is not None, "smoke: no pick"
    mode = pol.last_choice.get("mode")
    print("pick: %s %s %s mode=%s" % (pick.get("context_kind"), pick.get("action_type"),
                                      str(pick.get("key"))[:40], mode))
    gnn_ready = Ranker().ready
    want = "gnn" if gnn_ready else "gnn_random_fallback"
    assert mode == want, "smoke: mode %r, wanted %r (ranker ready=%s)" % (mode, want, gnn_ready)
    print("smoke OK (ranker ready=%s)" % gnn_ready)


def _score_one():
    sys.path.insert(0, os.path.join(r"D:\tw_stack", "advisor"))
    sys.path.insert(0, os.path.join(r"D:\tw_stack", "decisions"))
    import glob
    from store import DecisionStore
    r = Ranker()
    if not r.ready:
        raise SystemExit("score-one: ranker not ready (dir=%s)" % r.model_dir)
    for db in sorted(glob.glob(r"D:/twdata/runs/human/*/decisions.sqlite"), reverse=True):
        s = DecisionStore(os.path.dirname(db), readonly=True)
        try:
            with s.snapshot_read():
                recs = s.labelled_decisions()
        finally:
            s.close()
        for rec, taken, _ in reversed(recs):
            if not (rec.get("world") or {}).get("regions"):
                continue
            elig = [dict(o, context_kind=e["context_kind"], context_id=e["context_id"])
                    for e in rec.get("entities") or [] for o in e.get("offers") or []
                    if o.get("available")]
            if not elig:
                continue
            impacts = r.score_elig(elig, rec)
            pairs = sorted(zip(impacts, range(len(elig))), reverse=True)
            print("decision %s turn %s: %d offers scored" % (rec.get("decision_id"),
                                                             rec.get("turn"), len(elig)))
            for imp, i in pairs[:5]:
                o = elig[i]
                print("  %+.4f %s %s %s" % (imp, o["context_kind"], o["action_type"],
                                            str(o["key"])[:48]))
            return
    raise SystemExit("score-one: no region-bearing decision found")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        _smoke(sys.argv[2] if len(sys.argv) > 2 else r"D:/twdata/runs/human/run")
    elif len(sys.argv) > 1 and sys.argv[1] == "score-one":
        _score_one()
    else:
        raise SystemExit("usage: rank.py smoke [run_dir] | score-one")
