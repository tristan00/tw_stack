INSERT INTO dict.family (family, ref_tbl, ref_col, loc_col, note)
VALUES ('effect', 'effects', 'effect', NULL,
        'effect keys from effect:key(); values via effect:value(), scope via effect:scope()')
ON CONFLICT (family) DO NOTHING;

CREATE TABLE IF NOT EXISTS dict.effect (
  id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key           TEXT NOT NULL UNIQUE,
  is_reference  BOOLEAN NOT NULL,
  ref_build_id  INTEGER,
  note          TEXT,
  CHECK (is_reference = (ref_build_id IS NOT NULL))
);

ALTER TABLE corpus.income_breakdown_set_member
  DROP CONSTRAINT IF EXISTS income_breakdown_set_member_set_id_fkey;
ALTER TABLE corpus.income_breakdown_set_member
  ADD CONSTRAINT income_breakdown_set_member_set_id_fkey
  FOREIGN KEY (set_id) REFERENCES corpus.state_set;

ALTER TABLE corpus.effect_set_member
  DROP CONSTRAINT IF EXISTS effect_set_member_set_id_fkey;
ALTER TABLE corpus.effect_set_member
  ADD CONSTRAINT effect_set_member_set_id_fkey
  FOREIGN KEY (set_id) REFERENCES corpus.state_set;
ALTER TABLE corpus.effect_set_member
  DROP CONSTRAINT IF EXISTS effect_set_member_effect_bundle_id_fkey;
ALTER TABLE corpus.effect_set_member
  ADD CONSTRAINT effect_set_member_effect_bundle_id_fkey
  FOREIGN KEY (effect_bundle_id) REFERENCES dict.effect_bundle;
ALTER TABLE corpus.effect_set_member
  DROP CONSTRAINT IF EXISTS effect_set_member_effect_id_fkey;
ALTER TABLE corpus.effect_set_member
  ADD CONSTRAINT effect_set_member_effect_id_fkey
  FOREIGN KEY (effect_id) REFERENCES dict.effect;

ALTER TABLE corpus.faction_merc_set_member
  DROP CONSTRAINT IF EXISTS faction_merc_set_member_set_id_fkey;
ALTER TABLE corpus.faction_merc_set_member
  ADD CONSTRAINT faction_merc_set_member_set_id_fkey
  FOREIGN KEY (set_id) REFERENCES corpus.state_set;
ALTER TABLE corpus.faction_merc_set_member
  DROP CONSTRAINT IF EXISTS faction_merc_set_member_unit_id_fkey;
ALTER TABLE corpus.faction_merc_set_member
  ADD CONSTRAINT faction_merc_set_member_unit_id_fkey
  FOREIGN KEY (unit_id) REFERENCES dict.unit;

ALTER TABLE corpus.province_state
  DROP CONSTRAINT IF EXISTS province_state_income_breakdown_set_id_fkey;
ALTER TABLE corpus.province_state
  ADD CONSTRAINT province_state_income_breakdown_set_id_fkey
  FOREIGN KEY (income_breakdown_set_id) REFERENCES corpus.state_set;

ALTER TABLE corpus.snapshot_campaign
  DROP CONSTRAINT IF EXISTS snapshot_campaign_merc_pool_set_id_fkey;
ALTER TABLE corpus.snapshot_campaign
  ADD CONSTRAINT snapshot_campaign_merc_pool_set_id_fkey
  FOREIGN KEY (merc_pool_set_id) REFERENCES corpus.state_set;

ALTER TABLE corpus.snapshot_campaign
  ADD COLUMN IF NOT EXISTS effect_set_id BIGINT;
ALTER TABLE corpus.snapshot_campaign
  DROP CONSTRAINT IF EXISTS snapshot_campaign_effect_set_id_fkey;
ALTER TABLE corpus.snapshot_campaign
  ADD CONSTRAINT snapshot_campaign_effect_set_id_fkey
  FOREIGN KEY (effect_set_id) REFERENCES corpus.state_set;
