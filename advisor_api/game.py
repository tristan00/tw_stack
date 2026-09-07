import json

from advisor_api import db, labels, queries


RECORDS = {
    'turns': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, sc.settlements, sc.treasury, sc.income, sc.armies, sc.allies FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.snapshot_campaign sc USING(snapshot_id)",
    'research': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, k.key AS technology, m.researched, m.can_research, m.cost FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.campaign_state cs USING(snapshot_id) JOIN corpus.tech_set_member m ON m.set_id=cs.tech_set_id JOIN dict.tech_node k ON k.id=m.tech_node_id",
    'skills': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, cs.character_id, k.key AS skill, m.level AS rank, m.total_levels AS max_ranks FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.char_state cs USING(snapshot_id) JOIN corpus.skill_set_member m ON m.set_id=cs.skill_set_id JOIN dict.skill k ON k.id=m.skill_id",
    'items': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, cs.character_id, k.key AS item, m.name AS name FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.char_state cs USING(snapshot_id) JOIN corpus.item_slot_set_member m ON m.set_id=cs.equipped_set_id JOIN dict.ancillary k ON k.id=m.ancillary_id",
    'regions': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, k.key AS region, f.key AS owner, m.owner_id=c.faction_id AS owned, m.x, m.y FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.snapshot_world w USING(snapshot_id) JOIN corpus.region_set_member m ON m.set_id=w.region_set_id JOIN dict.region k ON k.id=m.region_id LEFT JOIN dict.faction f ON f.id=m.owner_id",
    'buildings': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, r.key AS region, k.key AS building, m.slot_index FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.province_state ps USING(snapshot_id) JOIN dict.region r ON r.id=ps.region_id JOIN corpus.built_set_member m ON m.set_id=ps.built_set_id JOIN dict.building k ON k.id=m.building_id",
    'diplomacy': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, f.key AS faction, m.standing, m.at_war, m.allied, m.trade FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.snapshot_world w USING(snapshot_id) JOIN corpus.relation_set_member m ON m.set_id=w.relation_set_id JOIN dict.faction f ON f.id=m.faction_id",
    'armies': "SELECT s.snapshot_id AS id, c.campaign_key AS campaign, c.leader, s.turn, s.ts AS recorded_at, w.cqi, w.units, w.hp, w.upkeep FROM corpus.snapshot s JOIN corpus.campaign c USING(campaign_id) JOIN corpus.world_army w USING(snapshot_id)",
    'events': "SELECT e.event_id AS id, c.campaign_key AS campaign, c.leader, e.turn, e.ts AS recorded_at, e.kind, e.choice FROM corpus.event e JOIN corpus.campaign c USING(campaign_id)",
}


@db.timed
def records(con, subject, limit=100, before=None, search=None):
    source = RECORDS.get(subject)
    if source is None:
        return None
    where = []
    args = []
    if before is not None:
        where.append('id < %s')
        args.append(before)
    if search:
        where.append('campaign ILIKE %s')
        args.append('%' + search + '%')
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    query = 'WITH records AS (' + source + ') SELECT * FROM records' + clause + ' ORDER BY id DESC LIMIT %s'
    rows = _rows(con, query, (*args, limit + 1))
    has_more = len(rows) > limit
    if has_more:
        boundary = rows[limit]['id']
        rows = [r for r in rows[:limit] if r['id'] > boundary]
        if not rows:
            rows = _rows(con, 'WITH records AS (' + source + ') SELECT * FROM records WHERE id=%s', (boundary,))
    return dict(subject=subject, rows=rows, before=rows[-1]['id'] if has_more and rows else None,
                scope='Newest recorded snapshots first; rows are observations, not unique acquisitions.')


