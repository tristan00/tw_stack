ALTER TABLE corpus.event
  ADD COLUMN IF NOT EXISTS ancillary_id  INTEGER,
  ADD COLUMN IF NOT EXISTS character_cqi INTEGER,
  ADD COLUMN IF NOT EXISTS region_id     INTEGER;

ALTER TABLE corpus.event
  DROP CONSTRAINT IF EXISTS event_ancillary_id_fkey;
ALTER TABLE corpus.event
  ADD CONSTRAINT event_ancillary_id_fkey
  FOREIGN KEY (ancillary_id) REFERENCES dict.ancillary;
ALTER TABLE corpus.event
  DROP CONSTRAINT IF EXISTS event_region_id_fkey;
ALTER TABLE corpus.event
  ADD CONSTRAINT event_region_id_fkey
  FOREIGN KEY (region_id) REFERENCES dict.region;
