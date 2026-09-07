CREATE TABLE IF NOT EXISTS corpus.event (
  event_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id INTEGER,
  turn        SMALLINT,
  ts          DOUBLE PRECISION NOT NULL,
  kind        TEXT NOT NULL,
  incident_id INTEGER,
  dilemma_id  INTEGER,
  choice      TEXT,
  faction_id  INTEGER
);

CREATE INDEX IF NOT EXISTS ix_event_campaign ON corpus.event (campaign_id, turn);

ALTER TABLE corpus.event
  DROP CONSTRAINT IF EXISTS event_campaign_id_fkey;
ALTER TABLE corpus.event
  ADD CONSTRAINT event_campaign_id_fkey
  FOREIGN KEY (campaign_id) REFERENCES corpus.campaign;
ALTER TABLE corpus.event
  DROP CONSTRAINT IF EXISTS event_incident_id_fkey;
ALTER TABLE corpus.event
  ADD CONSTRAINT event_incident_id_fkey
  FOREIGN KEY (incident_id) REFERENCES dict.incident;
ALTER TABLE corpus.event
  DROP CONSTRAINT IF EXISTS event_dilemma_id_fkey;
ALTER TABLE corpus.event
  ADD CONSTRAINT event_dilemma_id_fkey
  FOREIGN KEY (dilemma_id) REFERENCES dict.dilemma;
ALTER TABLE corpus.event
  DROP CONSTRAINT IF EXISTS event_faction_id_fkey;
ALTER TABLE corpus.event
  ADD CONSTRAINT event_faction_id_fkey
  FOREIGN KEY (faction_id) REFERENCES dict.faction;
