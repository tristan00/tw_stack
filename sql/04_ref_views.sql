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
