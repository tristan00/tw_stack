from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "reference"))

import features as F
import features_db

RULES_DIR = r"D:\twdata\rules"

PREDICATE_OPS = frozenset(("==", "!=", ">", ">=", "<", "<=", "in", "not_in"))
RULE_KEYS = frozenset(("name", "priority", "match", "action", "prefer"))
RULE_REQUIRED = frozenset(("name", "priority", "match", "action"))
MATCH_KEYS = frozenset(("faction", "race", "campaign", "entity", "offer"))
ENTITY_KEYS = frozenset(("kind", "state"))
OFFER_KEYS = frozenset(("action_type", "key", "gate", "params"))
ACTION_KEYS = frozenset(("action_type", "key"))
PREFER_KEYS = frozenset(("field", "order"))
PREFER_ORDERS = frozenset(("min", "max"))
ENTITY_KINDS = frozenset(("lord", "hero", "province", "campaign"))

PARAMS_FIELDS = frozenset((
    "ability", "ability_category", "action", "action_key", "active", "agent_type",
    "attribute", "building_key", "cand_rank", "candidate_index", "chance", "cost",
    "current_research", "damaged", "equipped_index", "faction", "flag", "gift",
    "in_progress", "innate", "is_agent", "is_upgrade", "item_key", "item_name",
    "n_candidates", "n_traits", "points_available", "pool_avail", "pool_index",
    "queue", "queued", "reach_max", "reach_rays", "refund", "region", "repairing",
    "rite_index", "sample_index", "skill_unlocked", "slot_empty", "slot_id",
    "slot_index", "target_cqi", "target_faction", "target_is_agent", "target_kind",
    "target_on_settlement", "target_own", "terms", "trait", "traits", "type_fielded",
    "unit", "x", "y"))


class _Ctx:

    def __init__(self, record):
        self.record = record or {}
        self.entities = {}
        for e in self.record.get("entities") or []:
            self.entities[(e.get("context_kind"), str(e.get("context_id")))] = e
        world = self.record.get("world") or {}
        self.armies = {str(a.get("cqi")): a for a in world.get("armies") or []
                       if a.get("cqi") is not None}
        self.hostile_armies = {str(h.get("cqi")): h for h in world.get("hostiles") or []
                               if h.get("kind") == "army" and h.get("cqi") is not None}
        self.hostiles = world.get("hostiles") or []
        self._memo = {}

    def entity_of(self, row):
        return self.entities.get((row.get("context_kind"), str(row.get("context_id"))))

    def derived(self, field, row):
        mk = (field, id(row))
        if mk not in self._memo:
            self._memo[mk] = DERIVED[field](row, self)
        return self._memo[mk]


def _target_units(row, ctx):
    if row.get("action_type") != "attack_army":
        return None
    tc = (row.get("params") or {}).get("target_cqi")
    if tc is None:
        return None
    h = ctx.hostile_armies.get(str(tc))
    return h.get("units") if h else None


def _units_advantage(row, ctx):
    mine = (((ctx.entity_of(row) or {}).get("state")) or {}).get("units")
    theirs = _target_units(row, ctx)
    if mine is None or theirs is None:
        return None
    return mine - theirs


def _chain_key(row, ctx):
    if row.get("action_type") != "building":
        return None
    if not os.path.isfile(features_db.DB):
        raise RuntimeError("ruleset: reference db missing at %s -- chain_key cannot be derived"
                           % features_db.DB)
    return features_db.building_features(str(row.get("key"))).get("building_chain")


def _move_dist_to_smaller_enemy(row, ctx):
    if row.get("action_type") != "move":
        return None
    p = row.get("params") or {}
    x, y = p.get("x"), p.get("y")
    if x is None or y is None:
        return None
    mine = (((ctx.entity_of(row) or {}).get("state")) or {}).get("units")
    if mine is None:
        return None
    best = None
    for h in ctx.hostiles:
        if h.get("kind") != "army":
            continue
        hu = h.get("units")
        if hu is None or hu >= mine:
            continue
        hx, hy = h.get("x"), h.get("y")
        if hx is None or hy is None:
            continue
        d = math.hypot(float(x) - float(hx), float(y) - float(hy))
        if best is None or d < best:
            best = d
    return best


