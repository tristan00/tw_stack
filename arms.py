from __future__ import annotations


NAMES = ("random", "greedy_catboost", "ruleset", "marwil_gnn", "greedy_gnn")

TRAINABLE = ("greedy_catboost", "marwil_gnn", "greedy_gnn")

INTERRUPT_NAMES = ("random", "greedy_catboost", "ruleset")


class ModelUnavailable(RuntimeError):
    pass

ALIASES = {
    "exploit_tree": "greedy_catboost",
    "gnn_marwil": "marwil_gnn",
    "gnn": "marwil_gnn",
    "gnn_greedy": "greedy_gnn",
}

NOT_A_DRAW = ("forced_end_turn",)

UNRECORDED = "(unrecorded)"

_FALLBACK = "_random_fallback"

_DELEGATED = "_delegated_"


def canonical(name):
    if name is None:
        return None
    n = str(name).strip()
    return ALIASES.get(n, n)


def arm_of(policy) -> str | None:
    if policy is None:
        return None
    p = str(policy).strip()
    if not p:
        return None
    if p == UNRECORDED or p in NOT_A_DRAW:
        return p
    p = canonical(p)
    if p in NAMES:
        return p
    if p.startswith("ruleset(") and p.endswith(")"):
        return "ruleset"
    if p.endswith(_FALLBACK):
        stem = canonical(p[:-len(_FALLBACK)])
        if stem in NAMES:
            return stem
    if _DELEGATED in p:
        stem = canonical(p.split(_DELEGATED, 1)[0])
        if stem in NAMES:
            return stem
    raise ValueError(
        "arms.arm_of: %r is not a policy shape this project writes. advisor/policy.py "
        "produces one of: a bare arm %s, 'ruleset(<rule>)', '<arm>%s', or %r. A new shape "
        "must be added here deliberately -- bucketing it silently would move every arm's "
        "share with nothing to show why." % (policy, list(NAMES), _FALLBACK, NOT_A_DRAW[0]))


def fell_back(policy) -> bool:
    if policy is None:
        return False
    p = str(policy).strip()
    return p.endswith(_FALLBACK) or _DELEGATED in p
