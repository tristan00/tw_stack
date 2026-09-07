CREATE SCHEMA IF NOT EXISTS refc;

CREATE OR REPLACE VIEW refc.building_chains AS
SELECT c."key" AS key,
       c.building_superchain AS superchain,
       c.chain_category AS chain_category,
       c.optional_sort_order::integer AS sort_order
  FROM ref.building_chains c;

CREATE OR REPLACE VIEW refc.buildings AS
SELECT b.level_name AS key,
       b.chain AS building_chain,
       b.level::integer AS level,
       b.create_cost::integer AS create_cost,
       b.create_time::integer AS create_time,
       b.upkeep_cost::integer AS upkeep_cost,
       b.food_cost::integer AS food_cost,
       b.development_point_cost::integer AS dev_point_cost,
       b.building_instance_key AS building_instance_key
  FROM ref.building_levels b;

CREATE OR REPLACE VIEW refc.tech AS
SELECT n."key" AS key,
       n.technology_key AS technology_key,
       n.technology_node_set AS node_set,
       n.tier::integer AS tier,
       n.research_points_required::integer AS research_points_required,
       n.cost_per_round::integer AS cost_per_round,
       n.food_cost::integer AS food_cost,
       n.required_parents::integer AS required_parents,
       t.building_level AS building_level,
       t.is_civil::integer AS is_civil,
       t.is_engineering::integer AS is_engineering,
       t.is_military::integer AS is_military,
       t.is_hidden::integer AS is_hidden
  FROM ref.technology_nodes n
  LEFT JOIN ref.technologies t ON t."key" = n.technology_key
UNION ALL
SELECT t."key", t."key", NULL, NULL, NULL, NULL, NULL, NULL, t.building_level,
       t.is_civil::integer, t.is_engineering::integer, t.is_military::integer,
       t.is_hidden::integer
  FROM ref.technologies t
 WHERE NOT EXISTS (SELECT 1 FROM ref.technology_nodes n
                    WHERE n.technology_key = t."key" OR n."key" = t."key");

CREATE OR REPLACE VIEW refc.units AS
SELECT m.unit AS key,
       m.land_unit AS land_unit,
       m.caste AS caste,
       l.category AS category,
       l.class AS class,
       m.recruitment_cost::integer AS recruitment_cost,
       m.upkeep_cost::integer AS upkeep_cost,
       m.create_time::integer AS create_time,
       m.food_cost::integer AS food_cost,
       m.multiplayer_cost::integer AS multiplayer_cost,
       m.tier::integer AS tier,
       m.num_men::integer AS num_men,
       m.is_naval::integer AS is_naval,
       m.ui_unit_group_land AS ui_unit_group_land
  FROM ref.main_units m
  LEFT JOIN ref.land_units l ON l."key" = m.land_unit;

CREATE OR REPLACE VIEW refc.skills AS
SELECT s."key" AS key,
       s.unlocked_at_rank::integer AS unlocked_at_rank,
       s.influence_cost::integer AS influence_cost,
       s.is_background_skill::integer AS is_background_skill,
       s.background_weighting::real AS background_weighting
  FROM ref.character_skills s;

CREATE OR REPLACE VIEW refc.rituals AS
SELECT r."key" AS key,
       r.category AS category,
       r.cast_time::integer AS cast_time,
       r.cooldown_time::integer AS cooldown_time,
       r.slave_cost::integer AS slave_cost,
       r.influence_cost::integer AS influence_cost,
       r.required_resources AS required_resources,
       r.expended_resources AS expended_resources
  FROM ref.rituals r;

CREATE OR REPLACE VIEW refc.tech_links AS
SELECT l.child_key AS child, l.parent_key AS parent,
       l.visible_in_ui::integer AS visible
  FROM ref.technology_node_links l;

CREATE OR REPLACE VIEW refc.skill_links AS
SELECT DISTINCT cn.character_skill_key AS child,
       pn.character_skill_key AS parent,
       l.link_type AS link_type,
       COALESCE(s."set", '') AS node_set
  FROM ref.character_skill_node_links l
  JOIN ref.character_skill_nodes cn ON cn."key" = l.child_key
  JOIN ref.character_skill_nodes pn ON pn."key" = l.parent_key
  LEFT JOIN (SELECT a."set", a.item AS child_item, b.item AS parent_item
               FROM ref.character_skill_node_set_items a
               JOIN ref.character_skill_node_set_items b ON b."set" = a."set") s
         ON s.child_item = l.child_key AND s.parent_item = l.parent_key
 WHERE cn.character_skill_key <> pn.character_skill_key;

