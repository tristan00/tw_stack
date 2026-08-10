from __future__ import annotations

import sys

NAMES = ("random", "exploit_tree", "ruleset", "gnn")


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


class Gnn:

    def __init__(self, gnn):
        self.gnn = gnn
        self.errors = 0

    @property
    def ready(self):
        return bool(self.gnn is not None and self.gnn.ready)

    def pick(self, elig, record):
        try:
            return self.gnn.pick(elig, record)
        except Exception as e:
            self.errors += 1
            sys.stderr.write("strategies: gnn pick failed (%d so far) -> %s\n"
                             % (self.errors, repr(e)[:140]))
            return None


def build(name, rng=None, ranker=None, ruleset=None, gnn=None):
    if name == "random":
        return Random(rng)
    if name == "exploit_tree":
        return ExploitTree(ranker)
    if name == "ruleset":
        return Ruleset(ruleset)
    if name == "gnn":
        return Gnn(gnn)
    raise ValueError("unknown strategy %r -- known: %s" % (name, ", ".join(NAMES)))
