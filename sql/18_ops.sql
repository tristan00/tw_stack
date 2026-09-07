CREATE TABLE IF NOT EXISTS ops.session (
  session_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  launch_id         INTEGER,
  trial             TEXT,
  segment_id        INTEGER,
  started_ts        DOUBLE PRECISION NOT NULL,
  ended_ts          DOUBLE PRECISION,
  status            TEXT NOT NULL,
  campaigns         INTEGER,
  turns             INTEGER,
  turns_per_hour    REAL,
  last_turn_seconds REAL,
  stalls            INTEGER,
  host              TEXT,
  code_version      TEXT,
  CHECK (status IN ('running', 'complete', 'failed', 'killed'))
);

CREATE TABLE IF NOT EXISTS ops.session_log (
  session_id BIGINT NOT NULL,
  ord        INTEGER NOT NULL,
  ts         DOUBLE PRECISION NOT NULL,
  level      TEXT NOT NULL,
  message    TEXT NOT NULL,
  PRIMARY KEY (session_id, ord)
);

CREATE TABLE IF NOT EXISTS ops.stall (
  stall_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id   BIGINT,
  campaign_id  INTEGER,
  turn         SMALLINT,
  ts           DOUBLE PRECISION NOT NULL,
  idle_seconds REAL NOT NULL,
  last_roots   TEXT[],
  recovered    BOOLEAN
);

CREATE TABLE IF NOT EXISTS ops.retrain (
  retrain_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  trial          TEXT,
  session_id     BIGINT,
  ts             DOUBLE PRECISION NOT NULL,
  campaign_index INTEGER,
  model          TEXT NOT NULL,
  trained        BOOLEAN,
  rows           INTEGER,
  mae            REAL,
  seconds        REAL,
  error          TEXT
);

ALTER TABLE ops.trial
  ADD COLUMN IF NOT EXISTS train_window  INTEGER,
  ADD COLUMN IF NOT EXISTS retrain_every INTEGER,
  ADD COLUMN IF NOT EXISTS turn_budget   INTEGER,
  ADD COLUMN IF NOT EXISTS start_pool    TEXT,
  ADD COLUMN IF NOT EXISTS status        TEXT;

CREATE TABLE IF NOT EXISTS ops.db_table_stat (
  tbl        TEXT PRIMARY KEY,
  n_rows     BIGINT,
  bytes      BIGINT,
  last_write DOUBLE PRECISION,
  refreshed  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.db_column_stat (
  tbl        TEXT NOT NULL,
  col        TEXT NOT NULL,
  pg_type    TEXT,
  null_frac  REAL,
  n_distinct REAL,
  sample     TEXT,
  refreshed  DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (tbl, col)
);

ALTER TABLE ops.stall
  DROP CONSTRAINT IF EXISTS stall_session_id_fkey;
ALTER TABLE ops.stall
  ADD CONSTRAINT stall_session_id_fkey
  FOREIGN KEY (session_id) REFERENCES ops.session;
ALTER TABLE ops.session_log
  DROP CONSTRAINT IF EXISTS session_log_session_id_fkey;
ALTER TABLE ops.session_log
  ADD CONSTRAINT session_log_session_id_fkey
  FOREIGN KEY (session_id) REFERENCES ops.session;
ALTER TABLE ops.retrain
  DROP CONSTRAINT IF EXISTS retrain_session_id_fkey;
ALTER TABLE ops.retrain
  ADD CONSTRAINT retrain_session_id_fkey
  FOREIGN KEY (session_id) REFERENCES ops.session;
