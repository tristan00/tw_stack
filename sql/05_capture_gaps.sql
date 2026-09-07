ALTER TABLE corpus.snapshot_campaign
  ADD COLUMN IF NOT EXISTS net_income        INTEGER,
  ADD COLUMN IF NOT EXISTS expenditure       INTEGER,
  ADD COLUMN IF NOT EXISTS upkeep            INTEGER,
  ADD COLUMN IF NOT EXISTS trade_value       INTEGER,
  ADD COLUMN IF NOT EXISTS tax_level         SMALLINT,
  ADD COLUMN IF NOT EXISTS influence         INTEGER,
  ADD COLUMN IF NOT EXISTS food_production   INTEGER,
  ADD COLUMN IF NOT EXISTS food_consumption  INTEGER,
  ADD COLUMN IF NOT EXISTS imperium_level    SMALLINT,
  ADD COLUMN IF NOT EXISTS merc_pool_set_id  BIGINT;

ALTER TABLE corpus.world_army
  ADD COLUMN IF NOT EXISTS raiding_income INTEGER,
  ADD COLUMN IF NOT EXISTS upkeep         INTEGER;

ALTER TABLE corpus.province_state
  ADD COLUMN IF NOT EXISTS income_breakdown_set_id BIGINT;

CREATE TABLE IF NOT EXISTS corpus.income_breakdown_set_member (
  set_id BIGINT NOT NULL,
  ord    SMALLINT NOT NULL,
  label  TEXT NOT NULL,
  amount INTEGER,
  PRIMARY KEY (set_id, ord)
);

CREATE TABLE IF NOT EXISTS corpus.effect_set_member (
  set_id           BIGINT NOT NULL,
  ord              SMALLINT NOT NULL,
  effect_bundle_id INTEGER NOT NULL,
  effect_id        INTEGER NOT NULL,
  value            REAL,
  scope_id         INTEGER,
  PRIMARY KEY (set_id, ord)
);

CREATE TABLE IF NOT EXISTS corpus.faction_merc_set_member (
  set_id      BIGINT NOT NULL,
  ord         SMALLINT NOT NULL,
  unit_id     INTEGER NOT NULL,
  base_max    SMALLINT,
  current_max SMALLINT,
  PRIMARY KEY (set_id, ord)
);