CREATE OR REPLACE VIEW refc.ancillary_effects AS
SELECT a.ancillary AS ancillary, a.effect AS effect,
       a.effect_scope AS effect_scope, a.value::real AS value
  FROM ref.ancillary_to_effects a;

CREATE OR REPLACE VIEW refc.effects_meta AS
SELECT e.effect AS effect, e.priority::real AS priority,
       e.is_positive_value_good::integer AS positive_good
  FROM ref.effects e;

CREATE OR REPLACE VIEW refc.agent_abilities AS
SELECT a.ability AS key, a.category AS category FROM ref.abilities a;

CREATE OR REPLACE VIEW refc.agent_permitted_subtypes AS
SELECT p.faction AS faction, p.agent AS agent, p.subtype AS subtype
  FROM ref.faction_agent_permitted_subtypes p;

CREATE OR REPLACE VIEW refc.skill_actions AS
SELECT DISTINCT j.character_skill_key AS skill,
       b.agent_action_record AS agent_action
  FROM ref.character_skill_level_to_effects_junctions j
  JOIN ref.effect_bonus_value_agent_action_record_junctions b
    ON b.effect = j.effect_key
 WHERE b.bonus_value_id = 'active';

CREATE OR REPLACE VIEW refc.ancillaries AS
SELECT a."key" AS key, a.type AS type, a.category AS category,
       COALESCE(a.subcategory, '') AS subcategory,
       a.legendary_item::integer AS legendary,
       a.transferrable::integer AS transferrable,
       a.randomly_dropped::integer AS randomly_dropped,
       a.uniqueness_score AS uniqueness_score
  FROM ref.ancillaries a;

CREATE OR REPLACE VIEW refc.agent_actions AS
SELECT a.unique_id AS key, a.agent AS agent, a.ability AS ability,
       a.attribute AS attribute, a.chance_of_success AS chance_of_success,
       a.cannot_fail AS cannot_fail_result,
       a.succeed_always_override::integer AS succeed_always,
       a.critical_success_proportion_modifier AS crit_success_mod,
       a.opportune_failure_proportion_modifier AS opportune_failure_mod,
       a.critical_failure_proportion_modifier AS crit_failure_mod,
       a.show_action_info_in_ui::integer AS show_in_ui,
       a.subculture AS subculture,
       a.localised_action_name AS loc_name
  FROM ref.agent_actions a;

CREATE OR REPLACE VIEW refc.action_results AS
SELECT r."key" AS key, r.actor_effect_bundle AS actor_bundle,
       r.target_effect_bundle AS target_bundle,
       r.actor_effect_bundle_turns AS actor_bundle_turns,
       r.target_effect_bundle_turns AS target_bundle_turns
  FROM ref.action_results r;

CREATE OR REPLACE VIEW refc.action_result_outcomes AS
SELECT o."key" AS key, o.action_result_key AS action_result_key,
       o.outcome AS outcome, o.effect_record AS effect,
       o.effect_scope_record AS effect_scope, o.value AS value,
       o.affects_target::integer AS affects_target,
       o.advancement_stage AS advancement_stage
  FROM ref.action_results_additional_outcomes o;

CREATE OR REPLACE VIEW refc.trait_meta AS
SELECT t."key" AS trait, t.icon AS category FROM ref.character_traits t;

CREATE OR REPLACE VIEW refc.trait_levels AS
SELECT l.trait AS trait, l.level AS level,
       l.threshold_points AS threshold, l."key" AS level_key
  FROM ref.character_trait_levels l;

CREATE OR REPLACE VIEW refc.trait_effects AS
SELECT e.trait_level AS level_key, e.effect AS effect,
       e.effect_scope AS effect_scope, e.value AS value
  FROM ref.trait_level_effects e;

CREATE OR REPLACE VIEW refc.trait_antitraits AS
SELECT a.trait AS trait, a.antitrait AS antitrait FROM ref.trait_to_antitraits a;

CREATE OR REPLACE VIEW refc.tech_groups AS
SELECT n."key" AS node_key, n.optional_ui_group AS ui_group
  FROM ref.technology_nodes n
 WHERE n.optional_ui_group IS NOT NULL AND n.optional_ui_group <> '';

CREATE OR REPLACE VIEW refc.skill_categories AS
SELECT c."key" AS key, c.min_indent AS min_indent, c.max_indent AS max_indent,
       c."order" AS ord, COALESCE(c.agent_subtype_override, '') AS subtype_override
  FROM ref.character_skill_categories c;

CREATE OR REPLACE VIEW refc.skill_indents AS
SELECT n.character_skill_key AS skill, n.indent AS indent, count(*)::integer AS n
  FROM ref.character_skill_nodes n
 GROUP BY n.character_skill_key, n.indent;

