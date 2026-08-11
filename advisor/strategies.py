from __future__ import annotations

import sys

# Strategy names are ALGORITHM names, not architecture names. "gnn" described the encoder
# -- a relational graph net over the decision graph -- which is the part every future
# attempt will share, so it could not distinguish one attempt from the next. The learning
# algorithm is what actually differs: `gnn_marwil` is MARWIL/AWR (exponentially
# advantage-weighted imitation of logged actions, with a value baseline), which is what
# mapgraph/train.py implements. A later IQL or CQL attempt on the same encoder gets its own
# name beside this one rather than silently replacing what "gnn" meant.
#
# This is not a version suffix: `gnn_marwil` says what the thing IS. There is still exactly
# one implementation of it, and no older generation is kept alongside.
NAMES = ("random", "exploit_tree", "ruleset", "gnn_marwil")


class Random:

    def __init__(self, rng):
        self.rng = rng
        self.ready = True

    def pick(self, elig, record):
        pools = {}
        for r in elig:
            pools.setdefault(r["action_type"], []).append(r)
        return self.rng.choice(pools[self.rng.choice(sorted(pools))])


class ExploitTree:

    def __init__(self, ranker):
        self.ranker = ranker

    @property
    def ready(self):
        return bool(self.ranker.ready)

    def pick(self, elig, record):
        return max(elig, key=lambda r: r.get("exploit") or 0.0)


class Ruleset:

    def __init__(self, ruleset):
        self.ruleset = ruleset
        self.last_rule = None

    @property
    def ready(self):
        return self.ruleset is not None

    def pick(self, elig, record):
        self.last_rule = None
        hit = self.ruleset.match(elig, record)
        if hit is None:
            return None
        row, rule_name = hit
        self.last_rule = rule_name
        return row


def offer_key(r):
    """Identity of an offer, matching how the store keys action_offers rows."""
    return (r.get("context_kind"), str(r.get("context_id")),
            r.get("action_type"), str(r.get("key")))


class GnnMarwil:

    def __init__(self, gnn):
        self.gnn = gnn
        self.errors = 0
        self.last_scores = {}
        # Scores the policy computed for this decision before drawing. The gnn now forms
        # its opinion up front like catboost does, so picking is a lookup, not a second
        # forward pass -- and the number it chooses on is exactly the number recorded.
        self.scored = None

    @property
    def ready(self):
        return bool(self.gnn is not None and self.gnn.ready)

    def pick(self, elig, record):
        scored, self.scored = self.scored, None
        if scored:
            self.last_scores = scored
            best, best_v = None, None
            for r in elig:
                v = scored.get(offer_key(r))
                if v is not None and (best_v is None or v > best_v):
                    best, best_v = r, v
            if best is not None:
                return best
        # no precomputed scores (caller is not the policy, or scoring failed) -- score here
        self.last_scores = {}
        try:
            best = self.gnn.pick(elig, record)
        except Exception as e:
            self.errors += 1
            sys.stderr.write("strategies: gnn pick failed (%d so far) -> %s\n"
                             % (self.errors, repr(e)[:140]))
            return None
        impact = getattr(self.gnn, "last_impact", None) or []
        self.last_scores = {offer_key(r): float(v) for r, v in zip(elig, impact)}
        return best


def build(name, rng=None, ranker=None, ruleset=None, gnn=None):
    if name == "random":
        return Random(rng)
    if name == "exploit_tree":
        return ExploitTree(ranker)
    if name == "ruleset":
        return Ruleset(ruleset)
    if name == "gnn_marwil":
        return GnnMarwil(gnn)
    raise ValueError("unknown strategy %r -- known: %s" % (name, ", ".join(NAMES)))
