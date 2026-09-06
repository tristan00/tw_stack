from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import time
from collections import OrderedDict

from decisions import canon
from decisions import dicts as dicts_mod
from decisions import rowmap, schema_map

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


_LEGACY_TYPES = [None]


def legacy_types():
    if _LEGACY_TYPES[0] is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'legacy_types.json')
        with open(path, encoding='utf-8') as fh:
            _LEGACY_TYPES[0] = json.load(fh)
    return _LEGACY_TYPES[0]


_DICTS = {}


def _dicts(con):
    d = _DICTS.get(id(con))
    if d is None:
        d = _DICTS[id(con)] = dicts_mod.Dicts(con)
    return d


SET_CACHE_MAX = 50000
_SET_CACHE = OrderedDict()


def _copy_container(v):
    if isinstance(v, list):
        return list(v)
    return dict(v)


def _set_value(con, dc, key, set_id):
    from decisions import sets
    kind = schema_map.COLLECTIONS[key][0]
    ck = (kind, set_id)
    hit = _SET_CACHE.get(ck)
    if hit is None:
        hit = sets.read_collection(con, dc, key, set_id)
        if isinstance(hit, dict):
            hit = {k: hit[k] for k in sorted(hit)}
        _SET_CACHE[ck] = hit
        if len(_SET_CACHE) > SET_CACHE_MAX:
            _SET_CACHE.popitem(last=False)
    else:
        _SET_CACHE.move_to_end(ck)
    return _copy_container(hit)


PREFETCH_CHUNK = 500

_PREFETCH_TABLES = {
    'snapshot_campaign': None, 'snapshot_world': None,
    'world_army': 'ord', 'world_hostile': 'ord',
    'char_state': 'entity_seq', 'char_state_ext': 'character_id',
    'province_state': 'entity_seq', 'campaign_state': 'entity_seq',
}


class Prefetch:

    def __init__(self, con, ids, include_offers=True):
        t0 = time.time()
        ids = sorted({int(i) for i in ids})
        self.data = {}
        for table, key in _PREFETCH_TABLES.items():
            cols = sorted(rowmap.TABLES[table])
            sel = ['snapshot_id'] + ([key] if key else []) + cols
            order = " ORDER BY snapshot_id" + (", ord" if key == 'ord' else "")
            skip = len(sel) - len(cols)
            for r in con.execute(
                    "SELECT %s FROM corpus.%s WHERE snapshot_id = ANY(%%s)%s"
                    % (', '.join(sel), table, order), (ids,)):
                row = dict(zip(cols, r[skip:]))
                k = (table, r[0], r[1]) if key and key != 'ord' else (table, r[0])
                self.data.setdefault(k, []).append(row)
        self.head = {sid: (turn, ckey, kind) for sid, turn, ckey, kind in con.execute(
            "SELECT s.snapshot_id, s.turn, c.campaign_key, e.key FROM corpus.snapshot s"
            " JOIN corpus.campaign c ON c.campaign_id = s.campaign_id"
            " JOIN dict.enum e ON e.enum_id = s.kind_id"
            " WHERE s.snapshot_id = ANY(%s)", (ids,))}
        self.failures = {}
        for sid, msg, n in con.execute(
                "SELECT snapshot_id, message, n FROM corpus.snapshot_read_failure"
                " WHERE snapshot_id = ANY(%s) ORDER BY snapshot_id, message", (ids,)):
            self.failures.setdefault(sid, {})[msg] = n
        self.entities = {}
        for sid, seq, kind_id, character_id, region_id, cqi in con.execute(
                "SELECT se.snapshot_id, se.entity_seq, se.kind_id, se.character_id,"
                " se.region_id, ch.cqi FROM corpus.snapshot_entity se"
                " LEFT JOIN corpus.character ch ON ch.character_id = se.character_id"
                " WHERE se.snapshot_id = ANY(%s) ORDER BY se.snapshot_id, se.entity_seq",
                (ids,)):
            self.entities.setdefault(sid, []).append(
                (seq, kind_id, character_id, region_id, cqi))
        self.offers = {}
        if not include_offers:
            log('prefetch exit %.1f ms ids=%d' % ((time.time() - t0) * 1000, len(ids)))
            return
        for row in con.execute(
                "SELECT o.decision_id, o.offer_seq, o.entity_seq, ty.key, a.action_key,"
                " o.slot_index, o.score, o.exploit, o.rank FROM corpus.offer o"
                " JOIN dict.action a ON a.action_id = o.action_id"
                " JOIN dict.action_type ty ON ty.id = a.action_type_id"
                " WHERE o.decision_id = ANY(%s) ORDER BY o.decision_id, o.offer_seq",
                (ids,)):
            self.offers.setdefault(row[0], []).append(row[1:])
        log('prefetch exit %.1f ms ids=%d' % ((time.time() - t0) * 1000, len(ids)))

    def rows(self, table, snapshot_id, key=None):
        if _PREFETCH_TABLES[table] in (None, 'ord'):
            k = (table, snapshot_id)
        else:
            k = (table, snapshot_id, key)
        return self.data.get(k, [])


