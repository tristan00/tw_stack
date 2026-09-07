ALTER TABLE corpus.snapshot_campaign
  ADD COLUMN IF NOT EXISTS settlement_income INTEGER,
  ADD COLUMN IF NOT EXISTS province_income   INTEGER,
  ADD COLUMN IF NOT EXISTS raiding_income    INTEGER;

CREATE OR REPLACE VIEW corpus.income_by_source AS
SELECT sc.snapshot_id,
       sc.income                                        AS gross_income,
       sc.settlement_income                             AS building_income,
       sc.raiding_income                                AS raiding_income,
       sc.income - COALESCE(sc.settlement_income, 0)
                 - COALESCE(sc.raiding_income, 0)       AS background_income,
       sc.upkeep                                        AS army_upkeep,
       sc.expenditure                                   AS total_expenditure,
       sc.net_income                                    AS income_next_turn
  FROM corpus.snapshot_campaign sc
 WHERE sc.income IS NOT NULL;
