from __future__ import annotations

NAMES = ("random", "exploit_tree", "ruleset")


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


def build(name, rng=None, ranker=None, ruleset=None):
    if name == "random":
        return Random(rng)
    if name == "exploit_tree":
        return ExploitTree(ranker)
    if name == "ruleset":
        return Ruleset(ruleset)
    raise ValueError("unknown strategy %r -- known: %s" % (name, ", ".join(NAMES)))