def _invert(con, dc, table, colvals, out=None):
    spec = rowmap.TABLES[table]
    out = {} if out is None else out
    set_ids = {}
    for col, src in spec.items():
        if src is None:
            continue
        v = colvals.get(col)
        if src.startswith(rowmap.SET):
            set_ids[src[len(rowmap.SET):]] = v
            continue
        if v is not None:
            fam = dc.family_of(table, col)
            if fam is not None:
                v = dc.keys_for(table, col, [v]).get(v)
            arr = dicts_mod.ARRAY_FAMILIES.get((table, col))
            if arr is not None:
                back = dc.keys_for_family(arr, v)
                v = [back.get(i) for i in v]
        out[src] = v
    return out, set_ids


def _attach_sets(con, dc, out, set_ids, always=()):
    for key, sid in set_ids.items():
        if sid is not None:
            out[key] = _set_value(con, dc, key, sid)
        elif key in always:
            out[key] = None
    return out


def _campaign_dict(con, dc, snapshot_id, campaign_key, pre):
    rows = pre.rows('snapshot_campaign', snapshot_id)
    if not rows:
        return None
    out, set_ids = _invert(con, dc, 'snapshot_campaign', rows[0])
    _attach_sets(con, dc, out, set_ids)
    for key in ('_eval_ms', 'difficulty', 'leader'):
        if out.get(key) is None:
            out.pop(key, None)
    if out.get('selector') is None and out.get('difficulty') is None:
        out.pop('selector', None)
    out['campaign_uuid'] = campaign_key if campaign_key != out.get('faction') else None
    out['read_failures'] = pre.failures.get(snapshot_id, {})
    return out


_HOSTILE_ARMY_KEYS = ('cqi', 'dist', 'faction', 'hp', 'is_armed_citizenry', 'kind',
                      'province', 'stance', 'units', 'visible', 'x', 'y')
_HOSTILE_HERO_KEYS = ('agent_type', 'cqi', 'dist', 'faction', 'kind', 'province',
                      'subtype', 'visible', 'x', 'y')
HOSTILE_KEYS = {
    'settlement': ('dist', 'faction', 'kind', 'region', 'units', 'x', 'y'),
    'army': _HOSTILE_ARMY_KEYS, 'neutral_army': _HOSTILE_ARMY_KEYS,
    'hero': _HOSTILE_HERO_KEYS, 'neutral_hero': _HOSTILE_HERO_KEYS,
}


def _hostile(m):
    keys = HOSTILE_KEYS.get(m.get('kind'))
    if keys is None:
        return m
    return {k: m.get(k) for k in keys}


def _world_dict(con, dc, snapshot_id, pre):
    rows = pre.rows('snapshot_world', snapshot_id)
    armies = [_invert(con, dc, 'world_army', r)[0]
              for r in pre.rows('world_army', snapshot_id)]
    hostiles = [_hostile(_invert(con, dc, 'world_hostile', r)[0])
                for r in pre.rows('world_hostile', snapshot_id)]
    if not rows:
        return {}
    out, set_ids = _invert(con, dc, 'snapshot_world', rows[0])
    if all(v is None for v in out.values()) and all(
            v is None for v in set_ids.values()) and not armies and not hostiles:
        return {}
    _attach_sets(con, dc, out, set_ids)
    out['armies'] = armies
    out['hostiles'] = hostiles
    return out


CHAR_LORD_ONLY = ('horde_slots', 'merc_pools', 'recruitable', 'stances')
CHAR_HERO_ONLY = ('agent_type', 'can_embed', 'is_agent')


