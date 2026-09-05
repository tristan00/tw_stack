from __future__ import annotations

import hashlib
import struct
import sys
import time

from decisions import canon

MISSING = object()

KINDS = {
    'skills': 1, 'stances': 2, 'recruitable': 3, 'hidden_skills': 4, 'hidden_skill_states': 4,
    'traits': 5, 'trait_progress': 6, 'equipped': 7, 'anc_pool': 7, 'equipped_all': 7,
    'horde_slots': 8, 'merc_pools': 9, 'pending_queue': 10, 'effect_bundles': 11,
    'force_effect_bundles': 11, 'plague_bundles': 11, 'unit_cards': 12, 'buildable': 13,
    'slot_states': 14, 'built': 15, 'building_now': 16, 'resources': 17, 'corruption': 17,
    'hero_type_counts': 18, 'tech': 19, 'rites': 20, 'lord_pools': 21, 'missions': 22,
    'regions': 23, 'settlements': 24, 'ruins': 25, 'enemy_agents': 26, 'war_graph': 27,
    'relations': 28, 'stationed': 29,
}

UNKNOWN_KIND = 0


def log(msg):
    sys.stderr.write('%.3f  hydrate %s\n' % (time.time(), msg))


def is_collection(key, value):
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, dict) for v in value)
    if isinstance(value, dict):
        return key in canon.WILD_DICTS
    return False


def _tag(value):
    if value is MISSING:
        return b'M'
    return canon._tag(value)


def encode_members(kind, members):
    body = canon.SEP.join(b''.join(_tag(f) for f in member) for member in members)
    return struct.pack('>HI', kind, len(members)) + body


def set_hash(kind, members):
    return hashlib.sha256(encode_members(kind, members)).digest()


def encode_collection(key, value):
    kind = KINDS.get(key, UNKNOWN_KIND)
    if isinstance(value, list):
        fields = sorted({f for member in value for f in member})
        members = [tuple(member.get(f, MISSING) for f in fields) for member in value]
        shape = 'list'
        keys = None
    else:
        keys = list(value.keys())
        sample = [v for v in value.values() if isinstance(v, dict)]
        if len(sample) == len(value) and value:
            fields = sorted({f for v in value.values() for f in v})
            members = [tuple([k] + [value[k].get(f, MISSING) for f in fields]) for k in keys]
            shape = 'dict_of_dict'
        else:
            fields = []
            members = [(k, value[k]) for k in keys]
            shape = 'dict_of_scalar'
    return {'kind': kind, 'shape': shape, 'fields': fields, 'members': members,
            'hash': set_hash(kind, members), 'n': len(members)}


def decode_collection(enc):
    shape, fields, members = enc['shape'], enc['fields'], enc['members']
    if shape == 'list':
        out = []
        for member in members:
            out.append({f: v for f, v in zip(fields, member) if v is not MISSING})
        return out
    if shape == 'dict_of_dict':
        out = {}
        for member in members:
            out[member[0]] = {f: v for f, v in zip(fields, member[1:]) if v is not MISSING}
        return out
    return {k: v for k, v in members}


def encode_record(record):
    scalars, sets = {}, {}
    for key, value in record.items():
        if is_collection(key, value):
            sets[key] = encode_collection(key, value)
        else:
            scalars[key] = value
    return {'scalars': scalars, 'sets': sets}


def hydrate_record(encoded):
    out = dict(encoded['scalars'])
    for key, enc in encoded['sets'].items():
        out[key] = decode_collection(enc)
    return out


def roundtrip(record):
    return hydrate_record(encode_record(record))
