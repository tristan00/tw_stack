from dataclasses import dataclass

import numpy as np


NODE_VIEWS = ("observation", "change", "bundle", "trace", "mixed")
KERNELS = ("identity", "similarity", "contrast", "cochange", "reference",
           "geometric", "learned", "mixed")
LAYOUTS = ("knn", "reciprocal", "radius", "forest", "hub", "incidence", "dense", "empty")


@dataclass(frozen=True)
class GraphConfig:
    node_view: str = "mixed"
    resolution: int = 4
    history: int = 256
    kernel: str = "mixed"
    layout: str = "knn"
    degree: int = 8
    arity: int = 8
    time_bias: float = 0.1

    def __post_init__(self):
        for name, choices in (("node_view", NODE_VIEWS), ("kernel", KERNELS), ("layout", LAYOUTS)):
            if getattr(self, name) not in choices:
                raise ValueError("unknown %s: %s" % (name, getattr(self, name)))
        for name, minimum in (("resolution", 1), ("history", 0), ("degree", 0), ("arity", 2)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError("%s must be an integer >= %d" % (name, minimum))
        if not np.isfinite(self.time_bias):
            raise ValueError("time_bias must be finite")

@dataclass(frozen=True)
class GraphLimits:
    candidates: int = 64
    query_degree: int = 16
    block_size: int = 256
    max_nodes: int = 500_000
    max_edges: int = 4_000_000
    dense_pair_limit: int = 2_000_000
    seed: int = 0

    def __post_init__(self):
        for name, minimum in (("candidates", 16), ("query_degree", 0), ("block_size", 1),
                              ("max_nodes", 1), ("max_edges", 0), ("dense_pair_limit", 1)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError("%s must be an integer >= %d" % (name, minimum))
        if type(self.seed) is not int or not 0 <= self.seed < 2**31:
            raise ValueError("seed must be an integer in [0, 2**31)")


@dataclass(frozen=True)
class LearnedMetric:
    source: np.ndarray
    destination: np.ndarray

    def matrices(self, width):
        source = np.asarray(self.source, dtype=np.float32)
        destination = np.asarray(self.destination, dtype=np.float32)
        if source.ndim != 2 or source.shape != destination.shape or source.shape[0] != width:
            raise ValueError("metric matrices must have identical (feature_width, latent_width) shapes")
        if source.shape[1] < 1 or not np.isfinite(source).all() or not np.isfinite(destination).all():
            raise ValueError("metric matrices must be nonempty and finite")
        return source, destination
