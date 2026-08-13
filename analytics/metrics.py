from __future__ import annotations

"""How alike are two rankings of the same decision.

Pure functions. No database, no I/O, no configuration -- so the gates in
test_analytics.py can assert exact constants against hand-computed cases, and so the
formulas can be read without reading a query.

Every function here takes RANK VECTORS, not ordered lists: `xs[i]` is the position model A
gave offer `i`, 1 = best. That is the shape the corpus stores (`scores.packed` carries
`rank` and `gnn_rank` per offer) and converting to ordered lists at the boundary would
force an arbitrary tiebreak on the 9 decisions where the graph model ties. Callers mask
out offers either model did not rank, and pass the two aligned vectors.

THE HEADLINE IS `spearman`. `kendall_tau_b` is a second correlation that handles the tie
structure properly. `rbo` and `topk_overlap` are supplements: they answer "do they agree
about the TOP", which a correlation over the whole ordering can miss, and they are reported
as secondary everywhere they appear. They do not replace the correlations.

A note on what these numbers mean, because it is easy to read them as a score. Near +1 the
two models are ranking on the same thing and the second is close to redundant; near 0 they
are ranking on effectively unrelated criteria; near -1 one is the other reversed. None of
the three is a fault by itself. Nothing here grades its output.
"""

import numpy as np

MIN_N = 3

RBO_P = 0.9

TOP_KS = (1, 3, 5, 10)


def tie_averaged_ranks(v) -> np.ndarray:
    """Ranks 1..n with ties sharing their average position."""
    a = np.asarray(v, dtype=np.float64)
    n = a.size
    order = np.argsort(a, kind="stable")
    out = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and a[order[j + 1]] == a[order[i]]:
            j += 1
        out[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return out


def spearman(xs, ys):
    """Spearman's rho: Pearson correlation over tie-averaged ranks."""
    n = len(xs)
    if n < MIN_N:
        return None
    rx, ry = tie_averaged_ranks(xs), tie_averaged_ranks(ys)
    dx, dy = rx - rx.mean(), ry - ry.mean()
    sx, sy = float(np.sqrt((dx * dx).sum())), float(np.sqrt((dy * dy).sum()))
    if not sx or not sy:
        return None
    return float((dx * dy).sum() / (sx * sy))


def kendall_tau_b(xs, ys):
    """Kendall's tau-b: (C - D) / sqrt((n0 - Tx)(n0 - Ty))."""
    n = len(xs)
    if n < MIN_N:
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    dx = np.sign(x[:, None] - x[None, :])
    dy = np.sign(y[:, None] - y[None, :])
    iu = np.triu_indices(n, k=1)
    sx, sy = dx[iu], dy[iu]
    concordant = float(np.sum(sx * sy > 0))
    discordant = float(np.sum(sx * sy < 0))
    n0 = n * (n - 1) / 2.0
    tx = float(np.sum(sx == 0))
    ty = float(np.sum(sy == 0))
    denom = np.sqrt((n0 - tx) * (n0 - ty))
    if not denom:
        return None
    return float((concordant - discordant) / denom)


def _prefix_set(ranks: np.ndarray, k: int) -> set:
    """The offers a model placed at position k or better."""
    return set(np.nonzero(ranks <= k)[0].tolist())


def topk_overlap(xs, ys, k: int):
    """How much of each model's top k the other also put in its top k."""
    n = len(xs)
    if n < 1 or k < 1:
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    shared = len(_prefix_set(x, k) & _prefix_set(y, k))
    return float(min(1.0, shared / float(min(k, n))))


def rbo(xs, ys, p: float = RBO_P):
    """Rank-biased overlap, extrapolated."""
    n = len(xs)
    if n < MIN_N:
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    total, shared_at_d = 0.0, 0
    for k in range(1, n + 1):
        shared = len(_prefix_set(x, k) & _prefix_set(y, k))
        total += (shared / float(k)) * (p ** (k - 1))
        shared_at_d = shared
    return float((1.0 - p) * total + (shared_at_d / float(n)) * (p ** n))


def cross_ranks(xs, ys):
    """(where model B placed A's best offer, where A placed B's best offer)."""
    n = len(xs)
    if n < 1:
        return None, None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    return int(y[int(np.argmin(x))]), int(x[int(np.argmin(y))])


def same_best(xs, ys) -> bool:
    """Do the two models share a best offer."""
    if len(xs) < 1:
        return False
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    return bool(_prefix_set(x, 1) & _prefix_set(y, 1))


def compare(xs, ys) -> dict:
    """Every measure for one decision, in one pass."""
    n = len(xs)
    out = {
        "n": n,
        "rho": spearman(xs, ys),
        "tau_b": kendall_tau_b(xs, ys),
        "rbo": rbo(xs, ys),
        "top1_same": 1 if same_best(xs, ys) else 0,
    }
    for k in TOP_KS:
        if k != 1:
            out["top%d_overlap" % k] = topk_overlap(xs, ys, k)
    out["cat_top_in_gnn"], out["gnn_top_in_cat"] = cross_ranks(xs, ys)
    return out
