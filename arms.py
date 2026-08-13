from __future__ import annotations

"""Which strategy produced a recorded pick.

`advisor/policy.py:140-148` writes the `policy` string, and it encodes two things at once:
the arm that was drawn, and what happened when it was asked to choose.

    ruleset(spread_out)            the ruleset arm, and the rule that matched
    marwil_gnn_random_fallback     the arm was drawn but could not score, so random chose
    greedy_catboost                the arm chose, plainly
    forced_end_turn                NOT a strategy draw at all -- the loop ended the turn

Reporting grouped on the raw string, so `ruleset(spread_out)`, `ruleset(court_our_best_friend)`
and `ruleset(edict_whenever_possible)` each became their own row while every other strategy
aggregated by strategy. That is what this module fixes: one normalisation, used by every
consumer, so the arm shown on one page cannot mean something different on another.

`forced_end_turn` normalises to itself rather than to a strategy. `advisor_api/queries.py`
already excludes it from the mix with the reason written down -- counting a loop decision
inside the strategy mix understates every real arm's share -- and that exclusion stays the
caller's to make, deliberately: this module says what an arm IS, not who should count it.

It raises on a shape it does not recognise. A new arm that silently landed in an existing
bucket would move every number on the models pages with nothing to show it had happened.
"""

NAMES = ("random", "greedy_catboost", "ruleset", "marwil_gnn")

ALIASES = {
    "exploit_tree": "greedy_catboost",
    "gnn_marwil": "marwil_gnn",
    "gnn": "marwil_gnn",
}

NOT_A_DRAW = ("forced_end_turn",)

UNRECORDED = "(unrecorded)"

_FALLBACK = "_random_fallback"

_DELEGATED = "_delegated_"


def canonical(name):
    """A strategy name as this project spells it now, from any spelling it has used."""
    if name is None:
        return None
    n = str(name).strip()
    return ALIASES.get(n, n)


def arm_of(policy) -> str | None:
    """The strategy behind a recorded policy string."""
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


def is_draw(policy) -> bool:
    """Did a strategy actually choose this. False for loop decisions and unrecorded rows."""
    a = arm_of(policy)
    return bool(a) and a in NAMES


def fell_back(policy) -> bool:
    """The arm was drawn but did not choose -- random took over, or another arm did."""
    if policy is None:
        return False
    p = str(policy).strip()
    return p.endswith(_FALLBACK) or _DELEGATED in p
