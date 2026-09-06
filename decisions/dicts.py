from __future__ import annotations

import sys
import time

ENUM_DOMAINS = {
    ('skill_set_member', 'status_id'): 'skill_status',
    ('merc_pool_set_member', 'action_id'): 'merc_action',
    ('mission_set_member', 'status_id'): 'mission_status',
    ('world_hostile', 'kind_id'): 'hostile_kind',
    ('snapshot', 'kind_id'): 'snapshot_kind',
    ('snapshot_entity', 'kind_id'): 'entity_kind',
    ('interrupt', 'state_at_id'): 'state_at',
    ('interrupt', 'kind_id'): 'interrupt_kind',
    ('interrupt', 'policy_id'): 'policy',
    ('interrupt', 'refusal_id'): 'refusal',
    ('taken', 'policy_id'): 'policy',
    ('taken', 'refusal_id'): 'refusal',
    ('taken', 'failed_precheck_id'): 'precheck',
    ('campaign', 'outcome_id'): 'outcome',
    ('diplomacy_event', 'kind_id'): 'diplo_event_kind',
    ('diplomacy_event', 'channel_id'): 'diplo_channel',
    ('postmortem', 'outcome_id'): 'outcome',
}

ARRAY_FAMILIES = {
    ('region_set_member', 'adjacent'): 'region',
    ('char_state', 'hidden_skill_ids'): 'skill',
    ('char_state', 'pending_recruit_unit_ids'): 'unit',
    ('char_state_ext', 'armory_item_ids'): 'armory_item',
    ('province_state', 'edict_ids'): 'edict',
    ('lord_pool_candidate', 'trait_ids'): 'trait',
    ('war_graph_set_member', 'at_war_with'): 'faction',
    ('char_state', 'reach_setts_true'): 'region',
    ('snapshot_world', 'diplo_unseen'): 'faction',
    ('snapshot_world', 'reach_region_ids'): 'region',
    ('diplomacy_event', 'tracked_faction_ids'): 'faction',
}

FAMILY_SQL = """
SELECT c.relname AS src_table, a.attname AS src_column, cl.relname AS dict_table
  FROM pg_constraint k
  JOIN pg_class c ON c.oid = k.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_class cl ON cl.oid = k.confrelid
  JOIN pg_namespace fn ON fn.oid = cl.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.conkey[1]
 WHERE k.contype = 'f' AND n.nspname = 'corpus' AND fn.nspname = 'dict'
   AND array_length(k.conkey, 1) = 1
"""


def log(msg):
    sys.stderr.write('%.3f  dicts %s\n' % (time.time(), msg))


class Dicts:

    def __init__(self, con):
        self.con = con
        self.cache = {}
        self.enum_cache = {}
        self.rcache = {}
        self.families = {}
        self.has_is_reference = set()
        self.load_families()

    def load_families(self):
        t0 = time.time()
        for table, column, target in self.con.execute(FAMILY_SQL):
            self.families[(table, column)] = target
        for (name,) in self.con.execute(
                "SELECT table_name FROM information_schema.columns"
                " WHERE table_schema = 'dict' AND column_name = 'is_reference'"):
            self.has_is_reference.add(name)
        log('load_families exit %.0f ms %d columns %d reference dictionaries'
            % ((time.time() - t0) * 1000, len(self.families),
               len(self.has_is_reference)))

    def family_of(self, table, column):
        target = self.families.get((table, column))
        if target is None:
            return None
        if target == 'enum':
            return ('enum', ENUM_DOMAINS[(table, column)])
        return ('family', target)

    def resolve(self, family, keys):
        cache = self.cache.setdefault(family, {})
        want = {k for k in keys if k is not None and k not in cache}
        if want:
            found = set()
            for key, ident in self.con.execute(
                    "SELECT key, id FROM dict.%s WHERE key = ANY(%%s)" % family,
                    (sorted(want),)):
                cache[key] = ident
                found.add(key)
            missing = sorted(want - found)
            if missing:
                if family in self.has_is_reference:
                    sql = ("INSERT INTO dict.%s (key, is_reference)"
                           " SELECT k, false FROM unnest(%%s::text[]) AS k"
                           " ON CONFLICT (key) DO NOTHING RETURNING key, id" % family)
                else:
                    sql = ("INSERT INTO dict.%s (key)"
                           " SELECT k FROM unnest(%%s::text[]) AS k"
                           " ON CONFLICT (key) DO NOTHING RETURNING key, id" % family)
                for key, ident in self.con.execute(sql, (missing,)):
                    cache[key] = ident
                    self.rcache.get(('family', family), {})[ident] = key
                still = [k for k in missing if k not in cache]
                if still:
                    for key, ident in self.con.execute(
                            "SELECT key, id FROM dict.%s WHERE key = ANY(%%s)" % family,
                            (still,)):
                        cache[key] = ident
        return cache

    def resolve_enum(self, domain, keys):
        cache = self.enum_cache.setdefault(domain, {})
        want = {k for k in keys if k is not None and k not in cache}
        if want:
            for key, ident in self.con.execute(
                    "SELECT key, enum_id FROM dict.enum WHERE domain = %s AND key = ANY(%s)",
                    (domain, sorted(want))):
                cache[key] = ident
            missing = sorted(k for k in want if k not in cache)
            if missing:
                raise KeyError('dict.enum domain %r has no rows for %r'
                               % (domain, missing))
        return cache

    def _reverse(self, kind, name):
        ck = (kind, name)
        m = self.rcache.get(ck)
        if m is None:
            t0 = time.time()
            if kind == 'enum':
                rows = self.con.execute("SELECT enum_id, key FROM dict.enum")
            else:
                rows = self.con.execute("SELECT id, key FROM dict.%s" % name)
            m = self.rcache[ck] = {i: k for i, k in rows}
            log('reverse %s.%s loaded %d keys %.0f ms'
                % (kind, name, len(m), (time.time() - t0) * 1000))
        return m

    def keys_for(self, table, column, ids):
        spec = self.family_of(table, column)
        if spec is None:
            return None
        kind, name = spec
        m = self._reverse(kind, name if kind == 'family' else 'enum')
        return {i: m[i] for i in ids if i is not None and i in m}

    def keys_for_family(self, family, ids):
        m = self._reverse('family', family)
        return {i: m[i] for i in ids if i is not None and i in m}

    def ids_for(self, table, column, keys):
        spec = self.family_of(table, column)
        if spec is None:
            return None
        kind, name = spec
        if kind == 'enum':
            return self.resolve_enum(name, keys)
        return self.resolve(name, keys)
