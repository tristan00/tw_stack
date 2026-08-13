from __future__ import annotations

"""Inference for blocking screens: one forward pass per screen, a score per option.

The interrupt-path twin of mapgraph.rank. Same contract (`ready`, `score`), same reported
number -- `q` centred within the screen, `q - mean(q)`, because a softmax logit's absolute
shift is arbitrary and `q - v` would be subtracting a z-scored outcome from an
unnormalised logit (see rank.py for the full account).

Its weights are its own: D:\\twdata\\models\\mapgraph_interrupt. Nothing here reads the
action model.

READINESS IS DELIBERATELY NOT AN ACCURACY GATE. A trained model loads even when it scores
no better than guessing, and at 256 screens it very nearly does -- meta carries
`uniform_nll` next to the fit so the models card can say so. Refusing to load it would
mean the 10% arm never plays, never records a row, and the corpus never shows whether it
is improving, which is the entire reason it is in the mix this early.
"""

import json
import os
import sys

from advisor.mapgraph import schema as S
from advisor.mapgraph import interrupt_build as IB

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import common

THREADS_INFER = 2
MODEL_DIR = common.MODEL_MAPGRAPH_INTERRUPT


class Ranker:

    def __init__(self, model_dir=MODEL_DIR):
        self.model_dir = model_dir
        self.ready = False
        self.meta = None
        self.net = None
        self.errors = 0
        self.last_value = None
        self._warned = False
        meta_path = os.path.join(model_dir, "meta.json")
        if not os.path.exists(meta_path):
            return
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
            if meta.get("schema_hash") != S.schema_hash():
                sys.stderr.write(
                    "mapgraph.interrupt_rank: meta schema hash %s != code %s -- trained on "
                    "a different graph; unready until retrain\n"
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
            sys.stderr.write("mapgraph.interrupt_rank: load failed -> %s -- unready, gnn "
                             "draws on interrupts fall back to random\n" % repr(e)[:160])

    def score(self, screen, options, record, panel=None, meta=None):
        """{option: centred q}, or {} if this screen cannot be scored."""
        opts = sorted(options)
        if not self.ready or not opts:
            return {}
        if not (record or {}).get("world"):
            if not self._warned:
                self._warned = True
                sys.stderr.write(
                    "mapgraph.interrupt_rank: no world snapshot in hand for %s -- the "
                    "screen came up before this turn's first decision. Falling back.\n"
                    % screen)
            return {}
        import torch
        try:
            from advisor.mapgraph import net as N
        except ImportError:
            import net as N
        try:
            g = IB.build_screen_graph(record, screen, opts, meta=meta, panel=panel)
            if g is None or not g.action_nodes:
                return {}
            data = N.to_data(g)
            with torch.no_grad():
                out = self.net(data)
                q = out["q"].tolist()
                self.last_value = float(out["v"][0])
        except Exception as e:
            self.errors += 1
            sys.stderr.write("mapgraph.interrupt_rank: scoring %s failed (%d so far) -> %s\n"
                             % (screen, self.errors, repr(e)[:140]))
            return {}
        if len(q) != len(opts):
            self.errors += 1
            sys.stderr.write("mapgraph.interrupt_rank: %s scored %d nodes for %d options\n"
                             % (screen, len(q), len(opts)))
            return {}
        mid = sum(q) / float(len(q))
        return {o: v - mid for o, v in zip(opts, q)}


def _smoke(limit=6):
    sys.path.insert(0, common.DECISIONS)
    from store import DecisionStore
    from advisor.mapgraph import interrupt_train as IT

    r = Ranker()
    print("interrupt ranker ready: %s  dir: %s" % (r.ready, r.model_dir))
    if r.meta:
        f = r.meta.get("fit") or {}
        print("  rows %s  val_nll %s  uniform %s"
              % (r.meta.get("rows"), f.get("val_listwise_nll"), r.meta.get("uniform_nll")))
    s = DecisionStore(common.RUN_DIR, readonly=True)
    try:
        rows = s.interrupt_rows()
        ctx = IT.context_for(s, rows)
    finally:
        s.close()
    shown = 0
    for row in reversed(rows):
        c = ctx.get(row["interrupt_id"])
        if c is None or len(row.get("options") or {}) < 2:
            continue
        sc = r.score(row["screen"], sorted(row["options"]), c["record"],
                     panel=row.get("panel"), meta=row.get("options"))
        if not sc:
            print("%-20s -> no score" % row["screen"])
            continue
        best = max(sc, key=sc.get)
        print("%-20s took=%-28s model=%-28s %s"
              % (row["screen"], str(row.get("chosen"))[-26:], str(best)[-26:],
                 " ".join("%+.3f" % v for v in sorted(sc.values(), reverse=True))))
        shown += 1
        if shown >= limit:
            return
    if not shown:
        raise SystemExit("smoke: no scorable interrupt found")


if __name__ == "__main__":
    common.require_venv()
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        _smoke(int(sys.argv[2]) if len(sys.argv) > 2 else 6)
    else:
        raise SystemExit("usage: interrupt_rank.py smoke [n]")