@db.timed
def database(con):
    return _rows(con, """
        SELECT n.nspname AS origin, c.relname AS name,
               GREATEST(c.reltuples,0)::bigint AS estimated_rows, st.n_rows,
               st.last_write, st.refreshed,
               pg_total_relation_size(c.oid) AS bytes,
               (SELECT count(*) FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN ops.db_table_stat st ON st.tbl=n.nspname || '.' || c.relname
        WHERE n.nspname IN ('corpus','analytics','ops','dict','ref','refc') AND c.relkind='r'
        ORDER BY pg_total_relation_size(c.oid) DESC
    """)


@db.timed
def database_columns(con, schema, table):
    if schema not in ('corpus','analytics','ops','dict','ref','refc'):
        return None
    return _rows(con, """
        SELECT col.column_name AS name, col.data_type AS type, col.is_nullable AS nullable,
               st.null_frac, st.n_distinct, st.sample, st.refreshed
        FROM information_schema.columns col
        LEFT JOIN ops.db_column_stat st ON st.tbl=col.table_schema || '.' || col.table_name AND st.col=col.column_name
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
    """, (schema, table))


@db.timed
def experiments(con):
    return dict(trials=_rows(con, """
        SELECT trial, ts, campaigns, turns_total, running, status, train_window,
               retrain_every, turn_budget, start_pool, sett_mean, ll_mean,
               code_version FROM ops.trial ORDER BY ts DESC LIMIT 100
    """), sessions=_rows(con, """
        SELECT session_id, trial, segment_id, started_ts, ended_ts, status,
               campaigns, turns, turns_per_hour, last_turn_seconds, stalls
        FROM ops.session ORDER BY started_ts DESC LIMIT 200
    """), policies=_rows(con, """
        SELECT tp.trial, tp.scope, p.key AS policy, tp.weight
        FROM ops.trial_policy tp JOIN dict.enum p ON p.enum_id=tp.policy_id
        WHERE tp.trial IN (SELECT trial FROM ops.trial ORDER BY ts DESC LIMIT 100)
    """))


def _name(family, key):
    if not key:
        return None
    if family == 'faction':
        return labels._loc('factions_screen_name_' + key) or labels.pretty(key)
    return labels.name_for(family, key) or labels.pretty(key)


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]


@db.timed
def campaigns(con):
    return _rows(con, """
        SELECT c.campaign_key AS key, c.leader, f.key AS faction,
               m.key AS map, s.turn, s.ts
        FROM corpus.campaign c
        JOIN dict.faction f ON f.id = c.faction_id
        LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id
        JOIN LATERAL (
            SELECT s.turn, s.ts FROM corpus.snapshot s
            JOIN corpus.snapshot_campaign sc USING (snapshot_id)
            WHERE s.campaign_id = c.campaign_id ORDER BY s.snapshot_id DESC LIMIT 1
        ) s ON true
        ORDER BY c.campaign_id DESC LIMIT 100
    """)


