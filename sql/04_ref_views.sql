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
