from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from advisor.mapgraph import schema as S


DEFAULTS_PATH = Path(__file__).with_name("edge_defaults.json")


_CALIBRATION = json.loads(DEFAULTS_PATH.read_text())
TUNING_RANGES = _CALIBRATION["tuning_ranges"]


def default_limits():
    return dict(_CALIBRATION["limits"])


@dataclass(frozen=True)
class GraphBuildConfig:
    edge_limits: dict[str, int] = field(default_factory=default_limits)
    include_own_citizenry_nodes: bool = False

    def normalized(self):
        unknown = set(self.edge_limits) - set(S.RELATIONS)
        if unknown:
            raise ValueError("unknown edge types: %s" % sorted(unknown))
        limits = default_limits()
        limits.update(self.edge_limits)
        if set(limits) != set(S.RELATIONS):
            raise ValueError("edge defaults must cover every relation")
        if any(type(v) is not int or v < 0 for v in limits.values()):
            raise ValueError("edge limits must be nonnegative integers; zero disables")
        return GraphBuildConfig(limits, self.include_own_citizenry_nodes)

    def as_dict(self):
        c = self.normalized()
        return {"edge_limits": dict(c.edge_limits),
                "include_own_citizenry_nodes": c.include_own_citizenry_nodes}

    def fingerprint(self):
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

def from_dict(values=None):
    if isinstance(values, GraphBuildConfig):
        return values.normalized()
    return GraphBuildConfig(**dict(values or {})).normalized()


DEFAULT = GraphBuildConfig()
