from __future__ import annotations

import hashlib
import json
import struct
import sys
import time

SEP = b'\x1e'

ID_PATHS = {
    'cqi', 'faction_cqi', 'index', 'slot_index',
    'stationed', 'citizenry', 'locked_slots', 'reach_chars', 'cqis',
}
WILD_DICTS = {'resources', 'hero_type_counts', 'read_failures', 'stationed', 'reach_chars',
              'reach_setts', 'built', 'building_now', 'corruption', 'merc_pools',
              'lord_pools', 'trait_progress'}

COUNT_PATHS = {
    'rank', 'skill_points', 'units', 'pending_recruits', 'ap_remaining', 'ap_per_turn',
    'loyalty', 'x', 'y', 'xp', 'xp_next_level', 'subterfuge', 'zeal', 'authority',
    'resurrection_turns', 'turn', 'settlements', 'armies', 'lord_level', 'allies',
    'vassals', 'power_rank', 'difficulty', 'presave_radius', 'level', 'tier',
    'total_levels', 'points', 'threshold_points', 'turns_remaining', 'turns_left',
    'avail', 'max_slots', 'free_slots', 'buildings', 'settlement_level',
    'development_points', 'public_order', 'health', 'max_health', 'research_points',
    'n', 'dist', 'standing', 'corruption', 'trait_progress', 'hero_type_counts',
    'ranks',
}

MONEY_PATHS = {
    'income', 'treasury', 'gross_income', 'growth_per_turn', 'upkeep', 'cost',
    'refund', 'repair_cost', 'resources',
}

MEASURE_PATHS = {'ap_pct', 'hp', 'strength_pct', 'ts'}

INT_CLASS = ID_PATHS | COUNT_PATHS | MONEY_PATHS


class CollectError(ValueError):
    pass


def log(msg):
    sys.stderr.write('%.3f  canon %s\n' % (time.time(), msg))


def _to_int(path, value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise CollectError('fractional value at integer path %s: %r' % (path, value))
        return int(value)
    if isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            raise CollectError('non-numeric string at integer path %s: %r' % (path, value))
        if not f.is_integer():
            raise CollectError('fractional string at integer path %s: %r' % (path, value))
        return int(f)
    raise CollectError('unhandled type at integer path %s: %r' % (path, type(value)))


def normalise(node, path='', legacy=None):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            child = key if not path else path + '.' + key
            if key in WILD_DICTS and isinstance(value, dict):
                out[key] = {k: normalise(v, child + '{}', legacy) for k, v in value.items()}
                continue
            out[key] = normalise(value, child, legacy)
        return out
    if isinstance(node, list):
        return [normalise(v, path + '[]', legacy) for v in node]
    leaf = path.rsplit('.', 1)[-1].replace('[]', '').replace('{}', '')
    if node is None or isinstance(node, bool):
        return node
    if leaf in INT_CLASS:
        if legacy is not None and path not in legacy:
            legacy[path] = type(node).__name__
        return _to_int(path, node)
    if leaf in MEASURE_PATHS:
        if legacy is not None and path not in legacy:
            legacy[path] = type(node).__name__
        return float(node)
    return node


def legacy_view(node, types, path=''):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            child = k if not path else path + '.' + k
            if k in WILD_DICTS and isinstance(v, dict):
                out[k] = {kk: legacy_view(vv, types, child + '{}') for kk, vv in v.items()}
            else:
                out[k] = legacy_view(v, types, child)
        return out
    if isinstance(node, list):
        return [legacy_view(v, types, path + '[]') for v in node]
    want = types.get(path)
    if want is None or node is None or isinstance(node, bool):
        return node
    if want == 'float':
        return float(node)
    if want == 'str':
        return str(node)
    if want == 'int':
        if isinstance(node, float) and not node.is_integer():
            return node
        return int(node)
    return node


def canon(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':'), default=str)


def _tag(value):
    if value is None:
        return b'N'
    if isinstance(value, bool):
        return b'T' if value else b'F'
    if isinstance(value, int):
        return b'i' + struct.pack('>q', value)
    if isinstance(value, float):
        return b'd' + struct.pack('>d', value)
    if isinstance(value, str):
        raw = value.encode('utf-8')
        return b's' + struct.pack('>I', len(raw)) + raw
    if isinstance(value, (list, tuple)):
        return b'a' + struct.pack('>I', len(value)) + b''.join(_tag(v) for v in value)
    if isinstance(value, dict):
        items = sorted(value.items())
        return b'o' + struct.pack('>I', len(items)) + b''.join(
            _tag(k) + _tag(v) for k, v in items)
    raise CollectError('unencodable value %r' % (type(value),))


def enc(kind, members):
    body = SEP.join(b''.join(_tag(f) for f in member) for member in members)
    return struct.pack('>HI', kind, len(members)) + body


def set_hash(kind, members):
    return hashlib.sha256(enc(kind, members)).digest()
