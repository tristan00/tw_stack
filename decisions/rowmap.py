from __future__ import annotations

SET = 'set:'

SNAPSHOT_CAMPAIGN = {
    'faction_id': 'faction', 'faction_cqi': 'faction_cqi', 'turn': 'turn',
    'income': 'income', 'settlements': 'settlements', 'treasury': 'treasury',
    'is_researching': 'is_researching', 'armies': 'armies', 'lord_level': 'lord_level',
    'allies': 'allies', 'vassals': 'vassals', 'power_rank': 'power_rank',
    'll_wounded': 'll_wounded', 'game_version_id': 'game_version',
    'defeated': 'defeated', 'difficulty': 'difficulty', 'leader': 'leader',
    'selector_id': 'selector', 'presave_radius': 'presave_radius',
    'campaign_map_id': 'campaign_map', 'eval_ms': '_eval_ms',
    'resource_set_id': SET + 'resources',
    'hero_count_set_id': SET + 'hero_type_counts',
    'effect_bundle_set_id': SET + 'effect_bundles',
}

SNAPSHOT_WORLD = {
    'citizenry': 'citizenry', 'diplo_unseen': 'diplo_unseen',
    'diplo_schema': 'diplo_schema', 'diplo_hostile_rows': 'diplo_hostile_rows',
    'reach_char_cqis': None, 'reach_region_ids': None,
    'region_set_id': SET + 'regions', 'settlement_set_id': SET + 'settlements',
    'ruin_set_id': SET + 'ruins', 'enemy_agent_set_id': SET + 'enemy_agents',
    'war_graph_set_id': SET + 'war_graph', 'relation_set_id': SET + 'relations',
    'stationed_set_id': SET + 'stationed',
}

WORLD_ARMY = {
    'cqi': 'cqi', 'subtype_id': 'subtype', 'agent_type_id': 'agent_type',
    'is_leader': 'is_leader', 'has_army': 'has_army', 'is_general': 'is_general',
    'rank': 'rank', 'x': 'x', 'y': 'y', 'ap_pct': 'ap_pct', 'stance_id': 'stance',
    'hp': 'hp', 'units': 'units', 'region_owner_id': 'region_owner',
    'region_id': 'region', 'province_id': 'province',
    'in_own_territory': 'in_own_territory', 'ap_remaining': 'ap_remaining',
    'ap_per_turn': 'ap_per_turn',
}

WORLD_HOSTILE = {
    'kind_id': 'kind', 'faction_id': 'faction', 'visible': 'visible', 'cqi': 'cqi',
    'subtype_id': 'subtype', 'agent_type_id': 'agent_type',
    'province_id': 'province', 'region_id': 'region', 'x': 'x', 'y': 'y',
    'dist': 'dist', 'is_armed_citizenry': 'is_armed_citizenry', 'units': 'units',
    'hp': 'hp', 'stance_id': 'stance',
}

CHAR_STATE = {
    'is_hero': None, 'rank': 'rank', 'skill_points': 'skill_points',
    'units': 'units', 'pending_recruits': 'pending_recruits', 'ap_pct': 'ap_pct',
    'ap_remaining': 'ap_remaining', 'ap_per_turn': 'ap_per_turn', 'hp': 'hp',
    'loyalty': 'loyalty', 'stance_id': 'stance', 'subtype_id': 'subtype',
    'region_id': 'region', 'x': 'x', 'y': 'y', 'garrisoned': 'garrisoned',
    'besieging': 'besieging', 'acted': 'acted', 'is_leader': 'is_leader',
    'wounded': 'wounded', 'is_agent': 'is_agent', 'can_embed': 'can_embed',
    'agent_type_id': 'agent_type', 'hidden_skill_ids': 'hidden_skills',
    'pending_recruit_unit_ids': 'pending_recruit_keys',
    'reach_chars_true': None, 'reach_setts_true': None,
    'move_x': None, 'move_y': None, 'reach_rays': None, 'reach_max': None,
    'skill_set_id': SET + 'skills', 'stance_set_id': SET + 'stances',
    'recruitable_set_id': SET + 'recruitable', 'unit_card_set_id': SET + 'unit_cards',
    'pending_queue_set_id': SET + 'pending_queue', 'equipped_set_id': SET + 'equipped',
    'horde_slot_set_id': SET + 'horde_slots', 'merc_pool_set_id': SET + 'merc_pools',
}

CHAR_STATE_EXT = {
    'xp': 'xp', 'xp_next_level': 'xp_next_level', 'subterfuge': 'subterfuge',
    'zeal': 'zeal', 'authority': 'authority',
    'resurrection_turns': 'resurrection_turns', 'upkeep': 'upkeep',
    'background_skill_id': 'background_skill', 'armory_item_ids': 'armory',
    'trait_set_id': SET + 'traits', 'trait_progress_set_id': SET + 'trait_progress',
    'hidden_skill_state_set_id': SET + 'hidden_skill_states',
    'effect_bundle_set_id': SET + 'effect_bundles',
    'force_effect_bundle_set_id': SET + 'force_effect_bundles',
}

PROVINCE_STATE = {
    'region_id': 'region', 'settlement_present': 'settlement_present',
    'province_id': 'province', 'complete_owner': 'complete_owner',
    'max_slots': 'max_slots', 'free_slots': 'free_slots',
    'can_set_edict': 'can_set_edict', 'selected_edict_id': 'selected_edict',
    'active_edict_id': 'active_edict', 'public_order': 'public_order',
    'buildings': 'buildings', 'is_capital': 'is_capital',
    'settlement_level': 'settlement_level', 'growth_per_turn': 'growth_per_turn',
    'gross_income': 'gross_income', 'development_points': 'development_points',
    'income': 'income', 'has_port': 'has_port', 'has_walls': 'has_walls',
    'locked_slots': 'locked_slots', 'edict_ids': 'edicts',
    'built_set_id': SET + 'built', 'building_now_set_id': SET + 'building_now',
    'corruption_set_id': SET + 'corruption', 'buildable_set_id': SET + 'buildable',
    'slot_state_set_id': SET + 'slot_states',
    'effect_bundle_set_id': SET + 'effect_bundles',
    'plague_bundle_set_id': SET + 'plague_bundles',
}

CAMPAIGN_STATE = {
    'current_research_id': 'current_research', 'research_points': 'research_points',
    'tech_set_id': SET + 'tech', 'rite_set_id': SET + 'rites',
    'lord_pool_set_id': SET + 'lord_pools', 'anc_pool_set_id': SET + 'anc_pool',
    'equipped_all_set_id': SET + 'equipped_all', 'mission_set_id': SET + 'missions',
}

TABLES = {
    'snapshot_campaign': SNAPSHOT_CAMPAIGN,
    'snapshot_world': SNAPSHOT_WORLD,
    'world_army': WORLD_ARMY,
    'world_hostile': WORLD_HOSTILE,
    'char_state': CHAR_STATE,
    'char_state_ext': CHAR_STATE_EXT,
    'province_state': PROVINCE_STATE,
    'campaign_state': CAMPAIGN_STATE,
}

ENTITY_TABLES = {
    'lord': ('char_state', 'char_state_ext'),
    'hero': ('char_state', 'char_state_ext'),
    'province': ('province_state',),
    'campaign': ('campaign_state',),
}
