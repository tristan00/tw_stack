from functools import lru_cache
import hashlib
import json
import logging
import math
import re

import numpy as np

from ..data import ActionQueries, History, NodeKind


LOG = logging.getLogger(__name__)
WIDTH = 64
EXCLUDED = frozenset(("offers", "params", "read_failures", "campaign_uuid", "campaign_id",
    "decision_id", "snapshot_id", "entity_seq", "selector", "presave_radius", "cqi", "faction_cqi",
    "validated", "validation", "counted", "refusal", "success", "verification", "reward", "target",
    "score", "exploit", "policy", "propensity", "executed", "execution", "confirmed", "latency"))


@lru_cache(maxsize=100_000)
def identifier(*parts):
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "little") & (2**63 - 1)


def excluded(key):
    return key.startswith("_") or key.lower() in EXCLUDED or bool(EXCLUDED.intersection(re.split(r"[^a-z]+", key.lower())))


def features(scope, data):
    values = np.zeros(WIDTH, dtype=np.float32)
    present = np.zeros(WIDTH, dtype=bool)

    def add(path, value):
        if value is None:
            return
        if isinstance(value, dict):
            for key in sorted(value):
                if not excluded(str(key)):
                    add(path + "/" + str(key), value[key])
        elif isinstance(value, (list, tuple)):
            add(path + "/length", len(value))
            for item in value:
                add(path + "/item", item)
        elif isinstance(value, (int, float, bool)):
            number = float(value)
            if math.isfinite(number):
                token = identifier(path, "number")
                values[token % WIDTH] += math.copysign(math.log1p(abs(number)), number) * (1 if token & 128 else -1)
                present[token % WIDTH] = True
        elif isinstance(value, str):
            token = identifier(path, value)
            values[token % WIDTH] += 1 if token & 128 else -1
            present[token % WIDTH] = True
        else:
            raise TypeError("unsupported raw game-state value at " + path)

    add(scope, data)
    values = np.sign(values) * np.log1p(np.abs(values))
    return values, present


def object_id(state, kind, key=None):
    if state.get("cqi") is not None:
        return identifier("character", str(state["cqi"]))
    if kind in ("province", "regions", "settlements", "ruins") or state.get("kind") == "settlement":
        region = state.get("region", key)
        if region is not None:
            return identifier("region", str(region))
    if kind == "campaign":
        return identifier("faction", str(state["faction"]))
    return -1


def position(state):
    return [float(state[k]) if isinstance(state.get(k), (int, float)) else np.nan for k in ("x", "y")]


def observations(record, step):
    camp = record["campaign"]
    yield "campaign", identifier("campaign"), object_id(camp, "campaign"), camp
    for entity in record["entities"]:
        kind, key, state = entity["context_kind"], entity["context_id"], entity["state"]
        eid = object_id(state, kind, key)
        yield kind, identifier("entity", kind, str(key)), eid, state
    for kind, collection in sorted(record["world"].items()):
        if excluded(kind):
            continue
        if isinstance(collection, dict):
            items = sorted(collection.items())
        elif isinstance(collection, list):
            items = list(enumerate(collection))
        else:
            items = [(kind, collection)]
        for key, value in items:
            state = value if isinstance(value, dict) else {"value": value}
            eid = object_id(state, kind, key if isinstance(collection, dict) else None)
            token = ("object", eid) if eid >= 0 else (("key", str(key)) if isinstance(collection, dict) else ("anonymous", step, key))
            yield "world/" + kind, identifier("world", kind, *token), eid, state


class CampaignEncoder:

    def __init__(self, campaign):
        self.campaign = campaign
        self.arrays = {key: [] for key in ("values", "present", "stream", "step", "turn", "entity", "reference", "kind", "xy")}
        self.queries = {key: [] for key in ("values", "ids", "entity", "reference", "xy")}

    def append(self, record, action):
        step = len(self.queries["ids"])
        if record["campaign_id"] != self.campaign:
            raise ValueError("record crossed a campaign boundary")
        seen = set()
        for scope, stream, entity, state in observations(record, step):
            if stream in seen:
                raise ValueError("ambiguous duplicate observation identity")
            seen.add(stream)
            values, present = features(scope, state)
            reference = identifier("region", str(state["region"])) if state.get("region") is not None else -1
            self._append(values, present, stream, step, record["turn"], entity,
                         reference, NodeKind.OBSERVATION, position(state))
        eseq = action["entity_seq"]
        if eseq is None:
            actor, actor_kind = record["campaign"], "campaign"
        else:
            matches = [e for e in record["entities"] if e["snapshot_id"] % 64 == eseq]
            if len(matches) != 1:
                raise ValueError("chosen action actor is absent or ambiguous")
            actor, actor_kind = matches[0]["state"], matches[0]["context_kind"]
        entity = object_id(actor, actor_kind)
        selected = {key: action[key] for key in ("action_type", "key", "slot_index")}
        selected["actor_kind"] = actor_kind
        values, present = features("action", selected)
        reference = identifier("region", str(action["key"]))
        xy = position(actor)
        self._append(values, present, identifier("chosen_action"), step, record["turn"],
                     entity, reference, NodeKind.ACTION, xy)
        for key, value in dict(values=values, ids=record["decision_id"], entity=entity, reference=reference, xy=xy).items():
            self.queries[key].append(value)
        return step

    def _append(self, values, present, stream, step, turn, entity, reference, kind, xy):
        for key, value in zip(self.arrays, (values, present, stream, step, turn, entity, reference, kind, xy)):
            self.arrays[key].append(value)

    def finish(self):
        data = {key: np.asarray(value, dtype=np.float32 if key in ("values", "xy") else bool if key == "present" else np.int64)
                for key, value in self.arrays.items()}
        names = tuple("raw_%d" % i for i in range(WIDTH))
        history = History.from_arrays(**data, names=names, episode=self.campaign)
        queries = {key: np.asarray(value, dtype=np.float32 if key in ("values", "xy") else np.int64)
                   for key, value in self.queries.items()}
        return history, queries


def query_at(arrays, index):
    return ActionQueries.from_arrays(**{key: value[index:index + 1] for key, value in arrays.items()})