CREATE OR REPLACE VIEW refc.skill_node_sets AS
SELECT s."key" AS node_set, COALESCE(s.agent_subtype_key, '') AS subtype,
       COALESCE(s.agent_key, '') AS agent
  FROM ref.character_skill_node_sets s;

CREATE OR REPLACE VIEW refc.merc_units AS
SELECT g.unit_record AS unit, j.pool AS pool,
       COALESCE(p.ui_recruitment_info, '') AS flavor,
       COALESCE(j.subculture_requirement, '') AS subculture,
       COALESCE(j.faction_requirement, '') AS faction,
       COALESCE(j.tech_requirement, '') AS tech,
       j."group" AS group_key, j.initial_unit_count AS base_count,
       g.max_count AS max_count, g.chance_to_replenish AS replenish_chance
  FROM ref.mercenary_pool_to_groups_junctions j
  JOIN ref.mercenary_unit_groups g ON g."key" = j."group"
  JOIN ref.mercenary_pools p ON p."key" = j.pool;

CREATE OR REPLACE VIEW refc.captive_options AS
WITH RECURSIVE chase(record_key, txt, depth) AS (
  SELECT o.id, l.text, 0
    FROM ref.campaign_post_battle_captive_options o
    LEFT JOIN ref.loc l ON l.tbl = 'campaign_post_battle_captive_options'
         AND l.col = 'onscreen_name' AND l."key" = o.id
  UNION ALL
  SELECT c.record_key, l2.text, c.depth + 1
    FROM chase c
    JOIN ref.loc l2 ON l2.loc_key = substring(c.txt from '^\{\{tr:([\w.]+)\}\}')
   WHERE c.depth < 5
)
SELECT DISTINCT ON (o.id) o.id AS record_key, o.campaign_group AS option_key,
       o.captive_outcome AS outcome, c.txt AS onscreen_name
  FROM ref.campaign_post_battle_captive_options o
  JOIN chase c ON c.record_key = o.id
 ORDER BY o.id, c.depth DESC;

CREATE OR REPLACE VIEW refc.captive_binding AS
WITH RECURSIVE walk(root, node, depth) AS (
  SELECT DISTINCT campaign_group, campaign_group, 0
    FROM ref.campaign_post_battle_captive_options
  UNION
  SELECT w.root, m.id, w.depth + 1
    FROM walk w JOIN ref.campaign_group_members m ON m."group" = w.node
   WHERE w.depth < 6
), crit AS (
  SELECT member, 'culture' AS entity_type, culture AS entity_key, context
    FROM ref.campaign_group_member_criteria_cultures WHERE context ~ '^[A-Z][A-Z_]*$'
  UNION ALL
  SELECT member, 'faction', faction, context
    FROM ref.campaign_group_member_criteria_factions WHERE context ~ '^[A-Z][A-Z_]*$'
  UNION ALL
  SELECT member, 'subculture', subculture, context
    FROM ref.campaign_group_member_criteria_subcultures WHERE context ~ '^[A-Z][A-Z_]*$'
), orig AS (
  SELECT DISTINCT w.root, c.entity_type, c.entity_key,
         EXISTS (SELECT 1 FROM crit c2
                  WHERE c2.member = c.member AND c2.context <> 'ORIGINATOR') AS cond
    FROM walk w JOIN crit c ON c.member = w.node
   WHERE c.context = 'ORIGINATOR'
), scored AS (
  SELECT g.entity_type, g.entity_key, b.button, o.id AS record_key,
         CASE WHEN o.captive_outcome = b.button THEN 0 ELSE 1 END AS r1,
         CASE WHEN g.cond THEN 1 ELSE 0 END AS r2,
         CASE WHEN position('_to_' in g.root) > 0 THEN 1 ELSE 0 END AS r3,
         CASE WHEN o.id ~ '^\d+$' THEN o.id::bigint ELSE 0 END AS r4
    FROM orig g
    JOIN ref.campaign_post_battle_captive_options o ON o.campaign_group = g.root
    JOIN (VALUES ('kill', 'kill'), ('release', 'release'), ('enslave', 'enslave'),
                 ('enslave_slaves_only', 'enslave'),
                 ('enslave_replenishment_only', 'enslave')) b(outcome, button)
      ON b.outcome = o.captive_outcome
)
SELECT DISTINCT ON (entity_type, entity_key, button)
       entity_type, entity_key, button, record_key
  FROM scored
 ORDER BY entity_type, entity_key, button, r1, r2, r3, r4, record_key;
