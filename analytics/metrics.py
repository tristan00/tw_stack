from __future__ import annotations


import numpy as np

MIN_N = 3

RBO_P = 0.9

TOP_KS = (1, 3, 5, 10)


def tie_averaged_ranks(v) -> np.ndarray:
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
    return set(np.nonzero(ranks <= k)[0].tolist())


def topk_overlap(xs, ys, k: int):
    n = len(xs)
    if n < 1 or k < 1:
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    shared = len(_prefix_set(x, k) & _prefix_set(y, k))
    return float(min(1.0, shared / float(min(k, n))))


def rbo(xs, ys, p: float = RBO_P):
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
    n = len(xs)
    if n < 1:
        return None, None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    return int(y[int(np.argmin(x))]), int(x[int(np.argmin(y))])


def same_best(xs, ys) -> bool:
    if len(xs) < 1:
        return False
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    return bool(_prefix_set(x, 1) & _prefix_set(y, 1))


def compare(xs, ys) -> dict:
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
