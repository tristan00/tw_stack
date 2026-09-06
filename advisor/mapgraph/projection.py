from __future__ import annotations

import time

from decisions import dicts, rowmap, schema_map


FIELDS = {
    "snapshot_campaign": "faction turn income settlements treasury armies lord_level allies vassals power_rank campaign_map",
    "snapshot_world": "citizenry",
    "world_army": "cqi subtype agent_type is_leader has_army rank x y ap_pct stance hp units region province in_own_territory",
    "world_hostile": "kind faction visible cqi subtype agent_type province region x y is_armed_citizenry units hp stance",
    "char_state": "rank skill_points units pending_recruits ap_pct hp stance subtype region x y garrisoned besieging acted is_leader agent_type hidden_skills pending_recruit_keys",
    "province_state": "region province max_slots free_slots can_set_edict selected_edict active_edict public_order settlement_level growth_per_turn income locked_slots",
    "campaign_state": "current_research",
}

COLLECTIONS = {
    "snapshot_world": "regions settlements ruins enemy_agents war_graph relations",
    "char_state": "skills unit_cards equipped recruitable pending_queue horde_slots merc_pools",
    "province_state": "built building_now corruption buildable slot_states",
    "campaign_state": "tech anc_pool equipped_all",
}

MEMBER_FIELDS = {
    "skills": "key status", "unit_cards": "key", "equipped": "key name",
    "recruitable": "key cost state", "pending_queue": "key turns_left",
    "horde_slots": "slot_index slot_id key empty", "merc_pools": "{key} key avail cost",
    "built": "{key} {value}", "building_now": "{key} key",
    "corruption": "{key} {value}", "buildable": "slot_index key cost",
    "slot_states": "index key repair_cost", "tech": "key researched can_research cost",
    "anc_pool": "key name", "equipped_all": "key",
}


def _select(table, columns, families, alias="p"):
    expressions, joins = [], []
    for column in columns:
        field = "%s.%s" % (alias, column)
        family = families.get((table, column))
        array_family = dicts.ARRAY_FAMILIES.get((table, column))
        if array_family:
            expressions.append(
                "CASE WHEN %s IS NULL THEN NULL ELSE ARRAY(SELECT d.key FROM"
                " unnest(%s) WITH ORDINALITY a(id, n) LEFT JOIN dict.%s d ON d.id=a.id"
                " ORDER BY a.n) END" % (field, field, array_family))
        elif family:
            name = "d%d" % len(joins)
            ident = "enum_id" if family == "enum" else "id"
            joins.append(" LEFT JOIN dict.%s %s ON %s.%s=%s"
                         % (family, name, name, ident, field))
            expressions.append(name + ".key")
        else:
            expressions.append(field)
    return expressions, "".join(joins)