@db.timed
def history(con, key):
    meta = con.execute("SELECT campaign_id, faction_id FROM corpus.campaign WHERE campaign_key = %s", (key,)).fetchone()
    if not meta:
        return None
    cid = meta['campaign_id']
    rows = _rows(con, """
        WITH latest AS (
            SELECT DISTINCT ON (s.turn) s.turn, cs.tech_set_id
            FROM corpus.snapshot s JOIN corpus.campaign_state cs USING(snapshot_id)
            WHERE s.campaign_id = %s ORDER BY s.turn, s.snapshot_id DESC
        ) SELECT l.turn, k.key, m.researched FROM latest l
          JOIN corpus.tech_set_member m ON m.set_id = l.tech_set_id
          JOIN dict.tech_node k ON k.id = m.tech_node_id ORDER BY l.turn
    """, (cid,))
    out = []
    known = {}
    for row in rows:
        previous = known.get(row['key'])
        if row['researched'] and previous is not True:
            out.append(dict(turn=row['turn'], subject='research', kind='completed' if previous is False else 'first recorded', key=row['key'], label=_name('research', row['key']), detail='Researched', character=None))
        known[row['key']] = row['researched']
    char_rows = _rows(con, """
        SELECT DISTINCT ON (s.turn, cs.character_id) s.turn, cs.character_id,
               ch.cqi, cs.skill_set_id, cs.equipped_set_id, cs.units, cs.rank,
               sub.key AS subtype
        FROM corpus.snapshot s JOIN corpus.char_state cs USING(snapshot_id)
        JOIN corpus.character ch USING(character_id)
        LEFT JOIN dict.agent_subtype sub ON sub.id = cs.subtype_id
        WHERE s.campaign_id = %s ORDER BY s.turn, cs.character_id, s.snapshot_id DESC
    """, (cid,))
    skill_ids = list({r['skill_set_id'] for r in char_rows})
    item_ids = list({r['equipped_set_id'] for r in char_rows})
    skills = {}
    for row in _rows(con, "SELECT m.set_id, k.key, m.level FROM corpus.skill_set_member m JOIN dict.skill k ON k.id=m.skill_id WHERE m.set_id=ANY(%s) AND m.level>0", (skill_ids,)):
        skills.setdefault(row['set_id'], {})[row['key']] = row['level']
    items = {}
    for row in _rows(con, "SELECT m.set_id, k.key FROM corpus.item_slot_set_member m JOIN dict.ancillary k ON k.id=m.ancillary_id WHERE m.set_id=ANY(%s)", (item_ids,)):
        items.setdefault(row['set_id'], set()).add(row['key'])
    previous = {}
    for row in char_rows:
        cqi = row['cqi']
        before = previous.get(cqi)
        who = labels.subtype_name(row['subtype'] or '') or str(cqi)
        old_skills = skills.get(before['skill_set_id'], {}) if before else {}
        for skill, level in skills.get(row['skill_set_id'], {}).items():
            if old_skills.get(skill) != level:
                out.append(dict(turn=row['turn'], subject='characters', kind='skill' if before else 'first recorded', key=skill, label=_name('skills', skill), detail=f"{who} · rank {old_skills.get(skill, 0)} → {level}", character=cqi))
        old_items = items.get(before['equipped_set_id'], set()) if before else set()
        new_items = items.get(row['equipped_set_id'], set())
        for item in old_items ^ new_items:
            kind = ('equipped' if item in new_items else 'unequipped') if before else 'first recorded'
            out.append(dict(turn=row['turn'], subject='items', kind=kind, key=item, label=_name('items', item), detail=who, character=cqi))
        if before and before['units'] != row['units']:
            out.append(dict(turn=row['turn'], subject='forces', kind='unit count', key=None, label=who, detail=f"{before['units']} → {row['units']}", character=cqi))
        previous[cqi] = row
    regions = _rows(con, """
        WITH latest AS (
            SELECT DISTINCT ON (s.turn) s.turn, w.region_set_id
            FROM corpus.snapshot s JOIN corpus.snapshot_world w USING(snapshot_id)
            WHERE s.campaign_id=%s ORDER BY s.turn, s.snapshot_id DESC
        ) SELECT l.turn, r.key, m.owner_id FROM latest l
          JOIN corpus.region_set_member m ON m.set_id=l.region_set_id
          JOIN dict.region r ON r.id=m.region_id ORDER BY l.turn
    """, (cid,))
    owners = {}
    for row in regions:
        owned = row['owner_id'] == meta['faction_id']
        old = owners.get(row['key'])
        if owned and old is not True or old is True and not owned:
            kind = 'first recorded' if old is None else 'acquired' if owned else 'lost'
            out.append(dict(turn=row['turn'], subject='realm', kind=kind, key=None, label=_name('garrison', row['key']), detail='Owned' if owned else 'Ownership changed', character=None))
        owners[row['key']] = owned
    building_rows = _rows(con, """
        WITH latest AS (
            SELECT DISTINCT ON (s.turn, ps.region_id) s.turn, ps.region_id, ps.built_set_id
            FROM corpus.snapshot s JOIN corpus.province_state ps USING(snapshot_id)
            WHERE s.campaign_id=%s ORDER BY s.turn, ps.region_id, s.snapshot_id DESC
        ) SELECT l.turn, r.key AS region, b.key, m.slot_index FROM latest l
          JOIN dict.region r ON r.id=l.region_id
          LEFT JOIN corpus.built_set_member m ON m.set_id=l.built_set_id
          LEFT JOIN dict.building b ON b.id=m.building_id ORDER BY l.turn, l.region_id
    """, (cid,))
    states = {}
    for row in building_rows:
        slots = states.setdefault((row['turn'], row['region']), {})
        if row['key']:
            slots[row['slot_index']] = row['key']
    buildings = {}
    for (turn, region), slots in states.items():
        old = buildings.get(region)
        for slot in slots.keys() | (old or {}).keys():
            before = (old or {}).get(slot)
            after = slots.get(slot)
            if before != after:
                building = after or before
                kind = 'first recorded' if old is None else 'building changed' if before and after else 'built' if after else 'removed'
                out.append(dict(turn=turn, subject='realm', kind=kind, key=None, label=_name('building', building), detail=_name('garrison', region), character=None))
        buildings[region] = slots
    diplomacy = _rows(con, """
        WITH latest AS (
            SELECT DISTINCT ON (s.turn) s.turn, w.relation_set_id
            FROM corpus.snapshot s JOIN corpus.snapshot_world w USING(snapshot_id)
            WHERE s.campaign_id=%s ORDER BY s.turn, s.snapshot_id DESC
        ) SELECT l.turn, f.key, m.at_war, m.allied, m.trade, m.nap
          FROM latest l JOIN corpus.relation_set_member m ON m.set_id=l.relation_set_id
          JOIN dict.faction f ON f.id=m.faction_id ORDER BY l.turn
    """, (cid,))
    relations = {}
    for row in diplomacy:
        before = relations.get(row['key'])
        for field, label in [('at_war','War'),('allied','Alliance'),('trade','Trade'),('nap','Non-aggression pact')]:
            if row[field] is not None and (before and before[field] != row[field] or not before and row[field]):
                out.append(dict(turn=row['turn'], subject='diplomacy', kind='changed' if before else 'first recorded', key=None, label=_name('faction',row['key']), detail=f"{label} · {'active' if row[field] else 'ended'}", character=None))
        relations[row['key']] = row
    return sorted(out, key=lambda r: r['turn'], reverse=True)


