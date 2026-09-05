from __future__ import annotations

LIST = 'list'
DICT_SCALAR = 'dict_scalar'
DICT_DICT = 'dict_dict'
DICT_LIST = 'dict_list'

COLLECTIONS = {
    'skills': (1, 'skill_set_member', LIST, {
        'skill_id': 'key', 'status_id': 'status', 'level': 'level',
        'total_levels': 'total_levels', 'tier': 'tier'}),
    'stances': (2, 'stance_set_member', LIST, {
        'stance_id': 'key', 'active': 'active', 'can_activate': 'can_activate',
        'can_afford': 'can_afford'}),
    'recruitable': (3, 'recruitable_set_member', LIST, {
        'unit_id': 'key', 'state': 'state', 'cost': 'cost', 'disabled': 'disabled'}),
    'hidden_skill_states': (4, 'hidden_skill_state_set_member', LIST, {
        'skill_id': 'key', 'level': 'level', 'total_levels': 'total_levels'}),
    'traits': (5, 'trait_set_member', LIST, {
        'trait_id': 'key', 'level': 'level', 'threshold_points': 'threshold_points',
        'points': 'points', 'chaos_realm': 'chaos_realm', 'level_key_id': 'level_key'}),
    'trait_progress': (6, 'trait_progress_set_member', DICT_SCALAR, {
        'trait_id': '{key}', 'points': '{value}'}),
    'equipped': (7, 'item_slot_set_member', LIST, {
        'index': 'index', 'ancillary_id': 'key', 'name': 'name'}),
    'anc_pool': (7, 'item_slot_set_member', LIST, {
        'index': 'index', 'ancillary_id': 'key', 'name': 'name'}),
    'equipped_all': (7, 'item_slot_set_member', LIST, {
        'index': 'index', 'ancillary_id': 'key', 'name': 'name'}),
    'horde_slots': (8, 'horde_slot_set_member', LIST, {
        'slot_index': 'slot_index', 'slot_id': 'slot_id', 'building_id': 'key',
        'empty': 'empty', 'available': 'available'}),
    'merc_pools': (9, 'merc_pool_set_member', DICT_LIST, {
        'action_id': '{key}', 'unit_id': 'key', 'avail': 'avail',
        'cost': 'cost', 'can': 'can'}),
    'pending_queue': (10, 'pending_queue_set_member', LIST, {
        'unit_id': 'key', 'turns_left': 'turns_left'}),
    'effect_bundles': (11, 'effect_bundle_set_member', LIST, {
        'effect_bundle_id': 'key', 'turns_remaining': 'turns_remaining'}),
    'force_effect_bundles': (11, 'effect_bundle_set_member', LIST, {
        'effect_bundle_id': 'key', 'turns_remaining': 'turns_remaining'}),
    'plague_bundles': (11, 'effect_bundle_set_member', LIST, {
        'effect_bundle_id': 'key', 'turns_remaining': 'turns_remaining'}),
    'unit_cards': (12, 'unit_card_set_member', LIST, {
        'unit_id': 'key', 'strength_pct': 'strength_pct', 'category_id': 'category',
        'xp': 'xp'}),
    'buildable': (13, 'buildable_set_member', LIST, {
        'slot_index': 'slot_index', 'building_id': 'key', 'active': 'active',
        'empty': 'empty', 'can_upgrade': 'can_upgrade', 'cost': 'cost',
        'upkeep': 'upkeep', 'level': 'level',
        'can_afford_resources': 'can_afford_resources'}),
    'slot_states': (14, 'slot_state_set_member', LIST, {
        'index': 'index', 'building_id': 'key', 'queued_building_id': 'queued_key',
        'damaged': 'damaged', 'can_repair': 'can_repair', 'repairing': 'repairing',
        'can_dismantle': 'can_dismantle', 'refund': 'refund', 'queued': 'queued',
        'empty': 'empty', 'health': 'health', 'max_health': 'max_health',
        'ruined': 'ruined', 'repair_cost': 'repair_cost', 'upgrading': 'upgrading',
        'dismantling': 'dismantling'}),
    'built': (15, 'built_set_member', DICT_SCALAR, {
        'slot_index': '{key}', 'building_id': '{value}'}),
    'building_now': (16, 'building_now_set_member', DICT_DICT, {
        'slot_index': '{key}', 'building_id': 'key', 'turns_left': 'turns_left',
        'paused': 'paused'}),
    'resources': (17, 'resource_set_member', DICT_SCALAR, {
        'resource_id': '{key}', 'value': '{value}'}),
    'corruption': (17, 'resource_set_member', DICT_SCALAR, {
        'resource_id': '{key}', 'value': '{value}'}),
    'hero_type_counts': (18, 'hero_count_set_member', DICT_SCALAR, {
        'agent_type_id': '{key}', 'n': '{value}'}),
    'tech': (19, 'tech_set_member', LIST, {
        'tech_node_id': 'key', 'researched': 'researched',
        'can_research': 'can_research', 'cost': 'cost'}),
    'rites': (20, 'rite_set_member', LIST, {
        'index': 'index', 'ritual_id': 'key', 'can_perform': 'can_perform',
        'reason_id': 'invalid_reason'}),
    'lord_pools': (21, 'lord_pool_set_member', DICT_DICT, {
        'subtype_id': '{key}', 'n': 'n'}),
    'missions': (22, 'mission_set_member', LIST, {
        'mission_id': 'mission', 'status_id': 'status',
        'turns_remaining': 'turns_remaining', 'is_quest': 'is_quest',
        'is_victory': 'is_victory', 'completed': 'completed',
        'cancelled': 'cancelled', 'pending': 'pending', 'category': 'category',
        'issuer_id': 'issuer'}),
    'regions': (23, 'region_set_member', LIST, {
        'region_id': 'region', 'x': 'x', 'y': 'y', 'province_id': 'province',
        'owner_id': 'owner', 'capital': 'capital', 'abandoned': 'abandoned',
        'adjacent': 'adjacent'}),
    'settlements': (24, 'settlement_set_member', LIST, {
        'region_id': 'region', 'capital': 'capital', 'units': 'units',
        'x': 'x', 'y': 'y'}),
    'ruins': (25, 'ruin_set_member', LIST, {
        'region_id': 'region', 'x': 'x', 'y': 'y'}),
    'enemy_agents': (26, 'enemy_agent_set_member', LIST, {
        'cqi': 'cqi', 'x': 'x', 'y': 'y', 'faction_id': 'faction',
        'at_war': 'at_war'}),
    'war_graph': (27, 'war_graph_set_member', LIST, {
        'faction_id': 'faction', 'at_war_with': 'at_war_with'}),
    'relations': (28, 'relation_set_member', LIST, {
        'faction_id': 'faction', 'at_war': 'at_war', 'allied': 'allied',
        'trade': 'trade', 'their_vassal': 'their_vassal', 'standing': 'standing',
        'excluded': 'excluded', 'mil_ally': 'mil_ally', 'def_ally': 'def_ally',
        'nap': 'nap', 'mil_access': 'mil_access', 'our_master': 'our_master'}),
    'stationed': (29, 'stationed_set_member', DICT_SCALAR, {
        'region_id': '{key}', 'cqi': '{value}'}),
}

