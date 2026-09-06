from __future__ import annotations


import hashlib
import json
from dataclasses import dataclass, replace

from advisor.mapgraph import schema as S


SPATIAL_TYPES = ("lord", "hero", "settlement")


@dataclass(frozen=True)
class GraphBuildConfig:
    spatial_neighbor_count: int = S.KNN_K
    spatial_max_distance: float | None = None
    spatial_node_types: tuple[str, ...] = SPATIAL_TYPES
    spatial_pair_types: tuple[str, ...] | None = None
    attack_context_radius: float = 25.0
    attack_context_max_neighbors: int | None = None
    attack_context_node_types: tuple[str, ...] = SPATIAL_TYPES
    include_own_citizenry_nodes: bool = False
    enabled_relation_types: tuple[str, ...] | None = None

    def normalized(self):
        unknown_spatial = ((set(self.spatial_node_types)
                            | set(self.attack_context_node_types)) - set(SPATIAL_TYPES))
        if unknown_spatial:
            raise ValueError("unknown spatial node types: %s" % sorted(unknown_spatial))
        unknown_relations = (set(self.enabled_relation_types or ()) - set(S.RELATIONS))
        if unknown_relations:
            raise ValueError("unknown relation types: %s" % sorted(unknown_relations))
        pair_parts = {part for pair in (self.spatial_pair_types or ())
                      for part in str(pair).split(":")}
        unknown_pairs = pair_parts - set(SPATIAL_TYPES)
        if unknown_pairs:
            raise ValueError("unknown spatial pair types: %s" % sorted(unknown_pairs))
        spatial = tuple(sorted(set(self.spatial_node_types)))
        attack = tuple(sorted(set(self.attack_context_node_types)))
        pairs = None if self.spatial_pair_types is None else tuple(sorted(set(
            _pair_key(*p.split(":", 1)) for p in self.spatial_pair_types)))
        relations = None if self.enabled_relation_types is None else tuple(sorted(set(
            self.enabled_relation_types)))
        return replace(
            self,
            spatial_neighbor_count=max(0, int(self.spatial_neighbor_count)),
            spatial_max_distance=_positive_or_none(self.spatial_max_distance),
            spatial_node_types=spatial,
            spatial_pair_types=pairs,
            attack_context_radius=max(0.0, float(self.attack_context_radius)),
            attack_context_max_neighbors=_positive_int_or_none(
                self.attack_context_max_neighbors),
            attack_context_node_types=attack,
            enabled_relation_types=relations,
        )

    def as_dict(self):
        c = self.normalized()
        return {k: list(v) if isinstance(v, tuple) else v
                for k, v in c.__dict__.items()}

    def fingerprint(self):
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def allows_pair(self, a, b):
        return (self.spatial_pair_types is None
                or _pair_key(a, b) in self.spatial_pair_types)

    def allows_relation(self, relation):
        return (self.enabled_relation_types is None
                or relation in self.enabled_relation_types)


def _positive_or_none(value):
    if value is None:
        return None
    value = float(value)
    return value if value > 0 else None


def _positive_int_or_none(value):
    if value is None:
        return None
    value = int(value)
    return value if value > 0 else None


def _pair_key(a, b):
    return ":".join(sorted((str(a), str(b))))


def from_dict(values=None):
    if isinstance(values, GraphBuildConfig):
        return values.normalized()
    values = dict(values or {})
    for key in ("spatial_node_types", "spatial_pair_types",
                "attack_context_node_types", "enabled_relation_types"):
        if values.get(key) is not None:
            values[key] = tuple(values[key])
    return GraphBuildConfig(**values).normalized()


DEFAULT = GraphBuildConfig()
