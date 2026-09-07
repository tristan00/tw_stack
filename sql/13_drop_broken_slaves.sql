ALTER TABLE corpus.snapshot_campaign
  DROP COLUMN IF EXISTS slaves,
  DROP COLUMN IF EXISTS max_slaves;

ALTER TABLE corpus.province_state
  DROP COLUMN IF EXISTS slaves_number,
  DROP COLUMN IF EXISTS slaves_max,
  DROP COLUMN IF EXISTS slaves_income_mod;