def _char_state(con, dc, snapshot_id, entity_seq, character_id, cqi, world, pre):
    colvals = pre.rows('char_state', snapshot_id, entity_seq)[0]
    is_hero = bool(colvals.get('is_hero'))
    out, set_ids = _invert(con, dc, 'char_state', colvals)
    out['cqi'] = cqi
    if is_hero:
        for key in CHAR_LORD_ONLY:
            set_ids.pop(key, None)
    else:
        for key in CHAR_HERO_ONLY:
            out.pop(key, None)
    _attach_sets(con, dc, out, set_ids,
                 always=() if is_hero else ('horde_slots',))
    xs, ys = colvals.get('move_x') or [], colvals.get('move_y') or []
    tiles = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        tile = {'x': x, 'y': y, 'sample_index': i}
        if colvals.get('reach_rays') is not None:
            tile['reach_rays'] = colvals['reach_rays']
        if colvals.get('reach_max') is not None:
            tile['reach_max'] = colvals['reach_max']
        tiles.append(tile)
    out['move_tiles'] = tiles
    true_chars = set(colvals.get('reach_chars_true') or [])
    char_cqis = ({a.get('cqi') for a in world.get('armies') or []}
                 | {h.get('cqi') for h in world.get('hostiles') or []}) - {None}
    out['reach_chars'] = {str(cqi): cqi in true_chars for cqi in sorted(char_cqis)}
    rst = colvals.get('reach_setts_true') or []
    true_setts = set(dc.keys_for_family('region', rst).get(i) for i in rst)
    sett_keys = ({s.get('region') for s in world.get('settlements') or []}
                 | {r.get('region') for r in world.get('ruins') or []}
                 | {h.get('region') for h in world.get('hostiles') or []
                    if h.get('kind') == 'settlement' and h.get('region')})
    out['reach_setts'] = {k: k in true_setts for k in sorted(sett_keys - {None})}
    ext = pre.rows('char_state_ext', snapshot_id, character_id)
    if ext:
        eout, eset_ids = _invert(con, dc, 'char_state_ext', ext[0])
        if ext[0].get('armory_item_ids') is None:
            eout = {}
        out.update(eout)
        _attach_sets(con, dc, out, eset_ids)
    return out


def _province_state(con, dc, snapshot_id, entity_seq, pre):
    out, set_ids = _invert(con, dc, 'province_state',
                           pre.rows('province_state', snapshot_id, entity_seq)[0])
    return _attach_sets(con, dc, out, set_ids)


def _campaign_state(con, dc, snapshot_id, entity_seq, campaign, pre):
    out = {k: v for k, v in campaign.items() if k != 'read_failures'}
    sout, set_ids = _invert(con, dc, 'campaign_state',
                            pre.rows('campaign_state', snapshot_id, entity_seq)[0])
    out.update(sout)
    return _attach_sets(con, dc, out, set_ids)


def _entities(con, dc, snapshot_id, campaign, world, pre):
    ents = pre.entities.get(snapshot_id, [])
    kinds = dc.keys_for('snapshot_entity', 'kind_id', [e[1] for e in ents])
    out = []
    for seq, kind_id, character_id, region_id, cqi in ents:
        kind = kinds[kind_id]
        if kind in ('lord', 'hero'):
            context_id = cqi
            state = _char_state(con, dc, snapshot_id, seq, character_id, cqi, world,
                                pre)
        elif kind == 'province':
            state = _province_state(con, dc, snapshot_id, seq, pre)
            context_id = state.get('region')
        else:
            state = _campaign_state(con, dc, snapshot_id, seq, campaign, pre)
            context_id = state.get('faction')
        out.append({'snapshot_id': snapshot_id * MAX_ENTITIES + seq,
                    'context_kind': kind, 'context_id': context_id,
                    'state': state, 'offers': []})
    return out


MAX_ENTITIES = 64


ICB_KEYS = ('_eval_ms', 'allies', 'armies', 'campaign_uuid', 'defeated',
            'difficulty', 'faction', 'faction_cqi', 'game_version', 'income',
            'is_researching', 'leader', 'll_wounded', 'lord_level', 'power_rank',
            'settlements', 'treasury', 'turn', 'vassals')
IWB_KEYS = ('armies', 'enemy_agents', 'hostiles', 'regions', 'ruins', 'settlements')


def record(con, snapshot_id, legacy=True, pre=None):
    t0 = time.time()
    dc = _dicts(con)
    if pre is None:
        pre = Prefetch(con, [snapshot_id])
    head = pre.head.get(snapshot_id)
    if head is None:
        raise KeyError('snapshot %s not in the store' % snapshot_id)
    turn, campaign_key, kind = head
    campaign = _campaign_dict(con, dc, snapshot_id, campaign_key, pre) or {}
    world = _world_dict(con, dc, snapshot_id, pre)
    if kind == 'interrupt':
        campaign = {k: campaign[k] for k in ICB_KEYS if k in campaign}
        world = {k: world[k] for k in IWB_KEYS if k in world}
        for a in world.get('armies') or ():
            a.pop('ap_per_turn', None)
            a.pop('ap_remaining', None)
    entities = _entities(con, dc, snapshot_id, campaign, world, pre)
    if legacy:
        types = legacy_types()
        campaign = canon.legacy_view(campaign, types['CB'])
        world = canon.legacy_view(world, types['WB'])
        for e in entities:
            e['state'] = canon.legacy_view(e['state'], types['EB'])
            if e['context_kind'] in ('lord', 'hero') and e['context_id'] is not None:
                e['context_id'] = str(e['context_id'])
    log('record exit %.1f ms snapshot_id=%s entities=%d'
        % ((time.time() - t0) * 1000, snapshot_id, len(entities)))
    return {'decision_id': snapshot_id, 'turn': turn, 'campaign_id': campaign_key,
            'campaign': campaign, 'world': world, 'entities': entities}


