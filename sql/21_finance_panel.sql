CREATE TABLE IF NOT EXISTS corpus.finance_panel_row (
  row_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id  INTEGER,
  turn         SMALLINT,
  ts           DOUBLE PRECISION NOT NULL,
  kind         TEXT NOT NULL,
  ord          SMALLINT,
  component_id TEXT,
  label        TEXT,
  value        TEXT
);

CREATE INDEX IF NOT EXISTS ix_finance_panel_row_campaign
  ON corpus.finance_panel_row (campaign_id, turn);

ALTER TABLE corpus.finance_panel_row
  DROP CONSTRAINT IF EXISTS finance_panel_row_campaign_id_fkey;
ALTER TABLE corpus.finance_panel_row
  ADD CONSTRAINT finance_panel_row_campaign_id_fkey
  FOREIGN KEY (campaign_id) REFERENCES corpus.campaign;