def _in_own_territory(row, ctx):
    cqi = (((ctx.entity_of(row) or {}).get("state")) or {}).get("cqi")
    if cqi is None:
        return None
    a = ctx.armies.get(str(cqi))
    return a.get("in_own_territory") if a else None


def _target_army_units(row, ctx):
    if row.get("action_type") != "hero_action":
        return None
    tc = (row.get("params") or {}).get("target_cqi")
    if tc is None:
        return None
    a = ctx.armies.get(str(tc))
    return a.get("units") if a else None


DERIVED = {"target_units": _target_units,
           "units_advantage": _units_advantage,
           "chain_key": _chain_key,
           "move_dist_to_smaller_enemy": _move_dist_to_smaller_enemy,
           "in_own_territory": _in_own_territory,
           "target_army_units": _target_army_units}

PREFER_FIELDS = frozenset(DERIVED) | PARAMS_FIELDS


def _pattern_wellformed(pattern):
    p = str(pattern)
    if "*" not in p:
        return True
    if p.startswith("*") and p.endswith("*") and len(p) > 2:
        return "*" not in p[1:-1]
    if p.endswith("*"):
        return "*" not in p[:-1]
    return False


def _match_pattern(pattern, value):
    p = str(pattern)
    if value is None:
        return False
    v = str(value)
    if "*" not in p:
        return v == p
    if p.startswith("*") and p.endswith("*"):
        return p[1:-1] in v
    return v.startswith(p[:-1])


def _scalar_eq(expected, actual):
    if isinstance(expected, str):
        if "*" in expected:
            return _match_pattern(expected, actual)
        return actual is not None and str(actual) == expected
    if isinstance(expected, bool) or isinstance(actual, bool):
        return (actual is not None and expected is not None
                and bool(actual) == bool(expected)) or (actual is None and expected is None)
    return actual == expected


