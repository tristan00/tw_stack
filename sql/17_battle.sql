CREATE TABLE IF NOT EXISTS corpus.battle (
  battle_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id         INTEGER,
  turn                SMALLINT,
  ts                  DOUBLE PRECISION NOT NULL,
  battle_type         TEXT,
  autoresolved        BOOLEAN,
  attacker_result     TEXT,
  defender_result     TEXT,
  attacker_casualties INTEGER,
  defender_casualties INTEGER,
  attacker_kills      INTEGER,
  defender_kills      INTEGER,
  attacker_hp_lost    INTEGER,
  defender_hp_lost    INTEGER,
  attacker_routed     REAL,
  defender_routed     REAL,
  siege               BOOLEAN,
  naval               BOOLEAN,
  ambush              BOOLEAN,
  night               BOOLEAN,
  region_id           INTEGER
);

CREATE TABLE IF NOT EXISTS corpus.battle_participant (
  battle_id  BIGINT NOT NULL,
  ord        SMALLINT NOT NULL,
  side       TEXT NOT NULL,
  char_cqi   INTEGER,
  mf_cqi     INTEGER,
  faction_id INTEGER,
  PRIMARY KEY (battle_id, side, ord)
);

CREATE TABLE IF NOT EXISTS corpus.battle_unit (
  battle_id BIGINT NOT NULL,
  ord       SMALLINT NOT NULL,
  side      TEXT NOT NULL,
  part_ord  SMALLINT NOT NULL,
  unit_id   INTEGER,
  unit_cqi  INTEGER,
  PRIMARY KEY (battle_id, ord)
);

CREATE INDEX IF NOT EXISTS ix_battle_campaign ON corpus.battle (campaign_id, turn);

ALTER TABLE corpus.battle
  DROP CONSTRAINT IF EXISTS battle_campaign_id_fkey;
ALTER TABLE corpus.battle
  ADD CONSTRAINT battle_campaign_id_fkey
  FOREIGN KEY (campaign_id) REFERENCES corpus.campaign;
ALTER TABLE corpus.battle
  DROP CONSTRAINT IF EXISTS battle_region_id_fkey;
ALTER TABLE corpus.battle
  ADD CONSTRAINT battle_region_id_fkey
  FOREIGN KEY (region_id) REFERENCES dict.region;

ALTER TABLE corpus.battle_participant
  DROP CONSTRAINT IF EXISTS battle_participant_battle_id_fkey;
ALTER TABLE corpus.battle_participant
  ADD CONSTRAINT battle_participant_battle_id_fkey
  FOREIGN KEY (battle_id) REFERENCES corpus.battle;
ALTER TABLE corpus.battle_participant
  DROP CONSTRAINT IF EXISTS battle_participant_faction_id_fkey;
ALTER TABLE corpus.battle_participant
  ADD CONSTRAINT battle_participant_faction_id_fkey
  FOREIGN KEY (faction_id) REFERENCES dict.faction;

ALTER TABLE corpus.battle_unit
  DROP CONSTRAINT IF EXISTS battle_unit_battle_id_fkey;
ALTER TABLE corpus.battle_unit
  ADD CONSTRAINT battle_unit_battle_id_fkey
  FOREIGN KEY (battle_id) REFERENCES corpus.battle;
ALTER TABLE corpus.battle_unit
  DROP CONSTRAINT IF EXISTS battle_unit_unit_id_fkey;
ALTER TABLE corpus.battle_unit
  ADD CONSTRAINT battle_unit_unit_id_fkey
  FOREIGN KEY (unit_id) REFERENCES dict.unit;
