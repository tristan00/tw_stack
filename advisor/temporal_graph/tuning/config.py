from dataclasses import asdict

from ..config import GraphConfig, GraphLimits, KERNELS, LAYOUTS, NODE_VIEWS
from advisor.reward_worker import validate_budget


SPACE = {
    "node_view": list(NODE_VIEWS),
    "layout": list(LAYOUTS),
    "kernel": [k for k in KERNELS if k != "learned"],
    "history": [0, 1, 2, 4, 8, 16, 32, 64],
    "resolution": [1, 2, 4, 8, 16, 32, 64],
    "degree": [0, 1, 2, 4, 8, 16, 32, 64],
    "arity": [2, 4, 8, 16, 32, 64],
    "candidates": [16, 32, 64, 128],
    "query_degree": [4, 8, 16, 32],
    "hidden": [2, 4, 8, 16, 32, 64, 96, 128],
    "aggregation": ["mean", "sum", "attention"],
    "update": ["residual", "gru"],
    "time_features": ["action", "turn", "both"],
    "batch": [8, 16, 32, 64],
}
RANGES = {
    "time_bias": dict(low=-2.0, high=2.0),
    "layers": dict(low=1, high=4),
    "dropout": dict(low=0.0, high=0.5),
    "lr": dict(low=1e-5, high=1e-3, log=True),
    "weight_decay": dict(low=1e-6, high=1e-1, log=True),
    "grad_clip": dict(low=0.1, high=10.0, log=True),
    "epochs": dict(low=5, high=120, step=5),
    "patience": dict(low=2, high=15),
}
CONDITIONS = {
    "resolution": "bundle, trace, mixed node views; otherwise 1",
    "arity": "hub or incidence layout; otherwise 2",
    "degree": "knn, reciprocal, radius, hub, incidence; forest=1, dense/empty=0",
    "candidates": "sparse layouts; dense uses exact enumeration",
}


class InvalidConfiguration(ValueError):
    pass


def suggest(trial):
    def categorical(name):
        return trial.suggest_categorical(name, SPACE[name])

    def numeric(name):
        method = trial.suggest_int if name in ("layers", "epochs", "patience") else trial.suggest_float
        return method(name, **RANGES[name])

    view, layout = categorical("node_view"), categorical("layout")
    graph = GraphConfig(node_view=view, layout=layout, kernel=categorical("kernel"),
        history=categorical("history"),
        resolution=categorical("resolution") if view in ("bundle", "trace", "mixed") else 1,
        degree=(1 if layout == "forest" else 0) if layout in ("forest", "dense", "empty") else categorical("degree"),
        arity=categorical("arity") if layout in ("hub", "incidence") else 2,
        time_bias=numeric("time_bias"))
    limits = GraphLimits(candidates=categorical("candidates") if layout != "dense" else 128,
        query_degree=categorical("query_degree"), max_nodes=20_000, max_edges=200_000)
    if layout != "dense" and max(graph.degree, limits.query_degree) > limits.candidates:
        raise InvalidConfiguration("degree and query_degree must fit the sampled candidate pool")
    model = {key: categorical(key) for key in
             ("hidden", "aggregation", "update", "time_features", "batch")}
    model.update({key: numeric(key) for key in RANGES if key != "time_bias"})
    return dict(graph=asdict(graph), limits=asdict(limits), model=model)