def _as_number(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _eval_op(op, expected, actual):
    if op == "==":
        return _scalar_eq(expected, actual)
    if op == "!=":
        return not _scalar_eq(expected, actual)
    if op == "in":
        return any(_scalar_eq(e, actual) for e in expected)
    if op == "not_in":
        return not any(_scalar_eq(e, actual) for e in expected)
    a, b = _as_number(actual), _as_number(expected)
    if a is None or b is None:
        return False
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    return a <= b


def _eval_predicate(spec, actual):
    if isinstance(spec, dict):
        return all(_eval_op(op, val, actual) for op, val in spec.items())
    return _scalar_eq(spec, actual)


def _eval_predicates(preds, values):
    for k, spec in (preds or {}).items():
        if not _eval_predicate(spec, (values or {}).get(k)):
            return False
    return True


def _validate_predicates(preds, where, source):
    if preds is None:
        return
    if not isinstance(preds, dict):
        raise ValueError("%s: %s must be an object of key -> predicate, got %r"
                         % (source, where, preds))
    for key, spec in preds.items():
        if isinstance(spec, dict):
            if not spec:
                raise ValueError("%s: empty predicate for %s.%s" % (source, where, key))
            bad = set(spec) - PREDICATE_OPS
            if bad:
                raise ValueError("%s: unknown operator(s) %s in %s.%s -- allowed: %s"
                                 % (source, sorted(bad), where, key, sorted(PREDICATE_OPS)))
            for op, val in spec.items():
                if op in ("in", "not_in") and not isinstance(val, (list, tuple)):
                    raise ValueError("%s: %s.%s operator %r needs a list, got %r"
                                     % (source, where, key, op, val))
        elif not isinstance(spec, (str, int, float, bool)) and spec is not None:
            raise ValueError("%s: predicate for %s.%s must be a scalar or {op: value}, got %r"
                             % (source, where, key, spec))


def _validate_pattern(pattern, where, source):
    if not isinstance(pattern, str):
        raise ValueError("%s: %s must be a string pattern, got %r" % (source, where, pattern))
    if not _pattern_wellformed(pattern):
        raise ValueError("%s: %s pattern %r not supported -- exact, trailing-* or *contains* only"
                         % (source, where, pattern))


def _require_keys(d, allowed, required, what, source):
    if not isinstance(d, dict):
        raise ValueError("%s: %s must be an object, got %r" % (source, what, d))
    unknown = set(d) - allowed
    if unknown:
        raise ValueError("%s: %s has unknown key(s) %s -- allowed: %s"
                         % (source, what, sorted(unknown), sorted(allowed)))
    missing = required - set(d)
    if missing:
        raise ValueError("%s: %s missing required key(s) %s" % (source, what, sorted(missing)))


@dataclass(frozen=True)
class Rule:
    name: str
    priority: int
    match: dict
    action: dict
    prefer: dict | None

    @classmethod
    def from_dict(cls, d, source):
        _require_keys(d, RULE_KEYS, RULE_REQUIRED,
                      "rule %r" % (d.get("name") if isinstance(d, dict) else d), source)
        name = str(d["name"])
        tag = "rule %r" % name
        try:
            priority = int(d["priority"])
        except (TypeError, ValueError):
            raise ValueError("%s: %s priority must be an int, got %r"
                             % (source, tag, d["priority"]))
        match = d["match"]
        _require_keys(match, MATCH_KEYS, frozenset(), "%s match" % tag, source)
        if "faction" in match:
            _validate_pattern(match["faction"], "%s match.faction" % tag, source)
        if "race" in match:
            _validate_predicates({"race": match["race"]}, "%s match" % tag, source)
        _validate_predicates(match.get("campaign"), "%s match.campaign" % tag, source)
        ent = match.get("entity")
        if ent is not None:
            _require_keys(ent, ENTITY_KEYS, frozenset(), "%s match.entity" % tag, source)
            if "kind" in ent and ent["kind"] not in ENTITY_KINDS:
                raise ValueError("%s: %s match.entity.kind %r not in %s"
                                 % (source, tag, ent["kind"], sorted(ENTITY_KINDS)))
            _validate_predicates(ent.get("state"), "%s match.entity.state" % tag, source)
        off = match.get("offer")
        if off is not None:
            _require_keys(off, OFFER_KEYS, frozenset(), "%s match.offer" % tag, source)
            if "action_type" in off and off["action_type"] not in F.ACTION_TYPES:
                raise ValueError("%s: %s match.offer.action_type %r not in features.ACTION_TYPES"
                                 % (source, tag, off["action_type"]))
            if "key" in off:
                _validate_pattern(off["key"], "%s match.offer.key" % tag, source)
            if "gate" in off:
                _validate_pattern(off["gate"], "%s match.offer.gate" % tag, source)
            _validate_predicates(off.get("params"), "%s match.offer.params" % tag, source)
        action = d["action"]
        _require_keys(action, ACTION_KEYS, frozenset(("action_type",)), "%s action" % tag, source)
        if action["action_type"] not in F.ACTION_TYPES:
            raise ValueError("%s: %s action.action_type %r not in features.ACTION_TYPES"
                             % (source, tag, action["action_type"]))
        if off is not None and "action_type" in off \
                and off["action_type"] != action["action_type"]:
            raise ValueError("%s: %s match.offer.action_type %r contradicts action.action_type %r"
                             " -- the rule can never fire"
                             % (source, tag, off["action_type"], action["action_type"]))
        if "key" in action:
            _validate_pattern(action["key"], "%s action.key" % tag, source)
        prefer = d.get("prefer")
        if prefer is not None:
            _require_keys(prefer, PREFER_KEYS, PREFER_KEYS, "%s prefer" % tag, source)
            if prefer["order"] not in PREFER_ORDERS:
                raise ValueError("%s: %s prefer.order %r not in %s"
                                 % (source, tag, prefer["order"], sorted(PREFER_ORDERS)))
            if prefer["field"] not in PREFER_FIELDS:
                raise ValueError("%s: %s prefer.field %r not in the derived+params vocabulary"
                                 " -- derived: %s" % (source, tag, prefer["field"],
                                                      sorted(DERIVED)))
        return cls(name=name, priority=priority, match=dict(match), action=dict(action),
                   prefer=dict(prefer) if prefer else None)


def _field_value(field, row, ctx):
    if field in DERIVED:
        return ctx.derived(field, row)
    return (row.get("params") or {}).get(field)


def _row_matches(rule, row, ctx):
    action = rule.action
    if row.get("action_type") != action["action_type"]:
        return False
    if "key" in action and not _match_pattern(action["key"], row.get("key")):
        return False
    m = rule.match
    ent_spec = m.get("entity")
    if ent_spec:
        if "kind" in ent_spec and row.get("context_kind") != ent_spec["kind"]:
            return False
        state_spec = ent_spec.get("state")
        if state_spec:
            ent = ctx.entity_of(row)
            if ent is None:
                return False
            if not _eval_predicates(state_spec, ent.get("state") or {}):
                return False
    off = m.get("offer")
    if off:
        if "action_type" in off and row.get("action_type") != off["action_type"]:
            return False
        if "key" in off and not _match_pattern(off["key"], row.get("key")):
            return False
        if "gate" in off and not _match_pattern(off["gate"], row.get("gate") or "none"):
            return False
        for k, spec in (off.get("params") or {}).items():
            if not _eval_predicate(spec, _field_value(k, row, ctx)):
                return False
    return True


@dataclass(frozen=True)
class RuleSet:
    name: str
    path: str
    sha256: str
    rules: tuple

    @classmethod
    def load(cls, name):
        path = os.path.join(RULES_DIR, "%s.json" % name)
        if not os.path.isfile(path):
            raise FileNotFoundError("rule set %r not found at %s -- rule files are operator data"
                                    " outside the repo; write one there first" % (name, path))
        raw_bytes = open(path, "rb").read()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        raw = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError('%s: top level must be {"rules": [...]}' % path)
        _require_keys(raw, frozenset(("rules",)), frozenset(("rules",)), "top level", path)
        if not isinstance(raw["rules"], list):
            raise ValueError('%s: "rules" must be a list' % path)
        rules = [Rule.from_dict(r, path) for r in raw["rules"]]
        if not rules:
            raise ValueError("%s: rule set is empty" % path)
        names = [r.name for r in rules]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError("%s: duplicate rule name(s) %s" % (path, dupes))
        rules.sort(key=lambda r: -r.priority)
        return cls(name=str(name), path=path, sha256=digest, rules=tuple(rules))

    def match(self, elig_rows, record):
        ctx = _Ctx(record)
        campaign = ctx.record.get("campaign") or {}
        faction = campaign.get("faction")
        race = F.race_of(faction)
        for rule in self.rules:
            m = rule.match
            if "faction" in m and not _match_pattern(m["faction"], faction):
                continue
            if "race" in m and not _eval_predicate(m["race"], race):
                continue
            if not _eval_predicates(m.get("campaign"), campaign):
                continue
            hits = [r for r in elig_rows or [] if _row_matches(rule, r, ctx)]
            if not hits:
                continue
            if rule.prefer:
                field, order = rule.prefer["field"], rule.prefer["order"]
                scored = [(_field_value(field, r, ctx), r) for r in hits]
                scored = [(v, r) for v, r in scored if v is not None]
                if not scored:
                    continue
                pick = min if order == "min" else max
                return pick(scored, key=lambda t: t[0])[1], rule.name
            return hits[0], rule.name
        return None


def main(argv):
    if len(argv) < 3 or argv[1] != "check":
        raise SystemExit("usage: ruleset.py check <name>")
    rs = RuleSet.load(argv[2])
    print("loaded %s: %d rule(s) from %s" % (rs.name, len(rs.rules), rs.path))
    print("sha256 %s" % rs.sha256)
    for r in rs.rules:
        pref = " prefer %s %s" % (r.prefer["order"], r.prefer["field"]) if r.prefer else ""
        print("  %4d  %-32s -> %s %s%s" % (r.priority, r.name, r.action["action_type"],
                                           r.action.get("key", "*"), pref))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
