ALTER TABLE ops.db_table_stat
  ADD COLUMN IF NOT EXISTS writes BIGINT;

CREATE OR REPLACE VIEW analytics.model_generation AS
  SELECT trial, generation, started AS seg_from_ts,
         LEAD(started) OVER (ORDER BY started) AS seg_to_ts,
         campaigns, corpus_n_decisions AS corpus_decisions
  FROM ops.trial WHERE NOT archived AND started IS NOT NULL;

ALTER TABLE analytics.model_agreement
  ADD COLUMN IF NOT EXISTS n_a SMALLINT,
  ADD COLUMN IF NOT EXISTS n_b SMALLINT;

ALTER TABLE analytics.agreement_breakdown
  ADD COLUMN IF NOT EXISTS a_rank    REAL,
  ADD COLUMN IF NOT EXISTS a_pct     REAL,
  ADD COLUMN IF NOT EXISTS b_rank    REAL,
  ADD COLUMN IF NOT EXISTS b_pct     REAL,
  ADD COLUMN IF NOT EXISTS delta_pct REAL,
  ADD COLUMN IF NOT EXISTS fell_back INTEGER;

ALTER TABLE analytics.agreement_summary
  ADD COLUMN IF NOT EXISTS rho_q1 REAL,
  ADD COLUMN IF NOT EXISTS rho_q3 REAL;

ALTER TABLE analytics.agreement_series
  ADD COLUMN IF NOT EXISTS rho_q1 REAL,
  ADD COLUMN IF NOT EXISTS rho_q3 REAL;