class OfferDriftError(RuntimeError):

    def __init__(self, decision_id, offer_seq, identity):
        super().__init__('stored offer %s of decision %s has no generated match: %s'
                         % (offer_seq, decision_id, identity))
        self.decision_id = decision_id
        self.offer_seq = offer_seq


SYNTHETIC_ACTIONS = ('noop', 'end_turn')

MAX_OFFERS = 1048576


def _options_mod():
    import common
    if common.ADVISOR not in sys.path:
        sys.path.insert(0, common.ADVISOR)
    import options
    return options


def _generated_index(record):
    idx = {}
    for ck, cid, o in _options_mod().generate(record):
        idx.setdefault((ck, str(cid), o.get('action_type'), str(o.get('key'))),
                       []).append(o)
    return idx


def _pick_generated(cands, slot_index):
    if not cands:
        return None
    if slot_index is None:
        return cands.pop(0)
    for i, o in enumerate(cands):
        if (o.get('params') or {}).get('slot_index') == slot_index:
            return cands.pop(i)
    return None


def _stored_offer_rows(con, decision_id):
    return con.execute(
        "SELECT o.offer_seq, o.entity_seq, ty.key, a.action_key, o.slot_index,"
        " o.score, o.exploit, o.rank FROM corpus.offer o"
        " JOIN dict.action a ON a.action_id = o.action_id"
        " JOIN dict.action_type ty ON ty.id = a.action_type_id"
        " WHERE o.decision_id = %s ORDER BY o.offer_seq", (decision_id,)).fetchall()


def offers(con, record, pre=None):
    did = record['decision_id']
    rows = pre.offers.get(did, []) if pre is not None else _stored_offer_rows(con, did)
    if not rows:
        return record
    ents = record.get('entities') or []
    idx = _generated_index(record)
    for seq, eseq, at, ak, slot, score, exploit, rank in rows:
        if eseq is None or eseq >= len(ents):
            raise OfferDriftError(did, seq, (eseq, at, ak))
        e = ents[eseq]
        entry = {'offer_id': did * MAX_OFFERS + seq, 'action_type': at, 'key': ak,
                 'params': {}, 'score': score, 'exploit': exploit, 'rank': rank}
        if at not in SYNTHETIC_ACTIONS:
            got = _pick_generated(
                idx.get((e['context_kind'], str(e['context_id']), at, str(ak))), slot)
            if got is None:
                raise OfferDriftError(did, seq, (e['context_kind'],
                                                 e['context_id'], at, ak))
            entry['params'] = got.get('params') or {}
        e['offers'].append(entry)
    return record


def attach_taken(con, record, entity_seq, action_type, key):
    ents = record.get('entities') or []
    if entity_seq is None or entity_seq >= len(ents):
        return record
    e = ents[entity_seq]
    entry = {'action_type': action_type, 'key': key, 'params': {}}
    if action_type not in SYNTHETIC_ACTIONS:
        for ck, cid, o in _options_mod().generate(record):
            if (ck == e['context_kind'] and cid == str(e['context_id'])
                    and o.get('action_type') == action_type
                    and str(o.get('key')) == str(key)):
                entry['params'] = o.get('params') or {}
                break
    e['offers'].append(entry)
    return record


def records(con, lo, hi, kind='decision', legacy=True):
    dc = _dicts(con)
    kind_id = dc.resolve_enum('snapshot_kind', [kind])[kind]
    ids = [r[0] for r in con.execute(
        "SELECT snapshot_id FROM corpus.snapshot WHERE snapshot_id BETWEEN %s AND %s"
        " AND kind_id = %s ORDER BY snapshot_id", (lo, hi, kind_id))]
    for i in range(0, len(ids), PREFETCH_CHUNK):
        chunk = ids[i:i + PREFETCH_CHUNK]
        pre = Prefetch(con, chunk)
        for snapshot_id in chunk:
            yield record(con, snapshot_id, legacy=legacy, pre=pre)
