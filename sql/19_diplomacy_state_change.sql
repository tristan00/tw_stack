CREATE TABLE IF NOT EXISTS analytics.diplomacy_state_change (
  campaign_id INTEGER NOT NULL,
  faction_id  INTEGER NOT NULL,
  kind        TEXT NOT NULL,
  from_turn   SMALLINT NOT NULL,
  to_turn     SMALLINT NOT NULL,
  PRIMARY KEY (campaign_id, faction_id, kind, from_turn)
);

CREATE INDEX IF NOT EXISTS ix_diplomacy_state_change_faction
  ON analytics.diplomacy_state_change (faction_id, kind);