@db.timed
def campaign(con, key, turn=None):
    meta = con.execute("""
        SELECT c.campaign_id, c.campaign_key AS key, c.leader,
               c.faction_id, f.key AS faction, m.key AS map
        FROM corpus.campaign c JOIN dict.faction f ON f.id = c.faction_id
        LEFT JOIN dict.campaign_map m ON m.id = c.campaign_map_id
        WHERE c.campaign_key = %s
    """, (key,)).fetchone()
    if not meta:
        return None
    cid = meta['campaign_id']
    series = _rows(con, """
        SELECT DISTINCT ON (s.turn) s.turn, s.snapshot_id, s.ts,
               sc.settlements, sc.treasury, sc.income, sc.net_income,
               sc.armies, sc.allies, sc.vassals, sc.power_rank, sc.lord_level,
               sc.settlement_income, sc.province_income, sc.raiding_income,
               sc.trade_value, sc.upkeep, sc.expenditure,
               ibs.background_income
               , MAX(sc.settlements) OVER history AS peak_settlements
               , MAX(sc.lord_level) OVER history AS peak_level
               , MAX(sc.allies) OVER history AS peak_allies
               , MAX(sc.vassals) OVER history AS peak_vassals
               , FIRST_VALUE(sc.settlements) OVER history AS first_settlements
               , FIRST_VALUE(sc.lord_level) OVER history AS first_level
               , FIRST_VALUE(sc.allies) OVER history AS first_allies
               , FIRST_VALUE(sc.vassals) OVER history AS first_vassals
        FROM corpus.snapshot s JOIN corpus.snapshot_campaign sc USING (snapshot_id)
        LEFT JOIN corpus.income_by_source ibs USING (snapshot_id)
        WHERE s.campaign_id = %s
        WINDOW history AS (ORDER BY s.snapshot_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ORDER BY s.turn, s.snapshot_id DESC
    """, (cid,))
    for point in series:
        gains = [point['peak_' + field] - point['first_' + field] if point['peak_' + field] is not None and point['first_' + field] is not None else None for field in ('settlements', 'level', 'allies', 'vassals')]
        point['score'] = queries._weighted_reward(*gains)
    selected = next((r for r in reversed(series) if turn is None or r['turn'] == turn), None)
    if selected is None:
        return None
    sid = selected['snapshot_id']
    selected_turn = selected['turn']
    world = con.execute("SELECT * FROM corpus.snapshot_world WHERE snapshot_id = %s", (sid,)).fetchone()
    state = con.execute("""
        SELECT cs.* FROM corpus.campaign_state cs JOIN corpus.snapshot s USING(snapshot_id)
        WHERE s.campaign_id = %s AND s.snapshot_id <= %s
        ORDER BY s.snapshot_id DESC LIMIT 1
    """, (cid, sid)).fetchone()
    chars = _rows(con, """
        SELECT DISTINCT ON (cs.character_id) cs.*, ch.cqi, sub.key AS subtype,
               reg.key AS region, st.key AS stance, s.turn AS recorded_turn
        FROM corpus.char_state cs JOIN corpus.character ch USING(character_id)
        JOIN corpus.snapshot s USING(snapshot_id)
        LEFT JOIN dict.agent_subtype sub ON sub.id = cs.subtype_id
        LEFT JOIN dict.region reg ON reg.id = cs.region_id
        LEFT JOIN dict.stance st ON st.id = cs.stance_id
        WHERE ch.campaign_id = %s AND cs.snapshot_id <= %s
          AND s.turn = %s
        ORDER BY cs.character_id, cs.snapshot_id DESC
    """, (cid, sid, selected_turn))
    for ch in chars:
        ch['label'] = meta['leader'] if ch['is_leader'] else labels.subtype_name(ch['subtype'] or '')
        ch['region'] = _name('garrison', ch['region'])
        ch['stance'] = (ch['stance'] or '').removeprefix('MILITARY_FORCE_ACTIVE_STANCE_TYPE_').replace('_', ' ').lower()
        ch['skills'] = _rows(con, """
            SELECT k.key, m.level, m.total_levels, m.tier, st.key AS status
            FROM corpus.skill_set_member m JOIN dict.skill k ON k.id = m.skill_id
            JOIN dict.enum st ON st.enum_id = m.status_id
            WHERE m.set_id = %s ORDER BY m.ord
        """, (ch['skill_set_id'],))
        lines = labels.skill_lines(ch['subtype'])
        unlocks = labels.skill_unlock_ranks([s['key'] for s in ch['skills']])
        prerequisites = labels.skill_parents_for(ch['subtype'])
        effects = {}
        for effect in _rows(con, """
            SELECT character_skill_key AS key, level, effect_key AS effect, value, effect_scope
            FROM ref.character_skill_level_to_effects_junctions
            WHERE character_skill_key=ANY(%s)
        """, ([s['key'] for s in ch['skills']],)):
            effects.setdefault((effect['key'], effect['level']), []).append(effect)
        for skill in ch['skills']:
            skill['label'] = _name('skills', skill['key'])
            skill['line'] = labels.skill_line_of(lines, skill['key']) or 'Other'
            skill['effect'] = labels.skill_description(skill['key'])
            skill['unlock_rank'] = unlocks.get(skill['key'])
            skill['parents'] = prerequisites.get(skill['key'], [])
            skill['effects'] = [labels._effect_row(e['effect'], e['effect_scope'], e['value'], labels._effect_positive()) for e in effects.get((skill['key'], max(1, skill['level'])), [])]
        ch['items'] = _rows(con, """
            SELECT a.key, m.name AS label FROM corpus.item_slot_set_member m
            JOIN dict.ancillary a ON a.id = m.ancillary_id WHERE m.set_id = %s ORDER BY m.ord
        """, (ch['equipped_set_id'],))
        for item in ch['items']:
            item['effects'] = labels.item_effect_rows(item['key'])
        ch['traits'] = _rows(con, """
            SELECT t.key, m.level FROM corpus.char_state_ext ext
            JOIN corpus.trait_set_member m ON m.set_id=ext.trait_set_id
            JOIN dict.trait t ON t.id=m.trait_id
            WHERE ext.snapshot_id=%s AND ext.character_id=%s
        """, (ch['snapshot_id'], ch['character_id']))
        for trait in ch['traits']:
            trait['label'] = labels.trait_name(trait['key']) or labels.pretty(trait['key'])
            trait['effects'] = next((level['effects'] for level in labels.trait_level_rows(trait['key']) if level['level']==trait['level']), [])
        ch['trait_progress'] = _rows(con, """
            SELECT t.key, m.points FROM corpus.char_state_ext ext
            JOIN corpus.trait_progress_set_member m ON m.set_id=ext.trait_progress_set_id
            JOIN dict.trait t ON t.id=m.trait_id
            WHERE ext.snapshot_id=%s AND ext.character_id=%s
            ORDER BY m.points DESC, m.ord
        """, (ch['snapshot_id'], ch['character_id']))
        for progress in ch['trait_progress']:
            progress['label'] = labels.trait_name(progress['key']) or labels.pretty(progress['key'])
            progress['threshold'] = next((level['threshold'] for level in labels.trait_level_rows(progress['key'])
                                          if level['threshold'] and level['threshold'] > progress['points']), None)
        bonuses = {}
        for source, entries in [('skills', [e for s in ch['skills'] if s['level']>0 for e in effects.get((s['key'],s['level']), [])])]:
            for effect in entries:
                group = (effect['effect'], effect['effect_scope'])
                bonus = bonuses.setdefault(group, {'value': 0, 'sources': set()})
                bonus['value'] += effect['value'] or 0
                bonus['sources'].add(source)
        for item in ch['items']:
            for effect in _rows(con, 'SELECT effect, effect_scope, value FROM refc.ancillary_effects WHERE ancillary=%s', (item['key'],)):
                group = (effect['effect'], effect['effect_scope'])
                bonus = bonuses.setdefault(group, {'value': 0, 'sources': set()})
                bonus['value'] += effect['value'] or 0
                bonus['sources'].add('items')
        for trait in ch['traits']:
            for effect in _rows(con, """
                SELECT e.effect, e.effect_scope, e.value FROM refc.trait_levels l
                JOIN refc.trait_effects e ON e.level_key=l.level_key
                WHERE l.trait=%s AND l.level=%s
            """, (trait['key'], trait['level'])):
                group = (effect['effect'], effect['effect_scope'])
                bonus = bonuses.setdefault(group, {'value': 0, 'sources': set()})
                bonus['value'] += effect['value'] or 0
                bonus['sources'].add('traits')
        ch['bonuses'] = [{**labels._effect_row(effect, scope, b['value'], labels._effect_positive()), 'sources': sorted(b['sources'])} for (effect, scope), b in bonuses.items()]
        ch['units_list'] = _rows(con, """
            SELECT u.key, m.strength_pct, m.xp FROM corpus.unit_card_set_member m
            JOIN dict.unit u ON u.id = m.unit_id WHERE m.set_id = %s ORDER BY m.ord
        """, (ch['unit_card_set_id'],))
        for unit in ch['units_list']:
            unit['label'] = _name('recruit_unit', unit['key'])
    chars.sort(key=lambda ch: (not ch['is_leader'], ch['is_hero'], -ch['rank']))
    research = []
    if state:
        research = _rows(con, """
            SELECT k.key, m.researched, m.can_research, m.cost,
                   m.tech_node_id = %s AS researching
            FROM corpus.tech_set_member m JOIN dict.tech_node k ON k.id = m.tech_node_id
            WHERE m.set_id = %s ORDER BY m.ord
        """, (state['current_research_id'], state['tech_set_id']))
        parents = labels.tech_parents()
        lines = labels.tech_lines()
        tech_effects = {}
        for effect in _rows(con, """
            SELECT t.key, e.effect, e.value, e.effect_scope FROM refc.tech t
            JOIN ref.technology_effects_junction e ON e.technology=t.technology_key
            WHERE t.key=ANY(%s)
        """, ([t['key'] for t in research],)):
            tech_effects.setdefault(effect['key'], []).append(labels._effect_row(effect['effect'], effect['effect_scope'], effect['value'], labels._effect_positive()))
        for tech in research:
            tech['label'] = labels.tech_name(tech['key']) or labels.pretty(tech['key'])
            tech['effect'] = labels.tech_description(tech['key'])
            tech['branch'] = lines.get(tech['key'])
            tech['parent'] = parents.get(tech['key'])
            tech['effects'] = tech_effects.get(tech['key'], [])
    regions = []
    relations = []
    if world:
        regions = _rows(con, """
            SELECT m.region_id, r.key, m.x, m.y, m.adjacent, m.owner_id,
                   m.owner_id = %s AS owned, p.key AS province,
                   f.key AS owner, m.capital, g.cx, g.cy, g.outline
            FROM corpus.region_set_member m JOIN dict.region r ON r.id = m.region_id
            LEFT JOIN dict.province p ON p.id = m.province_id
            LEFT JOIN dict.faction f ON f.id = m.owner_id
            LEFT JOIN ops.region_geometry g ON g.region_id = m.region_id
            WHERE m.set_id = %s
        """, (meta['faction_id'], world['region_set_id']))
        for region in regions:
            region['label'] = _name('garrison', region['key'])
            region['province'] = labels.pretty(region['province'] or '')
            region['outline'] = json.loads(region['outline']) if region['outline'] else []
        relations = _rows(con, """
            SELECT f.key, m.standing, m.at_war, m.allied, m.trade, m.nap,
                   m.mil_access, m.mil_ally, m.def_ally, m.their_vassal,
                   CASE WHEN m.at_war THEN 'at war' WHEN m.allied THEN 'allied'
                        WHEN m.nap THEN 'non-aggression' WHEN m.trade THEN 'trade' END AS since_kind,
                   (SELECT MIN(d.from_turn) FROM analytics.diplomacy_state_change d
                     WHERE d.campaign_id = %s AND d.faction_id = m.faction_id
                       AND d.kind = CASE WHEN m.at_war THEN 'at_war' WHEN m.allied THEN 'allied'
                                         WHEN m.nap THEN 'nap' WHEN m.trade THEN 'trade' END
                       AND d.from_turn <= %s AND d.to_turn >= %s) AS since
            FROM corpus.relation_set_member m JOIN dict.faction f ON f.id = m.faction_id
            WHERE m.set_id = %s ORDER BY m.standing DESC NULLS LAST
        """, (cid, selected_turn, selected_turn, world['relation_set_id']))
        for relation in relations:
            relation['label'] = _name('faction', relation['key'])
    provinces = _rows(con, """
        SELECT DISTINCT ON (ps.region_id) ps.*, r.key, s.turn AS recorded_turn
        FROM corpus.province_state ps JOIN corpus.snapshot s USING(snapshot_id)
        JOIN dict.region r ON r.id = ps.region_id
        WHERE s.campaign_id = %s AND s.snapshot_id <= %s AND s.turn = %s
        ORDER BY ps.region_id, ps.snapshot_id DESC
    """, (cid, sid, selected_turn))
    breakdown_ids = sorted({p['income_breakdown_set_id'] for p in provinces
                            if p['income_breakdown_set_id'] is not None})
    breakdowns = {}
    for row in _rows(con, """
        SELECT set_id, label, amount FROM corpus.income_breakdown_set_member
        WHERE set_id = ANY(%s) ORDER BY set_id, ord
    """, (breakdown_ids,)) if breakdown_ids else []:
        breakdowns.setdefault(row['set_id'], []).append(
            dict(label=row['label'], amount=row['amount']))
    for province in provinces:
        province['label'] = _name('garrison', province['key'])
        province['income_breakdown'] = breakdowns.get(province['income_breakdown_set_id'], [])
        province['slots'] = _rows(con, """
            SELECT k.key, m.slot_index FROM corpus.built_set_member m
            JOIN dict.building k ON k.id = m.building_id WHERE m.set_id = %s ORDER BY m.slot_index
        """, (province['built_set_id'],))
        for slot in province['slots']:
            slot['label'] = _name('building', slot['key'])
    events = _rows(con, """
        SELECT e.event_id, e.turn, e.kind, e.choice, e.character_cqi,
               i.key AS incident, d.key AS dilemma, a.key AS item, r.key AS region
        FROM corpus.event e LEFT JOIN dict.incident i ON i.id = e.incident_id
        LEFT JOIN dict.dilemma d ON d.id = e.dilemma_id
        LEFT JOIN dict.ancillary a ON a.id = e.ancillary_id
        LEFT JOIN dict.region r ON r.id = e.region_id
        WHERE e.campaign_id = %s AND e.turn <= %s ORDER BY e.turn DESC, e.event_id DESC
    """, (cid, selected_turn))
    for event in events:
        event['label'] = labels.pretty(event['incident'] or event['dilemma'] or event['item'] or event['kind'])
    armies = _rows(con, """
        SELECT w.*, sub.key AS subtype, r.key AS region, st.key AS stance
        FROM corpus.world_army w LEFT JOIN dict.agent_subtype sub ON sub.id = w.subtype_id
        LEFT JOIN dict.region r ON r.id = w.region_id
        LEFT JOIN dict.stance st ON st.id = w.stance_id
        WHERE w.snapshot_id = %s AND w.has_army ORDER BY w.ord
    """, (sid,))
    for army in armies:
        army['label'] = meta['leader'] if army['is_leader'] else labels.subtype_name(army['subtype'] or '')
        army['region'] = _name('garrison', army['region'])
        army['stance'] = (army['stance'] or '').removeprefix('MILITARY_FORCE_ACTIVE_STANCE_TYPE_').replace('_', ' ').lower()
    hostiles = _rows(con, """
        SELECT w.cqi, w.x, w.y, w.units, w.hp, f.key AS faction
        FROM corpus.world_hostile w JOIN dict.faction f ON f.id = w.faction_id
        WHERE w.snapshot_id = %s AND w.visible
    """, (sid,))
    finance = [dict(component_id=field, label=label, value=f"{selected[field]:,}", turn=selected_turn,
                    kind='expenditure' if field in ('upkeep','expenditure') else 'income')
               for field, label in [('settlement_income','Settlement income'),
                                    ('raiding_income','Raiding'), ('background_income','Other income'),
                                    ('province_income','Province income'), ('trade_value','Trade value'),
                                    ('upkeep','Army upkeep'), ('expenditure','Total expenditure'),
                                    ('net_income','Income next turn')]
               if selected[field] is not None]
    pool = []
    if state:
        pool = _rows(con, """
            SELECT a.key, m.name AS label FROM corpus.item_slot_set_member m
            JOIN dict.ancillary a ON a.id = m.ancillary_id WHERE m.set_id = %s
        """, (state['anc_pool_set_id'],))
        for item in pool:
            item['effects'] = labels.item_effect_rows(item['key'])
    return dict(meta=meta, selected=selected, series=series, characters=chars,
                research=research, regions=regions, provinces=provinces,
                diplomacy=relations, armies=armies, hostiles=hostiles,
                events=events, finance=finance, pool=pool)