LORD_POOL_CHILD = 'lord_pool_candidate'
LORD_POOL_LISTS = {
    'can': 'can', 'agents': 'agent', 'bg_skills': 'bg_skill_id',
    'subtypes': 'cand_subtype_id', 'traits': 'trait_ids',
}
LORD_POOL_DROPPED = ('cqis', 'ranks', 'units', 'n')


def candidates(value):
    out = []
    for subtype, pool in value.items():
        if not isinstance(pool, dict):
            continue
        n = pool.get('n') or 0
        for i in range(int(n)):
            row = {'subtype_id': subtype, 'ord': i}
            for field, column in LORD_POOL_LISTS.items():
                seq = pool.get(field)
                row[column] = seq[i] if isinstance(seq, list) and i < len(seq) else None
            out.append(row)
    return out

NOT_SETS = ('armies', 'hostiles', 'move_tiles', 'reach_chars', 'reach_setts',
            'read_failures')


def kind_of(key):
    entry = COLLECTIONS.get(key)
    return entry[0] if entry else None


def members(key, value):
    kind, table, shape, cols = COLLECTIONS[key]
    out = []
    if shape == LIST:
        for item in value:
            out.append({c: item.get(src) for c, src in cols.items()})
        return out
    if shape == DICT_SCALAR:
        for k, v in value.items():
            row = {}
            for c, src in cols.items():
                row[c] = k if src == '{key}' else (v if src == '{value}' else None)
            out.append(row)
        return out
    if shape == DICT_DICT:
        for k, v in value.items():
            if not isinstance(v, dict):
                v = {}
            row = {}
            for c, src in cols.items():
                row[c] = k if src == '{key}' else v.get(src)
            out.append(row)
        return out
    if shape == DICT_LIST:
        for k, items in value.items():
            for item in (items if isinstance(items, list) else []):
                row = {}
                for c, src in cols.items():
                    row[c] = k if src == '{key}' else item.get(src)
                out.append(row)
        return out
    raise ValueError('unknown shape %r for %s' % (shape, key))


def unconsumed(key, value):
    kind, table, shape, cols = COLLECTIONS[key]
    used = {src for src in cols.values() if not src.startswith('{')}
    if key == 'lord_pools':
        used |= set(LORD_POOL_LISTS) | set(LORD_POOL_DROPPED)
    seen = set()
    if shape == LIST:
        for item in value:
            seen |= set(item)
    elif shape == DICT_DICT:
        for v in value.values():
            if isinstance(v, dict):
                seen |= set(v)
    elif shape == DICT_LIST:
        for items in value.values():
            for item in (items if isinstance(items, list) else []):
                seen |= set(item)
    return sorted(seen - used)