class Projection:

    def __init__(self, con):
        self.con = con
        self.families = {(t, c): f for t, c, f in con.execute(dicts.FAMILY_SQL)}
        self.query_seconds = 0.0
        self.query_rows = 0

    def _rows(self, sql, params):
        started = time.perf_counter()
        cursor = self.con.execute(sql, params)
        self.query_seconds += time.perf_counter() - started
        self.query_rows += max(0, cursor.rowcount)
        return cursor

    def read(self, heads):
        from advisor.mapgraph.project_offers import offer_params
        started = time.perf_counter()
        ids = [h[0] for h in heads]
        records = {h[0]: {"decision_id": h[0], "campaign_id": h[10],
                          "campaign": {}, "world": {}, "entities": []} for h in heads}
        entities = {}
        links = {}
        for sid, seq, kind, cqi, region in self._rows(
                "SELECT se.snapshot_id,se.entity_seq,k.key,ch.cqi,r.key"
                " FROM corpus.snapshot_entity se JOIN dict.enum k ON k.enum_id=se.kind_id"
                " LEFT JOIN corpus.character ch ON ch.character_id=se.character_id"
                " LEFT JOIN dict.region r ON r.id=se.region_id"
                " WHERE se.snapshot_id=ANY(%s) ORDER BY se.snapshot_id,se.entity_seq", (ids,)):
            cid = str(cqi) if kind in ("lord", "hero") else region
            e = {"context_kind": kind, "context_id": cid, "state": {}, "offers": []}
            entities[(sid, seq)] = e
            records[sid]["entities"].append(e)
        for table, fields in FIELDS.items():
            wanted = set(fields.split())
            columns = [c for c, f in rowmap.TABLES[table].items() if f in wanted]
            names = [rowmap.TABLES[table][c] for c in columns]
            extra = []
            if table == "char_state":
                extra = ["reach_chars_true", "reach_setts_true", "move_x", "move_y"]
            columns += extra
            names += extra
            set_fields = [(c, f[len(rowmap.SET):]) for c, f in rowmap.TABLES[table].items()
                          if f and f.startswith(rowmap.SET)
                          and f[len(rowmap.SET):] in COLLECTIONS.get(table, "").split()]
            columns += [c for c, _ in set_fields]
            expr, joins = _select(table, columns, self.families)
            entity = table in ("char_state", "province_state", "campaign_state")
            key = "p.entity_seq" if entity else "0"
            order = ",p.ord" if table in ("world_army", "world_hostile") else ""
            sql = ("SELECT p.snapshot_id,%s,%s FROM corpus.%s p%s"
                   " WHERE p.snapshot_id=ANY(%%s) ORDER BY p.snapshot_id%s"
                   % (key, ",".join(expr), table, joins, order))
            for sid, seq, *values in self._rows(sql, (ids,)):
                row = dict(zip(names, values[:len(names)]))
                for (_, collection), set_id in zip(set_fields, values[len(names):]):
                    if set_id is not None:
                        links.setdefault((table, collection), []).append((sid, seq, set_id))
                rec = records[sid]
                if entity:
                    entities[(sid, seq)]["state"].update(row)
                elif table == "snapshot_campaign":
                    rec["campaign"] = row
                    rec["turn"] = row["turn"]
                elif table == "snapshot_world":
                    rec["world"].update(row)
                else:
                    name = "armies" if table == "world_army" else "hostiles"
                    if name == "hostiles" and row["kind"] == "settlement":
                        row = {k: row[k] for k in ("kind", "faction", "region", "x", "y", "units")}
                    rec["world"].setdefault(name, []).append(row)
        for table, keys in COLLECTIONS.items():
            entity = table in ("char_state", "province_state", "campaign_state")
            for key in keys.split():
                _, member, shape, mapping = schema_map.COLLECTIONS[key]
                wanted = set(MEMBER_FIELDS[key].split()) if key in MEMBER_FIELDS else set(mapping.values())
                columns = [c for c, f in mapping.items() if f in wanted]
                names = [mapping[c] for c in columns]
                expr, joins = _select(member, columns, self.families, "m")
                parents = links.get((table, key), ())
                set_ids = sorted({set_id for _, _, set_id in parents})
                if not set_ids:
                    continue
                sql = ("SELECT m.set_id,%s FROM corpus.%s m%s WHERE m.set_id=ANY(%%s)"
                       " ORDER BY m.set_id,m.ord") % (",".join(expr), member, joins)
                members = {}
                for set_id, *values in self._rows(sql, (set_ids,)):
                    row = dict(zip(names, values))
                    if shape == schema_map.LIST:
                        members.setdefault(set_id, []).append(row)
                    else:
                        group = members.setdefault(set_id, {})
                        name = row.pop("{key}")
                        if shape == schema_map.DICT_SCALAR:
                            group[name] = row["{value}"]
                        elif shape == schema_map.DICT_DICT:
                            group[name] = row
                        elif shape == schema_map.DICT_LIST:
                            group.setdefault(name, []).append(row)
                for sid, eseq, set_id in parents:
                    if set_id in members:
                        target = entities[(sid, eseq)]["state"] if entity else records[sid]["world"]
                        target[key] = members[set_id]
        for rec in records.values():
            world = rec["world"]
            chars = {r.get("cqi") for r in world.get("armies", []) + world.get("hostiles", [])} - {None}
            regions = {r.get("region") for r in world.get("settlements", []) + world.get("ruins", [])}
            regions.update(r.get("region") for r in world.get("hostiles", []) if r["kind"] == "settlement")
            for e in rec["entities"]:
                st = e["state"]
                if e["context_kind"] == "campaign":
                    e["context_id"] = rec["campaign"]["faction"]
                if e["context_kind"] in ("lord", "hero"):
                    true_chars = set(st.pop("reach_chars_true") or [])
                    true_regions = set(st.pop("reach_setts_true") or [])
                    st["reach_chars"] = {str(k): True for k in sorted(chars & true_chars)}
                    st["reach_setts"] = {k: True for k in sorted((regions - {None}) & true_regions)}
        previous = None
        for sid, seq, eseq, at, key, slot, action_key, ability in self._rows(
                "SELECT o.decision_id,o.offer_seq,o.entity_seq,t.key,a.action_key,o.slot_index,h.key,h.ability"
                " FROM corpus.offer o JOIN dict.action a ON a.action_id=o.action_id"
                " JOIN dict.action_type t ON t.id=a.action_type_id"
                " LEFT JOIN corpus.char_state cs ON cs.snapshot_id=o.decision_id AND cs.entity_seq=o.entity_seq"
                " LEFT JOIN dict.agent_type ag ON ag.id=cs.agent_type_id"
                " LEFT JOIN refc.agent_actions h ON t.key='hero_action' AND h.agent=ag.key"
                " AND h.key LIKE '%%assist_army_' || split_part(a.action_key,'@',1)"
                " WHERE o.decision_id=ANY(%s) ORDER BY o.decision_id,o.offer_seq,h.key", (ids,)):
            e = entities[(sid, eseq)]
            try:
                params = offer_params(records[sid], e, at, key, slot, action_key, ability)
            except (StopIteration, KeyError, IndexError, ValueError) as error:
                raise ValueError("graph projection decision %s offer %s (%s, %s): %s"
                                 % (sid, seq, at, key, error)) from error
            if previous == (sid, seq):
                e["offers"].pop()
            e["offers"].append({"action_type": at, "key": key, "params": params})
            previous = (sid, seq)
        return records, time.perf_counter() - started
